"""Learned views in MeasureService (13, 6.3): each measured delivery of a skeleton counts, per
reader (source, target model, view), arm and section, what the agent used and missed; once a night the
readers' views move; the context-use report shows each learned view against the view's own."""

from datetime import date, timedelta

from niadra.domain.compile import Variant
from niadra.domain.measure.model import DailyCounters, DailyKey
from niadra.domain.measure.views import (
    DEFAULT_ARM,
    DELIVERY,
    LEARNED_ARM,
    ViewUseCounters,
    ViewUseKey,
    arm,
    reader_key,
)
from niadra.domain.measure.weights import WeightOutcome
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell
from tests.unit.measure.test_service import PROFILE, T0, World, _person, standard_call
from tests.unit.measure.test_slot_service import NIGHT, YESTERDAY
from tests.unit.measure.test_slot_service import world as world

GPT = Variant(view="voice", slotted=True, provider="openai", model="gpt-4o")


async def totals(world: World) -> dict[tuple[str, str, str], dict[str, int]]:
    rows = await world.measure_repo().view_use_daily(date(2026, 1, 1), date(2026, 12, 31))
    found: dict[tuple[str, str, str], ViewUseCounters] = {}
    for row in rows:
        found.setdefault((row.key.target, row.key.arm, row.key.section), ViewUseCounters()).add(row.counters)
    return {k: c.as_dict() for k, c in found.items()}


async def test_a_skeleton_delivery_counts_per_reader_arm_and_section(world: World) -> None:
    world.compile.manifests["m1"]["variant"] = GPT.key
    session = standard_call(world)
    await world.measure(session)
    group = arm(SPACE_ID, PROFILE)
    counted = await totals(world)
    # The agent said another name: the fact was carried and contradicted, not used.
    assert counted[("openai/gpt-4o", group, "fact")] == {
        "deliveries": 1,
        "used": 0,
        "missed": 0,
        "questions": 0,
        "repeated": 0,
    }
    assert counted[("openai/gpt-4o", group, DELIVERY)]["questions"] == 1
    assert counted[("openai/gpt-4o", group, DELIVERY)]["repeated"] == 1
    # Measured again by a new extractor: the old counts leave, the new ones come, never twice.
    said = world.turn("call-1", T0 + timedelta(minutes=4))
    world.extracted(
        session,
        claims=[{"predicate": "preferred_name", "value": "Marina", "event_id": str(said)}],
        versions="x2",
    )
    await world.measure(session)
    again = await totals(world)
    assert again[("openai/gpt-4o", group, DELIVERY)]["deliveries"] == 1


async def test_a_pack_that_is_no_skeleton_counts_nothing(world: World) -> None:
    world.compile.manifests["m1"]["variant"] = Variant(view="brief").key  # a view without a skeleton
    await world.measure(standard_call(world))
    assert await totals(world) == {}


async def seed(world: World, day: date = YESTERDAY) -> None:
    repo = world.measure_repo()
    for group, sections in (
        (LEARNED_ARM, {"fact": (200, 150, 0), "episode": (200, 2, 0), "action": (200, 90, 0)}),
        (DEFAULT_ARM, {}),
    ):
        for section, (deliveries, used, missed) in sections.items():
            key = ViewUseKey(day, VOICE_SOURCE, "neutral", "voice", group, section)
            await repo.add_view_use(key, ViewUseCounters(deliveries, used, missed))
        key = ViewUseKey(day, VOICE_SOURCE, "neutral", "voice", group, DELIVERY)
        await repo.add_view_use(
            key, ViewUseCounters(200 if group == LEARNED_ARM else 60, 160 if group == LEARNED_ARM else 48)
        )


async def test_the_views_move_once_a_night_after_three(world: World, cell: Cell) -> None:
    await seed(world)
    cell.clock.at = NIGHT - timedelta(hours=1)
    assert await world.service.learn_views(SPACE_ID) == 0
    cell.clock.at = NIGHT
    assert await world.service.learn_views(SPACE_ID) == 1
    assert await world.service.learn_views(SPACE_ID) == 0  # once a day
    [run] = await world.measure_repo().view_budget_runs(10)
    assert run.outcome is WeightOutcome.APPLIED
    key = reader_key(VOICE_SOURCE, "neutral", "voice")
    assert run.factors[key]["episode"] < 1.0

    report = await world.service.context_use(_person(), since=YESTERDAY, until=YESTERDAY)
    assert report.view_budgets is not None
    assert report.view_budgets.version == 1
    [reader] = report.view_budgets.readers
    assert (reader.source_id, reader.target, reader.view) == (VOICE_SOURCE, "neutral", "voice")
    assert reader.budgets["episode"] <= reader.defaults["episode"]
    assert reader.tokens_delta <= 0
    assert report.view_budgets.runs[0].outcome == "applied"


async def test_with_the_space_experiment_worse_nothing_changes(world: World, cell: Cell) -> None:
    await seed(world)
    repo = world.measure_repo()
    for experiment, repeated in (("exp:memory", 40), ("exp:control", 20)):
        key = DailyKey(YESTERDAY, VOICE_SOURCE, "voice", "voice", experiment)
        await repo.add_daily(key, DailyCounters(deliveries=100, questions=100, repeated=repeated))
    cell.clock.at = NIGHT
    assert await world.service.learn_views(SPACE_ID) == 0
    [run] = await repo.view_budget_runs(10)
    assert run.outcome is WeightOutcome.VETOED
    assert run.factors == {}
