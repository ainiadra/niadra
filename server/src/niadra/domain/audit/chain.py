"""The receipt hash chain (5.11, 5.13).

Each (space, slice, day) is its own chain: `hash = sha256(prev_hash || canonical(receipt))`. The first link
starts from a genesis value bound to the chain's coordinates, so a chain cannot be moved to another slice or
day without breaking. The daily root of a space is a Merkle root over the heads of its slices.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from niadra.domain.receipts import ReceiptDraft

MAX_SLICES = 16

# `extra` is transport metadata for the projections and is not stored, so it stays out of the hash.
_UNCHAINED = frozenset({"extra"})
CHAINED_FIELDS = tuple(f.name for f in fields(ReceiptDraft) if f.name not in _UNCHAINED)


@dataclass(frozen=True, slots=True)
class StoredReceipt:
    draft: ReceiptDraft
    slice: int
    day: date
    seq: int
    prev_hash: bytes
    hash: bytes

    def as_dict(self) -> dict[str, Any]:
        row = {name: getattr(self.draft, name) for name in CHAINED_FIELDS}
        row.update(slice=self.slice, day=self.day, seq=self.seq, prev_hash=self.prev_hash, hash=self.hash)
        return row


@dataclass(frozen=True, slots=True)
class ChainHead:
    slice: int
    day: date
    seq: int
    hash: bytes


@dataclass(frozen=True, slots=True)
class ChainBreak:
    slice: int
    seq: int
    reason: str


def slice_for(profile_id: UUID | None, slices: int) -> int:
    """Stable slice of a profile; receipts without a profile go to slice 0."""
    slices = max(1, min(slices, MAX_SLICES))
    if slices == 1 or profile_id is None:
        return 0
    return int.from_bytes(hashlib.sha256(profile_id.bytes).digest()[:4], "big") % slices


def receipt_day(read_at: datetime) -> date:
    return read_at.astimezone(UTC).date()


def genesis(space_id: UUID, slice_: int, day: date) -> bytes:
    return hashlib.sha256(f"niadra-receipts:{space_id}:{slice_}:{day.isoformat()}".encode()).digest()


def empty_head(space_id: UUID, slice_: int, day: date) -> ChainHead:
    return ChainHead(slice=slice_, day=day, seq=0, hash=genesis(space_id, slice_, day))


def canonical(draft: ReceiptDraft, *, slice_: int, day: date, seq: int) -> bytes:
    """Deterministic bytes of everything the chain protects, the chain position included."""
    body = {name: _plain(getattr(draft, name)) for name in CHAINED_FIELDS}
    body.update(slice=slice_, day=day.isoformat(), seq=seq)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def link_hash(prev_hash: bytes, draft: ReceiptDraft, *, slice_: int, day: date, seq: int) -> bytes:
    return hashlib.sha256(prev_hash + canonical(draft, slice_=slice_, day=day, seq=seq)).digest()


def append(head: ChainHead, drafts: Sequence[ReceiptDraft]) -> tuple[list[StoredReceipt], ChainHead]:
    """Links `drafts` after `head` in the given order; returns the stored rows and the new head."""
    linked: list[StoredReceipt] = []
    seq, prev = head.seq, head.hash
    for draft in drafts:
        seq += 1
        digest = link_hash(prev, draft, slice_=head.slice, day=head.day, seq=seq)
        linked.append(StoredReceipt(draft, head.slice, head.day, seq, prev, digest))
        prev = digest
    return linked, ChainHead(head.slice, head.day, seq, prev)


def verify(space_id: UUID, receipts: Iterable[StoredReceipt]) -> tuple[list[ChainHead], list[ChainBreak]]:
    """Recomputes every chain of a day. Receipts must come ordered by (slice, seq).

    Returns the recomputed heads and every break found: a missing or reordered link, a stored hash
    that does not match the content, or a `prev_hash` that does not point at the previous link.
    """
    heads: dict[int, ChainHead] = {}
    breaks: list[ChainBreak] = []
    for receipt in receipts:
        head = heads.get(receipt.slice) or empty_head(space_id, receipt.slice, receipt.day)
        if receipt.seq != head.seq + 1:
            breaks.append(ChainBreak(receipt.slice, receipt.seq, "sequence_gap"))
        if receipt.prev_hash != head.hash:
            breaks.append(ChainBreak(receipt.slice, receipt.seq, "prev_hash_mismatch"))
        expected = link_hash(
            receipt.prev_hash, receipt.draft, slice_=receipt.slice, day=receipt.day, seq=receipt.seq
        )
        if expected != receipt.hash:
            breaks.append(ChainBreak(receipt.slice, receipt.seq, "content_mismatch"))
        heads[receipt.slice] = ChainHead(receipt.slice, receipt.day, receipt.seq, receipt.hash)
    return [heads[s] for s in sorted(heads)], breaks


def merkle_root(heads: Sequence[ChainHead]) -> bytes:
    """Root over the slice heads of one day, leaves ordered by slice (RFC 6962 style prefixes)."""
    level = [
        hashlib.sha256(b"\x00" + f"{h.slice}:{h.seq}:".encode() + h.hash).digest()
        for h in sorted(heads, key=lambda h: h.slice)
    ]
    if not level:
        return hashlib.sha256(b"").digest()
    while len(level) > 1:
        paired = [
            hashlib.sha256(b"\x01" + a + b).digest() for a, b in zip(level[::2], level[1::2], strict=False)
        ]
        if len(level) % 2:
            paired.append(level[-1])
        level = paired
    return level[0]


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    raise TypeError(f"cannot canonicalize {type(value).__name__}")
