"""Per-turn slots under the rules of 19.2 (11, 3.4): which slotted items the agent's answer used, and
the credit each retrieval channel gets for them."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import QuestionType
from niadra.domain.measure.inputs import slot_deliveries_from_receipts
from niadra.domain.measure.model import (
    AgentClaim,
    AgentQuestion,
    ContextUseRow,
    Delivery,
    DeliveryOutcome,
    ItemOutcome,
    ManifestItem,
    SessionAction,
)
from niadra.domain.measure.signals import CONTACT_REASON, MeasureRules, SessionEvidence
from niadra.domain.measure.slots import (
    ChannelRun,
    MeasuredSlots,
    SlotDelivery,
    SlotEntry,
    SlotOutcome,
    attach,
    measure_slots,
    slot_counters,
    without_slotted_misses,
)

T0 = datetime(2026, 9, 22, 14, 7, tzinfo=UTC)
VOICE = UUID("0192f5a0-0000-7000-8000-00000000c002")
DESK = UUID("0192f5a0-0000-7000-8000-00000000c009")
EPISODE = "0192f5a0-0000-7000-8000-0000000000b2"
VALUE = "0192f5a0-0000-7000-8000-0000000000d1"
FACT = "0192f5a0-0000-7000-8000-0000000000a1"
OBJECT = "0192f5a0-0000-7000-8000-0000000000c7"

KNOWN = {
    EPISODE: ManifestItem(EPISODE, "episode", category="technical_visit"),
    VALUE: ManifestItem(VALUE, "value", value="88.280"),
    FACT: ManifestItem(FACT, "fact", predicate="zip_code", value="01310-100"),
    OBJECT: ManifestItem(OBJECT, "object", object_ref="order//4411", object_id=OBJECT),
}
RULES = MeasureRules(single_valued=frozenset({"zip_code"}))


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def entry(item_id: str, *channels: str, position: int = 1) -> SlotEntry:
    return SlotEntry(item_id, item_id.partition(":")[0], channels, position, 12)


def slots(minutes: float, *entries: SlotEntry, source: UUID = VOICE) -> SlotDelivery:
    runs = {c for e in entries for c in e.channels}
    return SlotDelivery(source, at(minutes), entries, tuple((c, ChannelRun(1)) for c in sorted(runs)))


def said(value: str, minutes: float, predicate: str | None = None, **extra: object) -> AgentClaim:
    return AgentClaim(uuid7(), VOICE, at(minutes), predicate=predicate, value=value, **extra)  # type: ignore[arg-type]


def verdicts(measured: list[MeasuredSlots]) -> dict[str, tuple[bool, bool, bool]]:
    return {i.entry.public_id: (i.used, i.repeated, i.contradicted) for m in measured for i in m.items}


def test_the_receipt_plan_is_read_from_its_detail() -> None:
    plan = {
        "budget": 60,
        "tokens": 24,
        "items": [
            {
                "id": f"episode:{EPISODE}",
                "kind": "episode",
                "channels": ["exact", "lexical"],
                "position": 1,
                "tokens": 14,
            },
            {"id": f"value:{VALUE}", "kind": "value", "channels": ["values"], "position": 2, "tokens": 10},
        ],
        "derived": ["count"],
        "channels": {"exact": {"hits": 1}, "lexical": {"hits": 3}, "semantic": {"hits": 0, "skipped": True}},
    }
    receipts = [
        {"surface": "pack", "kind": "read", "source_id": VOICE, "read_at": at(1), "manifest_hash": "m1"},
        {
            "surface": "slots",
            "kind": "read",
            "source_id": VOICE,
            "read_at": at(1),
            "experiment_group": "exp:memory",
            "detail": {"slots": json.dumps(plan), "analysis": "{}"},
        },
        {"surface": "slots", "kind": "read", "source_id": VOICE, "read_at": at(2), "detail": {"slots": "{"}},
        {
            "surface": "slots",
            "kind": "read",
            "source_id": VOICE,
            "read_at": at(99),
            "detail": {"slots": "{}"},
        },
    ]
    [found] = slot_deliveries_from_receipts(receipts, since=T0, until=at(30))
    assert [(e.item_id, e.public_id, e.channels, e.position) for e in found.items] == [
        (f"episode:{EPISODE}", EPISODE, ("exact", "lexical"), 1),
        (f"value:{VALUE}", VALUE, ("values",), 2),
    ]
    assert dict(found.channels)["semantic"] == ChannelRun(0, skipped=True)
    assert found.experiment_group == "exp:memory"


def test_the_agent_saying_a_slotted_value_back_uses_it_however_punctuated() -> None:
    delivered = [slots(1, entry(f"value:{VALUE}", "values", "exact"))]
    evidence = SessionEvidence(claims=(said("o registro é 88280", 2),))
    assert verdicts(measure_slots(delivered, KNOWN, evidence, RULES)) == {VALUE: (True, False, False)}


def test_an_answer_about_the_reason_of_contact_uses_a_slotted_conversation() -> None:
    delivered = [slots(1, entry(f"episode:{EPISODE}", "lexical"))]
    evidence = SessionEvidence(claims=(said("a visita foi remarcada", 2, predicate=CONTACT_REASON),))
    assert verdicts(measure_slots(delivered, KNOWN, evidence, RULES))[EPISODE] == (True, False, False)


def test_asking_again_what_a_slot_said_is_repetition_and_a_different_value_is_contradiction() -> None:
    delivered = [slots(1, entry(f"fact:{FACT}", "exact"), entry(f"object:{OBJECT}", "temporal", position=2))]
    evidence = SessionEvidence(
        questions=(AgentQuestion(QuestionType.ASKS_DATA, uuid7(), VOICE, at(2), object_ref="order//4411"),),
        claims=(said("04538-133", 3, predicate="zip_code"),),
    )
    found = verdicts(measure_slots(delivered, KNOWN, evidence, RULES))
    assert found[OBJECT] == (False, True, False)
    assert found[FACT] == (False, False, True)


def test_an_action_on_the_slotted_object_is_use() -> None:
    delivered = [slots(1, entry(f"object:{OBJECT}", "exact"))]
    evidence = SessionEvidence(actions=(SessionAction(uuid7(), VOICE, at(4), object_id=OBJECT),))
    assert verdicts(measure_slots(delivered, KNOWN, evidence, RULES))[OBJECT] == (True, False, False)


def test_only_the_answer_to_that_turn_counts_by_the_same_source() -> None:
    first = slots(1, entry(f"value:{VALUE}", "values"))
    second = slots(5, entry(f"episode:{EPISODE}", "lexical"))
    evidence = SessionEvidence(
        claims=(
            said("88280", 0.5),  # before the read
            said("88280", 6),  # after the next read: the answer to another turn
            AgentClaim(uuid7(), DESK, at(2), value="88280"),  # another agent
        )
    )
    measured = measure_slots([first, second], KNOWN, evidence, RULES)
    assert verdicts(measured)[VALUE] == (False, False, False)


def test_an_unknown_item_is_judged_by_its_id_alone() -> None:
    delivered = [slots(1, entry("summary:tail", "semantic"))]
    evidence = SessionEvidence(claims=(said("tudo certo", 2),))
    assert verdicts(measure_slots(delivered, KNOWN, evidence, RULES)) == {"tail": (False, False, False)}


def test_each_channel_that_brought_an_item_gets_its_credit() -> None:
    measured = MeasuredSlots(
        SlotDelivery(VOICE, at(1), (), (("exact", ChannelRun(1)), ("semantic", ChannelRun(0, skipped=True)))),
        (
            SlotOutcome(entry(f"value:{VALUE}", "exact", "values"), used=True),
            SlotOutcome(entry(f"episode:{EPISODE}", "values"), repeated=True, contradicted=True),
        ),
    )
    row = _row(slots=(measured.to_json(),))
    counted = {name: c.as_dict() for name, c in slot_counters(row).items()}
    assert counted["exact"] == {
        "turns": 1,
        "skipped": 0,
        "slots": 1,
        "used": 1,
        "repeated": 0,
        "contradicted": 0,
    }
    assert counted["values"] == {
        "turns": 0,
        "skipped": 0,
        "slots": 2,
        "used": 1,
        "repeated": 1,
        "contradicted": 1,
    }
    assert counted["semantic"] == {
        "turns": 1,
        "skipped": 1,
        "slots": 0,
        "used": 0,
        "repeated": 0,
        "contradicted": 0,
    }
    assert slot_counters(_row(slots=(measured.to_json(),), not_measured_reason="receipt_gap")) == {}


def test_slots_join_the_pack_read_they_came_with() -> None:
    early = DeliveryOutcome(Delivery("m1", VOICE, at(0)), view="voice")
    late = DeliveryOutcome(Delivery("m2", VOICE, at(10)), view="voice")
    desk = DeliveryOutcome(Delivery("m1", DESK, at(3)), view="voice")
    turn = MeasuredSlots(slots(4, entry(f"value:{VALUE}", "values")), ())
    after = MeasuredSlots(slots(10, entry(f"value:{VALUE}", "values")), ())
    joined = attach([early, late, desk], [turn, after])
    assert joined == {0: [turn], 1: [after]}


def test_a_contradicted_item_a_slot_carried_is_no_selection_miss() -> None:
    outcome = DeliveryOutcome(
        Delivery("m1", VOICE, at(0)),
        view="voice",
        items=(
            ItemOutcome(FACT, "fact", delivered=False, selection_miss=True),
            ItemOutcome("other", "fact", delivered=False, selection_miss=True),
        ),
    )
    carried = MeasuredSlots(slots(1), (SlotOutcome(entry(f"fact:{FACT}", "exact"), contradicted=True),))
    assert [i.item_id for i in without_slotted_misses(outcome, [carried]).items] == ["other"]


def _row(**extra: object) -> ContextUseRow:
    fields: dict[str, object] = {
        "conversation_key": "c",
        "manifest_hash": "m1",
        "source_id": VOICE,
        "measure_version": "m3/x",
        "session_id": uuid7(),
        "profile_id": uuid7(),
        "channel": "voice",
        "view": "voice",
        "experiment_group": "",
        "items": (),
        "questions": 0,
        "repeated": 0,
        "used": 0,
        "contradicted": 0,
        "late": False,
        "measured_at": datetime.combine(date(2026, 9, 22), datetime.min.time(), UTC),
    }
    return ContextUseRow(**(fields | extra))  # type: ignore[arg-type]
