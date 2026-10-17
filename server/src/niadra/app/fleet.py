"""The fleet migration: every space database to the new schema, in waves, before the new pods roll
(8.2). `serve setup` runs it as a Helm pre-upgrade hook; a failure stops the upgrade.

Sandboxes go first, then small production spaces, then the large ones; a wave finishes before the
next one starts, and a failure in a wave stops the run after that wave (the hook fails, the running
release stays). Each database takes its own migration lock and records its revision in the directory.

A creation the provisioner has not finished is its own, except for the schema: one that serves already
migrates with its wave and keeps its status, so the provisioner still finishes it, and the report and the
log name every unfinished creation, served or not.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from niadra.domain.spaces.directory import (
    DbStatus,
    SpaceDatabase,
    detail,
    waves,
)
from niadra.domain.spaces.stamp import check_stamp
from niadra.ports.infra import Clock
from niadra.ports.provisioning import SpaceDatabaseAdmin
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 2
LEADER_ATTEMPTS = 30
LEADER_WAIT_S = 2.0
MIGRATED = (DbStatus.READY, DbStatus.MIGRATING, DbStatus.FAILED)


@dataclass
class FleetReport:
    head: str
    upgraded: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    waves: list[list[str]] = field(default_factory=list)
    # Creations the provisioner has not finished; those that serve are migrated with the fleet.
    unfinished: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


class FleetMigrator:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        admin: SpaceDatabaseAdmin,
        clock: Clock,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        leader_attempts: int = LEADER_ATTEMPTS,
        leader_wait_s: float = LEADER_WAIT_S,
    ) -> None:
        self._uow = uow
        self._admin = admin
        self._clock = clock
        self._concurrency = concurrency
        self._leader_attempts = leader_attempts
        self._leader_wait_s = leader_wait_s

    async def run(self) -> FleetReport:
        # The running release's reconciler holds the same lock during a pass: wait for it, so nobody
        # provisions a database while the fleet migrates.
        for _ in range(self._leader_attempts):
            async with self._admin.leader() as leading:
                if leading:
                    return await self._migrate_fleet()
            await asyncio.sleep(self._leader_wait_s)
        raise RuntimeError("the provisioning lock stayed taken; is another setup running?")

    async def _migrate_fleet(self) -> FleetReport:
        history = self._admin.history
        head = history.head()
        report = FleetReport(head=head)
        async with self._uow.cell() as cell:
            rows = await cell.directory.entries()
        fleet = [r for r in rows if r.status in MIGRATED and (not r.unfinished or r.serving)]
        report.unfinished = sorted(r.db_name for r in rows if r.unfinished and not r.gone)
        if report.unfinished:
            serving = sorted(r.db_name for r in rows if r.unfinished and r.serving)
            log.warning(
                "space databases whose creation has not finished; the provisioner retries them",
                extra={"databases": report.unfinished, "migrated_with_the_fleet": serving},
            )
        slots = asyncio.Semaphore(self._concurrency)

        async def one(row: SpaceDatabase) -> None:
            async with slots:
                await self._migrate(row, head, report)

        for wave in waves(fleet):
            report.waves.append([row.db_name for row in wave])
            await asyncio.gather(*(one(row) for row in wave))
            if report.failed:
                log.error(
                    "fleet migration stopped after a failed wave",
                    extra={"failed": sorted(report.failed), "wave": len(report.waves)},
                )
                break
        return report

    async def _migrate(self, row: SpaceDatabase, target: str, report: FleetReport) -> None:
        history = self._admin.history
        try:
            current = history.reached(row.schema_version, target)
        except Exception as exc:
            # A revision this release does not know: the database is ahead of the code.
            report.failed[row.db_name] = detail(exc)
            return
        if current:
            if row.unfinished:
                # The provisioner marks it ready once it finished the creation.
                return
            if row.status is not DbStatus.READY:
                await self._save(row.moved(DbStatus.READY, status_detail=None))
            return
        if not row.unfinished:
            await self._save(row.moved(DbStatus.MIGRATING))
        try:
            version = await self._admin.migrate(row, target)
            check_stamp(row.space_id, row.db_name, await self._admin.read_stamp(row.instance, row.db_name))
        except Exception as exc:
            log.exception("space database migration failed", extra={"database": row.db_name})
            report.failed[row.db_name] = detail(exc)
            # The failed revision rolled back as a whole: the database stays at the revision it reached.
            reached = await self._reached(row)
            await self._save(row.moved(DbStatus.FAILED, schema_version=reached, status_detail=detail(exc)))
            return
        report.upgraded[row.db_name] = version
        if row.unfinished:
            await self._save(row.moved(row.status, schema_version=version))
            return
        await self._save(row.moved(DbStatus.READY, schema_version=version, status_detail=None))

    async def _reached(self, row: SpaceDatabase) -> str | None:
        try:
            return await self._admin.schema_version(row)
        except Exception:
            return row.schema_version

    async def _save(self, row: SpaceDatabase) -> None:
        async with self._uow.cell() as cell:
            await cell.directory.save(row, self._clock.now())
