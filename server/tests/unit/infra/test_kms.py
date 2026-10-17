"""The master key in KMS: envelope encryption bound to the space."""

import json
import os
from typing import Any
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from niadra.adapters.outbound.keys.kms import (
    CELL_KEYS,
    KMS_PREFIX,
    KmsKeyVault,
    load_cell_keys,
)
from niadra.adapters.outbound.keys.local import MemoryKeyStore

KEY = "alias/niadra-cell"
ARN = "arn:aws:kms:us-east-2:111122223333:key/4d1c-cell"


class FakeKms:
    """KMS as the vault sees it: the key never leaves, and a blob opens only under its key and context."""

    def __init__(self) -> None:
        self._master = AESGCM(os.urandom(32))
        self.calls: list[str] = []

    async def generate_data_key(self, **p: Any) -> dict[str, Any]:
        self.calls.append("generate_data_key")
        assert p["KeySpec"] == "AES_256"
        plaintext = os.urandom(32)
        return {"Plaintext": plaintext, "CiphertextBlob": self._wrap(plaintext, p), "KeyId": ARN}

    async def encrypt(self, **p: Any) -> dict[str, Any]:
        self.calls.append("encrypt")
        return {"CiphertextBlob": self._wrap(p["Plaintext"], p), "KeyId": ARN}

    async def decrypt(self, **p: Any) -> dict[str, Any]:
        self.calls.append("decrypt")
        if p["KeyId"] != ARN:
            raise PermissionError("IncorrectKeyException")
        blob = p["CiphertextBlob"]
        plaintext = self._master.decrypt(blob[:12], blob[12:], _aad(p["EncryptionContext"]))
        return {"Plaintext": plaintext, "KeyId": ARN}

    def _wrap(self, plaintext: bytes, p: dict[str, Any]) -> bytes:
        assert p["KeyId"] == KEY
        nonce = os.urandom(12)
        return nonce + self._master.encrypt(nonce, plaintext, _aad(p["EncryptionContext"]))


