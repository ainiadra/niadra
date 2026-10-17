"""The matter the extraction names (17, 6.2) and the language it writes in (6.9): a short English key the
customer's conversations about one problem share, a label in the customer's words, and what validation
keeps of them."""

import json
from dataclasses import replace
from typing import Any

import pytest

from niadra.app.extract import recent_matters
from niadra.domain.extract.matter import matter_key, matter_label, settle
from niadra.domain.extract.prompt import output_schema, system_message
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult
from niadra.domain.extract.rules import PROMPT_VERSION
from niadra.domain.extract.validate import validate
from niadra.domain.vocabulary import Speaker
from tests.unit.compile.factories import episode
from tests.unit.extract.helpers import event, material, prompt_for, rules, validate_one


def answer(**episode: Any) -> str:
    base = {
        "summary": "Cliente relata que marcaram tentativa de entrega sem ninguém vir. O agente registrou a "
        "ocorrência 55721.",
        "intent": "complaint",
        "category": "orders",
        "outcome": "pending",
        "resolution": None,
        "sentiment": -0.4,
    }
    return json.dumps({"episode": {**base, **episode}})


TURNS = [
    event(Speaker.CUSTOMER, "De novo marcaram tentativa de entrega sem ninguém vir.", 1),
    event(Speaker.AI_AGENT, "Sinto muito. Registrei a ocorrência 55721.", 2),
]


def test_the_prompt_asks_for_the_matter_and_shows_the_customers_recent_ones() -> None:
    m = replace(
        material(TURNS),
        recent_matters=(
            ("delivery_failed", "entrega não realizada"),
            ("package_damaged", "caixa danificada"),
        ),
    )
    p = prompt_for(m)
    assert (
        "recent_matters: delivery_failed (entrega não realizada), package_damaged (caixa danificada)"
        in p.user
    )
    matter_schema = output_schema(rules())["properties"]["matters"]["items"]
    assert set(matter_schema["required"]) == {"key", "label", "sessions"}
    system = system_message(rules())
    assert "share one entry" in system
    assert "in the language the customer writes in" in system  # 6.9: the customer's language
    assert "Options offered keep the feature that sets each apart" in system
    assert "servicing a health plan or insurance is not health" in system
    # p11: an item names the operation whose doing fulfils it, by meaning, next to the meanings declared.
    assert "whose doing fulfils the item, chosen by its meaning" in system
    # p12: an agent checking with its own team is no item unless it commits to an answer, and checking is no
    # operation (phase 2: five "vou verificar com o advogado" became pending items, some waiting for
    # `assess_risk`).
    assert "checking, looking into or asking someone about a matter is not doing an operation" in system
    # p22: whatever their role or title, asked or told.
    assert "is no item unless it gives the customer a time for the answer" in system
    # p13: items carry a kind, and instructions about one object come apart from them.
    assert "kind: promise (someone" in system
    # p14: an instruction keeps the reason its turns give, and only that one; a contact preference names the
    # person (the example's "its manager" was copied as "o gerente" in 3 of 11 replays of 05/10/2026).
    assert 'and when the turns say why, "; " and the reason in the turns\' own words' in system
    assert "with who decided it when they say" in system
    assert "never one they do not say" in system
    assert '"<name>: only by WhatsApp, after 10am"' in system
    assert "its manager" not in system
    assert PROMPT_VERSION >= "p14"


def test_a_session_without_recent_matters_says_nothing_of_them() -> None:
    assert "recent_matters" not in prompt_for(material(TURNS)).user


def test_the_key_is_kept_well_formed_and_the_label_as_written() -> None:
    m = material(TURNS)
    result = validate_one(
        answer(matter="Delivery-Failed", matter_label="entrega não realizada"), prompt_for(m), m, rules()
    )
    assert result.episode.matter == "delivery_failed"
    assert result.episode.matter_label == "entrega não realizada"


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("protocol_55721", "entrega"),  # a digit in the key
        ("delivery_failed", "ocorrência 55721"),  # a number in the label
        ("delivery_failed", "[email_1] entrega"),  # a placeholder
        ("one_two_three_four_five", "entrega"),  # not a short key
        ("service_delayed", "guincho atrasado"),  # no thing named: a late tow and a late repair would meet
    ],
)
def test_a_key_or_label_that_could_carry_an_identifier_is_dropped(key: str, label: str) -> None:
    m = material(TURNS)
    result = validate_one(answer(matter=key, matter_label=label), prompt_for(m), m, rules())
    if matter_key(key) is None:
        assert (result.episode.matter, result.episode.matter_label) == (None, None)
    else:
        assert result.episode.matter == "delivery_failed"
        assert result.episode.matter_label is None


