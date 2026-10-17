"""Spec 12, test 6: the setup runner migrates the fleet in waves with real Alembic revisions.

The fleet here is a cell of its own (a separate cell database and two space databases) on a copy of the
space history with test revisions appended, so the shared spaces of the session never see them.
"""

import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.provisioning import (
    DATABASE_GRANTS,
    AlembicHistory,
    PgSpaceDatabaseAdmin,
    statements,
)
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.fleet import FleetMigrator
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, new_entry
from niadra.domain.spaces.stamp import SpaceStamp
from tests.integration.conftest import ROOT, PgServer, alembic, dsn_for, libpq

FLEET_CELL = "niadra_test_fleet_cell"
REVISION = '''"""{doc}

Revision ID: {rev}
Revises: {down}
"""

from alembic import op

revision = "{rev}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("{sql}")


def downgrade() -> None:
    pass
'''


@dataclass
class Fleet:
    history: AlembicHistory
    admin: PgSpaceDatabaseAdmin
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    sandbox: SpaceDatabase
    production: SpaceDatabase
    initial: str
    folder: Path

    def add(self, rev: str, sql: str) -> None:
        down = self.history.head()
        body = REVISION.format(doc=f"fleet test {rev}", rev=rev, down=down, sql=sql)
        (self.folder / "space" / "versions" / f"{rev}.py").write_text(body)
        self.history = AlembicHistory(self.folder / "alembic.ini")
        self.admin._history = self.history  # the runner reads the history through the admin

    async def ahead(self, entry: SpaceDatabase, target: str | None = None) -> None:
        """A database a previous run already took further, with its row saying so."""
        version = await self.admin.migrate(entry, target)
        async with self.uow.cell() as uow:
            await uow.directory.save(entry.moved(DbStatus.READY, schema_version=version), self.clock.now())

    async def rows(self) -> dict[UUID, SpaceDatabase]:
        async with self.uow.cell() as uow:
            return {r.space_id: r for r in await uow.directory.entries()}

    async def version(self, entry: SpaceDatabase) -> str | None:
        return await self.admin.schema_version(entry)


@pytest.fixture
async def fleet(server: PgServer, tmp_path: Path) -> AsyncIterator[Fleet]:
    shutil.copytree(
        ROOT / "migrations" / "space", tmp_path / "space", ignore=shutil.ignore_patterns("__pycache__")
    )
    ini = (ROOT / "alembic.ini").read_text().replace("%(here)s/migrations/space", str(tmp_path / "space"))
    (tmp_path / "alembic.ini").write_text(ini)
    with psycopg.connect(**libpq()) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {FLEET_CELL} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {FLEET_CELL} OWNER niadra_owner")
    with psycopg.connect(**libpq(database=FLEET_CELL)) as conn:
        for statement in statements(DATABASE_GRANTS.format(database=FLEET_CELL)):
            conn.execute(statement)
    alembic("cell", dsn_for("owner", FLEET_CELL), "upgrade head")

    servers = replace(server.servers, cell_database=FLEET_CELL)
    history = AlembicHistory(tmp_path / "alembic.ini")
    admin = PgSpaceDatabaseAdmin(servers, history)
    cell_engine = make_engine(servers.cell_url("owner"), pool_size=2)
    uow = PgUnitOfWorkFactory(LocalKeyVault(b"k" * 32), EngineRegistry(servers, cell_engine), cell_engine)
    clock = FixedClock()
    entries = []
    for environment in ("sandbox", "production"):
        entry = new_entry(uuid4(), uuid4(), environment, clock.now())
        await admin.create_database(entry)
        version = await admin.migrate(entry)
        await admin.write_stamp(
            entry, SpaceStamp(entry.space_id, entry.tenant_id, "us-east-2", environment, "c", 8)
        )
        entry = entry.moved(DbStatus.READY, schema_version=version, ready_at=clock.now())
        async with uow.cell() as tx:
            await tx.directory.insert(entry, clock.now())
        entries.append(entry)
    try:
        yield Fleet(history, admin, uow, clock, entries[0], entries[1], history.head(), tmp_path)
    finally:
        for entry in entries:
            await admin.drop_database(entry.instance, entry.db_name)
        await admin.close()
        await cell_engine.dispose()
        with psycopg.connect(**libpq()) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {FLEET_CELL} WITH (FORCE)")


