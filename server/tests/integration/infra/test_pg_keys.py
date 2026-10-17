"""Wrapped data keys in `data_keys` of the cell database, shared by every process of the cell (5.14.1)."""

from uuid import uuid4

import pytest

from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.domain.common.errors import Conflict
from tests.integration.conftest import CELL_DB, PgServer


async def test_a_second_process_decrypts_with_the_stored_key(server: PgServer) -> None:
    engine = server.engine("system", CELL_DB)
    try:
        store = PgWrappedKeyStore(engine, "kek-1")
        space = uuid4()
        ciphertext, version = await LocalKeyVault(b"k" * 32, store).encrypt(
            space, b"marina", b"facts:1:value"
        )
        assert (
            await LocalKeyVault(b"k" * 32, store).decrypt(space, ciphertext, b"facts:1:value", version)
            == b"marina"
        )
        assert list((await store.load(space)).keys()) == [1]
        await store.save(space, 1, (await store.load(space))[1])  # same key again is fine
        with pytest.raises(Conflict):
            await store.save(space, 1, b"another wrapped key")
        # A deleted space: its wrapped keys go, and no process can open its ciphertext any more.
        assert await store.destroy(space) == [1]
        with pytest.raises(KeyError):
            await LocalKeyVault(b"k" * 32, store).decrypt(space, ciphertext, b"facts:1:value", version)
    finally:
        await engine.dispose()
