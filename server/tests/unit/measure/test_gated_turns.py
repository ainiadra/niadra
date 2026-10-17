"""The intent gate in measurement (13, 6.4): a turn served without slots is counted apart, with whether
the agent then asked the customer for something memory held, so the gate is calibrated by its cost; it
never moves a retrieval weight."""

import json
from datetime import datetime, timedelta

from niadra.domain.measure.slots import GATED, SlotCounters
from niadra.domain.measure.weights import window_counters
from niadra.testing.cell import VOICE_SOURCE, Cell
from tests.unit.measure.test_service import T0, World, _person
from tests.unit.measure.test_slot_service import slot_totals
from tests.unit.measure.test_slot_service import world as world


def gated_read(world: World, conversation: str, at: datetime) -> None:
    plan = {"budget": 0, "tokens": 0, "items": [], "derived": [], "channels": {}}
    world.audit.receipts.append(
        {
            "conversation_id": conversation,
            "kind": "read",
            "surface": "slots",
            "source_id": VOICE_SOURCE,
            "manifest_hash": None,
            "read_at": at,
            "experiment_group": None,
            "profile_id": None,
            "detail": {
                "slots": json.dumps(plan),
                "analysis": json.dumps({"intent": "none", "forms": ["thanks"]}),
                "why": json.dumps({"items": [], "derived": []}),
                "intent": "none",
            },
        }
    )


async def test_a_gated_turn_followed_by_asking_again_is_counted_apart(world: World, cell: Cell) -> None:
    session = world.session("call-g", T0)
    world.read("call-g", T0 + timedelta(seconds=30))
    gated_read(world, "call-g", T0 + timedelta(minutes=1))
    asked = world.turn("call-g", T0 + timedelta(minutes=2))
    world.extracted(
        session, questions=[{"type": "asks_data", "predicate": "preferred_name", "event_id": str(asked)}]
    )
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    [turn] = row.slots
    assert turn == {
        "at": (T0 + timedelta(minutes=1)).isoformat(),
        "intent": "none",
        "channels": {},
        "items": [],
        "asked_again": True,
        "evidence": [str(asked)],
    }
    totals = await slot_totals(world)
    assert totals == {GATED: SlotCounters(turns=1, repeated=1).as_dict()}

    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    assert report.slot_channels == []  # the gate is not a retrieval channel
    [gated] = report.gated_turns
    assert (gated.view, gated.turns, gated.asked_again) == ("voice", 1, 1)


async def test_a_gated_turn_the_agent_answered_without_asking_counts_no_repetition(world: World) -> None:
    session = world.session("call-h", T0)
    world.read("call-h", T0 + timedelta(seconds=30))
    gated_read(world, "call-h", T0 + timedelta(minutes=1))
    # A question about something memory never held is not what the gate cost.
    asked = world.turn("call-h", T0 + timedelta(minutes=2))
    world.extracted(
        session, questions=[{"type": "asks_data", "predicate": "favorite_color", "event_id": str(asked)}]
    )
    await world.measure(session)
    totals = await slot_totals(world)
    assert totals == {GATED: SlotCounters(turns=1).as_dict()}


async def test_the_gate_counters_never_reach_the_weights(world: World) -> None:
    session = world.session("call-i", T0)
    world.read("call-i", T0 + timedelta(seconds=30))
    gated_read(world, "call-i", T0 + timedelta(minutes=1))
    world.turn("call-i", T0 + timedelta(minutes=2))
    world.extracted(session)
    await world.measure(session)
    rows = await world.measure_repo().slot_daily(T0.date(), T0.date())
    counters = window_counters(rows)
    assert set(counters["voice"]) == {GATED}
    assert sum(c.slots for c in counters["voice"].values()) == 0  # nothing a weight learns from