async def test_the_fleet_upgrades_a_database_one_revision_behind_sandboxes_first(fleet: Fleet) -> None:
    fleet.add("fleet00001", "ALTER TABLE profiles ADD COLUMN fleet_probe text")
    # The sandbox is at the new head already; production is one revision behind.
    await fleet.ahead(fleet.sandbox)

    report = await FleetMigrator(fleet.uow, fleet.admin, fleet.clock).run()

    assert report.ok
    assert report.waves == [[fleet.sandbox.db_name], [fleet.production.db_name]]
    assert report.upgraded == {fleet.production.db_name: "fleet00001"}
    rows = await fleet.rows()
    assert {r.schema_version for r in rows.values()} == {"fleet00001"}
    assert {r.status for r in rows.values()} == {DbStatus.READY}
    assert await fleet.version(fleet.production) == "fleet00001"


async def test_a_failing_migration_leaves_its_database_failed_at_the_last_good_revision(fleet: Fleet) -> None:
    production = fleet.production.db_name
    fleet.add(
        "fleet00002",
        f"DO $$ BEGIN IF current_database() = '{production}' THEN RAISE EXCEPTION 'boom in production'; "
        "END IF; END $$",
    )

    report = await FleetMigrator(fleet.uow, fleet.admin, fleet.clock).run()

    assert not report.ok  # `serve setup` exits 1 and the Helm hook stops the upgrade
    assert list(report.failed) == [production]
    assert "boom in production" in report.failed[production]
    rows = await fleet.rows()
    sandbox, failed = rows[fleet.sandbox.space_id], rows[fleet.production.space_id]
    assert (sandbox.status, sandbox.schema_version) == (DbStatus.READY, "fleet00002")
    assert (failed.status, failed.schema_version) == (DbStatus.FAILED, fleet.initial)
    assert "boom in production" in (failed.status_detail or "")
    assert await fleet.version(fleet.production) == fleet.initial
    assert failed.serving  # the revision rolled back whole


async def test_databases_of_the_same_wave_migrate_together(fleet: Fleet) -> None:
    # 24/09: the first real space migration ran two sandboxes of one wave at once, and Alembic's
    # process-wide context made both fail. Two more production databases join the fleet, so the
    # production wave migrates three of them concurrently.
    extra = []
    for _ in range(2):
        entry = new_entry(uuid4(), uuid4(), "production", fleet.clock.now())
        await fleet.admin.create_database(entry)
        version = await fleet.admin.migrate(entry)
        await fleet.admin.write_stamp(
            entry, SpaceStamp(entry.space_id, entry.tenant_id, "us-east-2", "production", "c", 8)
        )
        entry = entry.moved(DbStatus.READY, schema_version=version, ready_at=fleet.clock.now())
        async with fleet.uow.cell() as tx:
            await tx.directory.insert(entry, fleet.clock.now())
        extra.append(entry)
    try:
        fleet.add("fleet00009", "ALTER TABLE profiles ADD COLUMN wave_probe text")
        await fleet.ahead(fleet.sandbox)

        report = await FleetMigrator(fleet.uow, fleet.admin, fleet.clock, concurrency=3).run()

        assert report.ok, report
        assert sorted(report.waves[-1]) == sorted([fleet.production.db_name, *(e.db_name for e in extra)])
        for entry in (fleet.production, *extra):
            assert await fleet.version(entry) == "fleet00009"
    finally:
        for entry in extra:
            await fleet.admin.drop_database(entry.instance, entry.db_name)
