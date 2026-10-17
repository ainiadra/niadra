"""Per-turn slots in MeasureService (11, 3.4): measured per retrieval channel, shown "by channel" in the
context-use report, and learned into the space's fusion weights once a night."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from niadra.domain.measure.model import DailyCounters, DailyKey
from niadra.domain.measure.slots import SlotCounters, SlotDailyKey
from niadra.domain.measure.weights import RULES, WeightOutcome
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.serve.slots import DEFAULT_WEIGHTS
from niadra.domain.vocabulary import Speaker
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell
from tests.unit.measure.test_service import HANDLE, T0, World, _person

EPISODE = "0192f5a0-0000-7000-8000-0000000000b2"
VALUE = UUID("0192f5a0-0000-7000-8000-0000000000d1")
DOC = UUID("0192f5a0-0000-7000-8000-0000000000a9")


@pytest.fixture
def world(cell: Cell) -> World:
    world = World(cell)
    # The skeleton held the older conversation back for the slots (a manifest exclusion, rule `skeleton`).
    world.compile.manifests["m1"] = {
        **world.compile.manifests["m1"],
        "excluded": [
            *world.compile.manifests["m1"]["excluded"],
            {
                "id": EPISODE,
                "kind": "episode",
                "category": "technical_visit",
                "reason": "budget",
                "rule": "skeleton",
            },
        ],
    }
    world.memory.values.append(
        SettledValue(
            value_id=VALUE,
            origin_handle_id=HANDLE,
            type=ValueType.RECORD,
            raw="88280",
            said_at=T0 - timedelta(days=20),
            speaker=Speaker.CUSTOMER,
            session_id=VALUE,
            channel="app",
            source_id=VOICE_SOURCE,
            created_at=T0 - timedelta(days=20),
        )
    )
    return world


def slots_read(
    world: World,
    conversation: str,
    at: datetime,
    *items: tuple[str, list[str]],
    why: dict[str, object] | None = None,
) -> None:
    plan = {
        "budget": 60,
        "tokens": 20,
        "items": [
            {
                "id": item_id,
                "kind": item_id.partition(":")[0],
                "channels": channels,
                "position": n,
                "tokens": 10,
            }
            for n, (item_id, channels) in enumerate(items, start=1)
        ],
        "derived": [],
        "channels": {"exact": {"hits": 1}, "values": {"hits": 1}, "semantic": {"hits": 0, "skipped": True}},
    }
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
                "analysis": json.dumps({"asked_types": ["record"]}),
                **({"why": json.dumps(why)} if why is not None else {}),
            },
        }
    )


def call_with_slots(world: World, versions: str = "x1", why: dict[str, object] | None = None):
    session = world.session("call-7", T0)
    world.read("call-7", T0 + timedelta(seconds=30))
    slots_read(
        world,
        "call-7",
        T0 + timedelta(minutes=1),
        (f"value:{VALUE}", ["values", "exact"]),
        (f"episode:{EPISODE}", ["lexical"]),
        why=why,
    )
    said = world.turn("call-7", T0 + timedelta(minutes=2))
    world.extracted(
        session, claims=[{"value": "o registro é 88280", "event_id": str(said)}], versions=versions
    )
    return session


async def slot_totals(world: World) -> dict[str, dict[str, int]]:
    rows = await world.measure_repo().slot_daily(date(2026, 1, 1), date(2026, 12, 31))
    totals: dict[str, SlotCounters] = {}
    for row in rows:
        totals.setdefault(row.key.slot_channel, SlotCounters()).add(row.counters)
    return {name: c.as_dict() for name, c in totals.items()}


async def test_the_slots_of_a_read_are_measured_per_channel(world: World) -> None:
    session = call_with_slots(world)
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    [turn] = row.slots
    by_id = {i["id"]: i for i in turn["items"]}
    assert by_id[str(VALUE)]["used"]
    assert by_id[str(VALUE)]["channels"] == ["values", "exact"]
    assert not by_id[EPISODE]["used"]
    assert "88280" not in json.dumps(row.slots)  # verdicts and ids only, never the value
    assert "why" not in by_id[EPISODE]  # a receipt written before `why` existed measures without it
    totals = await slot_totals(world)
    assert totals["values"] == {
        "turns": 1,
        "skipped": 0,
        "slots": 1,
        "used": 1,
        "repeated": 0,
        "contradicted": 0,
    }
    assert totals["lexical"]["slots"] == 1
    assert totals["lexical"]["used"] == 0
    assert totals["semantic"] == {
        "turns": 1,
        "skipped": 1,
        "slots": 0,
        "used": 0,
        "repeated": 0,
        "contradicted": 0,
    }

    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    by_channel = {(c.view, c.slot_channel): c for c in report.slot_channels}
    values = by_channel[("voice", "values")]
    assert (values.slots, values.used, values.weight, values.default_weight) == (1, 1, 2.0, 2.0)
    assert values.usage_rate is not None
    assert values.usage_rate.value == 1.0
    assert by_channel[("voice", "semantic")].skipped == 1
    assert report.retrieval_weights is not None
    assert report.retrieval_weights.version == 0
    assert report.retrieval_weights.defaults == dict(DEFAULT_WEIGHTS)


async def test_measuring_again_moves_the_channel_counters_once(world: World) -> None:
    session = call_with_slots(world)
    await world.measure(session)
    first = await slot_totals(world)
    # A new extractor measures the session again (L13): the old row leaves the counters.
    said = world.turn("call-7", T0 + timedelta(minutes=3))
    world.extracted(session, claims=[{"value": "o registro é 88280", "event_id": str(said)}], versions="x2")
    await world.measure(session)
    assert await slot_totals(world) == first
    rows = await world.measure_repo().rows_for_session(session.session_id)
    assert [r.measure_version.rpartition("/")[2] for r in rows] == ["x2"]


async def test_a_session_without_slots_adds_no_channel_rows(world: World) -> None:
    session = world.session("call-8", T0)
    world.read("call-8", T0 + timedelta(seconds=30))
    world.extracted(session)
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.slots == ()
    assert await slot_totals(world) == {}
    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    assert report.slot_channels == []


async def test_a_contradiction_of_a_slotted_item_is_no_selection_miss(world: World) -> None:
    held = {"id": str(DOC), "kind": "fact", "predicate": "document", "reason": "budget", "rule": "skeleton"}
    world.compile.manifests["m1"]["excluded"].append(held)
    for conversation, slotted in (("call-9", True), ("call-10", False)):
        session = world.session(conversation, T0)
        world.read(conversation, T0 + timedelta(seconds=30))
        if slotted:
            slots_read(world, conversation, T0 + timedelta(minutes=1), (f"fact:{DOC}", ["exact"]))
        said = world.turn(conversation, T0 + timedelta(minutes=2))
        world.extracted(session, claims=[{"value": "outro", "event_id": str(said)}])
        run = world.extract.runs[session.session_id]
        assert run.result is not None
        claim = replace(run.result.agent_claims[0], contradicts=(DOC,))
        world.extract.runs[session.session_id] = replace(
            run, result=replace(run.result, agent_claims=(claim,))
        )
        await world.measure(session)
        [row] = await world.measure_repo().rows_for_session(session.session_id)
        missed = [i for i in row.items if i["id"] == str(DOC) and i.get("selection_miss")]
        if slotted:
            assert missed == []
            [turn] = row.slots
            assert turn["items"][0]["contradicted"]
        else:
            assert len(missed) == 1  # without the slot it was a selection miss, as before


# The nightly weights. Sao Paulo is UTC-3: 06:00 UTC is 03:00 there.
NIGHT = datetime(2026, 9, 26, 6, 30, tzinfo=UTC)
YESTERDAY = date(2026, 9, 25)


async def seed_verdicts(world: World, day: date = YESTERDAY) -> None:
    repo = world.measure_repo()
    for name, used in (("values", 150), ("exact", 80), ("lexical", 70), ("semantic", 10)):
        key = SlotDailyKey(day, VOICE_SOURCE, "whatsapp", "chat", "", name)
        await repo.add_slot_daily(key, SlotCounters(turns=200, slots=200, used=used))


async def test_the_weights_move_once_a_night_after_three(world: World, cell: Cell) -> None:
    await seed_verdicts(world)
    cell.clock.at = NIGHT - timedelta(hours=1)  # 02:30 in Sao Paulo
    assert await world.service.learn_weights(SPACE_ID) == 0
    assert await world.measure_repo().weight_runs(10) == []

    cell.clock.at = NIGHT
    assert await world.service.learn_weights(SPACE_ID) == 1
    assert await world.service.learn_weights(SPACE_ID) == 0  # once a day
    [run] = await world.measure_repo().weight_runs(10)
    assert run.outcome is WeightOutcome.APPLIED
    assert run.version == 1
    assert run.for_view("chat")["values"] > DEFAULT_WEIGHTS["values"]
    assert run.for_view("chat")["semantic"] < DEFAULT_WEIGHTS["semantic"]
    assert run.evidence["guard"]["state"] == "no_experiment"

    report = await world.service.context_use(_person(), since=YESTERDAY, until=YESTERDAY)
    assert report.retrieval_weights is not None
    assert report.retrieval_weights.version == 1
    assert report.retrieval_weights.runs[0].outcome == "applied"
    by_channel = {(c.view, c.slot_channel): c for c in report.slot_channels}
    assert by_channel[("chat", "values")].weight == run.for_view("chat")["values"]


async def test_without_verdicts_the_weights_stay_the_defaults(world: World, cell: Cell) -> None:
    cell.clock.at = NIGHT
    assert await world.service.learn_weights(SPACE_ID) == 0
    [run] = await world.measure_repo().weight_runs(10)
    assert run.outcome is WeightOutcome.NO_VERDICTS
    assert run.weights == {}
    # Verdicts older than the window count for nothing either.
    await seed_verdicts(world, day=YESTERDAY - timedelta(days=RULES.window_days))
    cell.clock.at = NIGHT + timedelta(days=1)
    await world.service.learn_weights(SPACE_ID)
    assert (await world.measure_repo().retrieval_weights()).outcome is WeightOutcome.NO_VERDICTS  # type: ignore[union-attr]


async def test_with_the_control_group_worse_nothing_changes(world: World, cell: Cell) -> None:
    await seed_verdicts(world)
    repo = world.measure_repo()
    # Customers with memory were asked again for what memory had more often than the held-out ones.
    for group, repeated in (("exp:memory", 40), ("exp:control", 20)):
        key = DailyKey(YESTERDAY, VOICE_SOURCE, "whatsapp", "chat", group)
        await repo.add_daily(key, DailyCounters(deliveries=100, questions=100, repeated=repeated))
    cell.clock.at = NIGHT
    assert await world.service.learn_weights(SPACE_ID) == 0
    [run] = await repo.weight_runs(10)
    assert run.outcome is WeightOutcome.VETOED
    assert run.weights == {}
    assert run.evidence["guard"]["state"] == "worse"


async def test_context_use_shows_per_slot_what_the_receipts_why_says(world: World) -> None:
    rank = {"position": 1, "weight": 2.0, "contribution": 0.032787}
    why = {
        "weights_version": 4,
        "items": [
            {"id": f"value:{VALUE}", "score": 0.065574, "channels": {"values": rank, "exact": rank}},
            {
                "id": f"episode:{EPISODE}",
                "score": 0.016393,
                "channels": {"lexical": {"position": 1, "weight": 1.0, "contribution": 0.016393}},
            },
        ],
        "derived": [{"kind": "no_record", "rule": "no_record", "asked_types": ["order"], "identifiers": 1}],
    }
    session = call_with_slots(world, why=why)
    await world.measure(session)
    detail = await world.service.conversation_detail(_person(), "call-7")
    [turn] = [t for entry in detail.entries for t in entry.slots]
    by_id = {i["id"]: i for i in turn["items"]}
    assert by_id[str(VALUE)]["why"] == {"score": 0.065574, "channels": {"values": rank, "exact": rank}}
    assert by_id[EPISODE]["why"]["score"] == 0.016393
    assert turn["derived"] == why["derived"]
    assert turn["weights_version"] == 4
