"""Measurement of guard lines (13, 6.1) and of backed answers (13, 6.2): a guard the agent went against is
a contradiction of its value, marked `guard`, and `guard.violated` leaves; the control group's guards
are counted apart; a settled value is contradicted by another value of its type; the SDK's backing
report is counted per agent and sends the session to review."""

import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.config.models import WebhookEndpoint
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import MEASURE_RULES
from niadra.domain.measure.slots import GuardCounters
from niadra.domain.records import EventRecord
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import EventKind, Speaker
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.measure.test_service import HANDLE, T0, World, _person
from tests.unit.measure.test_slot_service import VALUE
from tests.unit.measure.test_slot_service import world as world

SHORT = str(VALUE).replace("-", "")[-8:]


def subscribe(cell: Cell) -> None:
    config = cell.config.space(SPACE_ID)
    endpoint = WebhookEndpoint(
        endpoint_id="supervisor",
        url="https://supervisor.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["guard.violated"],
        secret_refs=["spaces/agent/webhook/supervisor"],
    )
    cell.config.put(config.model_copy(update={"webhook_endpoints": [endpoint]}))


def delivered(cell: Cell) -> list[dict[str, Any]]:
    rows = cell.uow.store.module("notify").get("deliveries", {}).values()
    return [d.body for d in rows if d.event_type == "guard.violated"]


def guard_read(world: World, conversation: str, at: datetime, arm: str) -> None:
    """A read whose slots wrote the guard of the settled value `VALUE` (a record) in `arm`."""
    plan = {
        "budget": 60,
        "tokens": 20,
        "items": [],
        "derived": ["guard"] if arm == "guards" else [],
        "channels": {"exact": {"hits": 0}, "values": {"hits": 1}},
        "guards": [{"id": f"value:{VALUE}", "value_type": "record", "arm": arm}],
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
            "detail": {"slots": json.dumps(plan), "analysis": json.dumps({"asked_types": ["record"]})},
        }
    )


def said(
    world: World,
    conversation: str,
    at: datetime,
    text: str,
    *,
    backing: dict[str, Any] | None = None,
    source: UUID = VOICE_SOURCE,
) -> UUID:
    event = EventRecord(
        event_id=uuid7(),
        kind=EventKind.MESSAGE,
        source_id=source,
        channel="voice",
        speaker=Speaker.AI_AGENT,
        occurred_at=at,
        received_at=at,
        handle_ids=(HANDLE,),
        text=text,
        conversation_id=conversation,
        stt_confidence=0.9,
        content_meta={"backing": backing} if backing is not None else None,
    )
    world.ingest.events.append(event)
    return event.event_id


def call(world: World, conversation: str, arm: str, text: str, backing: dict[str, Any] | None = None):
    session = world.session(conversation, T0)
    world.read(conversation, T0 + timedelta(seconds=30))
    guard_read(world, conversation, T0 + timedelta(minutes=1), arm)
    event_id = said(world, conversation, T0 + timedelta(minutes=2), text, backing=backing)
    world.extracted(session)
    return session, event_id


async def guard_totals(world: World) -> dict[tuple[str, str], tuple[int, int]]:
    rows = await world.measure_repo().guard_daily(date(2026, 1, 1), date(2026, 12, 31))
    totals: dict[tuple[str, str], GuardCounters] = {}
    for row in rows:
        totals.setdefault((row.key.value_type, row.key.arm), GuardCounters()).add(row.counters)
    return {k: (c.slots, c.contradicted) for k, c in totals.items()}


def test_the_rules_version_moved() -> None:
    assert MEASURE_RULES == "m4"


async def test_a_guard_the_sdk_flagged_is_a_contradiction_of_its_value_and_left_already(
    world: World, cell: Cell
) -> None:
    subscribe(cell)
    session, event_id = call(
        world,
        "call-g1",
        "guards",
        "O seu registro é 88281.",
        backing={"checked": 1, "unbacked_values": [{"kind": "number"}], "guard_violations": [SHORT]},
    )
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    [marked] = [i for i in row.items if i["id"] == str(VALUE)]
    assert marked == {
        "id": str(VALUE),
        "kind": "value",
        "guard": True,
        "used": False,
        "repeated": False,
        "contradicted": True,
        "evidence": [str(event_id)],
    }
    assert row.contradicted == 1
    [turn] = row.slots
    assert turn["guards"] == [
        {
            "id": str(VALUE),
            "value_type": "record",
            "arm": "guards",
            "violated": True,
            "flagged": True,
            "evidence": [str(event_id)],
        }
    ]
    assert "88280" not in json.dumps(row.slots)
    assert "88281" not in json.dumps(row.slots)
    totals = await world.daily_total(view="voice")
    assert totals["guard_violations"] == 1
    assert totals["contradicted"] == 1
    assert await guard_totals(world) == {("record", "guards"): (1, 1)}
    assert await world.measure_repo().contradicted_value_types(T0.date() - timedelta(days=30)) == {"record"}
    assert delivered(cell) == []  # the SDK's flag sent it when the turn arrived, not again now


