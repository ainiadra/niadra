"""The learned view of each agent and model (13, 6.3): without verdicts the budgets of today; bounded
steps within half and one and a half; never under a section's floor; nothing moves while a control group
says the learned view did worse."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.common.ids import uuid7
from niadra.domain.compile.model import Section, Variant
from niadra.domain.compile.views import (
    SLOTS,
    default_ceilings,
    learned_ceilings,
    learned_slot_budget,
    skeleton_spec,
    slot_budget,
    view_spec,
)
from niadra.domain.measure.model import ContextUseRow
from niadra.domain.measure.views import (
    DEFAULT_ARM,
    DELIVERY,
    LEARNED_ARM,
    VIEWS_RULES,
    ArmState,
    ViewBudgets,
    ViewUseCounters,
    arm,
    arm_guard,
    learn_views,
    reader_key,
    target_of,
    view_use_counters,
)
from niadra.domain.measure.weights import Guard, GuardState, WeightOutcome

DAY = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
BILLING = UUID("0192f5a0-0000-7000-8000-00000000c003")
KEY = reader_key(BILLING, "neutral", "voice")
FREE = Guard(GuardState.NO_EXPERIMENT)


def c(deliveries: int, used: int, missed: int = 0) -> ViewUseCounters:
    return ViewUseCounters(deliveries=deliveries, used=used, missed=missed)


def delivery(deliveries: int, used: int, questions: int = 0, repeated: int = 0) -> ViewUseCounters:
    return ViewUseCounters(deliveries=deliveries, used=used, questions=questions, repeated=repeated)


# A billing agent: uses the facts, never the support conversations the skeleton pins.
BILLING_AGENT = {
    KEY: {
        LEARNED_ARM: {
            "fact": c(200, 150),
            "episode": c(200, 2),
            "action": c(200, 90),
            DELIVERY: delivery(200, 160),
        },
        # The customers who keep the view's own budgets use what they get as often.
        DEFAULT_ARM: {DELIVERY: delivery(60, 48)},
    }
}


def night(
    current: ViewBudgets | None, counters=BILLING_AGENT, guard: Guard = FREE, days: int = 0
) -> ViewBudgets:
    return learn_views(
        current,
        counters,
        guard,
        run_id=uuid7(),
        day=DAY + timedelta(days=days),
        now=NOW + timedelta(days=days),
    )


def test_without_verdicts_the_budgets_are_todays() -> None:
    run = night(None, counters={})
    assert run.outcome is WeightOutcome.NO_VERDICTS
    assert run.factors == {}
    assert run.ceilings_for(BILLING, "neutral", "voice") == ()
    assert learned_slot_budget("voice", run.slot_factor(BILLING, "neutral", "voice")) == slot_budget("voice")
    # A reader with too few deliveries learns nothing either.
    few = {KEY: {LEARNED_ARM: {"episode": c(VIEWS_RULES.min_deliveries - 1, 0), "fact": c(10, 9)}}}
    assert night(None, few).factors == {}


def test_an_unused_section_goes_down_one_bounded_step_and_a_used_one_up() -> None:
    run = night(None)
    assert run.outcome is WeightOutcome.APPLIED
    assert run.version == 1
    factors = run.factors[KEY]
    assert factors["episode"] < 1.0
    assert factors["episode"] >= VIEWS_RULES.steps.down - 1e-6  # one nightly step, never more
    assert factors["fact"] > 1.0
    assert "action" not in factors  # neither clearly above nor below the pool
    ceilings = dict(run.ceilings_for(BILLING, "neutral", "voice"))
    voice = skeleton_spec(view_spec("voice")).sections
    assert ceilings[Section.EPISODES.value] < voice[Section.EPISODES].ceiling  # type: ignore[operator]
    evidence = run.evidence["readers"][KEY]
    assert evidence["arms"]["state"] == ArmState.SAME.value  # nothing learned yet: nothing to compare
    assert evidence["sections"]["episode"]["to"] == round(factors["episode"], 6)


def test_a_week_moves_at_most_ten_percent_and_the_band_holds_forever() -> None:
    run = None
    for day in range(7):
        run = night(run, days=day)
    assert run is not None
    assert run.factors[KEY]["episode"] >= 0.9 - 1e-9
    for day in range(7, 400):
        run = night(run, days=day)
    assert run.factors[KEY]["episode"] == VIEWS_RULES.steps.floor
    assert run.factors[KEY]["fact"] == VIEWS_RULES.steps.ceiling


def test_what_the_agent_missed_goes_back_up() -> None:
    shrunk = night(None)
    missed = {
        KEY: {
            LEARNED_ARM: {"episode": c(200, 2, missed=5), "fact": c(200, 150), DELIVERY: delivery(200, 160)}
        }
    }
    arms = {**missed[KEY], DEFAULT_ARM: {DELIVERY: delivery(60, 48)}}
    run = night(shrunk, {KEY: arms}, days=1)
    assert run.factors[KEY]["episode"] > shrunk.factors[KEY]["episode"]


def test_with_the_space_experiment_worse_nothing_changes() -> None:
    run = night(None, guard=Guard(GuardState.WORSE, lift=-0.05))
    assert run.outcome is WeightOutcome.VETOED
    assert run.factors == {}
    assert run.version == 0


def test_with_the_default_arm_doing_better_nothing_changes() -> None:
    shrunk = night(None)
    # The customers with the learned view use what they get less often than those with the view's own.
    arms = {
        LEARNED_ARM: {**BILLING_AGENT[KEY][LEARNED_ARM], DELIVERY: delivery(200, 80)},
        DEFAULT_ARM: {DELIVERY: delivery(60, 50)},
    }
    run = night(shrunk, {KEY: arms}, days=1)
    assert run.outcome is WeightOutcome.VETOED
    assert run.factors == shrunk.factors
    assert run.evidence["readers"][KEY]["arms"]["state"] == ArmState.WORSE.value
    # Too few default-arm deliveries to tell, once the view differs: nothing moves either.
    small = {LEARNED_ARM: BILLING_AGENT[KEY][LEARNED_ARM], DEFAULT_ARM: {DELIVERY: delivery(5, 5)}}
    assert night(shrunk, {KEY: small}, days=1).factors == shrunk.factors


def test_asked_again_more_often_with_the_learned_view_vetoes() -> None:
    learned = {KEY: {"episode": 0.8}}
    mine = {DELIVERY: ViewUseCounters(deliveries=200, used=160, questions=100, repeated=40)}
    theirs = {DELIVERY: ViewUseCounters(deliveries=60, used=48, questions=60, repeated=6)}
    guard = arm_guard({LEARNED_ARM: mine, DEFAULT_ARM: theirs}, learned=bool(learned))
    assert guard.state is ArmState.WORSE
    holds = arm_guard({LEARNED_ARM: mine, DEFAULT_ARM: {DELIVERY: ViewUseCounters(60, 48, 0, 60, 30)}}, True)
    assert holds.state is ArmState.HOLDS


def test_the_floors_never_move() -> None:
    voice = skeleton_spec(view_spec("voice")).sections
    tiny = dict(learned_ceilings("voice", {"episode": 0.001, "action": 0.001, "fact": 0.001}))
    assert tiny[Section.EPISODES.value] == voice[Section.EPISODES].floor
    assert tiny[Section.ACTIONS.value] == voice[Section.ACTIONS].floor
    assert tiny[Section.FACTS.value] == voice[Section.FACTS].floor
    # Identity, open items and patterns are never learned; nor are views without a skeleton.
    assert set(default_ceilings("voice")) == {"fact", "episode", "action"}
    assert learned_ceilings("brief", {"fact": 0.5}) == ()
    assert learned_ceilings("task:billing", {"fact": 0.5}) == ()
    assert learned_ceilings("voice", {"episode": 1.0}) == ()
    assert learned_slot_budget("voice", 0.5) == slot_budget("voice") // 2
    assert learned_slot_budget("brief", 0.5) == 0


def test_the_arm_is_stable_and_about_a_fifth() -> None:
    space = UUID(int=7)
    arms = [arm(space, UUID(int=n)) for n in range(2000)]
    assert arms == [arm(space, UUID(int=n)) for n in range(2000)]
    assert 0.15 < arms.count(DEFAULT_ARM) / len(arms) < 0.25


def row(**extra: object) -> ContextUseRow:
    defaults: dict[str, object] = {
        "conversation_key": "c",
        "manifest_hash": "m",
        "source_id": BILLING,
        "measure_version": "m4/x",
        "session_id": UUID(int=1),
        "profile_id": UUID(int=2),
        "channel": "voice",
        "view": "voice",
        "experiment_group": "",
        "items": (
            {"id": "f1", "kind": "fact", "used": True},
            {"id": "f2", "kind": "fact", "used": False},
            {"id": "e1", "kind": "episode", "used": False},
            {"id": "e2", "kind": "episode", "delivered": False, "selection_miss": True},
            {"id": "o1", "kind": "open_item", "used": True},
        ),
        "questions": 2,
        "repeated": 1,
        "used": 2,
        "contradicted": 0,
        "late": False,
        "measured_at": NOW,
        "slots": ({"items": [{"id": "x", "used": True}]},),
    }
    return ContextUseRow(**(defaults | extra))  # type: ignore[arg-type]


def test_a_delivery_counts_what_each_section_carried_used_and_missed() -> None:
    counted = view_use_counters(row())
    assert counted["fact"] == ViewUseCounters(deliveries=1, used=1)
    assert counted["episode"] == ViewUseCounters(deliveries=1, used=0, missed=1)
    assert counted[SLOTS] == ViewUseCounters(deliveries=1, used=1)
    assert counted[DELIVERY] == ViewUseCounters(deliveries=1, used=1, questions=2, repeated=1)
    assert "open_item" not in counted  # open items are a floor, never learned
    assert view_use_counters(row(not_measured_reason="receipt_gap")) == {}
    assert view_use_counters(row(view="brief")) == {}
    assert view_use_counters(row(experiment_group="exp:control")) == {}  # held out: saw nothing


def test_the_target_model_comes_from_the_pack_key() -> None:
    plain = Variant(view="voice", slotted=True)
    assert target_of(plain.key) == "neutral"
    assert (
        target_of(Variant(view="voice", slotted=True, provider="openai", model="gpt-4o").key)
        == "openai/gpt-4o"
    )
    assert target_of(None) == "neutral"
    assert target_of("garbage") == "neutral"


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 400), st.integers(0, 400), st.integers(0, 6)), min_size=1, max_size=4),
    st.integers(1, 30),
)
def test_factors_never_leave_the_band_whatever_the_verdicts(
    sections: list[tuple[int, int, int]], nights: int
) -> None:
    names = ["fact", "episode", "action", SLOTS]
    counters = {KEY: {LEARNED_ARM: {names[i]: c(d, min(u, d), m) for i, (d, u, m) in enumerate(sections)}}}
    run = None
    for day in range(nights):
        run = night(run, counters, days=day)
    assert run is not None
    for factor in run.factors.get(KEY, {}).values():
        assert VIEWS_RULES.steps.floor <= factor <= VIEWS_RULES.steps.ceiling
    # Same inputs, same factors.
    again = None
    for day in range(nights):
        again = night(again, counters, days=day)
    assert again is not None
    assert again.factors == run.factors
