"""What memory never keeps from an extraction: the answers below are the leaking ones of the 26/09
measurement, on the benchmark's synthetic sessions. Each goes through the real validation; nothing that
carries the document number, and no diagnosis under an ordinary predicate, may come out of it."""

import json
import re
from dataclasses import replace
from typing import Any

import pytest

from niadra.domain.extract.result import ExtractionResult
from niadra.domain.extract.rules import PredicateRule
from niadra.domain.extract.withhold import (
    held_numbers,
    marker_for,
    only_withheld,
    withhold_result,
    withhold_text,
)
from niadra.domain.vocabulary import Sensitivity, Speaker
from niadra.ports.infra import PiiSpan
from tests.unit.extract.helpers import event, material, prompt_for, rules, validate_one


def fact(predicate: str, value: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "op": "add",
        "predicate": predicate,
        "value": value,
        "target": None,
        "evidence": evidence,
        "valid_at": None,
        "certainty": 1.0,
        "contradicts": [],
        "about": None,
        "object": None,
    }


def answer(summary: str, resolution: str | None = None, category: str = "invoice", **parts: Any) -> str:
    episode = {
        "summary": summary,
        "intent": "other",
        "category": category,
        "outcome": "resolved",
        "resolution": resolution,
        "sentiment": 0.0,
    }
    return json.dumps({"episode": episode, **parts})


def run(customer: str, agent: str, raw: str, spans: list[PiiSpan] | None = None) -> ExtractionResult:
    turns = [event(Speaker.CUSTOMER, customer, 1), event(Speaker.AI_AGENT, agent, 2)]
    m = material(turns)
    p = prompt_for(m, spans={f"event:{turns[0].event_id}": spans} if spans else None)
    return validate_one(raw, p, m, rules())


def stored(result: ExtractionResult) -> str:
    """Every text of the result that reconcile writes or the run keeps."""
    return json.dumps(result.to_dict(), ensure_ascii=False)


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


