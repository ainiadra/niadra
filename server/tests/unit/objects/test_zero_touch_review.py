"""Zero touch reviewed as a security boundary: what an undeclared field may say on every path,
what a type nobody declared may say, what may stop a space's inference, what the evidence keeps and what a
change of the registry needs an approval for."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.config.object_types import ObjectTypeRegistry, escalate
from niadra.domain.compile import CompileInput, Variant, compile_pack
from niadra.domain.compile.candidates import object_text
from niadra.domain.compile.templates import Formatter
from niadra.domain.objects.infer import Shape, empty, observed_types, seen, wordable
from niadra.domain.objects.undeclared import held
from niadra.domain.records import BusinessObject, EventRecord, ObjectKey
from niadra.domain.serve.history import system_event_docs, vector_text
from niadra.domain.vocabulary import EventKind, Speaker, SubjectKind, Verification
from niadra.testing.cell import BILLING_SOURCE
from tests.unit.compile.factories import PHONE, T0, marina, settings, uid

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
CPF = "529.982.247-25"
FMT = Formatter("America/Sao_Paulo", "pt-BR")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("telefone_cliente", 5511912345678),  # a phone a system keeps as a number
        ("cpf", 52998224725),
        ("contato_principal", "5511912345678"),  # E.164 written with no "+"
        ("contato", {"email": "maria@exemplo.com"}),
        ("contatos", ["maria@exemplo.com"]),
        ("cliente", "Maria da Silva"),  # says whose with no word for "name"
        ("titular", "João Souza"),
        ("paciente", "Ana Lima"),
        ("responsible_customer", "Maria da Silva"),  # the customer's side wins over a working role
    ],
)
def test_a_personal_value_is_held_whatever_shape_it_comes_in(name: str, value: Any) -> None:
    assert held(name, value) == "personal"


def test_a_nested_value_that_is_long_or_deep_is_held_as_content() -> None:
    assert held("notes", {"text": "x" * 300}) == "content"
    deep: Any = "x"
    for _ in range(3000):
        deep = {"a": deep}
    assert held("payload", deep) == "content"  # never a recursion error on a read


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("claim_amount", 45000),
        ("days", 15),
        ("protocol", 2026100500012),
        ("case_class", "Procedimento Comum Cível"),
        ("last_status", "Em Andamento"),
        ("responsible_lawyer_name", "Rafael Menezes"),
        ("filing", {"type": "contestacao", "version": 2}),
    ],
)
def test_the_companys_own_values_are_still_served(name: str, value: Any) -> None:
    assert held(name, value) is None


def unknown(**state: Any) -> BusinessObject:
    """An object of a type nobody declared and the inference has not (yet) named."""
    return BusinessObject(uid(0x901), ObjectKey("ticket", "crm", "t-1"), state, T0, BILLING_SOURCE, PHONE)


def test_a_type_nobody_declared_says_no_personal_value_on_the_packs_line() -> None:
    obj = unknown(subject="Troca de plano", cpf_cliente=CPF, telefone=5511912345678)
    assert CPF not in object_text(obj, FMT)
    snapshot = replace(marina(), objects=(obj,))
    pack = compile_pack(CompileInput(snapshot, Variant(verification=Verification.V2), settings()))
    assert "Troca de plano" in pack.text
    assert CPF not in pack.text
    assert "5511912345678" not in pack.text


def test_a_type_nobody_declared_says_no_personal_value_on_a_history_row_or_vector() -> None:
    fields = {"subject": "Troca de plano", "cpf_cliente": CPF}
    event = EventRecord(
        uid(0x902),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "crm",
        Speaker.SYSTEM,
        T0,
        T0,
        (PHONE,),
        canonical_type="ticket.updated",
        fields=fields,
        object_refs=(ObjectKey("ticket", "crm", "t-1"),),
    )
    [doc] = system_event_docs([event], FMT, SubjectKind.PERSON, None, {}, {})
    assert "Troca de plano" in doc.text
    assert CPF not in doc.text
    assert CPF not in vector_text(unknown(**fields), FMT, {}, {})


def shape(n: int, **state: Any) -> Shape:
    return Shape("ticket", f"t{n}", {"state": "open", **state}, NOW - timedelta(days=1), True, "crm")


@pytest.mark.parametrize(
    "values",
    [
        # An instant with no offset compared with the event's aware time raised.
        [{"due_at": "2026-10-09T10:00:00"}],
        # A field whose values tie between two kinds and never were a text raised on `["string"]`.
        [{"x": 1}, {"x": "2026-10-05"}, {"x": 2}, {"x": "2026-10-06"}],
    ],
)
def test_no_value_a_system_sends_stops_the_spaces_inference(values: list[dict[str, Any]]) -> None:
    evidence = empty()
    for n, state in enumerate(values):
        evidence = json.loads(json.dumps(seen(evidence, [shape(n, **state)], {})))
    assert observed_types(evidence, frozenset(), NOW)


def test_the_evidence_keeps_a_digest_of_an_objects_id_never_the_id() -> None:
    by_cpf = Shape("customer", CPF, {"state": "active"}, NOW - timedelta(days=1), True, "crm")
    evidence = seen(empty(), [by_cpf], {})
    assert CPF not in json.dumps(evidence)
    kept_before = {
        "types": {"customer": {**evidence["types"]["customer"], "current": {CPF: ["active", "x"]}}}
    }
    assert CPF not in json.dumps(seen({**empty(), **kept_before}, [], {}))


def test_a_lowercase_value_of_a_field_about_a_person_is_never_sent_to_be_worded() -> None:
    evidence = seen(empty(), [shape(n, login="maria_silva", channel="whatsapp") for n in range(3)], {})
    names, codes = wordable(evidence)
    assert "login" in names
    assert "maria_silva" not in codes
    assert "whatsapp" in codes


COURT = {
    "type": "lawsuit",
    "ownership": "subject",
    "mirror_of": {"system": "court"},
    "fields": {"last_movement": {"type": "string"}, "court": {"type": "string"}},
    "content": {"mode": "full", "scan": "required", "fields": ["last_movement"]},
}


@pytest.mark.parametrize(
    "content",
    [
        None,  # the envelope dropped: the court's text goes on a line as it is
        {"mode": "full", "scan": "rules", "fields": ["last_movement"]},  # the scan relaxed
        {"mode": "full", "scan": "required", "fields": []},  # the field taken out
    ],
)
def test_taking_a_third_partys_words_out_of_their_envelope_needs_the_security_role(content: Any) -> None:
    before = ObjectTypeRegistry.model_validate({"types": [COURT]})
    changed = {k: v for k, v in COURT.items() if k != "content"} | ({"content": content} if content else {})
    after = ObjectTypeRegistry.model_validate({"types": [changed]})
    assert escalate(before, after)
    assert not escalate(after, before)


def test_a_field_held_by_rules_that_changed_since_is_counted_again_and_worded() -> None:
    """juridico-zero, 08/10: the lawyer's name was held before staff names were served, its evidence kept
    `held: 8`, and the field was never worded ("Responsible lawyer name" in a Portuguese space)."""
    lawyer = {"string": 27}
    before = {
        "seq": 9,
        "types": {
            "legal_case": {
                "system": "sistema-processos",
                "objects": 27,
                "owned": 27,
                "fields": {"responsible_lawyer_name": {"kinds": lawyer, "ahead": 0, "held": 8}},
                "states": {},
                "relations": {},
                "current": {},
                "unsafe": 0,
                "limited": 0,
            }
        },
        "limited": {},
        "operations": {},
    }
    names, _ = wordable(before)
    assert "responsible_lawyer_name" not in names
    after = seen(before, [], {})
    assert "responsible_lawyer_name" in wordable(after)[0]
