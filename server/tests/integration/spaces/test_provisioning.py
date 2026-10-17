"""Spec 12, tests 3, 4 and 11: the provisioner creates, gates, deletes, and sizes the hash partitions."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from niadra.adapters.inbound.http import ingest
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.workers.runtime import WorkerRuntime
from niadra.adapters.memory.fakes import InMemoryCache, RecordedDelivery, StaticConfig
from niadra.app.edge import EdgeService
from niadra.domain.common.errors import Unavailable
from niadra.domain.edge.keys import SourceKeyRow, fast_hash, slow_hash
from niadra.domain.pipeline.schedule import MAINTENANCE_DAILY, SPACE_DAILY, SPACE_HOURLY
from niadra.domain.spaces.deletion import RECEIPT_FIELDS, receipt_hash
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, Tier, WantedSpace, db_name_for, new_entry
from niadra.domain.spaces.stamp import SpaceStamp
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.domain.vocabulary import Environment, Scope
from niadra.testing.cell import VOICE_SOURCE, mint_source_key, sample_config
from tests.integration.conftest import (
    CELL_DB,
    SPACE_A,
    SPACE_B,
    CellStack,
    PgServer,
    cell_stack,
    owner_sql,
)

PEPPER = b"pepper" * 4


@dataclass
class Scenario:
    stack: CellStack
    config: StaticConfig
    space: UUID


@pytest.fixture
async def scenario(server: PgServer) -> AsyncIterator[Scenario]:
    """A cell whose snapshot holds A, B and a new space C, which has no database yet."""
    space = uuid4()
    config = StaticConfig(*(server.config.space(s) for s in (SPACE_A, SPACE_B)), sample_config(space))
    async with cell_stack(server, config=config) as stack:
        yield Scenario(stack, config, space)


async def databases(server: PgServer) -> set[str]:
    return {name for (name,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}


async def row(stack: CellStack, space: UUID) -> SpaceDatabase:
    async with stack.uow.cell() as uow:
        found = await uow.directory.get(space)
    assert found is not None
    return found


async def key_for(stack: CellStack, space: UUID) -> str:
    token, parsed = mint_source_key(Environment.SANDBOX, "us-east-2", "tests")
    source_row = SourceKeyRow(
        space_id=space,
        key_id=parsed.key_id,
        source_id=VOICE_SOURCE,
        fast_hash=fast_hash(PEPPER, parsed.key_id, parsed.secret),
        slow_hash=slow_hash(parsed.key_id, parsed.secret),
        scopes=frozenset({Scope.TRACK, Scope.CONTEXT}),
    )
    async with stack.uow.cell() as uow:
        await uow.keys.upsert_keys(space, [source_row])
    return token


async def test_a_new_space_answers_503_until_its_database_is_created_migrated_stamped_and_scheduled(
    server: PgServer, scenario: Scenario
) -> None:
    stack, space = scenario.stack, scenario.space
    edge = EdgeService(
        stack.uow, InMemoryCache(), scenario.config, stack.clock, {}, PEPPER, databases=stack.registry
    )
    app = create_app("ingest", type("C", (), {"edge": edge, "ingest": None})(), [ingest.router])
    token = await key_for(stack, space)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ingest") as http:
        early = await http.post("/v1/batch", json={"items": []}, headers={"authorization": f"Bearer {token}"})
    assert early.status_code == 503
    assert early.headers["Retry-After"] == "5"
    assert early.json()["code"] == "unavailable"

    plan = await stack.provisioner.reconcile_once()

    assert plan is not None
    assert [w.space_id for w in plan.create] == [space]
    entry = await row(stack, space)
    assert (entry.status, entry.tier, entry.hash_modulus) == (DbStatus.READY, Tier.T0, 1)
    assert entry.schema_version == stack.admin.history.head()
    assert db_name_for(space) in await databases(server)
    stamp = await stack.admin.read_stamp(entry.instance, entry.db_name)
    assert stamp is not None
    assert (stamp.space_id, stamp.cell_id, stamp.hash_modulus) == (space, "test-cell", 1)
    async with stack.uow(space) as uow:
        schedules = await uow.pipeline.list_schedules()
    assert {s.space_id for s in schedules} == {space}
    assert {*SPACE_HOURLY, *SPACE_DAILY, *MAINTENANCE_DAILY} <= {s.task for s in schedules}
    await stack.registry.require_serving(space)  # ready: no exception


async def test_a_truncated_snapshot_deletes_nothing_and_reports_drift(
    server: PgServer, scenario: Scenario
) -> None:
    stack, space = scenario.stack, scenario.space
    await stack.provisioner.reconcile_once()
    scenario.config.drop(space)  # missing from the snapshot, and not listed as removed

    plan = await stack.provisioner.reconcile_once()

    assert plan is not None
    assert space in plan.drift
    assert plan.delete == []
    assert (await row(stack, space)).status is DbStatus.READY
    assert db_name_for(space) in await databases(server)


async def test_a_removed_space_loses_its_database_keys_objects_and_cache_and_a_receipt_goes_out(
    server: PgServer, scenario: Scenario
) -> None:
    stack, space = scenario.stack, scenario.space
    await stack.provisioner.reconcile_once()
    name = db_name_for(space)
    ciphertext, version = await stack.vault.encrypt(space, b"marina", b"facts:1:value_ct")
    for key in (f"raw/{space}/h/2026-09-24/1", f"exports/{space}/run.zip", f"backups/{name}/1.dump"):
        await stack.objects.put(key, b"x")
    await stack.objects.put(f"raw/{SPACE_A}/h/kept", b"x")
    await stack.cache.set(f"t0:1:{space}:{{p}}:voice", b"pack")
    await stack.cache.set(f"hot:{space}:{{h}}", b"turns")
    await stack.cache.set(f"hot:{SPACE_A}:{{h}}", b"kept")
    await stack.secrets.put(f"spaces/{space}/webhook/w1", "secret")
    await key_for(stack, space)

    scenario.config.remove(space, stack.clock.now())
    await stack.provisioner.reconcile_once()

    entry = await row(stack, space)
    assert (entry.status, entry.deleted_at) == (DbStatus.DELETED, stack.clock.now())
    assert name not in await databases(server)
    keys = await owner_sql(server, CELL_DB, "SELECT count(*) FROM data_keys WHERE space_id = :s", s=space)
    assert keys == [(0,)]
    with pytest.raises(PermissionError):  # this process forgot the key
        await stack.vault.decrypt(space, ciphertext, b"facts:1:value_ct", version)
    async with cell_stack(server, config=scenario.config) as other:
        with pytest.raises(KeyError):  # and any other process finds none
            await other.vault.decrypt(space, ciphertext, b"facts:1:value_ct", version)
    assert sorted(stack.objects.objects) == [f"raw/{SPACE_A}/h/kept"]
    assert stack.cache.values == {f"hot:{SPACE_A}:{{h}}": b"kept"}
    assert stack.secrets.values == {}
    sources = await owner_sql(
        server, CELL_DB, "SELECT count(*) FROM source_keys WHERE space_id = :s", s=space
    )
    assert sources == [(0,)]

    [(cell_id, space_id, receipt)] = stack.control.receipts
    assert (cell_id, space_id) == ("test-cell", space)
    assert set(receipt) == RECEIPT_FIELDS
    assert (receipt["db_name"], receipt["dek_versions_destroyed"]) == (name, [1])
    assert (receipt["objects_deleted"], receipt["backups_deleted"], receipt["secrets_deleted"]) == (2, 1, 1)
    stored = await owner_sql(
        server,
        CELL_DB,
        "SELECT receipt, sent_at IS NOT NULL FROM deletion_receipts_outbox WHERE space_id = :s",
        s=space,
    )
    assert stored[0][1] is True
    assert receipt_hash(stored[0][0]) == receipt_hash(receipt)

    # A task of the deleted space is dropped by the workers.
    ran: list[Task] = []

    async def handler(task: Task) -> None:
        ran.append(task)

    worker = WorkerRuntime(
        stack.bus,
        stack.pipeline,
        {TaskName.EXTRACT: handler},
        queues=[q.value for q in QueueClass],
        databases=stack.registry,
    )
    delivery = RecordedDelivery(Task(TaskName.EXTRACT, space))
    await worker.handle(delivery)
    assert (delivery.acked, ran) == (True, [])
    with pytest.raises(Unavailable, match="deleted"):
        await stack.registry.require_serving(space)


@pytest.mark.parametrize("tier", [Tier.T0, Tier.T1])
async def test_the_space_history_honours_the_hash_modulus(
    server: PgServer, pg_stack: CellStack, tier: Tier
) -> None:
    space = uuid4()
    pg_stack.config.put(sample_config(space))
    entry = new_entry(space, sample_config(space).tenant_id, "sandbox", pg_stack.clock.now(), tier)
    try:
        async with pg_stack.uow.cell() as uow:
            await uow.directory.insert(entry, pg_stack.clock.now())
        await pg_stack.admin.create_database(entry)
        await pg_stack.admin.migrate(entry)
        partitions = await owner_sql(
            server,
            entry.db_name,
            "SELECT inhparent::regclass::text, count(*) FROM pg_inherits "
            "WHERE inhparent::regclass::text IN ('facts', 'episodes', 'event_keys_0') GROUP BY 1 ORDER BY 1",
        )
        expected = {1: 1, 32: 32}[entry.hash_modulus]
        assert partitions == [("episodes", expected), ("event_keys_0", 8), ("facts", expected)]
    finally:
        await pg_stack.admin.drop_database(entry.instance, entry.db_name)
        await owner_sql(server, CELL_DB, "DELETE FROM space_databases WHERE space_id = :s", s=space)


async def test_a_creation_that_fails_is_retried_after_its_backoff(
    server: PgServer, scenario: Scenario
) -> None:
    stack, space = scenario.stack, scenario.space
    # The name is taken by a database that is not a space's: the creation fails at the stamp check.
    await stack.admin.create_empty_database("primary", db_name_for(space))
    await owner_sql(server, db_name_for(space), "CREATE TABLE space_identity (one boolean)")
    await stack.provisioner.reconcile_once()
    failed = await row(stack, space)
    assert (failed.status, failed.failures) == (DbStatus.FAILED, 1)
    assert failed.retry_at == stack.clock.now() + timedelta(seconds=30)
    assert failed.status_detail
    with pytest.raises(Unavailable):
        await stack.registry.require_serving(space)

    await stack.admin.drop_database("primary", db_name_for(space))
    stack.clock.advance(seconds=31)
    await stack.provisioner.reconcile_once()
    assert (await row(stack, space)).status is DbStatus.READY


WEEK_MS = 7 * 24 * 3600 * 1000


def week_of(stack: CellStack) -> tuple[int, int]:
    """The number of the clock's week, as partitions are named, and a millisecond inside it."""
    now_ms = int(stack.clock.now().timestamp() * 1000)
    return now_ms // WEEK_MS, now_ms


