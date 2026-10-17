"""Spec 12, test 5: dump a space, write more, restore it in place; the old database is kept a week."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from niadra.app.backups import RESTORE_PURPOSE, BackupService
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.spaces.backups import latest_key, retired_name
from niadra.domain.spaces.directory import DbStatus, WantedSpace, db_name_for
from niadra.domain.tasks import TaskName
from niadra.testing.cell import sample_config
from tests.integration.conftest import CELL_DB, CellStack, PgServer, owner_sql
from tests.integration.memory.test_pg_memory import fact


async def test_a_restore_brings_the_space_back_to_its_dump_and_keeps_the_old_database(
    server: PgServer, pg_stack: CellStack
) -> None:
    stack = pg_stack
    space = uuid4()
    stack.config.put(sample_config(space))
    await stack.provisioner.create(WantedSpace(space, sample_config(space).tenant_id, "sandbox"))
    name = db_name_for(space)
    before, after = fact(uuid7(), "Marina"), fact(uuid7(), "Written after the dump")
    async with stack.uow(space) as uow:
        await uow.memory.insert_fact(before, normalize_value(before.value))
    backups = BackupService(stack.uow, stack.admin, stack.objects, stack.clock, cell_database=CELL_DB,
                            databases=stack.registry, cache=stack.cache, swap_grace_s=0,
                            keys=stack.vault)  # fmt: skip

    dumped = await backups.backup("primary", name, stack.admin.history.head())
    assert dumped.size_bytes > 0
    assert latest_key(name) in stack.objects.objects
    async with stack.uow(space) as uow:
        await uow.memory.insert_fact(after, normalize_value(after.value))
    await stack.cache.set(f"hot:{space}:{{h}}", b"a live turn from after the dump")

    stack.clock.advance(minutes=10)
    restored = await backups.restore_space(space, swap=True)

    assert (restored.swapped, restored.restored_as, restored.key) == (True, name, dumped.key)
    assert restored.retired == retired_name(name, stack.clock.now())
    async with stack.uow(space) as uow:
        facts = {
            f.value for f in await uow.memory.facts_for([before.origin_handle_id, after.origin_handle_id])
        }
        receipts = await uow.session.execute(
            text("SELECT kind, purpose, item_ids FROM receipts WHERE purpose = :p"), {"p": RESTORE_PURPOSE}
        )
        [(kind, _, items)] = receipts.all()
        # The outbox row's time comes from the database clock, not the test's.
        [rebuild] = await uow.pipeline.take_stale_outbox(datetime.now(UTC) + timedelta(seconds=5), 10)
    assert facts == {"Marina"}  # the state of the dump
    # A dump carries no privileges: the restore takes UPDATE and DELETE on events away again.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with stack.uow(space) as uow:
            await uow.session.execute(text("DELETE FROM events"))
    assert (kind, items) == ("admin", [dumped.key])
    assert rebuild.name is TaskName.HOT_REBUILD
    assert stack.cache.values == {}
    entry_status = await owner_sql(
        server, CELL_DB, "SELECT status, status_detail FROM space_databases WHERE space_id = :s", s=space
    )
    assert entry_status == [("ready", f"restored from {dumped.key}")]
    names = {n for (n,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}
    assert restored.retired in names

    # The reconciler keeps the retired copy for a week, then drops it.
    await stack.provisioner.reconcile_once()
    names = {n for (n,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}
    assert restored.retired in names
    stack.clock.advance(days=7, minutes=1)
    await stack.provisioner.reconcile_once()
    names = {n for (n,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}
    assert restored.retired not in names
    assert name in names


async def test_a_dump_is_verified_in_a_scratch_database_that_goes_away(
    server: PgServer, pg_stack: CellStack
) -> None:
    backups = BackupService(
        pg_stack.uow, pg_stack.admin, pg_stack.objects, pg_stack.clock, cell_database=CELL_DB
    )
    report = await backups.backup_all()
    assert report.failed == {}
    assert CELL_DB in report.dumped
    checked = await backups.verify(CELL_DB)
    assert checked.counts["space_databases"] >= 2
    names = {n for (n,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}
    assert not any(n.startswith(f"{CELL_DB}_v") for n in names)
    assert DbStatus.READY.value == "ready"