async def test_a_violation_only_the_rule_found_sends_guard_violated_from_the_measurement(
    world: World, cell: Cell
) -> None:
    subscribe(cell)
    session, event_id = call(world, "call-g2", "guards", "O número do registro é 88290, anotado.")
    await world.measure(session)
    [body] = delivered(cell)
    data = body["data"]
    assert data["guards"] == [SHORT]
    assert (data["event_id"], data["detected_by"], data["value_type"]) == (
        str(event_id),
        "measurement",
        "record",
    )
    assert data["conversation_id"] == "call-g2"
    assert "88280" not in json.dumps(body)
    assert "88290" not in json.dumps(body)
    # Measuring again (a new extractor) sends nothing new.
    world.extracted(session, versions="x2")
    await world.measure(session)
    assert len(delivered(cell)) == 1


async def test_an_answer_stating_the_guarded_value_is_no_violation(world: World) -> None:
    session, _ = call(world, "call-g3", "guards", "O seu registro continua 88280.")
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.contradicted == 0
    assert await guard_totals(world) == {("record", "guards"): (1, 0)}


async def test_the_control_groups_guards_are_counted_apart_and_contradict_nothing(
    world: World, cell: Cell
) -> None:
    subscribe(cell)
    session, _ = call(world, "call-g4", "control", "O número do registro é 88290.")
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.contradicted == 0
    assert row.slots[0]["guards"][0]["violated"] is True
    assert await guard_totals(world) == {("record", "control"): (1, 1)}
    assert (await world.daily_total(view="voice"))["guard_violations"] == 0
    assert delivered(cell) == []
    report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
    [use] = report.guards
    assert (use.value_type, use.arm, use.slots, use.contradicted) == ("record", "control", 1, 1)


async def test_a_slotted_value_is_contradicted_by_another_value_of_its_type(world: World) -> None:
    session = world.session("call-g5", T0)
    world.read("call-g5", T0 + timedelta(seconds=30))
    from tests.unit.measure.test_slot_service import slots_read

    slots_read(world, "call-g5", T0 + timedelta(minutes=1), (f"value:{VALUE}", ["values"]))
    # The receipt names the value's type since m4; an older one is judged by the claims only.
    receipt = world.audit.receipts[-1]
    plan = json.loads(receipt["detail"]["slots"])
    plan["items"][0]["value_type"] = "record"
    receipt["detail"]["slots"] = json.dumps(plan)
    event_id = said(world, "call-g5", T0 + timedelta(minutes=2), "Achei aqui, o registro é 88290.")
    world.extracted(session)
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    [item] = row.slots[0]["items"]
    assert item["contradicted"] is True
    assert item["value_type"] == "record"
    assert item["evidence"] == [str(event_id)]
    assert await guard_totals(world) == {("record", "value"): (1, 1)}


async def test_the_backing_report_is_counted_per_agent_and_sends_the_session_to_review(world: World) -> None:
    session = world.session("call-b1", T0)
    world.read("call-b1", T0 + timedelta(seconds=30))
    said(
        world,
        "call-b1",
        T0 + timedelta(minutes=1),
        "A parcela é R$ 249,90 e vence dia 10/10.",
        backing={"checked": 2, "unbacked_values": [{"kind": "amount"}, {"kind": "date"}]},
    )
    said(world, "call-b1", T0 + timedelta(minutes=2), "Posso ajudar em algo mais?", backing={"checked": 0})
    said(world, "call-b1", T0 + timedelta(minutes=3), "Até logo.")  # no report: counted in neither
    world.extracted(session)
    await world.measure(session)

    session_level = await world.daily_total(view="")
    assert (session_level["answers_checked"], session_level["unbacked_values"]) == (2, 2)
    report = await world.service.context_use(
        _person(), since=T0.date(), until=T0.date(), group_by=("source_id", "view")
    )
    [bucket] = [b for b in report.buckets if b.view == ""]
    assert bucket.source_id == VOICE_SOURCE
    assert (bucket.answers_checked, bucket.unbacked_values, bucket.unbacked_per_1000) == (2, 2, 1000.0)
    [task] = world.outbox(TaskName.SAMPLE_REVIEW)
    assert task.payload["conditions"] == ["unbacked_value"]
    # Measuring again counts nothing twice.
    world.extracted(session, versions="x2")
    await world.measure(session)
    assert (await world.daily_total(view=""))["answers_checked"] == 2


async def test_an_answer_without_unbacked_values_adds_no_review_condition(world: World) -> None:
    session = world.session("call-b2", T0)
    world.read("call-b2", T0 + timedelta(seconds=30))
    said(world, "call-b2", T0 + timedelta(minutes=1), "O protocolo é 81220.", backing={"checked": 1})
    world.extracted(session)
    await world.measure(session)
    [task] = world.outbox(TaskName.SAMPLE_REVIEW)
    assert "conditions" not in task.payload


@pytest.mark.parametrize("report", [{"checked": "x"}, "not a mapping", {"unbacked_values": 3}])
async def test_an_unreadable_report_is_skipped_never_fails_the_measurement(world: World, report: Any) -> None:
    session = world.session("call-b3", T0)
    world.read("call-b3", T0 + timedelta(seconds=30))
    said(world, "call-b3", T0 + timedelta(minutes=1), "Tudo certo.", backing=report)
    world.extracted(session)
    await world.measure(session)
    assert (await world.measure_repo().get_session(session.session_id)) is not None
