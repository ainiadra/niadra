"""The hours a customer says they may be contacted in (p26): asked for only when a customer turn names a time
of day and a way to be contacted, kept only from the customer's turns, as a window or a lift."""

import json
from typing import Any

import pytest

from niadra.app.extract import rules_for
from niadra.config.models import PredicateSpec
from niadra.domain.extract.result import ExtractionResult
from niadra.domain.vocabulary import Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, sample_config
from tests.unit.extract.helpers import event, material, prompt_for, rules, validate_one

EPISODE = {
    "summary": "Cliente pediu horário de contato",
    "intent": "other",
    "category": "invoice",
    "outcome": "resolved",
    "resolution": None,
    "sentiment": None,
}


def answer(**parts: Any) -> str:
    return json.dumps({"episode": EPISODE, **parts})


def hours(**fields: Any) -> dict[str, Any]:
    return {
        "channel": "voice",
        "not_before": "09:00",
        "not_after": None,
        "days": [],
        "evidence": ["e1"],
    } | fields


def test_the_hours_are_asked_for_only_when_a_customer_names_a_time_and_a_channel() -> None:
    said = prompt_for(material([event(Speaker.CUSTOMER, "Não me liguem antes das 9h, por favor", 1)]))
    assert "- hours:" in said.system
    assert "hours" in said.schema["properties"]
    for turns in (
        [event(Speaker.CUSTOMER, "Oi, tudo bem?", 1)],
        [event(Speaker.CUSTOMER, "Entregar o pedido só de manhã", 1)],
        [event(Speaker.AI_AGENT, "Posso te ligar amanhã às 10h?", 1)],
    ):
        plain = prompt_for(material(turns))
        assert "- hours:" not in plain.system
        assert "hours" not in plain.schema["properties"]


def test_a_customers_hours_are_kept_as_said() -> None:
    m = material([event(Speaker.CUSTOMER, "Só me mandem mensagem à tarde, entre 12h e 18h", 1)])
    result = validate_one(
        answer(hours=[hours(channel="whatsapp", not_before="12:00", not_after="18:00", days=[5, 1])]),
        prompt_for(m),
        m,
        rules(),
    )
    [said] = result.hours
    assert (said.channel, said.not_before, said.not_after, said.days, said.lift) == (
        "whatsapp",
        "12:00",
        "18:00",
        (1, 5),
        False,
    )
    assert said.evidence_event_ids == (m.sessions[0].events[0].event_id,)
    again = ExtractionResult.from_dict(json.loads(json.dumps(result.to_dict())))
    assert again.hours == result.hours


def test_any_channel_and_a_lift_are_kept() -> None:
    m = material([event(Speaker.CUSTOMER, "Pode ligar ou mandar mensagem a qualquer hora agora", 1)])
    p = prompt_for(m)
    [lift] = validate_one(
        answer(hours=[hours(channel="any", not_before=None, lift=True)]), p, m, rules()
    ).hours
    assert (lift.channel, lift.not_before, lift.not_after, lift.lift) == (None, None, None, True)


@pytest.mark.parametrize(
    "entry",
    [
        hours(not_before="9h"),
        hours(not_before="18:00", not_after="12:00"),
        hours(not_before=None),
        hours(channel="pigeon"),
        hours(days=[0]),
        hours(lift=True),
    ],
)
def test_hours_that_make_no_window_are_dropped(entry: dict[str, Any]) -> None:
    m = material([event(Speaker.CUSTOMER, "Não me liguem antes das 9h", 1)])
    assert validate_one(answer(hours=[entry]), prompt_for(m), m, rules()).hours == ()


def test_an_agents_hours_are_never_the_customers() -> None:
    m = material(
        [
            event(Speaker.CUSTOMER, "Não me liguem antes das 9h", 1),
            event(Speaker.AI_AGENT, "Ligo às 8h então", 2),
        ]
    )
    result = validate_one(answer(hours=[hours(evidence=["e2"])]), prompt_for(m), m, rules())
    assert result.hours == ()


def _with_contact_time() -> Any:
    """The sample rules with the starter's contact time predicate, as a space nobody configured has it."""
    config = sample_config()
    predicates = [
        *config.extraction.predicates,
        PredicateSpec(name="preferred_contact_time", category="contact", single_valued=True),
    ]
    extraction = config.extraction.model_copy(update={"predicates": predicates})
    return rules_for(config.model_copy(update={"extraction": extraction}), WHATSAPP_SOURCE)


def test_the_hours_become_the_contact_time_fact_when_the_model_kept_none() -> None:
    m = material([event(Speaker.CUSTOMER, "Não me liguem antes das 9h; WhatsApp só à tarde, 12h às 18h", 1)])
    result = validate_one(
        answer(hours=[hours(), hours(channel="whatsapp", not_before="12:00", not_after="18:00")]),
        prompt_for(m),
        m,
        _with_contact_time(),
    )
    [fact] = result.facts
    assert fact.predicate == "preferred_contact_time"
    assert fact.speaker is Speaker.CUSTOMER
    assert fact.value == (
        "ligação: não antes das 09:00, horário de America/Sao_Paulo; "
        "WhatsApp: só entre 12:00 e 18:00, horário de America/Sao_Paulo"
    )


def test_the_models_own_contact_time_fact_stays_in_the_persons_words() -> None:
    m = material([event(Speaker.CUSTOMER, "Não me liguem antes das 9h", 1)])
    said = {
        "op": "add",
        "predicate": "preferred_contact_time",
        "value": "Não ligar antes das 9h",
        "evidence": ["e1"],
    }
    result = validate_one(answer(hours=[hours()], facts=[said]), prompt_for(m), m, _with_contact_time())
    assert [f.value for f in result.facts] == ["Não ligar antes das 9h"]


def test_a_space_without_a_contact_time_predicate_gets_no_fact() -> None:
    m = material([event(Speaker.CUSTOMER, "Não me liguem antes das 9h", 1)])
    result = validate_one(answer(hours=[hours()]), prompt_for(m), m, rules())
    assert result.facts == ()
    assert len(result.hours) == 1


def test_hours_taken_back_say_any_time() -> None:
    m = material([event(Speaker.CUSTOMER, "Pode ligar a qualquer hora", 1)])
    lifted = hours(not_before=None, not_after=None, lift=True)
    result = validate_one(answer(hours=[lifted]), prompt_for(m), m, _with_contact_time())
    assert [f.value for f in result.facts] == ["ligação: a qualquer hora"]