def _aad(context: dict[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True).encode()


async def test_a_wrapped_key_of_one_space_never_opens_as_another() -> None:
    a, b = uuid4(), uuid4()
    store = MemoryKeyStore()
    kms = FakeKms()
    vault, _ = await KmsKeyVault.open(kms, KEY, store)
    await vault.encrypt(a, b"x", b"aad")
    # Someone copies space A's wrapped key into space B's row: KMS refuses the other context.
    store.keys[b] = dict(store.keys[a])
    store.kek_ids[(b, 1)] = store.kek_ids[(a, 1)]
    with pytest.raises(InvalidTag):
        await vault.encrypt(b, b"y", b"aad")


async def test_each_data_key_is_unwrapped_once_per_process() -> None:
    space = uuid4()
    store = MemoryKeyStore()
    kms = FakeKms()
    writer, _ = await KmsKeyVault.open(kms, KEY, store)
    ciphertext, version = await writer.encrypt(space, b"x", b"aad")
    kms.calls.clear()
    reader, _ = await KmsKeyVault.open(kms, KEY, store)
    for _ in range(5):
        assert await reader.decrypt(space, ciphertext, b"aad", version) == b"x"
    assert kms.calls.count("decrypt") == 3  # the two cell keys, then the space's key once


async def test_a_cell_born_on_kms_creates_its_roots_once() -> None:
    store = MemoryKeyStore()
    kms = FakeKms()
    first = await load_cell_keys(kms, KEY, store)
    second = await load_cell_keys(kms, KEY, store)
    assert first == second
    assert set(store.keys[CELL_KEYS]) == {1, 2}


async def test_a_rotation_adds_a_version_and_old_rows_keep_opening() -> None:
    # KMS rotates the master key yearly; data keys get new versions without re-encrypting.
    space = uuid4()
    store = MemoryKeyStore()
    vault, _ = await KmsKeyVault.open(FakeKms(), KEY, store)
    old, v1 = await vault.encrypt(space, b"antes", b"aad")
    assert await vault.rotate_space(space) == 2
    new, v2 = await vault.encrypt(space, b"depois", b"aad")
    assert (v1, v2) == (1, 2)
    assert await vault.decrypt(space, old, b"aad", v1) == b"antes"
    assert await vault.decrypt(space, new, b"aad", v2) == b"depois"


async def test_deleting_a_space_destroys_its_kms_wrapped_keys() -> None:
    # A deleted space (14): the blobs leave the store, so KMS has nothing left to unwrap.
    store = MemoryKeyStore()
    kms = FakeKms()
    vault, _ = await KmsKeyVault.open(kms, KEY, store)
    space, other = uuid4(), uuid4()
    ciphertext, version = await vault.encrypt(space, b"marina", b"facts:1:value")
    await vault.encrypt(other, b"joana", b"facts:2:value")
    await vault.rotate_space(space)
    assert await vault.key_versions(space) == [1, 2]

    assert await vault.destroy_space(space) == [1, 2]
    assert await store.load_wrapped(space) == {}
    with pytest.raises(PermissionError):
        await vault.decrypt(space, ciphertext, b"facts:1:value", version)
    fresh, _ = await KmsKeyVault.open(kms, KEY, store)
    with pytest.raises(KeyError):
        await fresh.decrypt(space, ciphertext, b"facts:1:value", version)
    assert await store.load_wrapped(other) != {}


CUSTOMER = "arn:aws:kms:us-east-2:444455556666:key/0b6f7a1e-5c1d-4d7e-9a0b-1c2d3e4f5a6b"


class TwoAccountKms(FakeKms):
    """The cell's key and a customer's, each wrapping only its own blobs; the customer's can be revoked."""

    def __init__(self) -> None:
        super().__init__()
        self.customer = AESGCM(os.urandom(32))
        self.customer_granted = True

    async def generate_data_key(self, **p: Any) -> dict[str, Any]:
        if p["KeyId"] != CUSTOMER:
            return await super().generate_data_key(**p)
        if not self.customer_granted:
            raise PermissionError("AccessDeniedException")
        plaintext, nonce = os.urandom(32), os.urandom(12)
        blob = nonce + self.customer.encrypt(nonce, plaintext, _aad(p["EncryptionContext"]))
        return {"Plaintext": plaintext, "CiphertextBlob": blob, "KeyId": CUSTOMER}

    async def decrypt(self, **p: Any) -> dict[str, Any]:
        if p["KeyId"] != CUSTOMER:
            return await super().decrypt(**p)
        if not self.customer_granted:
            raise PermissionError("AccessDeniedException")
        blob = p["CiphertextBlob"]
        return {
            "Plaintext": self.customer.decrypt(blob[:12], blob[12:], _aad(p["EncryptionContext"])),
            "KeyId": CUSTOMER,
        }


async def test_a_space_with_a_customer_key_writes_under_it_and_never_falls_back() -> None:
    # 10 D.1: the contract's key wraps the space's new data keys; without it the space stops writing.
    from niadra.domain.common.errors import Unavailable

    space, other = uuid4(), uuid4()
    store, kms = MemoryKeyStore(), TwoAccountKms()
    vault, _ = await KmsKeyVault.open(kms, KEY, store)
    before, v1 = await vault.encrypt(space, b"antes", b"events:1:content")
    vault.use_space_keys(lambda s: CUSTOMER if s == space else None)
    after, v2 = await vault.encrypt(space, b"depois", b"events:2:content")
    assert (v1, v2) == (1, 2)
    wrapped = await store.load_wrapped(space)
    assert (wrapped[1][1], wrapped[2][1]) == (KMS_PREFIX + ARN, KMS_PREFIX + CUSTOMER)
    # Old rows keep opening under the key that wrapped them; a second write does not rotate again.
    fresh, _ = await KmsKeyVault.open(kms, KEY, store)
    fresh.use_space_keys(lambda s: CUSTOMER if s == space else None)
    assert await fresh.decrypt(space, before, b"events:1:content", v1) == b"antes"
    assert await fresh.decrypt(space, after, b"events:2:content", v2) == b"depois"
    assert (await fresh.encrypt(space, b"mais", b"events:3:content"))[1] == 2
    # Another space stays on the cell's key.
    assert (await fresh.encrypt(other, b"x", b"events:1:content"))[1] == 1
    assert (await store.load_wrapped(other))[1][1] == KMS_PREFIX + ARN
    # The customer revokes the grant: the space refuses to write rather than use the cell's key.
    kms.customer_granted = False
    brand_new = uuid4()
    blocked, _ = await KmsKeyVault.open(kms, KEY, store)
    blocked.use_space_keys(lambda s: CUSTOMER)
    with pytest.raises(Unavailable):
        await blocked.encrypt(brand_new, b"x", b"events:1:content")
    assert await store.load_wrapped(brand_new) == {}