# (customer turn, agent turn, the number, the model's answer) from the runs.
LEAKS = [
    pytest.param(
        "My ID number for the invoice is 87798164.",
        "Thanks, I updated your file.",
        "87798164",
        answer(
            "O cliente informou o número de identificação da fatura.",
            facts=[fact("disputed_invoice", "87798164", ["e1"])],
            values=["code 87798164"],
            agent_claims=[{"predicate": None, "value": "updated your file", "object": None,
                           "contradicts": [], "event": "e2"}],
        ),
        id="fact-value-alone",
    ),
    pytest.param(
        "My ID number for the invoice is 61196801.",
        "Thanks, I updated your file.",
        "61196801",
        answer(
            "Customer provided invoice ID 61196801.",
            "Invoice ID 61196801 was updated.",
            facts=[fact("disputed_invoice", "61196801", ["e1"])],
            values=["code 61196801"],
        ),
        id="summary-invoice-id",
    ),
    pytest.param(
        "The receiver's ID number is 87191204.",
        "Thanks, I updated your file.",
        "87191204",
        answer(
            "O cliente informou o número de identificação do destinatário: 87191204. O agente disse que "
            "atualizou o cadastro.",
            "O agente informou que atualizou o cadastro.",
        ),
        id="summary-receiver",
    ),
    pytest.param(
        "My ID number is 91277628.",
        "Thanks, I updated your file.",
        "91277628",
        answer(
            "O cliente informou o número de identificação 91277628, e o agente disse que atualizou o "
            "cadastro.",
            profile=[{"slot": "preferred_channel", "value": "app", "evidence": ["e1"]}],
            facts=[fact("preferred_name", "ID 91277628", ["e1"])],
        ),
        id="summary-my-id",
    ),
    pytest.param(
        "O número do meu documento é 82661763.",
        "Obrigado, atualizei seu cadastro.",
        "82661763",
        answer(
            "Cliente informou o número do documento 82661763, e o agente afirmou que atualizou o cadastro.",
            "Cadastro atualizado com o documento 82661763.",
            open_items=[{"description": "Conferir o documento 82661763", "owner": "company",
                         "evidence": ["e1"], "due_at": None, "object": None,
                         "expected_operation": None, "about": None}],
        ),
        id="pt-document",
    ),
    pytest.param(
        "Meu documento para a nota é 54726939.",
        "Obrigado, atualizei seu cadastro.",
        "54726939",
        answer(
            "O cliente informou seu documento para a nota.",
            facts=[fact("disputed_invoice", "documento 54726939", ["e1"])],
        ),
        id="pt-document-for-the-invoice",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("customer", "agent", "number", "raw"), LEAKS)
def test_a_document_number_the_model_copied_is_nowhere_in_what_memory_keeps(
    customer: str, agent: str, number: str, raw: str
) -> None:
    result = run(customer, agent, raw)

    assert number not in digits(stored(result))
    assert result.values == ()
    assert all(not only_withheld(f.value) and number not in digits(f.value) for f in result.facts)
    assert result.withheld >= 1 or result.dropped >= 1


def test_a_fact_that_is_only_a_document_number_is_dropped_and_counted() -> None:
    raw = answer(
        "O cliente informou o número de identificação da fatura.",
        facts=[fact("disputed_invoice", "87798164", ["e1"]), fact("preferred_name", "Bia", ["e1"])],
    )
    result = run("My ID number for the invoice is 87798164. Call me Bia.", "Thanks.", raw)

    assert [f.value for f in result.facts] == ["Bia"]
    assert result.dropped == 1


def test_the_summary_keeps_the_sentence_with_a_typed_marker() -> None:
    raw = answer("O cliente informou o número de identificação 91277628.")
    result = run("My ID number is 91277628.", "Thanks, I updated your file.", raw)

    assert result.episode.summary == "O cliente informou o número de identificação [retido:documento]."
    assert result.withheld == 1


def test_a_redacted_document_comes_back_as_a_marker_never_as_the_value() -> None:
    """The PII model redacts a CPF with a check digit before the call; the model copies the placeholder
    into the summary and a fact. Restoring it would write the CPF into memory."""
    customer = "Meu CPF é 529.982.247-25, pode atualizar?"
    raw = answer(
        "Cliente pediu para atualizar o CPF [cpf_1].",
        facts=[fact("disputed_invoice", "CPF [cpf_1]", ["e1"])],
    )
    result = run(customer, "Atualizado.", raw, spans=[PiiSpan(10, 24, "cpf")])

    assert result.episode.summary == "Cliente pediu para atualizar o CPF [retido:documento]."
    assert result.facts == ()
    assert "52998224725" not in digits(stored(result))


def test_an_email_placeholder_still_comes_back() -> None:
    raw = answer("Cliente trocou o e-mail para [email_1].")
    result = run("Meu e-mail novo é ana@example.com", "Feito.", raw, spans=[PiiSpan(18, 33, "email")])

    assert result.episode.summary == "Cliente trocou o e-mail para ana@example.com."


def test_protocol_order_postal_code_and_amount_stay_next_to_a_document() -> None:
    customer = (
        "Meu CPF é 529.982.247-25. O pedido 45778204 veio errado, o protocolo é 2024091500123, "
        "CEP 01310-100, e cobraram R$ 171,61."
    )
    raw = answer(
        "Pedido 45778204 veio errado; protocolo 2024091500123; CEP 01310-100; cobrança de R$ 171,61. "
        "CPF 529.982.247-25 informado.",
        facts=[fact("disputed_invoice", "cobrança de R$ 171,61 no pedido 45778204", ["e1"])],
        values=["order 45778204", "protocol 2024091500123", "postal_code 01310-100", "amount R$ 171,61"],
    )
    result = run(customer, "Anotado, vamos corrigir.", raw)

    summary = result.episode.summary
    for kept in ("45778204", "2024091500123", "01310-100", "171,61"):
        assert kept in summary
    assert "529.982.247-25" not in summary
    assert "[retido:documento]" in summary
    assert [f.value for f in result.facts] == ["cobrança de R$ 171,61 no pedido 45778204"]
    assert {v.raw for v in result.values} == {"45778204", "2024091500123", "01310-100", "R$ 171,61"}


def test_a_card_or_a_password_the_model_wrote_is_masked_like_at_the_ingest_boundary() -> None:
    raw = answer("Cliente pagou com o cartão 4111 1111 1111 1111 e a senha: hunter2.")
    result = run("Paguei com o cartão.", "Ok.", raw)

    assert (
        result.episode.summary
        == "Cliente pagou com o cartão [retido:cartão ****1111] e a senha: [retido:senha]."
    )


def test_a_diagnosis_under_an_ordinary_predicate_is_dropped_when_the_schema_collects_no_health() -> None:
    """gemini on en-092: `order_problem = chronic asthma`. The sample schema has no health predicate, so
    the fact goes, and the conversation's episode is filed under health."""
    raw = answer(
        "Para a apólice de vida: meu diagnóstico é asma crônica.",
        facts=[fact("disputed_invoice", "chronic asthma", ["e1"]), fact("preferred_name", "Bia", ["e1"])],
        profile=[{"slot": "preferred_channel", "value": "asthma", "evidence": ["e1"]}],
    )
    result = run("For the life policy: my diagnosis is chronic asthma.", "Thanks, I updated your file.", raw)

    assert [f.value for f in result.facts] == ["Bia"]
    assert result.profile == ()
    assert result.episode.category == "health"
    assert result.dropped == 2


def test_a_diagnosis_under_an_ordinary_predicate_moves_to_the_schemas_health_predicate() -> None:
    health = PredicateRule("health_condition", "health", sensitivity=Sensitivity.SENSITIVE)
    base = rules()
    with_health = replace(base, predicates=(*base.predicates, health))
    turns = [event(Speaker.CUSTOMER, "Meu diagnóstico é hipertensão arterial.", 1)]
    m = material(turns)
    raw = answer(
        "Cliente informou hipertensão.", facts=[fact("disputed_invoice", "hipertensão arterial", ["e1"])]
    )

    result = validate_one(raw, prompt_for(m), m, with_health)

    [moved] = result.facts
    assert (moved.predicate, moved.category, moved.sensitivity) == (
        "health_condition",
        "health",
        Sensitivity.SENSITIVE,
    )
    assert moved.value == "hipertensão arterial"


def test_an_ordinary_conversation_keeps_its_category_and_facts() -> None:
    raw = answer(
        "Cliente contestou a fatura 88123 de R$ 99,90.",
        facts=[fact("disputed_invoice", "fatura 88123", ["e1"])],
    )
    result = run("Quero contestar a fatura 88123 de R$ 99,90.", "Vamos analisar.", raw)

    assert result.episode.category == "invoice"
    assert result.episode.summary == "Cliente contestou a fatura 88123 de R$ 99,90."
    assert [f.value for f in result.facts] == ["fatura 88123"]
    assert (result.dropped, result.withheld) == (0, 0)


def test_withholding_is_idempotent() -> None:
    held = held_numbers(["My ID number is 91277628."])
    once = withhold_text("ID 91277628. Cartão 4111111111111111. Conta 12345-6. Pedido 555666.", held, "pt")
    twice = withhold_text(once.text, held, "pt")

    assert once.text == (
        "ID [retido:documento]. Cartão [retido:cartão ****1111]. Conta [retido:conta]. Pedido 555666."
    )
    assert (twice.text, twice.withheld) == (once.text, 0)


def test_a_stored_result_is_withheld_the_same_way() -> None:
    """The backfill runs a stored run's result through the same pass, with the session's turns."""
    raw = answer(
        "O cliente informou o número 91277628.", facts=[fact("disputed_invoice", "91277628", ["e1"])]
    )
    before = run("Seu pedido?", "Ok.", raw)
    assert "91277628" in stored(before)

    after = withhold_result(before, rules(), held_numbers(["My ID number is 91277628."]), "pt")

    assert "91277628" not in stored(after)
    assert after.facts == ()
    assert withhold_result(after, rules(), held_numbers(["My ID number is 91277628."]), "pt") == after


@pytest.mark.parametrize(
    ("lang", "kind", "expected"),
    [("pt", "document", "[retido:documento]"), ("en", "account", "[withheld:account]"),
     ("es", "document", "[retenido:documento]"), ("pt", "secret", "[retido:segredo]")],
)  # fmt: skip
def test_markers_are_typed_in_the_spaces_language(lang: str, kind: str, expected: str) -> None:
    assert marker_for(kind, lang) == expected


def test_only_withheld_keeps_a_fact_that_says_something_else() -> None:
    assert only_withheld("CPF do cliente [retido:documento]")
    assert only_withheld("[withheld:document]")
    assert not only_withheld("fatura em atraso, documento [retido:documento]")
    assert not only_withheld("fatura 88123")
