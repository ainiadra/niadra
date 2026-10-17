"""Provisioning and deletion of space databases, driven by the configuration snapshot (8.1, 9).

The reconciler runs in `loops` every 5 s and right after a snapshot is applied, and gives up a pass that
takes longer than a minute. It compares the snapshot's spaces with the directory and the databases on the
server, and holds the cell's provisioning lock while it acts, so only one runs even if `loops` is scaled
by accident:

- a space in the snapshot without a row: create its database, migrate it, stamp it, create this
  week's partitions, and only then let it serve; install its schedules, mark it ready
  (`creating -> migrating -> ready`);
- a creation that failed: retry after 30 s, 1 min, 5 min, then every 15 min;
- a space the snapshot lists as removed, before anything else: drop its database (within 75 s of
  the snapshot, `PASS_DEADLINE_S`), destroy its keys, delete its objects, dumps, secrets and cache
  keys, and send the control plane a deletion receipt;
- a space missing from the snapshot and not removed: an alert, never a deletion;
- a database on the server without a row: an alert.
"""

import asyncio
import contextlib
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from niadra.app.pipeline import PipelineService
from niadra.domain.pipeline.schedule import PARTITIONED_BY_WEEK, week_starts
from niadra.domain.spaces.backups import backup_prefix, retired_to_drop
from niadra.domain.spaces.deletion import DeletionReceipt, space_object_prefixes
from niadra.domain.spaces.directory import (
    PRIMARY_INSTANCE,
    DbStatus,
    Reconciliation,
    SpaceDatabase,
    Tier,
    WantedSpace,
    detail,
    new_entry,
    next_retry,
    reconcile,
)
from niadra.domain.spaces.stamp import SpaceStamp
from niadra.ports.control import ControlPlaneClient
from niadra.ports.infra import (
    Clock,
    ConfigProvider,
    DirectoryState,
    KeyValueCache,
    KeyVault,
    NoTelemetry,
    ObjectStore,
    SecretStore,
    SpaceDatabases,
    SpaceTelemetry,
)
from niadra.ports.provisioning import LockLost, SpaceDatabaseAdmin
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

RECONCILE_EVERY_S = 5.0
PASS_DEADLINE_S = 60.0
"""A pass still running after this long is abandoned, and the next one starts: nothing it waits on (a
connection that stopped answering, a lock) may hold up the removals and creations after it. Every step is
idempotent, so the next pass picks up where it stopped. On 30/09/2026 a pass of a `loops` process up for
four hours waited on such a connection, and two removed spaces kept their databases until it restarted.

The bound it sets: a removed space's database is dropped within 75 s of the cell applying the snapshot that
removes it. That is the next pass (the snapshot wakes it; the tick is 5 s), after the deadline of a pass
stuck before it, plus the 10 s the driver takes to give up on the statement that pass waited on. When the
session holding the provisioning lock is the one that stops answering, the lease on the lock ends the pass
sooner (`LockLost`): the server lets the lock go 15 s after the last statement it heard, and the next pass
takes it at the tick."""
RECEIPTS_PER_PASS = 50


@dataclass(frozen=True, slots=True)
class ProvisioningSettings:
    cell_id: str
    # How long the managed database keeps its automatic snapshots (RDS BackupRetentionPeriod): a deleted
    # space's ciphertext may survive there until then, unreadable without its destroyed keys.
    instance_backup_retention: timedelta = timedelta(days=1)