class EarlyWriter:
    """The directory as the provisioner refreshes it, with a client at the door: the first time a
    refresh finds the new space serving, which is when every process starts routing to its database, a
    row is written through the application role at once, and whether its week's partition existed then
    is noted."""

    def __init__(self, stack: CellStack, space: UUID) -> None:
        self._stack, self._space = stack, space
        self.partitioned_when_serving: list[bool] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stack.registry, name)

    async def refresh(self) -> None:
        await self._stack.registry.refresh()
        if self.partitioned_when_serving or self._space not in self._stack.registry.serving_spaces():
            return
        week, now_ms = week_of(self._stack)
        [(exists,)] = await owner_sql(
            self._stack.test,
            db_name_for(self._space),
            "SELECT to_regclass(:t) IS NOT NULL",
            t=f"event_handles_w{week}",
        )
        self.partitioned_when_serving.append(exists)
        async with self._stack.uow(self._space) as uow:
            await uow.session.execute(
                text(
                    "INSERT INTO event_handles (space_id, event_id, handle_id, received_at) "
                    "VALUES (:s, uuid7_floor(:ms), gen_random_uuid(), now())"
                ),
                {"s": self._space, "ms": now_ms},
            )


async def test_a_new_space_database_is_routed_only_once_its_week_partitions_exist(
    server: PgServer, scenario: Scenario
) -> None:
    # 28/09/2026: the database served once migrated and stamped, before its partitions. An early write
    # landed in the default partition, the partition step then failed on every retry, and the space
    # stayed `failed` while it served.
    stack, space = scenario.stack, scenario.space
    door = EarlyWriter(stack, space)
    stack.provisioner._databases = door

    await stack.provisioner.reconcile_once()

    entry = await row(stack, space)
    assert (entry.status, entry.status_detail) == (DbStatus.READY, None)
    assert door.partitioned_when_serving == [True]
    week, _ = week_of(stack)
    placed = await owner_sql(
        server, entry.db_name, "SELECT tableoid::regclass::text, count(*) FROM event_handles GROUP BY 1"
    )
    assert placed == [(f"event_handles_w{week}", 1)]


