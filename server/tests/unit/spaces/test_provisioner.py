"""The provisioner's state machine and deletion, on a database server in memory (8.1, 9)."""

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import ServingSpaces
from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.adapters.outbound.keys.local import LocalKeyVault, MemoryKeyStore
from niadra.app.pipeline import PipelineService
from niadra.app.provisioning import Provisioner, ProvisioningSettings
from niadra.domain.common.errors import Unavailable
from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.pipeline.schedule import MAINTENANCE_DAILY, PARTITIONED_BY_WEEK, week_starts
from niadra.domain.spaces.deletion import RECEIPT_FIELDS
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, Tier, db_name_for
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.spaces.fakes import FakeAdmin, FakeHistory

HEAD = "r1"


class Control:
    def __init__(self) -> None:
        self.receipts: list[tuple[str, UUID, dict[str, Any]]] = []
        self.down = False

    async def snapshot(self, cell_id: str, after: int | None, wait_s: float) -> None:
        return None

    async def revocations(self, cell_id: str, after: int) -> dict[str, Any]:
        return {}

    async def post_deletion_receipt(self, cell_id: str, space_id: UUID, receipt: dict[str, Any]) -> None:
        if self.down:
            raise Unavailable("the control plane is away")
        self.receipts.append((cell_id, space_id, receipt))

    async def push_usage(self, cell_id: str, items: Sequence[Mapping[str, Any]]) -> int:
        return len(items)


@dataclass
class World:
    cell: Cell
    admin: FakeAdmin
    control: Control
    vault: LocalKeyVault
    provisioner: Provisioner

    async def row(self, space_id: UUID = SPACE_ID) -> SpaceDatabase:
        async with self.cell.uow.cell() as uow:
            entry = await uow.directory.get(space_id)
        assert entry is not None
        return entry


@pytest.fixture
def world(cell: Cell) -> World:
    admin = FakeAdmin(FakeHistory(HEAD))
    control = Control()
    vault = LocalKeyVault(b"k" * 32, MemoryKeyStore())
    provisioner = Provisioner(
        cell.uow,
        admin,
        ServingSpaces(),
        cell.config,
        PipelineService(cell.uow, cell.bus, cell.clock),
        vault,
        cell.secrets,
        cell.objects,
        cell.cache,
        control,
        cell.clock,
        ProvisioningSettings("cell-1", timedelta(days=35)),
    )
    return World(cell, admin, control, vault, provisioner)


async def test_a_new_space_is_created_migrated_stamped_scheduled_and_ready(world: World) -> None:
    plan = await world.provisioner.reconcile_once()
    assert plan is not None
    assert [w.space_id for w in plan.create] == [SPACE_ID]
    entry = await world.row()
    assert (entry.status, entry.schema_version, entry.failures) == (DbStatus.READY, HEAD, 0)
    assert entry.ready_at == world.cell.clock.now()
    name = db_name_for(SPACE_ID)
    assert world.admin.databases_[name] == HEAD
    assert world.admin.stamps[name].space_id == SPACE_ID
    assert world.admin.stamps[name].hash_modulus == 1
    store = InMemoryTaskStore(world.cell.uow.store)
    tasks = {k.task for k in store._s["schedules"] if k.space_id == SPACE_ID}
    assert set(MAINTENANCE_DAILY) | {TaskName.USAGE_ROLLUP, TaskName.AUDIT_ANCHOR} <= tasks
    this_week = week_starts(int(world.cell.clock.now().timestamp() * 1000))[0]
    assert {(name, parent, this_week) for parent in PARTITIONED_BY_WEEK} <= world.admin.partitions
    # A second pass has nothing to do.
    again = await world.provisioner.reconcile_once()
    assert again is not None
    assert (again.create, again.retry) == ([], [])


class WatchedSpaces(ServingSpaces):
    """Records, at each directory refresh, whether the row lets the database serve and whether its
    week partitions exist then: a refresh is when the processes start routing requests to it."""

    def __init__(self, cell: Cell, admin: FakeAdmin) -> None:
        super().__init__()
        self._cell, self._admin = cell, admin
        self.seen: list[tuple[bool, bool]] = []

    async def refresh(self) -> None:
        async with self._cell.uow.cell() as uow:
            entry = await uow.directory.get(SPACE_ID)
        if entry is not None:
            partitioned = any(db == entry.db_name for db, _, _ in self._admin.partitions)
            self.seen.append((entry.serving, partitioned))


