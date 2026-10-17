"""The fields an object's type holds private (personal data, a sensitive category, access rules) never reach a
text a model reads: the pack's system line, the history's event rows, nor the vector a line is searched by.
One canary per path."""

from dataclasses import replace
from datetime import timedelta

import pytest

from niadra.app.compile import compile_settings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.compile import CompileInput, Variant, compile_pack
from niadra.domain.compile.templates import Formatter
from niadra.domain.records import BusinessObject, EventRecord, ObjectKey
from niadra.domain.serve.history import (
    VECTOR_CHUNK_CHARS,
    pooled,
    system_event_docs,
    vector_chunks,
    vector_text,
)
from niadra.domain.vocabulary import EventKind, Speaker, SubjectKind, Verification
from niadra.testing.cell import BILLING_SOURCE, sample_config
from tests.unit.compile.factories import PHONE, T0, marina, settings, uid

TAX_ID = "123.456.789-09"
DIAGNOSIS = "hipertensão controlada desde 2019"
ADDRESS = "Rua das Acácias, 118, apto 42"
PROPOSAL = {
    "type": "proposal",
    "ownership": "subject",
    "mirror_of": {"system": "crm"},
    "fields": {
        "plan_code": {"type": "string"},
        "client_tax_id": {"type": "string", "pii": True},
        "health_declaration": {"type": "text", "pii": True, "sensitivity": "health"},
        "delivery_address": {
            "type": "string",
            "access": [{"readers": ["agent:voice"], "effect": "deny"}],
        },
    },
    "sources": {"crm": {"kind": "push", "precedence": 1}},
}
STATE = {
    "plan_code": "essencial-200",
    "client_tax_id": TAX_ID,
    "health_declaration": DIAGNOSIS,
    "delivery_address": ADDRESS,
}
SECRETS = (TAX_ID, DIAGNOSIS, ADDRESS)


def private() -> dict[str, frozenset[str]]:
    return ObjectTypeRegistry.model_validate({"types": [PROPOSAL]}).private_fields()


def proposal() -> BusinessObject:
    key = ObjectKey("proposal", "crm", "p-1")
    return BusinessObject(uid(0x701), key, STATE, T0 - timedelta(days=1), BILLING_SOURCE, PHONE)


def test_the_registry_names_every_field_a_text_leaves_out() -> None:
    assert private() == {"proposal": frozenset({"client_tax_id", "health_declaration", "delivery_address"})}
    config = sample_config()
    registry = ObjectTypeRegistry.model_validate({"types": [PROPOSAL]})
    assert compile_settings(config.model_copy(update={"object_types": registry})).private_fields == private()


def test_the_packs_system_line_carries_no_private_field() -> None:
    snapshot = replace(marina(), objects=(proposal(),))
    for level in (Verification.V1, Verification.V2, Verification.V3):
        pack = compile_pack(
            CompileInput(snapshot, Variant(verification=level), settings(private_fields=private()))
        )
        assert "essencial-200" in pack.text
        assert not [s for s in SECRETS if s in pack.text]
        assert not [s for s in SECRETS if s in str(pack.manifest)]


def test_the_historys_event_row_carries_no_private_field() -> None:
    event = EventRecord(
        uid(0x702),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "crm",
        Speaker.SYSTEM,
        T0,
        T0,
        (PHONE,),
        canonical_type="proposal.updated",
        fields=STATE,
        object_refs=(ObjectKey("proposal", "crm", "p-1"),),
    )
    fmt = Formatter("America/Sao_Paulo", "pt-BR")
    [doc] = system_event_docs([event], fmt, SubjectKind.PERSON, None, private())
    assert "essencial-200" in doc.text
    assert not [s for s in SECRETS if s in doc.text]


def test_the_vector_of_an_object_encodes_no_private_field() -> None:
    text = vector_text(proposal(), Formatter("America/Sao_Paulo", "pt-BR"), private())
    assert "essencial-200" in text
    assert not [s for s in SECRETS if s in text]


