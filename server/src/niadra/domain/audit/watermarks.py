"""Producer watermarks (5.11).

The receipt queue has no order, so every receipt carries (producer, producer_seq). Per producer the writer
keeps the highest contiguous sequence stored; the producer's watermark is the read time of that receipt,
and the cell watermark is the lowest watermark among active producers. The day close, the measurement
and the projections wait for it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from niadra.domain.receipts import ProducerMark

GAP_TIMEOUT = timedelta(minutes=5)
"""A gap that does not close in this time is a lost receipt: an incident, and the watermark moves on."""

SILENCE_TIMEOUT = timedelta(seconds=60)
"""Idle producers send a mark every 5 s; one silent this long vanished without its final mark."""


class ProducerStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ProducerState:
    producer_id: str
    contiguous_seq: int
    watermark_at: datetime
    status: ProducerStatus
    last_heard_at: datetime
    pending_seqs: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class LostRange:
    producer_id: str
    first_seq: int
    last_seq: int


def record_receipts(
    state: ProducerState | None, producer_id: str, receipts: Iterable[tuple[int, datetime]], now: datetime
) -> ProducerState:
    """Folds stored receipts `(producer_seq, read_at)` into the producer's state.

    Redelivered receipts count again on purpose: they are stored, and the first delivery may have been
    stored by a writer that died before updating the watermark.
    """
    batch = sorted(receipts)
    if not batch:
        return state or ProducerState(producer_id, 0, now, ProducerStatus.ACTIVE, now)
    if state is None:
        # A new process starts at seq 1; its first receipts define where its watermark begins.
        state = ProducerState(producer_id, 0, batch[0][1], ProducerStatus.ACTIVE, now)
    pending = set(state.pending_seqs) | {seq for seq, _ in batch if seq > state.contiguous_seq}
    contiguous = state.contiguous_seq
    while contiguous + 1 in pending:
        contiguous += 1
        pending.discard(contiguous)
    # Any receipt at or below the contiguous head proves its read time; pending ones known only by seq do not.
    watermark_at = max([state.watermark_at, *(read_at for seq, read_at in batch if seq <= contiguous)])
    status = ProducerStatus.ACTIVE if state.status is ProducerStatus.EXPIRED else state.status
    return ProducerState(producer_id, contiguous, watermark_at, status, now, frozenset(pending))


def apply_mark(state: ProducerState | None, mark: ProducerMark, now: datetime) -> ProducerState | None:
    """An idle or stopping producer vouches that nothing after `last_seq` was read before `mark.at`.

    The mark moves the watermark only once every receipt up to `last_seq` is stored. A final mark that
    arrives before its last receipts counts as an idle mark; the producer then expires, with an alert.
    Marks of unknown producers with receipts still in flight are ignored until those receipts arrive.
    """
    if state is None:
        if mark.last_seq > 0:
            return None
        state = ProducerState(mark.producer_id, 0, mark.at, ProducerStatus.ACTIVE, now)
    state = replace(state, last_heard_at=max(state.last_heard_at, now))
    if state.contiguous_seq < mark.last_seq:
        return state
    status = ProducerStatus.CLOSED if mark.final else state.status
    return replace(state, watermark_at=max(state.watermark_at, mark.at), status=status)


def skip_lost_gap(state: ProducerState, now: datetime) -> tuple[ProducerState, LostRange | None]:
    """Declares the oldest gap lost when it stayed open past `GAP_TIMEOUT`, so the cell watermark moves on."""
    if not state.pending_seqs or now - state.watermark_at <= GAP_TIMEOUT:
        return state, None
    next_stored = min(state.pending_seqs)
    lost = LostRange(state.producer_id, state.contiguous_seq + 1, next_stored - 1)
    pending = set(state.pending_seqs)
    contiguous = next_stored - 1
    while contiguous + 1 in pending:
        contiguous += 1
        pending.discard(contiguous)
    return replace(state, contiguous_seq=contiguous, pending_seqs=frozenset(pending)), lost


def expire_silent(state: ProducerState, now: datetime) -> ProducerState:
    if state.status is ProducerStatus.ACTIVE and now - state.last_heard_at > SILENCE_TIMEOUT:
        return replace(state, status=ProducerStatus.EXPIRED)
    return state


def cell_watermark(states: Sequence[ProducerState]) -> datetime | None:
    """Every receipt read before this instant is stored; None before any producer reported."""
    active = [s.watermark_at for s in states if s.status is ProducerStatus.ACTIVE]
    if active:
        return min(active)
    return max((s.watermark_at for s in states), default=None)