async def test_a_new_space_serves_only_once_its_week_partitions_exist(world: World) -> None:
    watched = WatchedSpaces(world.cell, world.admin)
    world.provisioner._databases = watched
    await world.provisioner.reconcile_once()
    assert (await world.row()).status is DbStatus.READY
    assert (True, True) in watched.seen
    assert (True, False) not in watched.seen  # never routed before the partitions


async def test_a_failure_before_the_partitions_leaves_the_database_out_of_service(world: World) -> None:
    async def refused(*_: object) -> None:
        raise RuntimeError("partition refused")

    world.admin.ensure_week_partitions = refused  # type: ignore[method-assign]
    await world.provisioner.reconcile_once()
    failed = await world.row()
    assert (failed.status, failed.schema_version, failed.serving) == (DbStatus.FAILED, None, False)
    assert failed.status_detail == "RuntimeError: partition refused"


async def test_a_failed_creation_backs_off_then_succeeds(world: World) -> None:
    world.admin.failing[db_name_for(SPACE_ID)] = RuntimeError("the server said no")
    await world.provisioner.reconcile_once()
    failed = await world.row()
    assert (failed.status, failed.failures, failed.schema_version) == (DbStatus.FAILED, 1, None)
    assert failed.status_detail == "RuntimeError: the server said no"
    assert failed.retry_at == world.cell.clock.now() + timedelta(seconds=30)

    world.cell.clock.advance(seconds=10)
    await world.provisioner.reconcile_once()
    assert (await world.row()).failures == 1  # still waiting

    del world.admin.failing[db_name_for(SPACE_ID)]
    world.cell.clock.advance(seconds=25)
    await world.provisioner.reconcile_once()
    ready = await world.row()
    assert (ready.status, ready.failures, ready.retry_at, ready.status_detail) == (
        DbStatus.READY,
        0,
        None,
        None,
    )


async def test_another_reconciler_holding_the_lock_leaves_everything_alone(world: World) -> None:
    world.admin.busy = True
    assert await world.provisioner.reconcile_once() is None
    async with world.cell.uow.cell() as uow:
        assert await uow.directory.entries() == []


async def test_a_space_missing_from_a_snapshot_is_never_deleted(world: World) -> None:
    await world.provisioner.reconcile_once()
    world.cell.config.drop(SPACE_ID)
    plan = await world.provisioner.reconcile_once()
    assert plan is not None
    assert plan.drift == [SPACE_ID]
    assert (await world.row()).status is DbStatus.READY
    assert db_name_for(SPACE_ID) in world.admin.databases_


async def test_an_unknown_database_on_the_server_is_reported(world: World) -> None:
    stray = db_name_for(uuid4())
    world.admin.databases_[stray] = HEAD
    plan = await world.provisioner.reconcile_once()
    assert plan is not None
    assert plan.orphans == [stray]
    assert stray in world.admin.databases_


async def test_a_removed_space_is_deleted_everywhere_and_the_receipt_reaches_the_control_plane(
    world: World,
) -> None:
    await world.provisioner.reconcile_once()
    cell = world.cell
    name = db_name_for(SPACE_ID)
    await world.vault.encrypt(SPACE_ID, b"marina", b"facts:1:value")
    for key in (
        f"raw/{SPACE_ID}/h/2026-09-22/1",
        f"media/{SPACE_ID}/h/2",
        f"turns/short/{SPACE_ID}/c0ffee/t1.bin",
        f"turns/kept/{SPACE_ID}/c0ffee/t1.bin",
        f"backups/{name}/1.dump",
    ):
        await cell.objects.put(key, b"x")
    await cell.objects.put(f"raw/{uuid4()}/other", b"x")
    await cell.objects.put(f"turns/short/{uuid4()}/c0ffee/t1.bin", b"x")
    await cell.cache.set(f"t0:1:{SPACE_ID}:{{p}}:voice", b"pack")
    await cell.cache.set(f"hot:{SPACE_ID}:{{h}}", b"turns")
    await cell.secrets.put(f"spaces/{SPACE_ID}/webhook/w1", "secret")
    async with cell.uow.cell() as uow:
        row = SourceKeyRow(SPACE_ID, "k1", uuid4(), b"f", "s", frozenset({Scope.TRACK}))
        await uow.keys.upsert_keys(SPACE_ID, [row])
    world.control.down = True

    cell.config.remove(SPACE_ID, cell.clock.now())
    await world.provisioner.reconcile_once()

    entry = await world.row()
    assert (entry.status, entry.deleted_at) == (DbStatus.DELETED, cell.clock.now())
    assert name not in world.admin.databases_
    assert list(cell.objects.objects) == [k for k in cell.objects.objects if str(SPACE_ID) not in k]
    assert len(cell.objects.objects) == 2
    assert cell.cache.values == {}
    assert cell.secrets.values == {}
    with pytest.raises(PermissionError):
        await world.vault.decrypt(SPACE_ID, b"x" * 40, b"facts:1:value", 1)
    async with cell.uow.cell() as uow:
        assert await uow.keys.source_key_ids(SPACE_ID, row.source_id) == []
        assert await uow.directory.pending_receipts() == 1
    assert world.control.receipts == []  # the control plane is away: the receipt waits

    world.control.down = False
    await world.provisioner.reconcile_once()
    [(cell_id, space_id, receipt)] = world.control.receipts
    assert (cell_id, space_id) == ("cell-1", SPACE_ID)
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["dek_versions_destroyed"] == [1]
    assert (receipt["objects_deleted"], receipt["backups_deleted"], receipt["secrets_deleted"]) == (4, 1, 1)
    expiry = cell.clock.now() + timedelta(days=35)
    assert receipt["instance_snapshot_expiry"] == expiry.isoformat()
    async with cell.uow.cell() as uow:
        assert await uow.directory.pending_receipts() == 0


