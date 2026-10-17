"""Envelope encryption with a local key-encryption key (KEK).

Each space gets random 256-bit data keys (DEKs), wrapped by the KEK and cached in memory.
The KEK comes from a secret file or environment variable; a Vault Transit adapter can
replace this class without touching callers.
"""

import asyncio
import hashlib
import hmac as hmac_lib
import os
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from niadra.domain.common.errors import Conflict

NONCE_BYTES = 12


def hmac_root_of(kek: bytes) -> bytes:
    """The keyed-hash root a local KEK implies; a move to KMS keeps it, so stored hashes still match."""
    return hashlib.sha256(b"niadra-hmac-root" + kek).digest()


def derived_key(root: bytes, space_id: UUID, purpose: str) -> bytes:
    """One keyed-hash key per space and purpose (5.14.1)."""
    return hmac_lib.new(root, f"{space_id}:{purpose}".encode(), hashlib.sha256).digest()


class WrappedKeyStore(Protocol):
    """Where wrapped data keys persist (the `data_keys` table of the cell database)."""

    async def load(self, space_id: UUID) -> dict[int, bytes]: ...

    async def save(self, space_id: UUID, version: int, wrapped: bytes, kek_id: str | None = None) -> None: ...

    async def destroy(self, space_id: UUID) -> list[int]:
        """Deletes every wrapped key of the space; returns the versions deleted."""
        ...


class MemoryKeyStore:
    def __init__(self, kek_id: str = "local") -> None:
        self.keys: dict[UUID, dict[int, bytes]] = {}
        self.kek_ids: dict[tuple[UUID, int], str] = {}
        self._kek_id = kek_id

    async def load(self, space_id: UUID) -> dict[int, bytes]:
        return dict(self.keys.get(space_id, {}))

    async def load_wrapped(self, space_id: UUID) -> dict[int, tuple[bytes, str]]:
        return {v: (w, self.kek_ids[(space_id, v)]) for v, w in self.keys.get(space_id, {}).items()}

    async def save(self, space_id: UUID, version: int, wrapped: bytes, kek_id: str | None = None) -> None:
        if version in self.keys.get(space_id, {}):
            if self.keys[space_id][version] != wrapped:
                raise Conflict("the data key version already exists")
            return
        self.keys.setdefault(space_id, {})[version] = wrapped
        self.kek_ids[(space_id, version)] = kek_id or self._kek_id

    async def destroy(self, space_id: UUID) -> list[int]:
        versions = sorted(self.keys.pop(space_id, {}))
        for version in versions:
            self.kek_ids.pop((space_id, version), None)
        return versions


class LocalKeyVault:
    def __init__(self, kek: bytes, store: WrappedKeyStore | None = None) -> None:
        if len(kek) != 32:
            raise ValueError("the KEK must be 32 bytes")
        self._kek = AESGCM(kek)
        self._hmac_root = hmac_root_of(kek)
        self._store = store or MemoryKeyStore()
        self._deks: dict[UUID, dict[int, bytes]] = {}
        self._destroyed: set[UUID] = set()
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def encrypt(self, space_id: UUID, plaintext: bytes, aad: bytes) -> tuple[bytes, int]:
        version, dek = await self._current(space_id)
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(dek).encrypt(nonce, plaintext, aad), version

    async def decrypt(self, space_id: UUID, ciphertext: bytes, aad: bytes, dek_version: int) -> bytes:
        dek = await self._dek(space_id, dek_version)
        return AESGCM(dek).decrypt(ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:], aad)

    async def hmac(self, space_id: UUID, purpose: str, value: bytes) -> bytes:
        key = derived_key(self._hmac_root, space_id, purpose)
        return hmac_lib.new(key, value, hashlib.sha256).digest()

    async def key_versions(self, space_id: UUID) -> list[int]:
        return sorted(await self._store.load(space_id))

    async def destroy_space(self, space_id: UUID) -> list[int]:
        """The wrapped keys leave the store and the unwrapped ones this process's memory; other
        processes forget theirs when they restart, and the space's database is gone by then."""
        self._destroyed.add(space_id)
        self._deks.pop(space_id, None)
        return await self._store.destroy(space_id)

    async def _current(self, space_id: UUID) -> tuple[int, bytes]:
        keys = await self._keys(space_id)
        if not keys:
            async with self._locks.setdefault(space_id, asyncio.Lock()):
                keys = await self._keys(space_id)
                if not keys:
                    dek = AESGCM.generate_key(bit_length=256)
                    nonce = os.urandom(NONCE_BYTES)
                    try:
                        await self._store.save(
                            space_id, 1, nonce + self._kek.encrypt(nonce, dek, space_id.bytes)
                        )
                    except Conflict:
                        # Another process created the space's first key a moment earlier: use theirs.
                        self._deks.pop(space_id, None)
                        keys = await self._keys(space_id)
                    else:
                        keys[1] = dek
        version = max(keys)
        return version, keys[version]

    async def _dek(self, space_id: UUID, version: int) -> bytes:
        keys = await self._keys(space_id)
        if version not in keys:
            self._deks.pop(space_id, None)  # another process may have rotated; reload once
            keys = await self._keys(space_id)
        if version not in keys:
            raise KeyError(f"unknown data key version {version}")
        return keys[version]

    async def _keys(self, space_id: UUID) -> dict[int, bytes]:
        if space_id in self._destroyed:
            raise PermissionError("the space's keys were destroyed")
        if space_id not in self._deks:
            self._deks[space_id] = {
                version: self._kek.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], space_id.bytes)
                for version, wrapped in (await self._store.load(space_id)).items()
            }
        return self._deks[space_id]
