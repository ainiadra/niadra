"""Time words in a history search, in Portuguese, English and Spanish, without an LLM (10, D.1)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from niadra.config.models import Mapping, MappingRule, SpaceConfig
from niadra.domain.records import BusinessObject, EventRecord, ObjectKey
from niadra.domain.serve.relative_time import (
    Relation,
    ResolvedAnchor,
    SystemMark,
    find_when,
    marks_from_events,
    marks_from_objects,
    read_anchor,
    read_when,
    resolve_anchor,
)
from niadra.domain.vocabulary import EventKind, Speaker

TZ = "America/Sao_Paulo"
# Thursday 24/09/2026, 15:00 in São Paulo.
NOW = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)


def days(expression: str) -> tuple[date, date] | None:
    """First and last local day of the window."""
    window = read_when(expression, NOW, TZ)
    if window is None:
        return None
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(TZ)
    return window.since.astimezone(tz).date(), (window.until - timedelta(microseconds=1)).astimezone(
        tz
    ).date()


D = date


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("hoje", (D(2026, 9, 24), D(2026, 9, 24))),
        ("today", (D(2026, 9, 24), D(2026, 9, 24))),
        ("Ontem", (D(2026, 9, 23), D(2026, 9, 23))),
        ("ayer", (D(2026, 9, 23), D(2026, 9, 23))),
        ("anteontem", (D(2026, 9, 22), D(2026, 9, 22))),
        ("the day before yesterday", (D(2026, 9, 22), D(2026, 9, 22))),
        ("semana passada", (D(2026, 9, 14), D(2026, 9, 20))),
        ("na semana passada", (D(2026, 9, 14), D(2026, 9, 20))),
        ("last week", (D(2026, 9, 14), D(2026, 9, 20))),
        ("la semana pasada", (D(2026, 9, 14), D(2026, 9, 20))),
        ("esta semana", (D(2026, 9, 21), D(2026, 9, 27))),
        ("mês passado", (D(2026, 8, 1), D(2026, 8, 31))),
        ("last month", (D(2026, 8, 1), D(2026, 8, 31))),
        ("el mes pasado", (D(2026, 8, 1), D(2026, 8, 31))),
        ("este mês", (D(2026, 9, 1), D(2026, 9, 30))),
        ("ano passado", (D(2025, 1, 1), D(2025, 12, 31))),
        ("el año pasado", (D(2025, 1, 1), D(2025, 12, 31))),
        ("3 days ago", (D(2026, 9, 21), D(2026, 9, 21))),
        ("há três dias", (D(2026, 9, 21), D(2026, 9, 21))),
        ("hace dos semanas", (D(2026, 9, 7), D(2026, 9, 13))),
        ("duas semanas atrás", (D(2026, 9, 7), D(2026, 9, 13))),
        ("a month ago", (D(2026, 8, 1), D(2026, 8, 31))),
        ("faz um ano", (D(2025, 1, 1), D(2025, 12, 31))),
        ("em março", (D(2026, 3, 1), D(2026, 3, 31))),
        ("in November", (D(2025, 11, 1), D(2025, 11, 30))),
        ("en marzo de 2025", (D(2025, 3, 1), D(2025, 3, 31))),
        ("dezembro de 2025", (D(2025, 12, 1), D(2025, 12, 31))),
        ("2025", (D(2025, 1, 1), D(2025, 12, 31))),
        ("last 3 days", (D(2026, 9, 22), D(2026, 9, 24))),
        ("últimos 7 dias", (D(2026, 9, 18), D(2026, 9, 24))),
        ("las últimas 2 semanas", (D(2026, 9, 11), D(2026, 9, 24))),
        ("segunda passada", (D(2026, 9, 21), D(2026, 9, 21))),
        ("on Thursday", (D(2026, 9, 17), D(2026, 9, 17))),
        ("el viernes pasado", (D(2026, 9, 18), D(2026, 9, 18))),
        ("na sexta-feira", (D(2026, 9, 18), D(2026, 9, 18))),
        ("recentemente", (D(2026, 9, 18), D(2026, 9, 24))),
    ],
)
def test_expressions_read_as_calendar_windows(expression: str, expected: tuple[date, date]) -> None:
    assert days(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["", "quando deu problema", "the other day", "em dezembro de 2030", "3 fortnights ago", "99999"],
)
def test_what_it_does_not_know_it_leaves_out(expression: str) -> None:
    assert read_when(expression, NOW, TZ) is None


def test_the_window_is_in_the_space_time_zone() -> None:
    # 01:30 UTC on the 25th is still the 24th in São Paulo: "today" is the 24th there.
    late = datetime(2026, 9, 25, 1, 30, tzinfo=UTC)
    window = read_when("today", late, TZ)
    assert window is not None
    assert window.since == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    assert read_when("today", late, "UTC").since == datetime(2026, 9, 25, tzinfo=UTC)  # type: ignore[union-attr]


# Anchors on the customer's system events (11, 3.3): "depois da vistoria", "before the delivery".

LEXICON = SpaceConfig(
    space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox"
).anchor_lexicon()


@pytest.mark.parametrize(
    ("question", "relation", "event"),
    [
        ("O que eu relatei depois da vistoria?", Relation.AFTER, "inspection"),
        ("O que aconteceu após a entrega do pedido?", Relation.AFTER, "delivery"),
        ("E antes da instalação, o que eu tinha pedido?", Relation.BEFORE, "installation"),
        ("Desde a troca do cartão a compra não passa", Relation.SINCE, "card_replacement"),
        ("depois que o técnico veio, a internet caiu de novo", Relation.AFTER, "technician_visit"),
        ("O que eu falei depois da primeira coleta?", Relation.AFTER, "pickup"),
        ("Desde a última fatura estou pagando a mais", Relation.SINCE, "invoice"),
        ("What did I report after the technician's visit?", Relation.AFTER, "technician_visit"),
        ("What happened after my order was delivered?", Relation.AFTER, "delivery"),
        ("Before the installation, what did you promise?", Relation.BEFORE, "installation"),
        ("Since your last bill I have been overcharged", Relation.SINCE, "invoice"),
        ("prior to the pick-up, what did I say?", Relation.BEFORE, "pickup"),
        ("Anything since I paid?", Relation.SINCE, "payment"),
        ("¿Qué reporté después de la inspección?", Relation.AFTER, "inspection"),
        ("¿Qué pasó después del cambio de tarjeta?", Relation.AFTER, "card_replacement"),
        ("Antes de la entrega, ¿qué les dije?", Relation.BEFORE, "delivery"),
        ("Desde la visita del técnico no tengo señal", Relation.SINCE, "technician_visit"),
        ("Tras la recogida, ¿qué número me dieron?", Relation.AFTER, "pickup"),
        ("Desde el pago la cuenta sigue bloqueada", Relation.SINCE, "payment"),
    ],
)
def test_an_anchor_names_a_relation_and_a_kind_of_event(
    question: str, relation: Relation, event: str
) -> None:
    phrase = read_anchor(question, LEXICON)
    assert phrase is not None
    assert (phrase.relation, phrase.name) == (relation, event)


@pytest.mark.parametrize(
    "question",
    [
        "Quantas vezes eu já reclamei da internet caindo com vocês antes desta ligação?",
        "How many times have I complained about failed delivery attempts before this call?",
        "Qual é o CEP de entrega que vocês têm hoje no meu cadastro?",
        "Qual é a segunda via da fatura?",
        "Isso foi antes de ontem",
        "Antes de tudo, a entrega atrasou",
        "Vou pagar depois de amanhã",
        "Since you charged me twice, I want a refund",
        "¿Cuántas veces me quejé antes de esta llamada?",
        "What is the delivery ZIP code you have on file now?",
        "",
    ],
)
def test_no_anchor_without_an_event_word_right_after_the_time_word(question: str) -> None:
    assert read_anchor(question, LEXICON) is None


def resolved(question: str, marks: list[SystemMark]) -> ResolvedAnchor:
    phrase = read_anchor(question, LEXICON)
    assert phrase is not None
    anchor = resolve_anchor(phrase, marks, NOW)
    assert anchor is not None
    return anchor


def test_the_first_event_is_the_earliest_and_otherwise_the_latest() -> None:
    marks = [
        SystemMark("order.delivered", NOW - timedelta(days=30)),
        SystemMark("shipment.delivered", NOW - timedelta(days=5)),
        SystemMark("invoice.issued", NOW - timedelta(days=1)),
    ]
    assert resolved("depois da entrega", marks).mark.at == NOW - timedelta(days=5)
    assert resolved("depois da primeira entrega", marks).mark.at == NOW - timedelta(days=30)


def test_after_starts_just_after_the_event_and_before_ends_at_it() -> None:
    at = NOW - timedelta(days=10)
    marks = [SystemMark("inspection.completed", at)]
    after = resolved("after the inspection", marks).window
    assert after.since > at
    assert after.until > NOW
    assert resolved("before the inspection", marks).window.until == at


def test_an_anchor_without_its_event_resolves_to_nothing() -> None:
    phrase = read_anchor("depois da vistoria", LEXICON)
    assert phrase is not None
    assert resolve_anchor(phrase, [SystemMark("order.delivered", NOW - timedelta(days=3))], NOW) is None
    # An event later than the moment the question is read never anchors it (a replay of an older turn).
    assert resolve_anchor(phrase, [SystemMark("inspection.completed", NOW + timedelta(hours=1))], NOW) is None


def test_a_mapping_rule_names_its_own_event_and_shares_a_word_with_the_starter() -> None:
    rule = MappingRule(
        match="type == 'vistoria'",
        canonical_type="vistoria.concluida",
        idempotency_key="id",
        occurred_at="at",
        anchor_terms=["vistoria", "laudo"],
    )
    config = SpaceConfig(
        space_id=uuid4(),
        tenant_id=uuid4(),
        region="us-east-2",
        environment="sandbox",
        mappings=[Mapping(source_id=uuid4(), version="1", rules=[rule])],
    )
    lexicon = config.anchor_lexicon()
    marks = [SystemMark("vistoria.concluida", NOW - timedelta(days=2))]
    for question in ("depois da vistoria", "depois do laudo"):
        phrase = read_anchor(question, lexicon)
        assert phrase is not None
        assert resolve_anchor(phrase, marks, NOW) is not None


def test_marks_come_from_the_objects_last_event_and_from_system_events() -> None:
    obj = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey("claim", "erp", "21889"),
        state={"status": "completed", "last_event_type": "inspection.completed"},
        as_of=NOW - timedelta(days=12),
        source_id=uuid4(),
    )
    bare = BusinessObject(
        object_id=uuid4(), key=ObjectKey("order", "erp", "1"), state={}, as_of=NOW, source_id=uuid4()
    )
    assert marks_from_objects([obj, bare]) == (SystemMark("inspection.completed", obj.as_of, obj.object_id),)
    event = EventRecord(
        event_id=uuid4(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=uuid4(),
        channel="system",
        speaker=Speaker.SYSTEM,
        occurred_at=NOW,
        received_at=NOW,
        handle_ids=(),
        canonical_type="order.delivered",
    )
    message = replace(event, kind=EventKind.MESSAGE, canonical_type=None)
    assert marks_from_events([event, message]) == (SystemMark("order.delivered", NOW),)


# Calendar words inside a whole question.


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("O que eu falei na semana passada sobre a fatura?", (D(2026, 9, 14), D(2026, 9, 20))),
        ("What did I tell you last month?", (D(2026, 8, 1), D(2026, 8, 31))),
        ("¿Qué les dije hace dos semanas?", (D(2026, 9, 7), D(2026, 9, 13))),
        ("Liguei ontem e ninguém resolveu", (D(2026, 9, 23), D(2026, 9, 23))),
        ("o que combinamos na sexta-feira?", (D(2026, 9, 18), D(2026, 9, 18))),
        ("the order I placed in March", (D(2026, 3, 1), D(2026, 3, 31))),
    ],
)
def test_a_question_names_a_calendar_window(question: str, expected: tuple[date, date]) -> None:
    window = find_when(question, NOW, TZ)
    assert window is not None
    tz = ZoneInfo(TZ)
    assert (
        window.since.astimezone(tz).date(),
        (window.until - timedelta(microseconds=1)).astimezone(tz).date(),
    ) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Qual é o CEP de coleta que vocês têm hoje no meu cadastro?",
        "What is the ZIP code you have on file today?",
        "Quero a segunda via da conta",
        "Can you find out where my order is?",
        "I need to set up the router",
        "May I change the delivery address?",
        "O pedido 2025 chegou?",
        "Paguei dez reais a mais",
        "Hi, I sent you an email the other day. What protocol number did you give me?",
        "Oi, mandei um e-mail para vocês esses dias. Qual é o número de protocolo que me passaram?",
        "A quinta parcela veio errada",
    ],
)
def test_words_that_only_look_like_time_words_name_no_window(question: str) -> None:
    assert find_when(question, NOW, TZ) is None
