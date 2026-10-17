"""PostgreSQL implementation of `PrivacyRepository`. None of its tables holds content: nothing is sealed."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import BigInteger, Row, Table, bindparam, delete, func, literal, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.schema.operate import ErasureRequest as ErasureRow
from niadra.adapters.outbound.postgres.schema.operate import ExportRun as ExportRunRow
from niadra.adapters.outbound.postgres.schema.operate import PrivacyFeed, Tombstone
from niadra.adapters.outbound.postgres.schema.operate import RawObject as RawObjectRow
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasedRow, ErasureRequest, ErasureStatus, TargetKind
from niadra.domain.privacy.export import ON_DEMAND, ExportRun, ExportStatus, TombstoneRecord
from niadra.domain.privacy.raw_index import RAW_INDEX_READY, RawObject

FEED = cast(Table, PrivacyFeed.__table__)
ERASURES = cast(Table, ErasureRow.__table__)
RUNS = cast(Table, ExportRunRow.__table__)
TOMBSTONES = cast(Table, Tombstone.__table__)
RAW = cast(Table, RawObjectRow.__table__)

# Callers that share another module's signature do not pass the space; the transaction pinned it.
_CURRENT_SPACE = func.current_setting("niadra.space_id").cast(PG_UUID(as_uuid=True))


class PgPrivacyRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def record_feed(
        self, *, kind: str, profile_ids: Sequence[UUID], handle_ids: Sequence[UUID], privacy_version: int
    ) -> None:
        await self._db.execute(
            insert(FEED).values(
                space_id=_CURRENT_SPACE,
                id=uuid7(),
                kind=kind,
                profile_ids=list(profile_ids),
                handle_ids=list(handle_ids),
                privacy_version=privacy_version,
            )
        )

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        stmt = select(FEED).where(FEED.c.created_at > since).order_by(FEED.c.created_at, FEED.c.id)
        return [dict(row._mapping) for row in (await self._db.execute(stmt)).all()]

    async def create_erasure(self, space_id: UUID, request: ErasureRequest) -> tuple[ErasureRequest, bool]:
        stmt = (
            insert(ERASURES)
            .values(
                space_id=space_id,
                request_id=request.request_id,
                target_kind=request.target_kind.value,
                target_id=request.target_id,
                status=request.status.value,
                requested_by=request.requested_by,
                requested_at=request.requested_at,
                counts=request.counts,
            )
            .on_conflict_do_nothing()
            .returning(ERASURES.c.request_id)
        )
        if (await self._db.execute(stmt)).first() is not None:
            return request, True
        stored = await self.get_erasure(space_id, request.request_id)
        assert stored is not None
        return stored, False

    async def get_erasure(self, space_id: UUID, request_id: UUID) -> ErasureRequest | None:
        stmt = select(ERASURES).where(ERASURES.c.space_id == space_id, ERASURES.c.request_id == request_id)
        row = (await self._db.execute(stmt)).one_or_none()
        return _erasure(row) if row else None

    async def save_erasure(self, space_id: UUID, request: ErasureRequest) -> None:
        await self._db.execute(
            update(ERASURES)
            .where(ERASURES.c.space_id == space_id, ERASURES.c.request_id == request.request_id)
            .values(
                status=request.status.value,
                completed_at=request.completed_at,
                counts=request.counts,
                receipt_hash=request.receipt_hash,
            )
        )

    async def list_erasures(
        self,
        space_id: UUID,
        *,
        status: ErasureStatus | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ErasureRequest]:
        stmt = (
            select(ERASURES)
            .where(ERASURES.c.space_id == space_id)
            .order_by(ERASURES.c.requested_at.desc(), ERASURES.c.request_id.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(ERASURES.c.status == status.value)
        if before is not None:
            stmt = stmt.where(
                tuple_(ERASURES.c.requested_at, ERASURES.c.request_id) < tuple_(*map(literal, before))
            )
        return [_erasure(row) for row in (await self._db.execute(stmt)).all()]

    async def add_tombstones(self, space_id: UUID, rows: Sequence[ErasedRow], at: datetime) -> None:
        if rows:
            values = [
                {"space_id": space_id, "table_name": r.table, "row_key": r.key, "deleted_at": at}
                for r in rows
            ]
            await self._db.execute(insert(TOMBSTONES).values(values))

    async def tombstones_between(
        self, space_id: UUID, after_seq: int, upto_seq: int | None
    ) -> list[TombstoneRecord]:
        stmt = select(TOMBSTONES).where(
            TOMBSTONES.c.space_id == space_id, TOMBSTONES.c.change_seq > after_seq
        )
        if upto_seq is not None:
            stmt = stmt.where(TOMBSTONES.c.change_seq <= upto_seq)
        rows = (await self._db.execute(stmt.order_by(TOMBSTONES.c.change_seq))).all()
        return [TombstoneRecord(r.change_seq, r.table_name, r.row_key, r.deleted_at) for r in rows]

    async def current_change_seq(self) -> int:
        stmt = text("SELECT CASE WHEN is_called THEN last_value ELSE 0 END FROM change_seq")
        return int((await self._db.execute(stmt)).scalar_one())

    async def runs_covering(self, space_id: UUID, change_seqs: Sequence[int]) -> list[UUID]:
        if not change_seqs:
            return []
        seqs = (
            func.unnest(bindparam("seqs", list(change_seqs), type_=ARRAY(BigInteger)))
            .table_valued("seq")
            .render_derived()
        )
        holds_one = (
            select(seqs.c.seq).where(seqs.c.seq > RUNS.c.from_seq, seqs.c.seq <= RUNS.c.to_seq).exists()
        )
        stmt = (
            select(RUNS.c.run_id)
            .where(
                RUNS.c.space_id == space_id,
                RUNS.c.status == ExportStatus.COMPLETED.value,
                RUNS.c.schedule_id != ON_DEMAND,
                holds_one,
            )
            .order_by(RUNS.c.run_id)
        )
        return list((await self._db.execute(stmt)).scalars())

    async def create_export_run(self, space_id: UUID, run: ExportRun) -> None:
        await self._db.execute(insert(RUNS).values(space_id=space_id, **_run_values(run)))

    async def save_export_run(self, space_id: UUID, run: ExportRun) -> None:
        await self._db.execute(
            update(RUNS)
            .where(RUNS.c.space_id == space_id, RUNS.c.run_id == run.run_id)
            .values(**_run_values(run))
        )

    async def get_export_run(self, space_id: UUID, run_id: UUID) -> ExportRun | None:
        stmt = select(RUNS).where(RUNS.c.space_id == space_id, RUNS.c.run_id == run_id)
        row = (await self._db.execute(stmt)).one_or_none()
        return _run(row) if row else None

    async def export_runs(
        self, space_id: UUID, *, schedule_id: str | None, before_id: UUID | None, limit: int
    ) -> list[ExportRun]:
        stmt = select(RUNS).where(RUNS.c.space_id == space_id)
        if schedule_id is not None:
            stmt = stmt.where(RUNS.c.schedule_id == schedule_id)
        if before_id is not None:
            stmt = stmt.where(RUNS.c.run_id < before_id)
        rows = (await self._db.execute(stmt.order_by(RUNS.c.run_id.desc()).limit(limit))).all()
        return [_run(r) for r in rows]

    async def stored_packages(
        self,
        space_id: UUID,
        *,
        profile_id: UUID | None,
        created_before: datetime | None,
        limit: int,
    ) -> list[ExportRun]:
        stmt = select(RUNS).where(
            RUNS.c.space_id == space_id,
            RUNS.c.schedule_id == ON_DEMAND,
            RUNS.c.manifest.is_not(None),
            RUNS.c.manifest["deleted_at"].astext.is_(None),
        )
        if profile_id is not None:
            stmt = stmt.where(RUNS.c.manifest["profile_id"].astext == str(profile_id))
        if created_before is not None:
            stmt = stmt.where(RUNS.c.started_at < created_before)
        rows = (await self._db.execute(stmt.order_by(RUNS.c.run_id).limit(limit))).all()
        return [_run(r) for r in rows]

    async def purge_feed(self, space_id: UUID, before: datetime) -> int:
        stmt = delete(FEED).where(FEED.c.space_id == space_id, FEED.c.created_at < before)
        return int((await self._db.execute(stmt)).rowcount)  # type: ignore[attr-defined]

    async def purge_export_runs(self, space_id: UUID, before: datetime) -> int:
        stmt = delete(RUNS).where(RUNS.c.space_id == space_id, RUNS.c.started_at < before)
        return int((await self._db.execute(stmt)).rowcount)  # type: ignore[attr-defined]

    async def purge_tombstones(self, space_id: UUID, before: datetime) -> int:
        stmt = delete(TOMBSTONES).where(TOMBSTONES.c.space_id == space_id, TOMBSTONES.c.deleted_at < before)
        return int((await self._db.execute(stmt)).rowcount)  # type: ignore[attr-defined]

    async def index_raw(self, space_id: UUID, objects: Sequence[RawObject]) -> None:
        if not objects:
            return
        rows = [
            {
                "space_id": space_id,
                "object_key": o.key,
                "owner": o.owner,
                "written_at": o.written_at,
                "tokens": sorted(o.tokens),
            }
            for o in objects
        ]
        await self._db.execute(insert(RAW).values(rows).on_conflict_do_nothing())

    async def raw_named(self, space_id: UUID, tokens: Collection[bytes]) -> list[RawObject]:
        if not tokens:
            return []
        stmt = (
            select(RAW.c.object_key, RAW.c.owner, RAW.c.written_at)
            .where(RAW.c.space_id == space_id, RAW.c.tokens.overlap(sorted(tokens)))
            .order_by(RAW.c.object_key)
        )
        return [RawObject(r.object_key, r.owner, r.written_at) for r in await self._db.execute(stmt)]

    async def retoken_raw(self, space_id: UUID, key: str, tokens: Collection[bytes]) -> None:
        await self._db.execute(
            update(RAW)
            .where(RAW.c.space_id == space_id, RAW.c.object_key == key)
            .values(tokens=sorted(tokens))
        )

    async def raw_written_before(
        self, space_id: UUID, before: datetime, *, keep: Collection[str], limit: int
    ) -> list[str]:
        stmt = select(RAW.c.object_key).where(RAW.c.space_id == space_id, RAW.c.written_at < before)
        if keep:
            stmt = stmt.where(RAW.c.owner.not_in(sorted(keep)))
        stmt = stmt.order_by(RAW.c.written_at, RAW.c.object_key).limit(limit)
        return list((await self._db.execute(stmt)).scalars())

    async def unindex_raw(self, space_id: UUID, keys: Collection[str]) -> None:
        if keys:
            await self._db.execute(
                delete(RAW).where(RAW.c.space_id == space_id, RAW.c.object_key.in_(sorted(keys)))
            )

    async def unindex_raw_owned(self, space_id: UUID, owners: Collection[str]) -> None:
        if owners:
            await self._db.execute(
                delete(RAW).where(RAW.c.space_id == space_id, RAW.c.owner.in_(sorted(owners)))
            )

    async def raw_index_ready(self, space_id: UUID) -> bool:  # noqa: ARG002 (`space_state` is the database's)
        found = await self._db.scalar(select(SpaceState.value).where(SpaceState.key == RAW_INDEX_READY))
        return found is not None

    async def mark_raw_index_ready(self, space_id: UUID) -> None:  # noqa: ARG002
        stmt = insert(SpaceState).values(key=RAW_INDEX_READY, value=1)
        await self._db.execute(stmt.on_conflict_do_nothing(index_elements=[SpaceState.key]))


def _erasure(row: Row[Any]) -> ErasureRequest:
    return ErasureRequest(
        request_id=row.request_id,
        target_kind=TargetKind(row.target_kind),
        target_id=row.target_id,
        status=ErasureStatus(row.status),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        counts=dict(row.counts),
        receipt_hash=row.receipt_hash,
    )


def _run_values(run: ExportRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "schedule_id": run.schedule_id,
        "from_seq": run.from_seq,
        "to_seq": run.to_seq,
        "status": run.status.value,
        "manifest": run.manifest,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error": run.error,
    }


def _run(row: Row[Any]) -> ExportRun:
    return ExportRun(
        run_id=row.run_id,
        schedule_id=row.schedule_id,
        from_seq=row.from_seq,
        to_seq=row.to_seq,
        status=ExportStatus(row.status),
        started_at=row.started_at,
        manifest=row.manifest,
        finished_at=row.finished_at,
        error=row.error,
    )