class Provisioner:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        admin: SpaceDatabaseAdmin,
        databases: SpaceDatabases,
        config: ConfigProvider,
        pipeline: PipelineService,
        vault: KeyVault,
        secrets: SecretStore,
        objects: ObjectStore,
        cache: KeyValueCache,
        control: ControlPlaneClient,
        clock: Clock,
        settings: ProvisioningSettings,
        *,
        telemetry: SpaceTelemetry | None = None,
    ) -> None:
        self._uow = uow
        self._admin = admin
        self._databases = databases
        self._config = config
        self._pipeline = pipeline
        self._vault = vault
        self._secrets = secrets
        self._objects = objects
        self._cache = cache
        self._control = control
        self._clock = clock
        self._settings = settings
        self._telemetry = telemetry or NoTelemetry()
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Run a pass now: a snapshot was applied and may name a new or a removed space."""
        self._wake.set()

    async def run(
        self, stop: asyncio.Event, every_s: float = RECONCILE_EVERY_S, deadline_s: float = PASS_DEADLINE_S
    ) -> None:
        while not stop.is_set():
            deadline = asyncio.timeout(deadline_s)
            try:
                async with deadline:
                    await self.reconcile_once()
            except LockLost:
                log.error("space reconciliation lost the provisioning lock; the pass was abandoned")
            except Exception:
                if deadline.expired():
                    log.error(
                        "space reconciliation stalled; the pass was abandoned", extra={"after_s": deadline_s}
                    )
                else:
                    log.exception("space reconciliation failed")
            self._wake.clear()
            waiters = {asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self._wake.wait())}
            await asyncio.wait(waiters, timeout=every_s, return_when=asyncio.FIRST_COMPLETED)
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def reconcile_once(self) -> Reconciliation | None:
        async with self._admin.leader() as leading:
            if not leading:
                return None
            now = self._clock.now()
            async with self._uow.cell() as cell:
                rows = await cell.directory.entries()
            names = [n for instance in self._admin.instances for n in await self._admin.databases(instance)]
            wanted = [
                WantedSpace(
                    c.space_id,
                    c.tenant_id,
                    c.environment,
                    Tier(c.tier) if c.tier else Tier.T0,
                    c.instance or PRIMARY_INSTANCE,
                )
                for c in self._config.spaces()
            ]
            plan = reconcile(wanted, self._config.removed(), rows, names, now)
            # Removals first: a pass that runs out of time on creations never holds one up, and one that
            # fails holds up no creation.
            for row in plan.delete:
                try:
                    await self.delete(row)
                except Exception:
                    log.exception("space deletion failed", extra={"space_id": str(row.space_id)})
            for space in plan.create:
                await self.create(space)
            for row in plan.retry:
                await self._build(row)
            if plan.drift:
                # A truncated or stale snapshot must never delete; a person decides.
                drift = [str(space) for space in plan.drift]
                log.error("directory_drift: spaces missing from the snapshot", extra={"spaces": drift})
            if plan.orphans:
                orphans = plan.orphans
                log.error("orphan_database: databases without a directory row", extra={"databases": orphans})
            for name in retired_to_drop(names, now):
                await self._admin.drop_database(PRIMARY_INSTANCE, name)
                log.info("retired copy of a restored database dropped", extra={"database": name})
            await self.send_receipts()
            await self._report(plan)
            return plan

    async def create(self, space: WantedSpace) -> SpaceDatabase:
        entry = new_entry(
            space.space_id, space.tenant_id, space.environment, self._clock.now(), space.tier, space.instance
        )
        async with self._uow.cell() as cell:
            if not await cell.directory.insert(entry, self._clock.now()):
                found = await cell.directory.get(space.space_id)
                assert found is not None
                return found
        return await self._build(entry)

    async def _build(self, entry: SpaceDatabase) -> SpaceDatabase:
        """Every step is idempotent, so a creation that failed half-way is simply run again."""
        try:
            config = self._config.space(entry.space_id)
            await self._admin.create_database(entry)
            entry = await self._save(entry.moved(DbStatus.MIGRATING, status_detail=None))
            # An abandoned pass leaves a migration in flight to finish under its lock, never half-way.
            version = await asyncio.shield(self._admin.migrate(entry))
            stamp = SpaceStamp(
                entry.space_id,
                entry.tenant_id,
                config.region,
                entry.environment,
                self._settings.cell_id,
                entry.hash_modulus,
            )
            await self._admin.write_stamp(entry, stamp)
            now_ms = int(self._clock.now().timestamp() * 1000)
            await self._admin.ensure_week_partitions(entry, PARTITIONED_BY_WEEK, week_starts(now_ms))
            # Migrated, stamped and partitioned: the database can serve, and the rest runs through it. A
            # receipt or an event written before its week's partition exists lands in the default one.
            entry = await self._save(entry.moved(DbStatus.MIGRATING, schema_version=version))
            await self._databases.refresh()
            await self._pipeline.install_space_schedules(config, self._clock.now())
            now = self._clock.now()
            entry = await self._save(
                entry.moved(DbStatus.READY, ready_at=now, failures=0, retry_at=None, status_detail=None)
            )
            log.info("space database ready", extra={"space_id": str(entry.space_id), "revision": version})
        except Exception as exc:
            failures = entry.failures + 1
            log.exception("space database creation failed", extra={"space_id": str(entry.space_id)})
            entry = await self._save(
                entry.moved(
                    DbStatus.FAILED,
                    status_detail=detail(exc),
                    failures=failures,
                    retry_at=next_retry(failures, self._clock.now()),
                )
            )
        await self._databases.refresh()
        return entry

    async def delete(self, entry: SpaceDatabase) -> SpaceDatabase:
        """Every step is idempotent. The receipt is drafted with the key versions before they are
        destroyed, and sent only once the row says `deleted`."""
        space_id = entry.space_id
        entry = await self._save(entry.moved(DbStatus.DELETING))
        await self._databases.refresh()
        await self._databases.dispose(space_id)
        await self._admin.drop_database(entry.instance, entry.db_name)
        dropped_at = self._clock.now()
        objects = 0
        for prefix in space_object_prefixes(space_id):
            objects += await self._objects.delete_prefix(prefix)
        backups = await self._objects.delete_prefix(backup_prefix(entry.db_name))
        with contextlib.suppress(Exception):
            # Derived state with a TTL: a purge that fails leaves keys that expire and nobody can read.
            await self._cache.purge_space(space_id)
        secrets = await self._secrets.destroy_space(space_id)
        receipt = DeletionReceipt(
            space_id=space_id,
            cell_id=self._settings.cell_id,
            db_name=entry.db_name,
            dropped_at=dropped_at,
            dek_versions_destroyed=await self._vault.key_versions(space_id),
            objects_deleted=objects,
            backups_deleted=backups,
            secrets_deleted=secrets,
            instance_snapshot_expiry=dropped_at + self._settings.instance_backup_retention,
        )
        async with self._uow.cell() as cell:
            await cell.keys.delete_space(space_id)
            await cell.directory.draft_receipt(space_id, receipt.document(), self._clock.now())
        await self._vault.destroy_space(space_id)
        entry = await self._save(
            entry.moved(DbStatus.DELETED, deleted_at=self._clock.now(), status_detail=None)
        )
        log.info("space deleted", extra={"space_id": str(space_id)})
        return entry

    async def send_receipts(self) -> int:
        """Posts the receipts of finished deletions; one that fails waits for the next pass."""
        sent = 0
        async with self._uow.cell() as cell:
            waiting = await cell.directory.unsent_receipts(RECEIPTS_PER_PASS)
            done = {row.space_id for row in await cell.directory.entries() if row.status is DbStatus.DELETED}
        for space_id, receipt in waiting:
            if space_id not in done:
                continue
            try:
                await self._control.post_deletion_receipt(self._settings.cell_id, space_id, receipt)
            except Exception:
                log.warning("deletion receipt not delivered yet", extra={"space_id": str(space_id)})
                break
            async with self._uow.cell() as cell:
                await cell.directory.mark_receipt_sent(space_id, self._clock.now())
            sent += 1
        return sent

    async def _save(self, entry: SpaceDatabase) -> SpaceDatabase:
        async with self._uow.cell() as cell:
            await cell.directory.save(entry, self._clock.now())
        return entry

    async def _report(self, plan: Reconciliation) -> None:
        async with self._uow.cell() as cell:
            rows = await cell.directory.entries()
            pending = await cell.directory.pending_receipts()
        self._telemetry.directory(
            DirectoryState(
                by_status=Counter(row.status.value for row in rows),
                drift=len(plan.drift),
                orphans=len(plan.orphans),
                failures={r.space_id: r.failures for r in rows if r.failures and r.ready_at is None},
                pending_receipts=pending,
            )
        )
