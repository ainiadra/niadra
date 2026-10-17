"""Nightly dumps, their weekly check, and restoring one space from a dump (11).

The managed PostgreSQL restores whole instances only; these dumps are what restores one space without
touching the others. A restore loads the dump beside the live database, checks its stamp, brings it to
the current schema and, with `swap`, puts it in place: the space answers 503 for a few seconds while
the names change, and the old database is kept for a week.
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from niadra.app.audit import AuditService
from niadra.app.privacy import open_ledger
from niadra.domain.common.errors import Conflict, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureStatus, ledger_prefix, request_from_ledger
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.spaces.backups import (
    DumpInfo,
    dump_key,
    latest_key,
    restore_name,
    retired_name,
)
from niadra.domain.spaces.directory import PRIMARY_INSTANCE, DbStatus, SpaceDatabase
from niadra.domain.spaces.stamp import check_stamp
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.ports.infra import Clock, KeyValueCache, KeyVault, ObjectStore, SpaceDatabases
from niadra.ports.provisioning import SpaceDatabaseAdmin
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

GIB = 1024**3
# Five tables whose counts a verified dump must reproduce.
SPACE_CHECK_TABLES = ("events", "sessions", "profiles", "facts", "receipts")
CELL_CHECK_TABLES = ("space_databases", "data_keys", "source_keys", "config_documents", "receipt_watermarks")
# Every process reads the directory every 5 s: two ticks and a margin before the names change.
SWAP_GRACE_S = 12.0
RESTORE_PURPOSE = "space.restore"


@dataclass
class BackupReport:
    dumped: dict[str, DumpInfo] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Verification:
    db_name: str
    key: str
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class Restore:
    space_id: UUID
    restored_as: str
    key: str
    schema_version: str
    swapped: bool
    retired: str | None


class _NoSource:
    """The restore writes one receipt straight into the chain; it never reads the receipt queue."""

    def batches(self, max_items: int) -> AsyncIterator[object]:
        raise NotImplementedError


class BackupService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        admin: SpaceDatabaseAdmin,
        objects: ObjectStore,
        clock: Clock,
        *,
        cell_database: str,
        max_db_bytes: int = 50 * GIB,
        databases: SpaceDatabases | None = None,
        cache: KeyValueCache | None = None,
        swap_grace_s: float = SWAP_GRACE_S,
        keys: KeyVault | None = None,
    ) -> None:
        self._uow = uow
        self._admin = admin
        self._objects = objects
        self._clock = clock
        self._cell_database = cell_database
        self._max_db_bytes = max_db_bytes
        self._databases = databases
        self._cache = cache
        self._swap_grace_s = swap_grace_s
        self._keys = keys

    # Nightly dumps.

    async def backup_all(self) -> BackupReport:
        """The cell database and every serving space database up to the size limit; larger ones rely
        on the instance snapshots until they move to an instance of their own (T1)."""
        async with self._uow.cell() as cell:
            rows = [r for r in await cell.directory.entries() if r.serving]
        targets: list[tuple[str, str, str | None]] = [(PRIMARY_INSTANCE, self._cell_database, None)]
        targets += [(r.instance, r.db_name, r.schema_version) for r in rows]
        report = BackupReport()
        for instance, db_name, schema in targets:
            try:
                size = await self._admin.size_bytes(instance, db_name)
                if size > self._max_db_bytes:
                    report.skipped[db_name] = f"{size / GIB:.1f} GiB is over the dump limit"
                    continue
                report.dumped[db_name] = await self.backup(instance, db_name, schema)
            except Exception as exc:
                log.exception("database dump failed", extra={"database": db_name})
                report.failed[db_name] = f"{type(exc).__name__}: {exc}"[:500]
        return report

    async def backup(self, instance: str, db_name: str, schema_version: str | None) -> DumpInfo:
        taken_at = self._clock.now()
        key = dump_key(db_name, taken_at)
        digest = hashlib.sha256()

        async def chunks() -> AsyncIterator[bytes]:
            async for chunk in self._admin.dump(instance, db_name):
                digest.update(chunk)
                yield chunk

        size = await self._objects.put_stream(key, chunks())
        info = DumpInfo(key, db_name, size, digest.hexdigest(), schema_version, taken_at)
        await self._objects.put(latest_key(db_name), json.dumps(info.document()).encode(), "application/json")
        return info

    # The weekly check.

    async def verify(self, db_name: str, instance: str = PRIMARY_INSTANCE) -> Verification:
        """Restores the latest dump into a scratch database, counts five tables and drops it."""
        info = await self.latest(db_name)
        scratch = f"{db_name}_v{self._clock.now():%Y%m%d%H%M%S}"[:63]
        tables = CELL_CHECK_TABLES if db_name == self._cell_database else SPACE_CHECK_TABLES
        with tempfile.TemporaryDirectory() as folder:
            path = await self._download(info, Path(folder))
            await self._admin.create_empty_database(instance, scratch)
            try:
                await self._admin.restore(instance, scratch, path)
                counts = await self._admin.count_rows(instance, scratch, tables)
            finally:
                await self._admin.drop_database(instance, scratch)
        return Verification(db_name, info.key, counts)

    async def latest(self, db_name: str) -> DumpInfo:
        try:
            raw = await self._objects.get(latest_key(db_name))
        except NotFound:
            raise NotFound(f"no dump of {db_name} yet") from None
        return DumpInfo.from_document(json.loads(raw))

    # Restore.

    async def restore_space(self, space_id: UUID, key: str | None = None, *, swap: bool = False) -> Restore:
        async with self._uow.cell() as cell:
            row = await cell.directory.get(space_id)
        if row is None or row.gone:
            raise NotFound("no live space with this id")
        info = await self.latest(row.db_name)
        key = key or info.key
        now = self._clock.now()
        scratch = restore_name(row.db_name, now)
        with tempfile.TemporaryDirectory() as folder:
            path = await self._download(info if key == info.key else None, Path(folder), key)
            await self._admin.create_empty_database(row.instance, scratch)
            await self._admin.restore(row.instance, scratch, path)
        check_stamp(space_id, scratch, await self._admin.read_stamp(row.instance, scratch))
        restored = dataclasses.replace(row, db_name=scratch)
        version = await self._admin.migrate(restored)
        if not swap:
            return Restore(space_id, scratch, key, version, swapped=False, retired=None)
        retired = retired_name(row.db_name, now)
        await self._swap(row, scratch, retired, version, key)
        return Restore(space_id, row.db_name, key, version, swapped=True, retired=retired)

    async def _swap(self, row: SpaceDatabase, scratch: str, retired: str, version: str, key: str) -> None:
        await self._save(row.moved(DbStatus.MAINTENANCE, status_detail=f"restoring from {key}"))
        # Every process stops using the space and closes its pool before the names change.
        await asyncio.sleep(self._swap_grace_s)
        try:
            await self._admin.rename_database(row.instance, row.db_name, retired)
            await self._admin.rename_database(row.instance, scratch, row.db_name)
        except Exception:
            await self._admin.resume(row.db_name)
            await self._save(row.moved(row.status, status_detail="restore swap failed; see the ops log"))
            raise
        await self._admin.resume(row.db_name)
        async with self._uow.cell() as cell:
            await cell.config.bump_cache_epoch()
        if self._cache is not None:
            # The live turns in the cache are from after the restore point; the rebuild below refills them.
            await self._cache.purge_space(row.space_id)
        await self._save(
            row.moved(DbStatus.READY, schema_version=version, status_detail=f"restored from {key}")
        )
        if self._databases is not None:
            await self._databases.refresh()
        async with self._uow(row.space_id) as uow:
            await uow.pipeline.enqueue([Task(TaskName.HOT_REBUILD, row.space_id)])
        reapplied = await self.reapply_erasures(row.space_id, key)
        await self._record(row.space_id, key, reapplied)

    async def reapply_erasures(self, space_id: UUID, key: str) -> list[UUID]:
        """Erasures the dump does not know as completed run again: the dump brought back what
        they erased, and their requests with it or not at all. The ledger in object storage survives the
        restore; each request comes back pending, with its scope, and the cascade runs as the first time."""
        if self._keys is None:
            raise RuntimeError("re-applying erasures needs the key vault that sealed the ledger")
        entries = [
            await open_ledger(self._keys, space_id, await self._objects.get(k))
            for k in await self._objects.list_keys(ledger_prefix(space_id))
        ]
        reapplied: list[UUID] = []
        async with self._uow(space_id) as uow:
            for entry in entries:
                request = request_from_ledger(entry, key)
                found = await uow.privacy.get_erasure(space_id, request.request_id)
                if found is not None and found.status is ErasureStatus.COMPLETED:
                    continue  # the dump was taken after this erasure: nothing came back
                if found is None:
                    await uow.privacy.create_erasure(space_id, request)
                erase = Task(
                    TaskName.ERASE,
                    space_id,
                    {"request_id": str(request.request_id)},
                    profile_id=request.scope.profile_id,
                )
                await uow.pipeline.enqueue([erase])
                reapplied.append(request.request_id)
        if reapplied:
            log.info(
                "erasures re-applied after a restore",
                extra={"space_id": str(space_id), "count": len(reapplied)},
            )
        return reapplied

    async def _record(self, space_id: UUID, key: str, reapplied: list[UUID]) -> None:
        """One receipt of kind `admin` in the restored database's chain: where the space came back from."""
        audit = AuditService(self._uow, _NoSource(), self._objects, self._clock)  # type: ignore[arg-type]
        now = self._clock.now()
        # A producer of its own per restore, closed at once by the final mark below.
        producer = f"ops/restore/{uuid7()}"
        draft = ReceiptDraft(
            receipt_id=uuid7(),
            space_id=space_id,
            producer_id=producer,
            producer_seq=1,
            kind=ReceiptKind.ADMIN,
            surface=ReceiptSurface.CONSOLE,
            audience=Audience.HUMAN,
            read_at=now,
            # Stored and chained fields only (`extra` is not kept): the action, and the dump it came from.
            purpose=RESTORE_PURPOSE,
            item_ids=(key, *(str(r) for r in reapplied)),
        )
        # A final mark closes this one-off producer, so it never holds the cell's watermark back.
        await audit.write([draft], [ProducerMark(producer, 1, now, final=True)])

    async def _download(self, info: DumpInfo | None, folder: Path, key: str | None = None) -> Path:
        key = key or (info.key if info else None)
        if key is None:
            raise NotFound("no dump to restore")
        path = folder / "space.dump"
        digest = hashlib.sha256()
        with path.open("wb") as out:
            async for chunk in self._objects.get_stream(key):
                digest.update(chunk)
                out.write(chunk)
        if info is not None and digest.hexdigest() != info.sha256:
            raise Conflict(f"the dump {key} does not match the digest recorded when it was taken")
        return path

    async def _save(self, row: SpaceDatabase) -> None:
        async with self._uow.cell() as cell:
            await cell.directory.save(row, self._clock.now())
