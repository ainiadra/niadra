"""`PgSourceKeyRepository` against PostgreSQL: snapshot upserts by version, cell-wide lookup,
revocation, request idempotency and row-level security."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.edge.limits import IdempotencyRecord
from niadra.domain.vocabulary import Scope

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def _row(space: UUID, *, version: int = 1, status: str = "active") -> SourceKeyRow:
    key_id = uuid4().hex[:16]
    return SourceKeyRow(
        space_id=space,
        key_id=key_id,
        source_id=uuid4(),
        fast_hash=key_id.encode() * 2,
        slow_hash="scrypt$x",
        scopes=frozenset({Scope.TRACK, Scope.ACT}),
        status=status,
        version=version,
    )


async def test_snapshot_upsert_lookup_and_revoke(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    row = _row(space, version=2)
    # Source keys live in the cell database: a key names its space only once it is found.
    async with pg_uow.cell() as uow:
        assert await uow.keys.upsert_keys(space, [row]) == 1
        assert await uow.keys.upsert_keys(space, [replace(row, version=1, status="revoked")]) == 0

    async with pg_uow.cell() as uow:
        found = await uow.keys.find_by_fast_hash(row.fast_hash)
        assert await uow.keys.key_id_exists(row.key_id)
        assert not await uow.keys.key_id_exists("nope")
        assert await uow.keys.revoke(row.key_id)
        assert not await uow.keys.revoke(row.key_id)
        revoked = await uow.keys.find_by_fast_hash(row.fast_hash)

    assert found == row
    assert revoked is not None
    assert revoked.revoked


async def test_idempotency_record_is_replaced_only_after_expiry(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    first = IdempotencyRecord("idem-1", b"h1", 201, {"merge_id": "m1"}, NOW)
    other = IdempotencyRecord("idem-1", b"h2", 201, {"merge_id": "m2"}, NOW + timedelta(hours=1))
    late = IdempotencyRecord("idem-1", b"h2", 200, {"merge_id": "m3"}, NOW + timedelta(hours=25))
    async with pg_uow(space) as uow:
        assert await uow.edge.save_idempotency(space, first, NOW - timedelta(hours=24))
        assert not await uow.edge.save_idempotency(space, other, NOW - timedelta(hours=23))
        assert await uow.edge.save_idempotency(space, late, NOW + timedelta(hours=1))
        stored = await uow.edge.get_idempotency("idem-1")

    assert stored == late


async def test_row_level_security_isolates_idempotency_records(restricted_uow: PgUnitOfWorkFactory) -> None:
    mine, theirs = uuid4(), uuid4()
    async with restricted_uow(mine) as uow:
        await uow.edge.save_idempotency(mine, IdempotencyRecord("shared-key", b"h", 200, {}, NOW), NOW)

    async with restricted_uow(theirs) as uow:
        assert await uow.edge.get_idempotency("shared-key") is None
        assert await uow.edge.save_idempotency(
            theirs, IdempotencyRecord("shared-key", b"x", 201, {}, NOW), NOW
        )


async def test_keys_of_one_source_in_one_space(pg_uow: PgUnitOfWorkFactory) -> None:
    space, other_space = uuid4(), uuid4()
    first = _row(space)
    second = replace(_row(space), source_id=first.source_id, status="revoked")
    elsewhere = replace(_row(other_space), source_id=first.source_id)
    async with pg_uow.cell() as uow:
        await uow.keys.upsert_keys(space, [first, second, _row(space)])
        await uow.keys.upsert_keys(other_space, [elsewhere])

    async with pg_uow.cell() as uow:
        found = await uow.keys.source_key_ids(space, first.source_id)
        assert await uow.keys.delete_space(other_space) == 1
        assert await uow.keys.source_key_ids(other_space, first.source_id) == []

    assert found == sorted([first.key_id, second.key_id])


async def test_key_versions_name_only_the_keys_this_space_holds(pg_uow: PgUnitOfWorkFactory) -> None:
    space, other = uuid4(), uuid4()
    row, elsewhere = _row(space), _row(other)
    async with pg_uow.cell() as uow:
        await uow.keys.upsert_keys(space, [row])
        await uow.keys.upsert_keys(other, [elsewhere])
        assert await uow.keys.key_versions(space, [row.key_id, elsewhere.key_id, "missing"]) == {
            row.key_id: 1
        }
        assert await uow.keys.key_versions(space, []) == {}
