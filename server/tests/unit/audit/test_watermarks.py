from datetime import datetime, timedelta

from niadra.domain.audit.watermarks import (
    GAP_TIMEOUT,
    SILENCE_TIMEOUT,
    ProducerState,
    ProducerStatus,
    apply_mark,
    cell_watermark,
    expire_silent,
    record_receipts,
    skip_lost_gap,
)
from niadra.domain.receipts import ProducerMark

T0 = datetime.fromisoformat("2026-09-22T12:00:00+00:00")


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_out_of_order_receipts_advance_only_through_contiguous_sequences() -> None:
    state = record_receipts(None, "p", [(1, at(1)), (3, at(3)), (4, at(4))], at(5))
    assert (state.contiguous_seq, state.watermark_at, state.pending_seqs) == (1, at(1), frozenset({3, 4}))
    state = record_receipts(state, "p", [(2, at(2))], at(6))
    assert (state.contiguous_seq, state.pending_seqs) == (4, frozenset())
    # seq 3 and 4 were known only as pending; the watermark stays at what this batch proves
    assert state.watermark_at == at(2)


def test_redelivered_receipts_are_harmless() -> None:
    state = record_receipts(None, "p", [(1, at(1)), (2, at(2))], at(3))
    again = record_receipts(state, "p", [(1, at(1)), (2, at(2))], at(4))
    assert (again.contiguous_seq, again.watermark_at, again.pending_seqs) == (2, at(2), frozenset())


def test_an_idle_mark_moves_the_watermark_once_everything_is_stored() -> None:
    state = record_receipts(None, "p", [(1, at(1)), (3, at(3))], at(4))
    held = apply_mark(state, ProducerMark("p", last_seq=3, at=at(10)), at(10))
    assert held is not None
    assert held.watermark_at == at(1)
    state = record_receipts(held, "p", [(2, at(2))], at(11))
    moved = apply_mark(state, ProducerMark("p", last_seq=3, at=at(15)), at(15))
    assert moved is not None
    assert moved.watermark_at == at(15)


def test_a_final_mark_closes_the_producer() -> None:
    state = record_receipts(None, "p", [(1, at(1))], at(2))
    closed = apply_mark(state, ProducerMark("p", last_seq=1, at=at(3), final=True), at(3))
    assert closed is not None
    assert closed.status is ProducerStatus.CLOSED


def test_marks_of_unknown_producers_wait_for_their_receipts() -> None:
    assert apply_mark(None, ProducerMark("p", last_seq=4, at=at(1)), at(1)) is None
    idle = apply_mark(None, ProducerMark("p", last_seq=0, at=at(1)), at(1))
    assert idle is not None
    assert idle.watermark_at == at(1)


def test_a_gap_older_than_the_timeout_is_declared_lost() -> None:
    state = record_receipts(None, "p", [(1, at(1)), (3, at(3)), (4, at(4))], at(5))
    same, lost = skip_lost_gap(state, at(1) + GAP_TIMEOUT)
    assert lost is None
    assert same == state
    moved, lost = skip_lost_gap(state, at(2) + GAP_TIMEOUT)
    assert lost is not None
    assert (lost.first_seq, lost.last_seq) == (2, 2)
    assert (moved.contiguous_seq, moved.pending_seqs) == (4, frozenset())


def test_silent_producers_expire_and_stop_holding_the_cell_watermark() -> None:
    slow = ProducerState("slow", 5, at(0), ProducerStatus.ACTIVE, at(0))
    busy = ProducerState("busy", 9, at(50), ProducerStatus.ACTIVE, at(50))
    assert cell_watermark([slow, busy]) == at(0)
    expired = expire_silent(slow, at(1) + SILENCE_TIMEOUT)
    assert expired.status is ProducerStatus.EXPIRED
    assert cell_watermark([expired, busy]) == at(50)
    assert expire_silent(busy, at(51)) == busy


def test_an_expired_producer_that_speaks_again_is_active() -> None:
    expired = ProducerState("p", 1, at(1), ProducerStatus.EXPIRED, at(1))
    assert record_receipts(expired, "p", [(2, at(90))], at(91)).status is ProducerStatus.ACTIVE


def test_cell_watermark_without_active_producers() -> None:
    assert cell_watermark([]) is None
    closed = ProducerState("p", 3, at(7), ProducerStatus.CLOSED, at(7))
    assert cell_watermark([closed]) == at(7)
