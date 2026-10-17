"""In-memory `ReceiptRepository` and `WatermarkRepository` for unit tests."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.audit.chain import ChainHead, StoredReceipt, empty_head
from niadra.domain.audit.watermarks import ProducerState, cell_watermark
from niadra.domain.common.errors import Conflict
from niadra.domain.vocabulary import ReceiptSurface
from niadra.ports.repos.audit import AuditRoot, ReceiptQuery


class InMemoryReceiptRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("audit")
        self._receipts: dict[tuple[UUID, UUID], StoredReceipt] = self._s.setdefault("receipts", {})
        self._heads: dict[tuple[UUID, int, date], ChainHead] = self._s.setdefault("heads", {})
        self._roots: dict[tuple[UUID, date], AuditRoot] = self._s.setdefault("roots", {})
        # Stands in for `change_seq` on receipts: the order of insertion.
        self._change_seq: dict[tuple[UUID, UUID], int] = self._s.setdefault("change_seq", {})
        self._people: dict[UUID, tuple[str, datetime]] = self._s.setdefault("people", {})

    async def name_people(self, labels: Mapping[UUID, tuple[str, datetime]]) -> None:
        for person_id, (label, at) in labels.items():
            if person_id not in self._people or self._people[person_id][1] <= at:
                self._people[person_id] = (label, at)

    async def person_labels(self, person_ids: Sequence[UUID]) -> dict[UUID, str]:
        return {p: self._people[p][0] for p in person_ids if p in self._people}

    async def receipts_for_conversation(
        self, conversation_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._receipts.values()
            if conversation_id in (r.draft.conversation_id, r.draft.task_id)
            and (since is None or r.draft.read_at >= since)
        ]
        return [r.as_dict() for r in sorted(rows, key=lambda r: (r.draft.read_at, r.draft.receipt_id))]

    async def receipts_for_profile(
        self, profile_id: UUID, *, limit: int = 100, before_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._receipts.values()
            if r.draft.profile_id == profile_id and (before_id is None or r.draft.receipt_id < before_id)
        ]
        return [r.as_dict() for r in _newest_first(rows)[:limit]]

    async def expire_receipts(self, space_id: UUID, before: date, *, limit: int) -> int:
        doomed = sorted(k for k, r in self._receipts.items() if k[0] == space_id and r.day < before)[:limit]
        for key in doomed:
            del self._receipts[key]
        if len(doomed) < limit:
            for head in [k for k in self._heads if k[0] == space_id and k[2] < before]:
                del self._heads[head]
            for root in [k for k in self._roots if k[0] == space_id and k[1] < before]:
                del self._roots[root]
        return len(doomed)

    async def lock_head(self, space_id: UUID, slice_: int, day: date) -> ChainHead:
        return self._heads.setdefault((space_id, slice_, day), empty_head(space_id, slice_, day))

    async def save_head(self, space_id: UUID, head: ChainHead) -> None:
        self._heads[(space_id, head.slice, head.day)] = head

    async def stored_ids(self, space_id: UUID, receipt_ids: Sequence[UUID]) -> set[UUID]:
        return {rid for rid in receipt_ids if (space_id, rid) in self._receipts}

    async def append(self, space_id: UUID, receipts: Sequence[StoredReceipt]) -> None:
        for receipt in receipts:
            key = (space_id, receipt.draft.receipt_id)
            if key in self._receipts:
                raise Conflict("duplicate receipt id")
            self._receipts[key] = receipt
            self._change_seq[key] = len(self._change_seq) + 1

    async def list_receipts(self, space_id: UUID, query: ReceiptQuery) -> list[StoredReceipt]:
        def matches(r: StoredReceipt) -> bool:
            d = r.draft
            return (
                (query.profile_id is None or d.profile_id == query.profile_id)
                and (query.source_id is None or d.source_id == query.source_id)
                and (query.conversation_id is None or d.conversation_id == query.conversation_id)
                and (query.kind is None or d.kind == query.kind)
                and (query.since is None or d.read_at >= query.since)
                and (query.until is None or d.read_at < query.until)
                and (query.before_id is None or d.receipt_id < query.before_id)
            )

        return _newest_first([r for r in self._in_space(space_id) if matches(r)])[: query.limit]

    async def get_receipt(self, space_id: UUID, receipt_id: UUID) -> StoredReceipt | None:
        return self._receipts.get((space_id, receipt_id))

    async def receipts_with_item(
        self, space_id: UUID, item_id: str, *, profile_id: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        rows = [
            r
            for r in self._in_space(space_id)
            if item_id in r.draft.item_ids and (profile_id is None or r.draft.profile_id == profile_id)
        ]
        return _newest_first(rows)[:limit]

    async def day_receipts(self, space_id: UUID, day: date) -> list[StoredReceipt]:
        return sorted((r for r in self._in_space(space_id) if r.day == day), key=lambda r: (r.slice, r.seq))

    async def slot_receipts(
        self, space_id: UUID, day: date, *, after: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        found = sorted(
            (
                r
                for r in self._in_space(space_id)
                if r.day == day
                and r.draft.surface is ReceiptSurface.SLOTS
                and (after is None or r.draft.receipt_id > after)
            ),
            key=lambda r: r.draft.receipt_id,
        )
        return found[:limit]

    async def day_heads(self, space_id: UUID, day: date) -> list[ChainHead]:
        heads = [h for (space, _, d), h in self._heads.items() if space == space_id and d == day]
        return sorted(heads, key=lambda h: h.slice)

    async def save_root(self, space_id: UUID, root: AuditRoot) -> bool:
        if (space_id, root.day) in self._roots:
            return False
        self._roots[(space_id, root.day)] = root
        return True

    async def get_root(self, space_id: UUID, day: date) -> AuditRoot | None:
        return self._roots.get((space_id, day))

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "receipts":
            raise ValueError(f"audit does not export {table}")
        rows = sorted(
            (seq, self._receipts[key]) for key, seq in self._change_seq.items() if after_seq < seq <= upto_seq
        )
        return [{**receipt.as_dict(), "change_seq": seq} for seq, receipt in rows[:limit]]

    def _in_space(self, space_id: UUID) -> list[StoredReceipt]:
        return [r for (space, _), r in self._receipts.items() if space == space_id]


class InMemoryWatermarkRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._producers: dict[str, ProducerState] = store.module("audit").setdefault("producers", {})

    async def watermark(self) -> datetime | None:
        return cell_watermark(list(self._producers.values()))

    async def load_producers(self, producer_ids: Sequence[str] | None = None) -> list[ProducerState]:
        wanted = self._producers.keys() if producer_ids is None else producer_ids
        return [self._producers[p] for p in wanted if p in self._producers]

    async def save_producers(self, states: Sequence[ProducerState]) -> None:
        self._producers.update({s.producer_id: s for s in states})


def _newest_first(rows: list[StoredReceipt]) -> list[StoredReceipt]:
    return sorted(rows, key=lambda r: r.draft.receipt_id, reverse=True)
