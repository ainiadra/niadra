"""A deadline the customer states is an open item with `due_at` (17, 6.6), whether or not the extraction
model wrote it: "I need it by the 24th" on WhatsApp is on the voice agent's screen before hello."""

import json
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.deadlines import deadlines_in
from niadra.domain.records import OpenItem
from niadra.domain.vocabulary import OpenItemStatus, Speaker
from tests.unit.extract.helpers import T0, event, material, prompt_for, rules, validate_one

TZ = "America/Sao_Paulo"
# The claim the model summarized without the deadline, as the region's run did.
TOW = [
    event(
        Speaker.CUSTOMER,
        "Hi, this is Hannah. I opened claim 36461 because the tow truck did not show up on time.",
        1,
    ),
    event(Speaker.AI_AGENT, "Hi Hannah, I found record 36461. When do you need it solved?", 2),
    event(Speaker.CUSTOMER, "I need it by the 24th, because I am traveling with my family.", 3),
    event(Speaker.AI_AGENT, "Noted: solved by the 24th, on record 36461.", 4),
]


def answer(open_items: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "episode": {
                "summary": "Cliente relatou que o guincho não chegou a tempo; reclamação 36461 registrada.",
                "intent": "complaint",
                "category": "claims",
                "outcome": "pending",
                "resolution": None,
                "sentiment": -0.3,
                "matter": "tow_late",
                "matter_label": "tow truck late",
            },
            "open_items": open_items,
        }
    )


def item(due_at: str | None, evidence: list[str]) -> dict[str, Any]:
    return {
        "description": "Resolve claim 36461",
        "owner": "company",
        "due_at": due_at,
        "evidence": evidence,
        "object": None,
        "expected_operation": None,
        "about": None,
    }


def test_a_deadline_the_model_left_out_becomes_an_item_due_that_day() -> None:
    m = material(TOW)
    [deadline] = validate_one(answer([]), prompt_for(m), m, rules()).open_items
    assert deadline.due_at is not None
    assert deadline.due_at.date() == date(2026, 9, 24)
    # A day, its start in the space's zone, as the model's own dates: never a 23:59 someone seemed to set.
    assert deadline.due_at.time() == time()
    assert deadline.owner == "company"
    assert deadline.evidence_event_ids == (TOW[2].event_id,)
    assert deadline.description == "Deadline the customer asked for (tow truck late)"


def test_a_deadline_an_open_item_already_carries_gets_no_second_item() -> None:
    """Re-extracted, the session's turns state the deadline again and the model, shown the open item that
    carries it, writes none: the net adds no generic twin (B21, juridico-sandbox 03/10/2026)."""
    due = datetime.combine(date(2026, 9, 24), time(23, 59), tzinfo=ZoneInfo(TZ))
    known = OpenItem(
        uuid7(), uuid7(), "Resolve claim 36461", "company", OpenItemStatus.OPEN, T0, {}, due_at=due
    )
    m = material(TOW, items=[known])
    assert validate_one(answer([]), prompt_for(m), m, rules()).open_items == ()
    # A model item that cites the turn without a date still gets it.
    [promise] = validate_one(answer([item(None, ["e3"])]), prompt_for(m), m, rules()).open_items
    assert promise.due_at is not None
    assert promise.due_at.date() == date(2026, 9, 24)


def test_an_item_citing_the_turn_without_a_date_gets_it() -> None:
    m = material(TOW)
    [promise] = validate_one(answer([item(None, ["e3", "e4"])]), prompt_for(m), m, rules()).open_items
    assert promise.description == "Resolve claim 36461"
    assert promise.due_at is not None
    assert promise.due_at.date() == date(2026, 9, 24)


def test_an_item_the_model_already_dated_stands_alone() -> None:
    m = material(TOW)
    items = validate_one(answer([item("2026-09-24", ["e4"])]), prompt_for(m), m, rules()).open_items
    assert len(items) == 1
    assert items[0].due_at == datetime.fromisoformat("2026-09-24").replace(tzinfo=items[0].due_at.tzinfo)


def test_an_agents_deadline_or_a_past_one_is_not_the_customers() -> None:
    turns = [
        event(Speaker.CUSTOMER, "The internet was down until the 20th.", 1),
        event(Speaker.AI_AGENT, "We need to fix it by Friday.", 2),
    ]
    m = material(turns)
    assert validate_one(answer([]), prompt_for(m), m, rules()).open_items == ()


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("Preciso que resolvam até o dia 26.", date(2026, 9, 26)),
        ("preciso disso até sexta-feira", date(2026, 9, 25)),
        ("Lo necesito para el día 20 de octubre.", date(2026, 10, 20)),
        ("I need it fixed by September 30", date(2026, 9, 30)),
        ("preciso até 05/10", date(2026, 10, 5)),
        ("Preciso até o dia 10", date(2026, 10, 10)),  # the 10th already passed this month: the next one
        ("Can you fix it by tomorrow?", date(2026, 9, 23)),
    ],
)
def test_deadlines_in_three_languages_resolve_against_the_day_they_were_said(text: str, day: date) -> None:
    [found] = deadlines_in(text, T0, TZ)
    assert found.day == day


@pytest.mark.parametrize(
    "text",
    [
        "Preciso de resposta em até 24 horas",  # a span, not a day
        "Fiquei sem internet até o dia 20",  # nothing asked
        "I need the invoice from the 3rd",  # no deadline word
    ],
)
def test_what_is_no_deadline_stays_none(text: str) -> None:
    assert deadlines_in(text, T0, TZ) == []


def test_a_deadline_farther_than_four_months_is_never_invented() -> None:
    said = datetime(2026, 9, 22, tzinfo=UTC)
    assert deadlines_in("I need it by the 1st of March", said, TZ) == []
    assert deadlines_in("I need it by the 1st of December", said, TZ)[0].day == date(2026, 12, 1)
