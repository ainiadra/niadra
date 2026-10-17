"""From the measurement, the item units each source leaves unused (15.8, 19.2): the threshold,
the way back, what never qualifies, and the saving in the report."""

from datetime import timedelta

import pytest

from niadra.config.models import ContextTrimSettings
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import ContextUseRow
from niadra.domain.measure.trim import (
    ContextTrim,
    TrimUnit,
    UnitCounters,
    active_units,
    derive_units,
    unit_counters,
)
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.measure.test_service import T0, World, _person

PLAN = "fact-plan"
VISIT = "episode-visit"
MIN = 20


def plain_manifest() -> dict[str, object]:
    return {
        "view": "voice",
        "items": [
            {"id": PLAN, "kind": "fact", "predicate": "plan", "category": "contract"},
            {"id": VISIT, "kind": "episode", "category": "technical_visit"},
            {"id": "oi-visit", "kind": "open_item", "category": "open_item", "predicate": "technical_visit"},
        ],
        "excluded": [],
    }


def lean_manifest() -> dict[str, object]:
    return {
        "view": "voice",
        "items": [
            {"id": VISIT, "kind": "episode", "category": "technical_visit"},
            {"id": "oi-visit", "kind": "open_item", "category": "open_item", "predicate": "technical_visit"},
        ],
        "excluded": [
            {
                "id": PLAN,
                "kind": "fact",
                "predicate": "plan",
                "category": "contract",
                "reason": "unused",
                "rule": "context_use",
            }
        ],
        "trim": {"units": ["fact:contract"], "items": 1, "tokens_saved": 31},
    }


@pytest.fixture
def world(cell: Cell) -> World:
    w = World(cell)
    config = cell.config.space(SPACE_ID)
    trim = ContextTrimSettings(min_deliveries=MIN)
    cell.config.put(
        config.model_copy(update={"settings": config.settings.model_copy(update={"context_trim": trim})})
    )
    w.compile.manifests["plain"] = plain_manifest()
    w.compile.manifests["lean"] = lean_manifest()
    return w


async def call(world: World, n: int, *, manifest: str = "plain", questions=(), claims=()) -> None:
    """One measured call: the agent read `manifest` and answered once; `questions` and `claims` name the
    predicate of what it asked or said in that answer."""
    conversation = f"call-{manifest}-{n}"
    start = T0 + timedelta(minutes=n)
    session = world.session(conversation, start)
    world.read(conversation, start + timedelta(seconds=10), manifest=manifest)
    turn = str(world.turn(conversation, start + timedelta(minutes=1)))
    world.extracted(
        session,
        questions=[{"type": "asks_data", "predicate": p, "event_id": turn} for p in questions],
        claims=[{"predicate": p, "value": "x", "event_id": turn} for p in claims],
    )
    await world.measure(session)


async def trim_of(world: World) -> ContextTrim | None:
    trims = await world.measure_repo().trims()
    return next((t for t in trims if (t.source_id, t.view) == (VOICE_SOURCE, "voice")), None)


async def test_a_unit_never_used_is_trimmed_once_the_threshold_is_reached(world: World) -> None:
    for n in range(MIN - 1):
        # The agent states the reason of the call (the visit) and never the plan.
        await call(world, n, claims=["contact_reason"])
    below = await trim_of(world)
    assert below is not None
    assert below.units == ()
    await call(world, MIN - 1, claims=["contact_reason"])
    reached = await trim_of(world)
    assert reached is not None
    assert reached.units == (TrimUnit("fact:contract", MIN),)
    assert (reached.window_days, reached.min_deliveries) == (28, MIN)


async def test_the_unit_comes_back_when_the_agent_needs_it(world: World) -> None:
    for n in range(MIN):
        await call(world, n, claims=["contact_reason"])
    trimmed = await trim_of(world)
    assert trimmed is not None
    assert trimmed.unit_names == {"fact:contract"}
    # With the plan left out, the agent asks the customer for it: the unit returns at once.
    await call(world, 100, manifest="lean", questions=["plan"])
    back = await trim_of(world)
    assert back is not None
    assert back.units == ()
    [row] = [r for r in await world.measure_repo().rows_for_conversation("call-lean-100")]
    needed = next(i for i in row.items if i["id"] == PLAN)
    assert (needed["delivered"], needed["trimmed"], needed["selection_miss"]) == (False, True, False)
    assert needed["unit"] == "fact:contract"


async def test_a_session_measured_again_moves_its_units_once(world: World) -> None:
    await call(world, 1, claims=["contact_reason"])
    [session] = [s for s in world.sessions.sessions.values() if s.conversation_id == "call-plain-1"]
    world.extracted(session, claims=(), versions="x2")
    await world.measure(session)
    day = T0.date()
    totals = dict(await world.measure_repo().unit_totals(VOICE_SOURCE, "voice", day, day))
    # The first measurement used the visit; the second, of a newer extractor, did not: only it counts.
    assert totals["episode:technical_visit"] == UnitCounters(1, 0, 0)
    assert totals["fact:contract"] == UnitCounters(1, 0, 0)


