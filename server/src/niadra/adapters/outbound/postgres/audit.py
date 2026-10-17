"""PostgreSQL implementations of `ReceiptRepository` (space database) and `WatermarkRepository` (cell
database).

`receipts` is range-partitioned by `receipt_id` (UUIDv7 minted at read time), so every query that knows a
time window also bounds `receipt_id`, letting the planner prune weekly partitions (06, 6.2).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, Table, any_, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.read_path import AuditRoot as RootRow
from niadra.adapters.outbound.postgres.schema.read_path import (
    Receipt,
    ReceiptHead,
    ReceiptPerson,
    ReceiptWatermark,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.audit.chain import ChainHead, StoredReceipt, empty_head
from niadra.domain.audit.watermarks import ProducerState, ProducerStatus, cell_watermark
from niadra.domain.common.ids import uuid7_floor
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.ports.repos.audit import AuditRoot, ReceiptQuery

R = cast(Table, Receipt.__table__)
HEADS = cast(Table, ReceiptHead.__table__)
MARKS = cast(Table, ReceiptWatermark.__table__)
ROOTS = cast(Table, RootRow.__table__)
PEOPLE = cast(Table, ReceiptPerson.__table__)

# A receipt id and its read time come from the same clock read; the slack covers skew at the edges.
_ID_SLACK = timedelta(hours=1)


class PgReceiptRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def receipts_for_conversation(
        self, conversation_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(R).where((R.c.conversation_id == conversation_id) | (R.c.task_id == conversation_id))
        if since is not None:
            stmt = stmt.where(R.c.read_at >= since, R.c.receipt_id >= _id_floor(since - _ID_SLACK))
        rows = (await self._db.execute(stmt.order_by(R.c.read_at, R.c.receipt_id))).all()
        return [_receipt(row).as_dict() for row in rows]

    async def receipts_for_profile(
        self, profile_id: UUID, *, limit: int = 100, before_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(R).where(R.c.profile_id == profile_id)
        if before_id is not None:
            stmt = stmt.where(R.c.receipt_id < before_id)
        stmt = stmt.order_by(R.c.receipt_id.desc()).limit(limit)
        return [_receipt(row).as_dict() for row in (await self._db.execute(stmt)).all()]

    async def expire_receipts(self, space_id: UUID, before: date, *, limit: int) -> int:
        edge = datetime.combine(before, time(), UTC)
        picked = (
            select(R.c.receipt_id)
            .where(R.c.space_id == space_id, R.c.day < before, R.c.receipt_id < _id_floor(edge + _ID_SLACK))
            .limit(limit)
        )
        stmt = delete(R).where(R.c.space_id == space_id, R.c.receipt_id.in_(picked)).returning(R.c.receipt_id)
        removed = len((await self._db.execute(stmt)).all())
        if removed < limit:
            # The day's chain heads and root row describe receipts that are gone.
            await self._db.execute(delete(HEADS).where(HEADS.c.space_id == space_id, HEADS.c.day < before))
            await self._db.execute(delete(ROOTS).where(ROOTS.c.space_id == space_id, ROOTS.c.day < before))
        return removed

    async def lock_head(self, space_id: UUID, slice_: int, day: date) -> ChainHead:
        empty = empty_head(space_id, slice_, day)
        await self._db.execute(
            insert(HEADS)
            .values(space_id=space_id, slice=slice_, day=day, seq=0, hash=empty.hash)
            .on_conflict_do_nothing()
        )
        stmt = (
            select(HEADS.c.seq, HEADS.c.hash)
            .where(HEADS.c.space_id == space_id, HEADS.c.slice == slice_, HEADS.c.day == day)
            .with_for_update()
        )
        seq, digest = (await self._db.execute(stmt)).one()
        return ChainHead(slice_, day, seq, digest)

    async def save_head(self, space_id: UUID, head: ChainHead) -> None:
        await self._db.execute(
            update(HEADS)
            .where(HEADS.c.space_id == space_id, HEADS.c.slice == head.slice, HEADS.c.day == head.day)
            .values(seq=head.seq, hash=head.hash)
        )

    async def stored_ids(self, space_id: UUID, receipt_ids: Sequence[UUID]) -> set[UUID]:
        if not receipt_ids:
            return set()
        stmt = select(R.c.receipt_id).where(
            R.c.space_id == space_id,
            R.c.receipt_id.in_(receipt_ids),
            R.c.receipt_id.between(min(receipt_ids), max(receipt_ids)),
        )
        return set((await self._db.execute(stmt)).scalars())

    async def append(self, space_id: UUID, receipts: Sequence[StoredReceipt]) -> None:
        if receipts:
            await self._db.execute(insert(R).values([_row(space_id, r) for r in receipts]))

    async def list_receipts(self, space_id: UUID, query: ReceiptQuery) -> list[StoredReceipt]:
        stmt = select(R).where(R.c.space_id == space_id)
        if query.profile_id is not None:
            stmt = stmt.where(R.c.profile_id == query.profile_id)
        if query.source_id is not None:
            stmt = stmt.where(R.c.source_id == query.source_id)
        if query.conversation_id is not None:
            stmt = stmt.where(R.c.conversation_id == query.conversation_id)
        if query.kind is not None:
            stmt = stmt.where(R.c.kind == query.kind.value)
        if query.since is not None:
            stmt = stmt.where(
                R.c.read_at >= query.since, R.c.receipt_id >= _id_floor(query.since - _ID_SLACK)
            )
        if query.until is not None:
            stmt = stmt.where(R.c.read_at < query.until, R.c.receipt_id < _id_floor(query.until + _ID_SLACK))
        if query.before_id is not None:
            stmt = stmt.where(R.c.receipt_id < query.before_id)
        stmt = stmt.order_by(R.c.receipt_id.desc()).limit(query.limit)
        return [_receipt(row) for row in (await self._db.execute(stmt)).all()]

    async def get_receipt(self, space_id: UUID, receipt_id: UUID) -> StoredReceipt | None:
        stmt = select(R).where(R.c.space_id == space_id, R.c.receipt_id == receipt_id)
        row = (await self._db.execute(stmt)).one_or_none()
        return _receipt(row) if row else None

    async def name_people(self, labels: Mapping[UUID, tuple[str, datetime]]) -> None:
        if not labels:
            return
        stmt = insert(PEOPLE).values(
            [
                {"space_id": CURRENT_SPACE, "person_id": person_id, "label": label, "named_at": at}
                for person_id, (label, at) in labels.items()
            ]
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[PEOPLE.c.space_id, PEOPLE.c.person_id],
                set_={"label": stmt.excluded.label, "named_at": stmt.excluded.named_at},
                # An unchanged name writes nothing: a person acts many times under the same one.
                where=(PEOPLE.c.named_at <= stmt.excluded.named_at) & (PEOPLE.c.label != stmt.excluded.label),
            )
        )

    async def person_labels(self, person_ids: Sequence[UUID]) -> dict[UUID, str]:
        if not person_ids:
            return {}
        stmt = select(PEOPLE.c.person_id, PEOPLE.c.label).where(PEOPLE.c.person_id.in_(person_ids))
        return {row.person_id: row.label for row in (await self._db.execute(stmt)).all()}

    async def receipts_with_item(
        self, space_id: UUID, item_id: str, *, profile_id: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        stmt = select(R).where(R.c.space_id == space_id, literal(item_id) == any_(R.c.item_ids))
        if profile_id is not None:
            stmt = stmt.where(R.c.profile_id == profile_id)
        stmt = stmt.order_by(R.c.receipt_id.desc()).limit(limit)
        return [_receipt(row) for row in (await self._db.execute(stmt)).all()]

    async def day_receipts(self, space_id: UUID, day: date) -> list[StoredReceipt]:
        start = datetime.combine(day, time(), UTC)
        stmt = (
            select(R)
            .where(
                R.c.space_id == space_id,
                R.c.day == day,
                R.c.receipt_id >= _id_floor(start - _ID_SLACK),
                R.c.receipt_id < _id_floor(start + timedelta(days=1) + _ID_SLACK),
            )
            .order_by(R.c.slice, R.c.seq)
        )
        return [_receipt(row) for row in (await self._db.execute(stmt)).all()]

    async def slot_receipts(
        self, space_id: UUID, day: date, *, after: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        start = datetime.combine(day, time(), UTC)
        stmt = select(R).where(
            R.c.space_id == space_id,
            R.c.day == day,
            R.c.surface == ReceiptSurface.SLOTS.value,
            R.c.receipt_id >= _id_floor(start - _ID_SLACK),
            R.c.receipt_id < _id_floor(start + timedelta(days=1) + _ID_SLACK),
        )
        if after is not None:
            stmt = stmt.where(R.c.receipt_id > after)
        rows = await self._db.execute(stmt.order_by(R.c.receipt_id).limit(limit))
        return [_receipt(row) for row in rows.all()]

    async def day_heads(self, space_id: UUID, day: date) -> list[ChainHead]:
        stmt = select(HEADS).where(HEADS.c.space_id == space_id, HEADS.c.day == day).order_by(HEADS.c.slice)
        return [ChainHead(r.slice, r.day, r.seq, r.hash) for r in (await self._db.execute(stmt)).all()]

    async def save_root(self, space_id: UUID, root: AuditRoot) -> bool:
        stmt = (
            insert(ROOTS)
            .values(
                space_id=space_id,
                day=root.day,
                root=root.root,
                location=root.location,
                anchored_at=root.anchored_at,
            )
            .on_conflict_do_nothing()
            .returning(ROOTS.c.day)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def get_root(self, space_id: UUID, day: date) -> AuditRoot | None:
        stmt = select(ROOTS).where(ROOTS.c.space_id == space_id, ROOTS.c.day == day)
        row = (await self._db.execute(stmt)).one_or_none()
        return AuditRoot(row.day, row.root, row.location, row.anchored_at) if row else None

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "receipts":
            raise ValueError(f"audit does not export {table}")
        stmt = (
            select(R)
            .where(
                R.c.space_id == func.current_setting("niadra.space_id").cast(PG_UUID(as_uuid=True)),
                R.c.change_seq > after_seq,
                R.c.change_seq <= upto_seq,
            )
            .order_by(R.c.change_seq)
            .limit(limit)
        )
        return [
            {**_receipt(row).as_dict(), "change_seq": row.change_seq}
            for row in (await self._db.execute(stmt)).all()
        ]


class PgWatermarkRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def watermark(self) -> datetime | None:
        return cell_watermark([_producer(row) for row in (await self._db.execute(select(MARKS))).all()])

    async def load_producers(self, producer_ids: Sequence[str] | None = None) -> list[ProducerState]:
        stmt = select(MARKS).order_by(MARKS.c.producer_id).with_for_update()
        if producer_ids is not None:
            stmt = stmt.where(MARKS.c.producer_id.in_(producer_ids))
        return [_producer(row) for row in (await self._db.execute(stmt)).all()]

    async def save_producers(self, states: Sequence[ProducerState]) -> None:
        if not states:
            return
        stmt = insert(MARKS).values(
            [
                {
                    "producer_id": s.producer_id,
                    "contiguous_seq": s.contiguous_seq,
                    "watermark_at": s.watermark_at,
                    "state": s.status.value,
                    "last_heard_at": s.last_heard_at,
                    "pending_seqs": sorted(s.pending_seqs),
                }
                for s in states
            ]
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[MARKS.c.producer_id],
                set_={
                    c: stmt.excluded[c]
                    for c in ("contiguous_seq", "watermark_at", "state", "last_heard_at", "pending_seqs")
                },
            )
        )


def _id_floor(at: datetime) -> UUID:
    return uuid7_floor(int(at.timestamp() * 1000))


def _row(space_id: UUID, receipt: StoredReceipt) -> dict[str, Any]:
    d = receipt.draft
    return {
        "space_id": space_id,
        "receipt_id": d.receipt_id,
        "slice": receipt.slice,
        "day": receipt.day,
        "seq": receipt.seq,
        "producer_id": d.producer_id,
        "producer_seq": d.producer_seq,
        "kind": d.kind.value,
        "profile_id": d.profile_id,
        "source_id": d.source_id,
        "person_id": d.person_id,
        "conversation_id": d.conversation_id,
        "task_id": d.task_id,
        "object_id": d.object_id,
        "about_handle_id": d.about_handle_id,
        "purpose": d.purpose,
        "audience": d.audience.value,
        "surface": d.surface.value,
        "item_ids": list(d.item_ids),
        "pack_hash": d.pack_hash,
        "manifest_hash": d.manifest_hash,
        "verification": d.verification,
        "policy_version": d.policy_version,
        "experiment_group": d.experiment_group,
        "query_hash": d.query_hash,
        "withheld": d.withheld,
        "detail": dict(d.detail) or None,
        "read_at": d.read_at,
        "prev_hash": receipt.prev_hash,
        "hash": receipt.hash,
    }


def _receipt(row: Row[Any]) -> StoredReceipt:
    draft = ReceiptDraft(
        receipt_id=row.receipt_id,
        space_id=row.space_id,
        producer_id=row.producer_id,
        producer_seq=row.producer_seq,
        kind=ReceiptKind(row.kind),
        surface=ReceiptSurface(row.surface),
        audience=Audience(row.audience),
        read_at=row.read_at,
        profile_id=row.profile_id,
        source_id=row.source_id,
        person_id=row.person_id,
        conversation_id=row.conversation_id,
        task_id=row.task_id,
        object_id=row.object_id,
        about_handle_id=row.about_handle_id,
        purpose=row.purpose,
        item_ids=tuple(row.item_ids),
        pack_hash=row.pack_hash,
        manifest_hash=row.manifest_hash,
        verification=row.verification,
        policy_version=row.policy_version,
        experiment_group=row.experiment_group,
        query_hash=row.query_hash,
        withheld=row.withheld,
        detail=dict(row.detail or {}),
    )
    return StoredReceipt(draft, row.slice, row.day, row.seq, row.prev_hash, row.hash)


def _producer(row: Row[Any]) -> ProducerState:
    return ProducerState(
        producer_id=row.producer_id,
        contiguous_seq=row.contiguous_seq,
        watermark_at=row.watermark_at,
        status=ProducerStatus(row.state),
        last_heard_at=row.last_heard_at,
        pending_seqs=frozenset(row.pending_seqs),
    )
