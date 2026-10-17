"""Receipts, chain heads and daily roots (space database), and the producer watermarks (cell
database) (5.11, 5.13)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.audit.chain import ChainHead, StoredReceipt
from niadra.domain.audit.watermarks import ProducerState
from niadra.domain.vocabulary import ReceiptKind


@dataclass(frozen=True, slots=True)
class ReceiptQuery:
    """Filters of `GET /v1/receipts`; `before_id` is the keyset cursor (receipt ids are UUIDv7)."""

    profile_id: UUID | None = None
    source_id: UUID | None = None
    conversation_id: str | None = None
    kind: ReceiptKind | None = None
    since: datetime | None = None
    until: datetime | None = None
    before_id: UUID | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class AuditRoot:
    day: date
    root: bytes
    location: str
    anchored_at: datetime


class ReceiptRepository(Protocol):
    """Owned by the `audit` module."""

    async def receipts_for_conversation(
        self, conversation_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Receipts of one conversation or task, in read order; used by the measurement (19.3)."""
        ...

    async def receipts_for_profile(
        self, profile_id: UUID, *, limit: int = 100, before_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        """Newest first; `before_id` pages back from the last receipt read."""
        ...

    async def expire_receipts(self, space_id: UUID, before: date, *, limit: int) -> int:
        """Retention: receipts of the days before `before`; the heads and root rows of those days
        go once their receipts are gone. The anchored copies hold only roots and stay."""
        ...

    async def lock_head(self, space_id: UUID, slice_: int, day: date) -> ChainHead:
        """The head of one chain, locked until commit (`SELECT ... FOR UPDATE`); created empty."""
        ...

    async def save_head(self, space_id: UUID, head: ChainHead) -> None: ...

    async def stored_ids(self, space_id: UUID, receipt_ids: Sequence[UUID]) -> set[UUID]:
        """The subset already stored; the queue may deliver a receipt twice."""
        ...

    async def append(self, space_id: UUID, receipts: Sequence[StoredReceipt]) -> None:
        """One multi-row insert."""
        ...

    async def list_receipts(self, space_id: UUID, query: ReceiptQuery) -> list[StoredReceipt]:
        """Newest first by `receipt_id`."""
        ...

    async def get_receipt(self, space_id: UUID, receipt_id: UUID) -> StoredReceipt | None: ...

    async def name_people(self, labels: Mapping[UUID, tuple[str, datetime]]) -> None:
        """Keeps the name each person acted under and when, beside the chain; the latest one stays."""
        ...

    async def person_labels(self, person_ids: Sequence[UUID]) -> dict[UUID, str]:
        """The name kept for each person; one never named is left out."""
        ...

    async def receipts_with_item(
        self, space_id: UUID, item_id: str, *, profile_id: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        """Receipts that delivered `item_id`, newest first; the profile narrows the scan to its index."""
        ...

    async def day_receipts(self, space_id: UUID, day: date) -> list[StoredReceipt]:
        """Every receipt of one day, ordered by (slice, seq), for verification."""
        ...

    async def slot_receipts(
        self, space_id: UUID, day: date, *, after: UUID | None, limit: int
    ) -> list[StoredReceipt]:
        """The day's `slots` receipts in id order, after `after`: what the knowledge gaps read (13, 6.5)."""
        ...

    async def day_heads(self, space_id: UUID, day: date) -> list[ChainHead]: ...

    async def save_root(self, space_id: UUID, root: AuditRoot) -> bool:
        """False when the day was already anchored; roots are written once."""
        ...

    async def get_root(self, space_id: UUID, day: date) -> AuditRoot | None: ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` for `receipts` (22.3): rows of the pinned space by `change_seq`."""
        ...


class WatermarkRepository(Protocol):
    """Owned by the `audit` module; one row per receipt producer of the cell, in the cell database."""

    async def watermark(self) -> datetime | None:
        """The cell's receipt watermark: every receipt read before it is stored, in every space (5.11)."""
        ...

    async def load_producers(self, producer_ids: Sequence[str] | None = None) -> list[ProducerState]:
        """Locked for update; `None` loads every producer."""
        ...

    async def save_producers(self, states: Sequence[ProducerState]) -> None: ...