def test_a_recent_key_spelled_the_other_way_is_reused_with_its_label() -> None:
    m = replace(material(TURNS), recent_matters=(("delivery_failed", "entrega não realizada"),))
    result = validate_one(answer(matter="failed_delivery", matter_label=None), prompt_for(m), m, rules())
    assert result.episode.matter == "delivery_failed"
    assert result.episode.matter_label == "entrega não realizada"
    assert settle("internet_drops", None, (("internet_drop", "internet caindo"),)) == (
        "internet_drop",
        "internet caindo",
    )
    assert settle("delivery_late", "atraso", (("delivery_failed", "falha"),)) == ("delivery_late", "atraso")


def test_no_matter_is_a_null_and_a_label_alone_is_nothing() -> None:
    m = material(TURNS)
    result = validate_one(answer(matter=None, matter_label="entrega"), prompt_for(m), m, rules())
    assert (result.episode.matter, result.episode.matter_label) == (None, None)
    assert matter_label("  entrega   não   realizada ") == "entrega não realizada"


def test_a_conversation_about_sensitive_content_keeps_no_matter() -> None:
    turns = [
        event(
            Speaker.CUSTOMER, "Fiz o exame de HIV e o resultado deu positivo, preciso remarcar a consulta.", 1
        ),
        event(Speaker.AI_AGENT, "Remarquei a consulta para amanhã.", 2),
    ]
    m = material(turns)
    result = validate_one(
        answer(
            summary="Cliente pediu para remarcar a consulta.", matter="hiv_test", matter_label="exame de HIV"
        ),
        prompt_for(m),
        m,
        rules(),
    )
    assert (result.episode.matter, result.episode.matter_label) == (None, None)


def test_runs_stored_before_prompt_p7_load_without_a_matter() -> None:
    stored = ExtractionResult(EpisodeDraft("s", "complaint", "orders", "pending")).to_dict()
    del stored["episode"]["matter"], stored["episode"]["matter_label"]
    loaded = ExtractionResult.from_dict(stored)
    assert loaded.episode.matter is None


def test_the_customers_recent_matters_are_the_other_conversations_newest_first_each_once() -> None:
    newest = replace(
        episode(1, "orders", days_ago=2), matter="delivery_failed", matter_label="entrega não feita"
    )
    older = replace(
        episode(2, "orders", days_ago=20), matter="delivery_failed", matter_label="entrega falhou"
    )
    other = replace(
        episode(3, "orders", days_ago=30), matter="package_damaged", matter_label="caixa danificada"
    )
    unkeyed = episode(4, "orders", days_ago=1)
    # The batch's own sessions are left out before (`ExtractService._material`).
    assert recent_matters([older, other, newest, unkeyed]) == (
        ("delivery_failed", "entrega não feita"),
        ("package_damaged", "caixa danificada"),
    )


def test_sessions_of_a_batch_about_the_same_problem_share_one_key() -> None:
    # The model's matters of one batch settle like a recent one's: the same words are the same key.
    first = material([event(Speaker.CUSTOMER, "Faltou um item na entrega", 1)])
    second = material([event(Speaker.CUSTOMER, "De novo faltou item", 40)])
    batch = replace(first, sessions=(*first.sessions, *second.sessions))
    p = prompt_for(batch)
    raw = {
        "matters": [
            {"key": "delivery_item_missing", "label": "item faltando", "sessions": ["s1"]},
            {"key": "item_missing_delivery", "label": "item faltou", "sessions": ["s2"]},
        ],
        "sessions": [
            {"id": alias, "summary": "x", "intent": "complaint", "category": "invoice", "outcome": "pending",
             "resolution": None, "sentiment": None}
            for alias in ("s1", "s2")
        ],
    }  # fmt: skip
    results = validate(json.dumps(raw), p, batch, rules())
    assert [r.episode.matter for r in results.values()] == ["delivery_item_missing"] * 2
