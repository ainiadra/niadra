"""The fleet migration's rules: waves, a failed wave stops the run (8.2)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from niadra.app.fleet import FleetMigrator
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, Tier, db_name_for, new_entry
from niadra.domain.spaces.stamp import SpaceStamp
from niadra.testing.cell import Cell
from tests.unit.spaces.fakes import FakeAdmin, FakeHistory

NOW = datetime(2026, 9, 24, 3, tzinfo=UTC)
SANDBOX, SMALL, LARGE, NEW = (UUID(int=i) for i in (1, 2, 3, 4))


def entry(space: UUID, environment: str, version: str | None, **changes: object) -> SpaceDatabase:
    ready = {"schema_version": version, "ready_at": NOW} if version else {}
    return new_entry(space, UUID(int=9), environment, NOW).moved(DbStatus.READY, **{**ready, **changes})


async def setup(cell: Cell, admin: FakeAdmin, *rows: SpaceDatabase) -> None:
    async with cell.uow.cell() as uow:
        for row in rows:
            await uow.directory.insert(row, NOW)
            admin.databases_[row.db_name] = row.schema_version
            admin.stamps[row.db_name] = SpaceStamp(row.space_id, row.tenant_id, "us-east-2", "", "c", 8)


async def rows(cell: Cell) -> dict[UUID, SpaceDatabase]:
    async with cell.uow.cell() as uow:
        return {r.space_id: r for r in await uow.directory.entries()}


async def test_the_fleet_migrates_in_waves_and_marks_each_database(cell: Cell) -> None:
    admin = FakeAdmin(FakeHistory("r1", "r2"))
    await setup(
        cell,
        admin,
        entry(LARGE, "production", "r1", tier=Tier.T1),
        entry(SMALL, "production", "r1"),
        entry(SANDBOX, "sandbox", "r1"),
        new_entry(NEW, UUID(int=9), "sandbox", NOW),  # still being created: the provisioner's
    )
    report = await FleetMigrator(cell.uow, admin, cell.clock).run()
    assert report.ok
    assert report.waves == [[db_name_for(SANDBOX)], [db_name_for(SMALL)], [db_name_for(LARGE)]]
    assert [name for name, _ in admin.migrated] == [db_name_for(s) for s in (SANDBOX, SMALL, LARGE)]
    after = await rows(cell)
    assert {after[s].schema_version for s in (SANDBOX, SMALL, LARGE)} == {"r2"}
    assert {after[s].status for s in (SANDBOX, SMALL, LARGE)} == {DbStatus.READY}
    assert after[NEW].status is DbStatus.CREATING


async def test_an_unfinished_creation_that_serves_is_migrated_and_left_to_the_provisioner(
    cell: Cell, caplog: pytest.LogCaptureFixture
) -> None:
    # A creation whose partition step failed serves already: the fleet migrates it with its wave and leaves
    # its status to the provisioner.
    admin = FakeAdmin(FakeHistory("r1", "r2"))
    stuck = new_entry(SMALL, UUID(int=9), "production", NOW).moved(
        DbStatus.FAILED, schema_version="r1", status_detail="CheckViolation", failures=3, retry_at=NOW
    )
    creating = new_entry(NEW, UUID(int=9), "sandbox", NOW)  # no schema yet: it does not serve
    await setup(cell, admin, entry(SANDBOX, "sandbox", "r1"), stuck, creating)

    report = await FleetMigrator(cell.uow, admin, cell.clock).run()

    assert report.ok
    assert report.unfinished == sorted([db_name_for(SMALL), db_name_for(NEW)])
    assert report.waves == [[db_name_for(SANDBOX)], [db_name_for(SMALL)]]
    assert (db_name_for(SMALL), "r2") in admin.migrated
    assert db_name_for(NEW) not in {name for name, _ in admin.migrated}
    after = await rows(cell)
    # Migrated, and still the provisioner's to finish: not marked ready by the fleet.
    assert (after[SMALL].status, after[SMALL].schema_version, after[SMALL].ready_at) == (
        DbStatus.FAILED,
        "r2",
        None,
    )
    assert (after[SMALL].failures, after[SMALL].retry_at) == (3, NOW)
    assert (after[NEW].status, after[NEW].schema_version) == (DbStatus.CREATING, None)
    [warning] = [r for r in caplog.records if "creation has not finished" in r.getMessage()]
    assert warning.databases == report.unfinished  # type: ignore[attr-defined]
    assert warning.migrated_with_the_fleet == [db_name_for(SMALL)]  # type: ignore[attr-defined]


async def test_an_unfinished_creation_at_the_target_keeps_its_status(cell: Cell) -> None:
    admin = FakeAdmin(FakeHistory("r1"))
    stuck = new_entry(SMALL, UUID(int=9), "production", NOW).moved(DbStatus.FAILED, schema_version="r1")
    await setup(cell, admin, stuck)
    report = await FleetMigrator(cell.uow, admin, cell.clock).run()
    assert (report.ok, report.unfinished, admin.migrated) == (True, [db_name_for(SMALL)], [])
    assert (await rows(cell))[SMALL].status is DbStatus.FAILED


async def test_a_failure_marks_the_database_and_stops_after_its_wave(cell: Cell) -> None:
    admin = FakeAdmin(FakeHistory("r1", "r2"))
    await setup(
        cell,
        admin,
        entry(SANDBOX, "sandbox", "r1"),
        entry(UUID(int=5), "sandbox", "r1"),
        entry(SMALL, "production", "r1"),
    )
    admin.failing[db_name_for(SANDBOX)] = RuntimeError("column exists")
    report = await FleetMigrator(cell.uow, admin, cell.clock).run()
    assert not report.ok
    assert report.failed == {db_name_for(SANDBOX): "RuntimeError: column exists"}
    after = await rows(cell)
    assert after[SANDBOX].status is DbStatus.FAILED
    assert after[SANDBOX].status_detail == "RuntimeError: column exists"
    assert after[SANDBOX].schema_version == "r1"  # the revision it kept
    assert after[UUID(int=5)].schema_version == "r2"  # the rest of its wave went on
    assert after[SMALL].schema_version == "r1"  # the next wave never started


async def test_a_database_ahead_of_the_code_fails_the_run(cell: Cell) -> None:
    admin = FakeAdmin(FakeHistory("r1"))
    await setup(cell, admin, entry(SANDBOX, "sandbox", "r9"))
    report = await FleetMigrator(cell.uow, admin, cell.clock).run()
    assert list(report.failed) == [db_name_for(SANDBOX)]


async def test_the_runner_waits_for_the_provisioning_lock(cell: Cell) -> None:
    admin = FakeAdmin(FakeHistory("r1"))
    admin.busy = True
    migrator = FleetMigrator(cell.uow, admin, cell.clock, leader_attempts=2, leader_wait_s=0)
    with pytest.raises(RuntimeError, match="lock stayed taken"):
        await migrator.run()