def test_a_history_row_says_a_new_field_and_holds_one_that_may_be_personal() -> None:
    # A field the type does not declare is said on a history row and in a search vector as on a line,
    # unless it may be personal data or free text: then it waits for a declaration
    # (`domain.objects.undeclared`).
    declared = ObjectTypeRegistry.model_validate({"types": [PROPOSAL]}).declared_names()
    fields = {**STATE, "sent_on": "2026-09-02", "email_cliente": "maria@exemplo.com"}
    event = EventRecord(
        uid(0x703),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "crm",
        Speaker.SYSTEM,
        T0,
        T0,
        (PHONE,),
        canonical_type="proposal.updated",
        fields=fields,
        object_refs=(ObjectKey("proposal", "crm", "p-1"),),
    )
    fmt = Formatter("America/Sao_Paulo", "pt-BR")
    [doc] = system_event_docs([event], fmt, SubjectKind.PERSON, None, private(), declared)
    assert "sent_on: 2026-09-02" in doc.text
    assert "maria@" not in doc.text
    text = vector_text(replace(proposal(), state=fields), fmt, private(), declared)
    assert "Sent on: 02/09" in text
    assert "maria@" not in text
    assert not [s for s in SECRETS if s in text]


def test_a_history_row_says_every_field_it_may_show_declared_first() -> None:
    # A row kept four fields picked by name: a payment's status, fifth alphabetically, was
    # lost from history and search. Every field it may show is said now, the declared ones first.
    declared = ObjectTypeRegistry.model_validate({"types": [PROPOSAL]}).declared_names()
    fields = {
        **STATE,
        "amount": 120.5,
        "currency": "BRL",
        "due_date": "2026-10-20",
        "invoice_id": "F-9001",
        "status": "pago",
    }
    event = EventRecord(
        uid(0x704),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "crm",
        Speaker.SYSTEM,
        T0,
        T0,
        (PHONE,),
        canonical_type="proposal.updated",
        fields=fields,
        object_refs=(ObjectKey("proposal", "crm", "p-1"),),
    )
    fmt = Formatter("America/Sao_Paulo", "pt-BR")
    [doc] = system_event_docs([event], fmt, SubjectKind.PERSON, None, private(), declared)
    assert "status: pago" in doc.text
    assert "invoice_id: F-9001" in doc.text
    named = declared["proposal"]
    said = [part.split(":")[0] for part in doc.text.split(" · ") if ":" in part]
    first_undeclared = next(i for i, k in enumerate(said) if k not in named)
    assert all(k not in named for k in said[first_undeclared:])
    assert not [s for s in SECRETS if s in doc.text]


def test_a_history_row_with_many_fields_keeps_every_one() -> None:
    fields = {f"campo_{i:03d}": "x" * 40 for i in range(80)}
    event = EventRecord(
        uid(0x705),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "crm",
        Speaker.SYSTEM,
        T0,
        T0,
        (PHONE,),
        canonical_type="proposal.updated",
        fields=fields,
        object_refs=(ObjectKey("proposal", "crm", "p-1"),),
    )
    [doc] = system_event_docs([event], Formatter("America/Sao_Paulo", "pt-BR"), SubjectKind.PERSON)
    assert doc.text.count("campo_") == 80


def test_a_long_row_is_encoded_whole_in_pieces_and_pooled() -> None:
    # A row's search vector read its first 2,000 characters. Every piece is encoded now and
    # the row's vector is their mean.
    text = " ".join(f"palavra{i}" for i in range(400))
    pieces = vector_chunks(text)
    assert len(pieces) > 1
    assert all(len(p) <= VECTOR_CHUNK_CHARS for p in pieces)
    assert " ".join(pieces) == text
    assert vector_chunks("curto") == ["curto"]
    assert pooled([[0.6, 0.8]]) == [0.6, 0.8]
    assert pooled([[1.0, 0.0], [0.0, 1.0]]) == pytest.approx([2**-0.5, 2**-0.5])