async def test_a_space_stuck_with_rows_in_the_default_partition_recovers_on_its_next_retry(
    server: PgServer, scenario: Scenario
) -> None:
    # Migrated, stamped, serving, early rows in the default partitions, and `failed` because the partition
    # step could not run.
    stack, space = scenario.stack, scenario.space
    config = scenario.config.space(space)
    entry = new_entry(space, config.tenant_id, config.environment, stack.clock.now())
    async with stack.uow.cell() as uow:
        await uow.directory.insert(entry, stack.clock.now())
    await stack.admin.create_database(entry)
    version = await stack.admin.migrate(entry)
    stamp = SpaceStamp(space, config.tenant_id, config.region, config.environment, "test-cell", 1)
    await stack.admin.write_stamp(entry, stamp)
    week, now_ms = week_of(stack)
    await owner_sql(
        server,
        entry.db_name,
        "INSERT INTO event_handles (space_id, event_id, handle_id, received_at) "
        "SELECT :s, uuid7_floor(:ms + i), gen_random_uuid(), now() FROM generate_series(1, 3) AS i",
        s=space,
        ms=now_ms,
    )
    stuck = entry.moved(
        DbStatus.FAILED,
        schema_version=version,
        failures=4,
        retry_at=stack.clock.now(),
        status_detail="CheckViolation: updated partition constraint for default partition would be violated",
    )
    async with stack.uow.cell() as uow:
        await uow.directory.save(stuck, stack.clock.now())
    await stack.registry.refresh()
    await stack.registry.require_serving(space)  # it serves, as it did in that state

    await stack.provisioner.reconcile_once()

    ready = await row(stack, space)
    assert (ready.status, ready.failures, ready.status_detail) == (DbStatus.READY, 0, None)
    placed = await owner_sql(
        server, entry.db_name, "SELECT tableoid::regclass::text, count(*) FROM event_handles GROUP BY 1"
    )
    assert placed == [(f"event_handles_w{week}", 3)]


def test_wanted_spaces_carry_what_the_directory_needs() -> None:
    wanted = WantedSpace(uuid4(), uuid4(), "sandbox")
    assert wanted.environment == "sandbox"
