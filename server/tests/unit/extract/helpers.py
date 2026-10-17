"""Builders for prompt and validation tests: one session with numbered turns."""

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import session_turns, wire_answer
from niadra.app.extract import rules_for
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.prompt import BatchMaterial, Prompt, SessionMaterial, build_prompt
from niadra.domain.extract.result import ExtractionResult
from niadra.domain.extract.rules import ExtractionRules
from niadra.domain.extract.validate import validate
from niadra.domain.records import EventRecord, Fact, LinkRecord, OpenItem, SessionRecord
from niadra.domain.vocabulary import EventKind, FactStatus, OpenItemStatus, Sensitivity, Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, sample_config

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
HANDLE = UUID("0192f5a0-0000-7000-8000-0000000000f1")


def rules() -> ExtractionRules:
    return rules_for(sample_config(), WHATSAPP_SOURCE)


def event(
    speaker: Speaker, text: str, minute: int, *, stt: float | None = None, channel: str = "whatsapp"
) -> EventRecord:
    at = T0 + timedelta(minutes=minute)
    return EventRecord(
        event_id=uuid7(),
        kind=EventKind.MESSAGE,
        source_id=WHATSAPP_SOURCE,
        channel=channel,
        speaker=speaker,
        occurred_at=at,
        received_at=at,
        handle_ids=(HANDLE,),
        text=text,
        conversation_id="c1",
        stt_confidence=stt,
    )


def fact(predicate: str, value: str, *, sensitivity: Sensitivity = Sensitivity.NORMAL) -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=HANDLE,
        predicate=predicate,
        value=value,
        category="contact",
        sensitivity=sensitivity,
        speaker=Speaker.CUSTOMER,
        confidence=0.8,
        valid_at=T0 - timedelta(days=30),
        created_at=T0 - timedelta(days=30),
        status=FactStatus.ACTIVE,
        last_confirmed_at=T0 - timedelta(days=30),
    )


def item(description: str) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=HANDLE,
        description=description,
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=T0 - timedelta(days=1),
        origin={},
    )


def material(
    events: Sequence[EventRecord],
    facts: Sequence[Fact] = (),
    items: Sequence[OpenItem] = (),
    links: Sequence[LinkRecord] = (),
    delivered: frozenset[str] = frozenset(),
    channel: str = "whatsapp",
) -> SessionMaterial:
    session = SessionRecord(
        session_id=uuid7(),
        kind="conversation",
        source_id=WHATSAPP_SOURCE,
        channel=channel,
        opened_at=T0,
        last_event_at=T0 + timedelta(minutes=len(events)),
        conversation_id="c1",
        closed_at=T0 + timedelta(minutes=len(events) + 5),
    )
    return BatchMaterial(
        (SessionMaterial(session, tuple(events)),), tuple(facts), tuple(items), tuple(links), delivered
    )


def prompt_for(
    m: BatchMaterial,
    flagged: frozenset[UUID] = frozenset(),
    spans: dict | None = None,  # type: ignore[type-arg]
) -> Prompt:
    return build_prompt(m, rules(), flagged=flagged, spans=spans or {})


def voice(m: BatchMaterial) -> BatchMaterial:
    [one] = m.sessions
    return replace(m, sessions=(replace(one, session=replace(one.session, channel="voice")),))


def validate_one(raw: str, p: Prompt, m: BatchMaterial, *args: Any, **kwargs: Any) -> ExtractionResult:
    """The one session's result; an answer written the readable way (`episode`, ...) is encoded first."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "episode" in data:
        raw = json.dumps(wire_answer([data], session_turns(p.user)))
    [result] = validate(raw, p, m, *args, **kwargs).values()
    return result