async def test_a_deletion_interrupted_after_the_keys_went_keeps_its_receipt(world: World) -> None:
    await world.provisioner.reconcile_once()
    await world.vault.encrypt(SPACE_ID, b"marina", b"facts:1:value")
    world.cell.config.remove(SPACE_ID, world.cell.clock.now())
    entry = await world.row()
    await world.provisioner.delete(entry)
    async with world.cell.uow.cell() as uow:
        # As if the process died before marking the row: the deletion runs again.
        await uow.directory.save(entry.moved(DbStatus.DELETING), world.cell.clock.now())
    await world.provisioner.reconcile_once()
    [(_, _, receipt)] = world.control.receipts
    assert receipt["dek_versions_destroyed"] == [1]


async def test_a_pass_stuck_on_a_connection_does_not_keep_a_removed_space_s_database(world: World) -> None:
    # 30/09/2026: a pass of a `loops` process up for four hours waited on a connection that never answered
    # again, and the removed spaces kept their databases until the process restarted. The pass is abandoned
    # at its deadline and the next one drops the database.
    await world.provisioner.reconcile_once()
    world.admin.stalls = 1
    stop = asyncio.Event()
    running = asyncio.create_task(world.provisioner.run(stop, every_s=0.01, deadline_s=0.1))
    try:
        await asyncio.sleep(0.05)  # the pass is stuck when the snapshot removes the space
        world.cell.config.remove(SPACE_ID, world.cell.clock.now())
        world.provisioner.wake()
        async with asyncio.timeout(2):
            while (await world.row()).status is not DbStatus.DELETED:
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running
    assert world.admin.dropped == [db_name_for(SPACE_ID)]


async def test_a_removal_goes_before_the_creations_of_its_pass(world: World) -> None:
    await world.provisioner.reconcile_once()
    new = sample_config().model_copy(update={"space_id": uuid4()})
    world.cell.config.put(new)
    world.admin.stalled.add(db_name_for(new.space_id))
    world.cell.config.remove(SPACE_ID, world.cell.clock.now())
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await world.provisioner.reconcile_once()
    assert world.admin.dropped == [db_name_for(SPACE_ID)]


async def test_a_removal_that_fails_holds_up_no_creation(world: World) -> None:
    await world.provisioner.reconcile_once()
    world.admin.undroppable.add(db_name_for(SPACE_ID))
    new = sample_config().model_copy(update={"space_id": uuid4()})
    world.cell.config.put(new)
    world.cell.config.remove(SPACE_ID, world.cell.clock.now())
    await world.provisioner.reconcile_once()
    assert (await world.row(new.space_id)).status is DbStatus.READY
    assert (await world.row()).status is DbStatus.DELETING


def test_sample_config_is_the_space_of_these_tests() -> None:
    assert sample_config().space_id == SPACE_ID


async def test_a_placed_space_gets_its_database_on_the_named_instance_with_its_tier(world: World) -> None:
    # The snapshot names the tier and the instance (a cell dedicated to the tenant is t2); the
    # directory row says where the database lives, and the registry connects there.
    world.cell.config.put(sample_config().model_copy(update={"tier": "t2", "instance": "tenant-a"}))
    await world.provisioner.reconcile_once()
    entry = await world.row()
    assert (entry.status, entry.instance, entry.tier, entry.hash_modulus) == (
        DbStatus.READY,
        "tenant-a",
        Tier.T2,
        32,
    )
    assert world.admin.stamps[db_name_for(SPACE_ID)].hash_modulus == 32
