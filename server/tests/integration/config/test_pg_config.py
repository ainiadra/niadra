"""PgConfigRepository and the snapshot apply path against PostgreSQL (4.7)."""

import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.config import CachedConfigProvider, ConfigService
from niadra.domain.common.errors import InvalidInput
from niadra.domain.config import SPACE_DOC, StoredDocument
from niadra.domain.config.signing import SignedSnapshot, content_hash, sign
from niadra.testing.cell import SPACE_ID, sample_config
from tests.integration.conftest import CloningUnitOfWork

CELL = "it-cell"


@pytest.fixture
async def uow(pg_uow: PgUnitOfWorkFactory) -> PgUnitOfWorkFactory:
    async with pg_uow.cell() as tx:
        await tx.session.execute(text("TRUNCATE config_snapshot, config_documents"))
        await tx.session.execute(text("UPDATE cell_state SET value = 1 WHERE key = 'cache_epoch'"))
    return pg_uow


def snapshot(key: Ed25519PrivateKey, version: int, *configs: object) -> SignedSnapshot:
    documents = [c.model_dump(mode="json") for c in configs]  # type: ignore[attr-defined]
    manifest = {
        "cell_id": CELL,
        "version": version,
        "created_at": "2026-09-22T17:00:00Z",
        "spaces": {d["space_id"]: content_hash(d) for d in documents},
        "removed": {},
        "moved": {},
    }
    blobs = {content_hash(d): json.dumps(d).encode() for d in documents}
    return SignedSnapshot(json.dumps(manifest).encode(), sign(manifest, key), blobs)


async def test_snapshots_rotate_active_to_previous(uow: PgUnitOfWorkFactory) -> None:
    for version in (1, 2, 3):
        async with uow.cell() as tx:
            assert await tx.config.active_version(CELL, lock=True) == (version - 1 or None)
            await tx.config.save_snapshot(CELL, version, {"v": version}, b"sig")
    async with uow.cell() as tx:
        rows = (
            await tx.session.execute(text("SELECT slot, version FROM config_snapshot ORDER BY slot"))
        ).all()
    assert [tuple(r) for r in rows] == [("active", 3), ("previous", 2)]


async def test_documents_are_replaced_and_unchanged_ones_keep_their_version(uow: PgUnitOfWorkFactory) -> None:
    a, b = uuid4(), uuid4()
    async with uow.cell() as tx:
        await tx.config.replace_documents(
            SPACE_DOC,
            [StoredDocument(a, SPACE_DOC, "h-a", 1, {"x": 1}), StoredDocument(b, SPACE_DOC, "h-b", 1, {})],
        )
    async with uow.cell() as tx:
        await tx.config.replace_documents(SPACE_DOC, [StoredDocument(a, SPACE_DOC, "h-a", 2, {"x": 1})])
        [doc] = await tx.config.documents(SPACE_DOC)
    assert (doc.space_id, doc.version, doc.document) == (a, 1, {"x": 1})


async def test_cache_epoch_bumps_atomically(uow: PgUnitOfWorkFactory) -> None:
    async with uow.cell() as tx:
        assert await tx.config.cache_epoch() == 1
        assert await tx.config.bump_cache_epoch() == 2
    async with uow.cell() as tx:
        assert await tx.config.cache_epoch() == 2


async def test_apply_and_serve_through_postgres(uow: CloningUnitOfWork) -> None:
    key = Ed25519PrivateKey.generate()
    service = ConfigService(uow, [key.public_key()], CELL, "1.0.0")
    other = sample_config(uuid4())
    await uow.ensure(SPACE_ID)  # SPACE_ID has a database; `other` has none yet
    await service.apply(snapshot(key, 1, sample_config(), other))
    provider = CachedConfigProvider(uow, CELL)
    await provider.refresh()
    assert provider.space(SPACE_ID) == sample_config()
    config = sample_config()
    broken = config.model_copy(update={"sources": [*config.sources, config.sources[0]]})
    with pytest.raises(InvalidInput):
        await service.apply(snapshot(key, 2, broken))
    await service.apply(snapshot(key, 2, sample_config()))
    await provider.refresh()
    assert [c.space_id for c in provider.spaces()] == [SPACE_ID]
    assert provider.version == 2
    async with uow(SPACE_ID) as tx:
        rows = await tx.pipeline.list_schedules()
    # The serving space got its schedules in its own database: the minute, hourly and daily jobs and the
    # upkeep of the database. A space without a database gets them from the provisioner when it is created.
    assert {r.space_id for r in rows} == {SPACE_ID}
    assert len(rows) == 9


async def test_a_cell_keeps_a_cursor_by_name(uow: PgUnitOfWorkFactory) -> None:
    async with uow.cell() as tx:
        assert await tx.config.state("access_feed_us") is None
        await tx.config.set_state("access_feed_us", 1_700_000_000_000_000)
        await tx.config.set_state("access_feed_us", 1_700_000_000_000_001)
    async with uow.cell() as tx:
        assert await tx.config.state("access_feed_us") == 1_700_000_000_000_001
        assert await tx.config.cache_epoch() == 1