async def test_open_items_and_units_the_measurement_cannot_see_are_never_trimmed(world: World) -> None:
    world.compile.manifests["plain"] = {
        "view": "voice",
        "items": [
            {"id": "oi-visit", "kind": "open_item", "category": "open_item", "predicate": "technical_visit"},
            {"id": "t1", "kind": "trait", "category": "trait"},
            {"id": "s1", "kind": "summary", "category": "history"},
            {"id": "a1", "kind": "attr", "category": "profile"},
        ],
        "excluded": [],
    }
    for n in range(MIN + 5):
        await call(world, n)
    trim = await trim_of(world)
    assert trim is not None
    assert trim.units == ()


async def test_the_saving_shows_in_the_report_with_the_trim_and_its_evidence(world: World) -> None:
    for n in range(MIN):
        await call(world, n, claims=["contact_reason"])
    await call(world, 200, manifest="lean", claims=["contact_reason"])
    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    voice = next(b for b in report.buckets if b.source_id == VOICE_SOURCE)
    assert (voice.trimmed_deliveries, voice.tokens_saved) == (1, 31)
    [trim] = report.trims
    assert (trim.source_id, trim.view, trim.active, trim.inactive_reason) == (
        VOICE_SOURCE,
        "voice",
        True,
        None,
    )
    [unit] = trim.units
    assert (unit.unit, unit.kind, unit.category, unit.deliveries) == (
        "fact:contract",
        "fact",
        "contract",
        MIN,
    )
    detail = await world.service.conversation_detail(_person(), "call-lean-200")
    assert detail.entries[0].tokens_saved == 31
    # A vendor never sees another vendor's trims.
    other = await world.service.context_use(principal(WHATSAPP_SOURCE), since=T0.date(), until=T0.date())
    assert other.trims == []


async def test_an_opted_out_source_keeps_its_trim_on_record_but_inactive(world: World) -> None:
    for n in range(MIN):
        await call(world, n, claims=["contact_reason"])
    config = world.cell.config.space(SPACE_ID)
    exempt = ContextTrimSettings(min_deliveries=MIN, opt_out_sources=frozenset({VOICE_SOURCE}))
    world.cell.config.put(
        config.model_copy(update={"settings": config.settings.model_copy(update={"context_trim": exempt})})
    )
    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    [trim] = report.trims
    assert (trim.active, trim.inactive_reason) == (False, "opt_out")


def _row(items: tuple[dict[str, object], ...], reason: str | None = None) -> ContextUseRow:
    return ContextUseRow(
        conversation_key="c",
        manifest_hash="m",
        source_id=VOICE_SOURCE,
        measure_version="m2/x",
        session_id=uuid7(),
        profile_id=uuid7(),
        channel="voice",
        view="voice",
        experiment_group="",
        items=items,
        questions=0,
        repeated=0,
        used=0,
        contradicted=0,
        late=False,
        measured_at=T0,
        not_measured_reason=reason,
    )


def test_a_row_counts_one_delivery_per_unit_and_nothing_when_unmeasured_or_old() -> None:
    items = (
        {"id": "f1", "kind": "fact", "unit": "fact:contract", "used": False},
        {"id": "f2", "kind": "fact", "unit": "fact:contract", "used": True},
        {"id": "e1", "kind": "episode", "unit": "episode:visit", "used": False},
        {"id": "f3", "kind": "fact", "unit": "fact:plan", "delivered": False, "trimmed": True},
        {"id": "f4", "kind": "fact", "delivered": False, "selection_miss": True},
    )
    assert unit_counters(_row(items)) == {
        "fact:contract": UnitCounters(1, 1, 0),
        "episode:visit": UnitCounters(1, 0, 0),
        "fact:plan": UnitCounters(0, 0, 1),
    }
    assert unit_counters(_row(items, reason="not_extracted")) == {}
    # Rows measured before units existed, and holdout rows, carry none: they add and retire nothing.
    assert unit_counters(_row(({"id": "f1", "kind": "fact", "used": False},))) == {}


def test_only_enough_unused_and_unmissed_measurable_units_qualify() -> None:
    counted = {
        "fact:contract": UnitCounters(50, 0, 0),
        "fact:address": UnitCounters(49, 0, 0),
        "fact:plan": UnitCounters(80, 1, 0),
        "episode:visit": UnitCounters(90, 0, 1),
        "open_item": UnitCounters(500, 0, 0),
        "trait:trait": UnitCounters(500, 0, 0),
        "object:invoice": UnitCounters(60, 0, 0),
    }
    assert derive_units(counted, 50) == (TrimUnit("fact:contract", 50), TrimUnit("object:invoice", 60))


def test_the_trim_is_off_when_disabled_opted_out_or_critical() -> None:
    trim = ContextTrim(VOICE_SOURCE, "voice", (TrimUnit("fact:contract", 50),), T0, 28, 50)
    assert active_units(trim, enabled=True, opted_out=False, critical=False) == {"fact:contract"}
    for flags in ({"enabled": False}, {"opted_out": True}, {"critical": True}):
        options = {"enabled": True, "opted_out": False, "critical": False} | flags
        assert active_units(trim, **options) == frozenset()
    assert active_units(None, enabled=True, opted_out=False, critical=False) == frozenset()
