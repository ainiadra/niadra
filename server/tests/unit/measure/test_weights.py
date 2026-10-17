"""The nightly retrieval weights (11, 3.4): bounded steps, a fixed band, nothing without verdicts, and the
control group's veto."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import DailyCounters, DailyKey, DailyRow
from niadra.domain.measure.slots import SlotCounters
from niadra.domain.measure.weights import (
    RULES,
    Guard,
    GuardState,
    RetrievalWeights,
    WeightOutcome,
    control_guard,
    learn,
    weights_for,
)
from niadra.domain.serve.slots import DEFAULT_WEIGHTS

DAY = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
VOICE = UUID("0192f5a0-0000-7000-8000-00000000c002")
HOLDS = Guard(GuardState.HOLDS, lift=0.1)
FREE = Guard(GuardState.NO_EXPERIMENT)


def used(slots: int, used: int, contradicted: int = 0) -> SlotCounters:
    return SlotCounters(turns=slots, slots=slots, used=used, contradicted=contradicted)


# Values used clearly more than the rest; the semantic channel clearly less.
CLEAR = {
    "chat": {
        "values": used(200, 150),
        "exact": used(200, 80),
        "lexical": used(200, 70),
        "semantic": used(200, 10),
    }
}


def night(
    current: RetrievalWeights | None, counters=CLEAR, guard: Guard = HOLDS, days: int = 0
) -> RetrievalWeights:
    return learn(
        current,
        counters,
        guard,
        run_id=uuid7(),
        day=DAY + timedelta(days=days),
        now=NOW + timedelta(days=days),
    )


def test_without_verdicts_the_weights_are_todays() -> None:
    run = night(None, counters={})
    assert run.outcome is WeightOutcome.NO_VERDICTS
    assert run.version == 0
    assert weights_for(run, "chat") is None  # the fusion keeps DEFAULT_WEIGHTS
    assert weights_for(None, "voice") is None
    assert run.for_view("chat") == dict(DEFAULT_WEIGHTS)
    zero = night(None, counters={"chat": {"exact": SlotCounters(turns=40)}})
    assert zero.outcome is WeightOutcome.NO_VERDICTS


def test_a_channel_used_clearly_more_goes_up_one_step_and_one_used_less_goes_down() -> None:
    run = night(None)
    assert run.outcome is WeightOutcome.APPLIED
    assert run.version == 1
    chat = run.for_view("chat")
    assert chat["values"] == pytest.approx(DEFAULT_WEIGHTS["values"] * RULES.up, abs=1e-6)
    assert chat["semantic"] == pytest.approx(DEFAULT_WEIGHTS["semantic"] * RULES.down, abs=1e-6)
    assert chat["temporal"] == DEFAULT_WEIGHTS["temporal"]  # no evidence, no move
    assert run.evidence["views"]["chat"]["values"]["from"] == DEFAULT_WEIGHTS["values"]
    assert run.for_view("voice") == dict(DEFAULT_WEIGHTS)


def test_a_contradicted_channel_goes_down_even_when_used() -> None:
    counters = {"chat": {"exact": used(200, 120, contradicted=60), "lexical": used(200, 100, contradicted=2)}}
    chat = night(None, counters=counters).for_view("chat")
    assert chat["exact"] < DEFAULT_WEIGHTS["exact"]


def test_too_few_slots_or_a_lone_channel_move_nothing() -> None:
    few = {"chat": {"values": used(RULES.min_slots - 1, 29), "exact": used(RULES.min_slots - 1, 0)}}
    assert night(None, counters=few).outcome is WeightOutcome.UNCHANGED
    lone = {"chat": {"values": used(500, 400), "exact": used(10, 0)}}
    assert night(None, counters=lone).outcome is WeightOutcome.UNCHANGED
    even = {"chat": {"values": used(200, 100), "exact": used(200, 104)}}
    assert night(None, counters=even).outcome is WeightOutcome.UNCHANGED


def test_seven_nights_move_a_weight_at_most_ten_percent() -> None:
    run = None
    for day in range(7):
        run = night(run, days=day)
    assert run is not None
    assert run.version == 7
    chat = run.for_view("chat")
    assert DEFAULT_WEIGHTS["values"] < chat["values"] <= DEFAULT_WEIGHTS["values"] * 1.10
    assert DEFAULT_WEIGHTS["semantic"] * 0.90 <= chat["semantic"] < DEFAULT_WEIGHTS["semantic"]
    assert chat["values"] > DEFAULT_WEIGHTS["values"] * 1.0999


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.dictionaries(
            st.sampled_from(sorted(DEFAULT_WEIGHTS)),
            st.tuples(st.integers(0, 400), st.integers(0, 400), st.integers(0, 400)),
            max_size=5,
        ),
        min_size=1,
        max_size=120,
    )
)
def test_weights_never_leave_the_band(nights: list[dict[str, tuple[int, int, int]]]) -> None:
    run = None
    for day, raw in enumerate(nights):
        counters = {
            "voice": {
                name: SlotCounters(slots=s, used=min(u, s), contradicted=min(c, s))
                for name, (s, u, c) in raw.items()
            }
        }
        run = night(run, counters=counters, guard=FREE, days=day)
        for name, weight in run.for_view("voice").items():
            low, high = RULES.band(name)
            assert low - 1e-9 <= weight <= high + 1e-9


def test_a_run_is_deterministic() -> None:
    run_id = uuid7()
    first = learn(None, CLEAR, HOLDS, run_id=run_id, day=DAY, now=NOW)
    again = learn(None, CLEAR, HOLDS, run_id=run_id, day=DAY, now=NOW)
    assert first == again


def test_with_the_control_group_worse_nothing_changes() -> None:
    before = night(None)
    for state in (GuardState.WORSE, GuardState.NARROWED, GuardState.TOO_SMALL):
        run = night(before, guard=Guard(state), days=1)
        assert run.outcome is WeightOutcome.VETOED
        assert run.weights == before.weights
        assert run.version == before.version
        assert run.evidence["guard"]["state"] == state.value


def _daily(group: str, questions: int, repeated: int) -> DailyRow:
    return DailyRow(
        DailyKey(DAY, VOICE, "voice", "voice", group), DailyCounters(questions=questions, repeated=repeated)
    )


def test_the_guard_compares_the_customers_with_memory_and_the_held_out() -> None:
    assert control_guard([_daily("", 100, 30)], None).state is GuardState.NO_EXPERIMENT
    assert control_guard([_daily("exp:memory", 100, 10), _daily("exp:control", 5, 3)], None).state is (
        GuardState.TOO_SMALL
    )
    worse = control_guard([_daily("exp:memory", 100, 40), _daily("exp:control", 100, 30)], None)
    assert worse.state is GuardState.WORSE
    holds = control_guard([_daily("exp:memory", 100, 10), _daily("exp:control", 100, 30)], None)
    assert holds.state is GuardState.HOLDS
    assert holds.lift is not None
    assert abs(holds.lift - 0.2) < 1e-9
    # The gap was 0.3 when the weights last moved; 0.2 now is narrower than the tolerance allows.
    assert (
        control_guard([_daily("exp:memory", 100, 10), _daily("exp:control", 100, 30)], 0.3).state
        is GuardState.NARROWED
    )
    # Shadow deliveries and rows outside any experiment never enter the comparison.
    assert (
        control_guard(
            [_daily("exp:memory", 100, 10), _daily("exp:control", 100, 30), _daily("exp:shadow", 100, 90)],
            None,
        ).state
        is GuardState.HOLDS
    )


def test_the_baseline_is_the_gap_when_the_weights_last_moved() -> None:
    moved = night(None, guard=Guard(GuardState.HOLDS, lift=0.25))
    assert moved.baseline_lift == 0.25
    still = night(moved, counters={"chat": {"values": used(200, 100), "exact": used(200, 100)}}, days=1)
    assert still.outcome is WeightOutcome.UNCHANGED
    assert still.baseline_lift == 0.25


def test_verdicts_of_a_channel_without_a_default_weight_are_never_judged() -> None:
    # Verdicts a channel since removed left in the window: they neither move it nor weigh on others.
    stray = {"chat": {**CLEAR["chat"], "linked": used(200, 190)}}
    off = night(None, counters=stray)
    assert "linked" not in off.weights.get("chat", {})
    assert "linked" not in off.evidence["views"]["chat"]
    assert off.for_view("chat") == night(None).for_view("chat")
