from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from niadra.adapters.outbound.keys.local import LocalKeyVault, MemoryKeyStore


async def test_roundtrip_and_aad_binding() -> None:
    vault = LocalKeyVault(b"k" * 32)
    space = uuid4()
    ct, version = await vault.encrypt(space, b"marina", b"facts:1:value")
    assert await vault.decrypt(space, ct, b"facts:1:value", version) == b"marina"
    with pytest.raises(InvalidTag):
        await vault.decrypt(space, ct, b"facts:2:value", version)


async def test_keys_survive_a_restart_through_the_store() -> None:
    store = MemoryKeyStore()
    space = uuid4()
    ct, version = await LocalKeyVault(b"k" * 32, store).encrypt(space, b"x", b"a")
    assert await LocalKeyVault(b"k" * 32, store).decrypt(space, ct, b"a", version) == b"x"


async def test_hmac_is_stable_per_space_and_differs_across_spaces() -> None:
    vault = LocalKeyVault(b"k" * 32)
    a, b = uuid4(), uuid4()
    assert await vault.hmac(a, "handle", b"+5511") == await vault.hmac(a, "handle", b"+5511")
    assert await vault.hmac(a, "handle", b"+5511") != await vault.hmac(b, "handle", b"+5511")


async def test_destroyed_space_cannot_decrypt() -> None:
    vault = LocalKeyVault(b"k" * 32)
    space = uuid4()
    ct, version = await vault.encrypt(space, b"x", b"a")
    await vault.destroy_space(space)
    with pytest.raises(PermissionError):
        await vault.decrypt(space, ct, b"a", version)


async def test_one_companys_key_never_opens_anothers_data() -> None:
    # Rows of two spaces mixed by a bug fail to decrypt instead of leaking.
    vault = LocalKeyVault(b"k" * 32)
    a, b = uuid4(), uuid4()
    await vault.encrypt(b, b"warm up b", b"a")
    ciphertext, version = await vault.encrypt(a, b"marina", b"facts:1:value")
    with pytest.raises(InvalidTag):
        await vault.decrypt(b, ciphertext, b"facts:1:value", version)
