"""Object resolution by rule, item kinds and instructions in the extraction."""

import json
import re
from dataclasses import replace
from datetime import time, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.prompt import KnownObject, build_prompt, short_summary
from niadra.domain.extract.rules import OBSERVATION, PredicateRule
from niadra.domain.records import LinkRecord, ObjectKey
from niadra.domain.vocabulary import OpenItemKind, Speaker
from tests.unit.extract.helpers import HANDLE, T0, event, material, rules, validate_one

CNJ = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"
NUMBER = "0001234-56.2026.8.26.0100"
CASE = ObjectKey("lawsuit", "court", "c-1")
OTHER = ObjectKey("lawsuit", "court", "c-2")
ORDER = ObjectKey("order", "shop", "o-1")
EPISODE = {
    "summary": "Advogado deu instruções sobre o processo",
    "intent": "other",
    "category": "other",
    "outcome": "resolved",
    "resolution": None,
    "sentiment": None,
}


def legal_rules() -> Any:
    found = rules()
    observation = PredicateRule(OBSERVATION, "object")
    return replace(
        found,
        predicates=(*found.predicates, observation),
        key_patterns=(("lawsuit", "case_number", re.compile(CNJ)),),
    )


def known(key: ObjectKey, *, number: str | None = None, open: bool = True, days: int = 3) -> KnownObject:
    keys = (("case_number", number),) if number else ()
    return KnownObject(key, T0 - timedelta(days=days), open, keys)


def objects_line(user: str) -> list[str]:
    lines = user.splitlines()
    return lines[lines.index("objects:") + 1 :] if "objects:" in lines else []


def test_a_natural_key_in_the_text_names_the_customers_own_object_and_never_shows_the_key_twice() -> None:
    turns = [event(Speaker.CUSTOMER, f"No processo {NUMBER}, não propor acordo", 1)]
    m = replace(material(turns), objects=(known(CASE, number=NUMBER), known(OTHER, number="9")))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    [line] = [x for x in objects_line(p.user) if "c-1" in x]
    assert line == "[o1] lawsuit court c-1 (case_number written in e1)"
    assert NUMBER not in "\n".join(objects_line(p.user))
    assert not p.aliases.unresolved


def test_a_natural_key_of_no_object_the_customer_is_tied_to_is_counted_and_never_looked_up() -> None:
    turns = [event(Speaker.CUSTOMER, f"E o processo {NUMBER}?", 1)]
    m = replace(material(turns), objects=(known(CASE, number="0009999-00.2026.8.26.0100"),))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    assert p.aliases.unresolved == {"lawsuit": 1}
    assert all("c-1" not in line or "only open" in line for line in objects_line(p.user))


def test_the_only_open_object_of_a_type_is_offered_and_two_are_not() -> None:
    turns = [event(Speaker.CUSTOMER, "Sobre o pedido, entregar só de manhã", 1)]
    m = replace(material(turns), objects=(known(ORDER), known(CASE), known(OTHER)))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    shown = "\n".join(objects_line(p.user))
    assert "order shop o-1 (the only open order)" in shown
    assert "lawsuit" not in shown


def test_the_one_object_of_a_type_dated_on_the_day_the_turn_names_is_offered() -> None:
    turns = [event(Speaker.CUSTOMER, "O pedido de ontem chegou amassado", 1)]
    yesterday = KnownObject(ORDER, T0 - timedelta(days=1), True)
    older = KnownObject(ObjectKey("order", "shop", "o-0"), T0 - timedelta(days=9), True)
    m = replace(material(turns), objects=(yesterday, older))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    shown = "\n".join(objects_line(p.user))
    assert "order shop o-1 (order of" in shown
    assert "o-0" not in shown


def test_the_schema_asks_for_an_items_kind_and_for_instructions() -> None:
    turns = [event(Speaker.CUSTOMER, "Sobre o pedido, entregar só de manhã", 1)]
    p = build_prompt(
        replace(material(turns), objects=(known(ORDER),)), legal_rules(), flagged=frozenset(), spans={}
    )
    item = p.schema["properties"]["items"]["items"]["properties"]
    assert item["kind"]["enum"] == ["promise", "dispute", "request"]
    instruction = p.schema["properties"]["instructions"]["items"]["properties"]
    assert instruction["strength"]["enum"] == ["must", "must_not"]
    assert set(instruction) == {"text", "strength", "attr", "only_with", "contact", "refs"}
    assert "never without an object or a link id" in p.system


def test_a_batch_with_no_object_and_no_link_is_offered_no_instructions_and_items_keep_their_kind() -> None:
    p = build_prompt(
        material([event(Speaker.CUSTOMER, "oi", 1)]), legal_rules(), flagged=frozenset(), spans={}
    )
    assert "instructions" not in p.schema["properties"]
    assert "- instructions:" not in p.system
    assert "An object the turns did not tag" not in p.system
    assert p.schema["properties"]["items"]["items"]["properties"]["kind"]["enum"] == [
        "promise",
        "dispute",
        "request",
    ]


def test_a_link_alone_offers_the_instructions_without_the_object_rule() -> None:
    link = LinkRecord(uuid7(), HANDLE, uuid7(), "client_contact", False, T0 - timedelta(days=5), None)
    p = build_prompt(
        replace(material([event(Speaker.CUSTOMER, "oi", 1)]), links=(link,)),
        legal_rules(),
        flagged=frozenset(),
        spans={},
    )
    assert "instructions" in p.schema["properties"]
    assert "- instructions:" in p.system
    assert "An object the turns did not tag" not in p.system


def answer(**parts: Any) -> str:
    return json.dumps({"episode": EPISODE, **parts})


def case_material(*texts: tuple[Speaker, str]) -> Any:
    turns = [event(speaker, text, n) for n, (speaker, text) in enumerate(texts, start=1)]
    tagged = [replace(e, object_refs=(CASE,)) for e in turns]
    return material(tagged)


def test_an_instruction_about_the_object_is_kept_and_one_without_an_object_is_counted_apart() -> None:
    m = case_material((Speaker.CUSTOMER, "Neste processo, não propor acordo; e me ligue amanhã"))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        instructions=[
            {
                "text": "não propor acordo",
                "strength": "must_not",
                "attr": None,
                "evidence": ["e1"],
                "object": "o1",
            },
            {"text": "ligar amanhã", "strength": "must", "attr": None, "evidence": ["e1"]},
        ]
    )
    result = validate_one(raw, p, m, legal_rules())
    [kept] = result.instructions
    assert kept.object == CASE
    assert kept.strength == "must_not"
    assert result.instruction_unscoped == 1


def test_an_instruction_is_only_the_persons_and_its_field_is_one_of_the_objects_type() -> None:
    m = case_material(
        (Speaker.AI_AGENT, "Vou propor um acordo"),
        (Speaker.CUSTOMER, "Não, não proponha acordo neste processo"),
    )
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        instructions=[
            {"text": "propor acordo", "strength": "must", "attr": None, "evidence": ["e1"], "object": "o1"},
            {
                "text": "não propor acordo",
                "strength": "must_not",
                "attr": "lawsuit.settlement",
                "evidence": ["e2"],
                "object": "o1",
            },
            {
                "text": "x",
                "strength": "must_not",
                "attr": "order.settlement",
                "evidence": ["e2"],
                "object": "o1",
            },
        ]
    )
    result = validate_one(raw, p, m, legal_rules())
    assert [(i.text, i.attr) for i in result.instructions] == [
        ("não propor acordo", "lawsuit.settlement"),
        ("x", None),
    ]


def test_an_items_kind_is_kept_and_an_unknown_one_reads_as_a_promise() -> None:
    m = case_material((Speaker.CUSTOMER, "Contesto a cobrança de 300 e quero a segunda via"))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        open_items=[
            {"description": "Cobrança contestada", "kind": "dispute", "owner": "company", "evidence": ["e1"]},
            {"description": "Enviar segunda via", "kind": "other", "owner": "company", "evidence": ["e1"]},
        ]
    )
    result = validate_one(raw, p, m, legal_rules())
    assert [i.kind for i in result.open_items] == [OpenItemKind.DISPUTE, OpenItemKind.PROMISE]


def test_an_observation_needs_its_object() -> None:
    turns = [event(Speaker.CUSTOMER, f"No processo {NUMBER}, as chaves foram entregues em 15/09", 1)]
    m = replace(material(turns), objects=(known(CASE, number=NUMBER), known(OTHER)))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        facts=[
            {"op": "add", "predicate": OBSERVATION, "value": "chaves entregues em 15/09", "evidence": ["e1"],
             "object": "o1"},
            {"op": "add", "predicate": OBSERVATION, "value": "sem objeto", "evidence": ["e1"]},
        ]
    )  # fmt: skip
    result = validate_one(raw, p, m, legal_rules())
    assert [f.value for f in result.facts] == ["chaves entregues em 15/09"]


def test_what_is_said_of_an_object_without_its_id_goes_to_the_object_its_turns_are_tagged_with() -> None:
    """The model sometimes leaves the object out of refs; the company's system tagged the turn with it, the
    first rule of resolution, so the said deadline stays the case's instead of becoming the person's."""
    said = PredicateRule("said_due_date", "object", observes="due_date")
    found = replace(legal_rules(), predicates=(*legal_rules().predicates, said))
    m = case_material((Speaker.CUSTOMER, "O prazo deste processo é 02/10/2026"))
    p = build_prompt(m, found, flagged=frozenset(), spans={})
    raw = answer(
        facts=[
            {"op": "add", "predicate": "said_due_date", "value": "02/10/2026", "evidence": ["e1"]},
            {"op": "add", "predicate": OBSERVATION, "value": "prazo curto", "evidence": ["e1"]},
        ]
    )
    result = validate_one(raw, p, m, found)
    assert [(f.predicate, f.object_ref) for f in result.facts] == [
        ("said_due_date", CASE),
        (OBSERVATION, CASE),
    ]
    # Untagged, two objects offered and none in refs: it is no one's, never the person's.
    turns = [event(Speaker.CUSTOMER, "O prazo é 02/10/2026", 1)]
    loose = replace(material(turns), objects=(known(CASE), known(OTHER)))
    p = build_prompt(loose, found, flagged=frozenset(), spans={})
    raw = answer(
        facts=[{"op": "add", "predicate": "said_due_date", "value": "02/10/2026", "evidence": ["e1"]}]
    )
    assert validate_one(raw, p, loose, found).facts == ()


def test_a_fields_value_an_agent_gives_is_never_kept_as_the_persons_word() -> None:
    """What someone said a field of an object is stands beside the record as the person's word: the
    assistant giving the deadline in a staff chat is a claim, and the model sometimes writes it as the fact
    too (17 of 20 replays with p19's wording); kept, the lawyer's said deadline would be the assistant's."""
    said = PredicateRule("said_due_date", "object", observes="due_date")
    found = replace(legal_rules(), predicates=(*legal_rules().predicates, said))
    m = case_material(
        (Speaker.CUSTOMER, "Qual o prazo deste processo?"),
        (Speaker.AI_AGENT, "O prazo deste processo é 02/10/2026."),
        (Speaker.CUSTOMER, "Para mim o prazo é 03/10/2026."),
    )
    p = build_prompt(m, found, flagged=frozenset(), spans={})
    raw = answer(
        facts=[
            {"op": "add", "predicate": "said_due_date", "value": "02/10/2026", "evidence": ["e2"],
             "object": "o1"},
            {"op": "add", "predicate": "said_due_date", "value": "03/10/2026", "evidence": ["e3"],
             "object": "o1"},
        ]
    )  # fmt: skip
    assert [f.value for f in validate_one(raw, p, m, found).facts] == ["03/10/2026"]


def test_an_organizations_fact_said_by_its_contact_is_the_organizations() -> None:
    org = HANDLE.__class__(int=99)
    link = LinkRecord(HANDLE.__class__(int=5), HANDLE, org, "finance", True, T0 - timedelta(days=9), None)
    turns = [event(Speaker.CUSTOMER, "O e-mail do financeiro é financeiro@example.com", 1)]
    m = material(turns, links=[link])
    found = legal_rules()
    business = PredicateRule("business_email", "contact", organization=True)
    with_org = replace(found, predicates=(*found.predicates, business))
    p = build_prompt(m, with_org, flagged=frozenset(), spans={})
    assert "- business_email: contact, organization" in p.system
    raw = answer(
        facts=[
            {
                "op": "add",
                "predicate": "business_email",
                "value": "financeiro@example.com",
                "evidence": ["e1"],
            }
        ]
    )
    [fact] = validate_one(raw, p, m, with_org).facts
    assert fact.about_handle_id == org
    alone = material(turns)
    p = build_prompt(alone, with_org, flagged=frozenset(), spans={})
    assert not validate_one(raw, p, alone, with_org).facts


def test_an_instruction_about_no_object_is_about_the_organization_of_the_link() -> None:
    org = HANDLE.__class__(int=99)
    link = LinkRecord(HANDLE.__class__(int=5), HANDLE, org, "lawyer", True, T0 - timedelta(days=9), None)
    turns = [event(Speaker.CUSTOMER, "Com este cliente, não falar de valores com a esposa", 1)]
    m = material(turns, links=[link])
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(instructions=[{"text": "não falar de valores com a esposa", "strength": "must_not",
                                "attr": "lawsuit.value", "evidence": ["e1"]}])  # fmt: skip
    [told] = validate_one(raw, p, m, legal_rules()).instructions
    assert (told.object, told.about_handle_id, told.attr) == (None, org, None)


def test_an_instruction_is_about_an_object_only_as_its_own_turns_name_it() -> None:
    """Round 9 (juridico-sandbox, 06/10/2026): "não falar de valores dos processos com Clara", said in a note
    no case was tagged on, was batched with the lawyer's chat on one case, cited that case and was stored on
    it alone; Clara got the amount of the client's other case. An object holds only as the instruction's own
    turns name it (tagged, or writing its key); otherwise the instruction is the organization's."""
    org = HANDLE.__class__(int=99)
    link = LinkRecord(HANDLE.__class__(int=5), HANDLE, org, "lawyer", True, T0 - timedelta(days=9), None)
    turns = [
        replace(
            event(Speaker.CUSTOMER, "Neste processo, qual o prazo da contestação?", 1), object_refs=(CASE,)
        ),
        event(Speaker.CUSTOMER, "Anote sobre o cliente: não falar de valores dos processos com a Clara", 2),
    ]
    m = replace(material(turns, links=[link]), objects=(known(CASE),))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        instructions=[
            {"text": "Não falar de valores dos processos com Clara", "strength": "must_not", "attr": None,
             "evidence": ["e2"], "object": "o1", "only_with": "Clara"},
            {"text": "Neste processo, não propor acordo", "strength": "must_not", "attr": None,
             "evidence": ["e1"], "object": "o1"},
        ]
    )  # fmt: skip
    clara, settle = validate_one(raw, p, m, legal_rules()).instructions
    assert (clara.object, clara.about_handle_id) == (None, org)
    assert (settle.object, settle.about_handle_id) == (CASE, None)
    # Writing the case's number names it as a tag does.
    written = [event(Speaker.CUSTOMER, f"No processo {NUMBER}, não falar de valores com a Clara", 1)]
    m = replace(material(written, links=[link]), objects=(known(CASE, number=NUMBER),))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    told = {
        "text": "Não falar de valores com Clara",
        "strength": "must_not",
        "attr": None,
        "evidence": ["e1"],
    }
    raw = answer(instructions=[told | {"object": "o1"}])
    [named] = validate_one(raw, p, m, legal_rules()).instructions
    assert named.object == CASE
    assert "cites the link, even in a conversation about one object" in p.system


def test_an_instruction_names_the_one_person_it_holds_with_only_as_the_turns_write_them() -> None:
    """ "Não falar de valores com a Clara" holds with Clara, not with the client who talks to the attendant:
    the name is kept when the turns write it, and a name the model made up names no one."""
    org = HANDLE.__class__(int=99)
    link = LinkRecord(HANDLE.__class__(int=5), HANDLE, org, "lawyer", True, T0 - timedelta(days=9), None)
    turns = [event(Speaker.CUSTOMER, "Com este cliente, não falar de valores com a esposa dele, a Clara", 1)]
    m = material(turns, links=[link])
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    told = {
        "text": "não falar de valores com a esposa",
        "strength": "must_not",
        "attr": None,
        "evidence": ["e1"],
    }
    [kept] = validate_one(
        answer(instructions=[told | {"only_with": "Clara"}]), p, m, legal_rules()
    ).instructions
    assert kept.only_with == "Clara"
    [made_up] = validate_one(
        answer(instructions=[told | {"only_with": "Mariana"}]), p, m, legal_rules()
    ).instructions
    assert made_up.only_with is None
    assert "only_with: when it holds with one person only" in p.system


def test_a_predicate_that_observes_a_field_tells_the_model_which() -> None:
    """The model reads the field a predicate observes next to its name, so a turn giving that field's value
    for an object (a deadline the lawyer heard) goes to it, never to the object's recorded state."""
    found = legal_rules()
    said = PredicateRule("said_due_date", "object", observes="due_date")
    p = build_prompt(
        material([]), replace(found, predicates=(*found.predicates, said)), flagged=frozenset(), spans={}
    )
    assert "- said_due_date: object, observes due_date" in p.system
    assert "A predicate flagged observes <field> holds" in p.system
    # Batched with another session, the staff's stated deadline stayed in the summary only (p19).
    assert "customer or staff turn gives for that field" in p.system
    assert "even when the summary keeps it" in p.system


def test_an_items_object_says_whether_its_turns_name_it_or_only_the_model_cited_it() -> None:
    """An attendant's untagged turn names no case: an object the batch offered for another, tagged event and
    that the model cites for it is the model's alone (`grounded` False), and memory compares the item with
    the promises on the subject's other objects too (juridico-sandbox, 06/10/2026). A turn tagged with the
    object, or writing its key, names it."""
    turns = [
        event(Speaker.CUSTOMER, "O advogado prometeu mandar algum comprovante?", 1),
        replace(event(Speaker.AI_AGENT, "Vou verificar a audiência.", 2), object_refs=(OTHER,)),
    ]
    m = replace(material(turns), objects=(known(CASE), known(OTHER)))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    [cited] = [alias for alias, key in p.aliases.objects.items() if key == OTHER]
    raw = answer(
        open_items=[
            {"description": "Enviar comprovante ao financeiro", "kind": "promise", "owner": "company",
             "evidence": ["e1"], "object": cited},
            {"description": "Confirmar a audiência", "kind": "promise", "owner": "company",
             "evidence": ["e2"], "object": cited},
        ]
    )  # fmt: skip
    result = validate_one(raw, p, m, legal_rules())
    assert [(i.object, i.grounded) for i in result.open_items] == [(OTHER, False), (OTHER, True)]


def test_a_promise_without_its_object_goes_to_the_one_its_turns_write_the_key_of() -> None:
    """juridico-sandbox, 05/10/2026: the client names the case, the attendant promises a lawyer's call about
    it, and the model leaves the case out of refs. The turn the promise rests on writes the case's number
    (rule 2 of resolution), so the promise is the case's; one resting only on turns naming nothing stays
    loose."""
    turns = [
        event(Speaker.CUSTOMER, f"Preciso falar da contestação do processo {NUMBER}. Podem ligar amanhã?", 1),
        event(Speaker.AI_AGENT, "Um advogado vai te ligar amanhã de manhã.", 2),
    ]
    m = replace(material(turns), objects=(known(CASE, number=NUMBER), known(OTHER, number="9")))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(
        open_items=[
            {"description": "Advogado ligará amanhã de manhã", "kind": "promise", "owner": "company",
             "evidence": ["e1", "e2"]},
            {"description": "Retornar ao cliente", "kind": "promise", "owner": "company", "evidence": ["e2"]},
        ]
    )  # fmt: skip
    result = validate_one(raw, p, m, legal_rules())
    assert [(i.description, i.object) for i in result.open_items] == [
        ("Advogado ligará amanhã de manhã", CASE),
        ("Retornar ao cliente", None),
    ]
    # Two objects named by the turns: none is picked.
    both = [event(Speaker.CUSTOMER, f"Processos {NUMBER} e 0009999-56.2026.8.26.0100: me liguem", 1)]
    two = replace(
        material(both),
        objects=(known(CASE, number=NUMBER), known(OTHER, number="0009999-56.2026.8.26.0100")),
    )
    p = build_prompt(two, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(open_items=[{"description": "Ligar ao cliente", "kind": "promise", "owner": "company",
                              "evidence": ["e1"]}])  # fmt: skip
    [loose] = validate_one(raw, p, two, legal_rules()).open_items
    assert loose.object is None


def test_the_prompt_asks_for_one_item_per_commitment_and_for_contact_preferences_as_instructions() -> None:
    """Measured on the production prompts of juridico-sandbox (05/10/2026): "vou comunicar a sentença ao
    cliente hoje e enviar a cópia amanhã" gave both items in 1 of 5 runs before and 10 of 10 after; "prefere
    WhatsApp e só depois das 10h", said by a lawyer of a client's contact, became an instruction about the
    client in 0 of 5 runs before and 9 of 10 after. The contact preference is an instruction about the
    organization of a link, so the batch has one (p24 offers no instructions to a batch with nothing to
    cite)."""
    link = LinkRecord(uuid7(), HANDLE, uuid7(), "client_contact", False, T0 - timedelta(days=5), None)
    p = build_prompt(replace(material([]), links=(link,)), legal_rules(), flagged=frozenset(), spans={})
    assert "a turn that commits to several" in p.system
    assert "is one item for each, with its own due and operation" in p.system
    assert "how one of its people wants to be contacted" in p.system


def test_a_type_matches_a_declared_field_a_conversation_writes_not_only_its_natural_key() -> None:
    """A case keyed by the firm's own id is named in a conversation by its court number, a field."""
    declared = {
        "type": "lawsuit",
        "ownership": "subject",
        "mirror_of": {"system": "court"},
        "key": {"natural": ["case_id"], "match": {"case_number": CNJ}},
        "fields": {"case_id": {"type": "string"}, "case_number": {"type": "string"}},
    }
    registry = ObjectTypeRegistry.model_validate({"types": [declared]})
    [(type_, name, pattern)] = registry.key_patterns()
    assert (type_, name) == ("lawsuit", "case_number")
    assert pattern.search(f"processo {NUMBER}")
    undeclared = {**declared, "key": {"natural": ["case_id"], "match": {"court_number": CNJ}}}
    with pytest.raises(ValidationError, match="neither a natural key path nor a field"):
        ObjectTypeRegistry.model_validate({"types": [undeclared]})


def test_an_agent_that_declines_to_note_an_instruction_cancels_nothing() -> None:
    """juridico-sandbox, 06/10/2026: the lawyer wrote to the firm's assistant "Anote sobre o cliente: ... se a
    esposa dele, a Sra. Clara, escrever, não falar de valores", the assistant answered "Não consigo registrar
    aqui ... restrições sobre terceiros", and the production prompt replayed on that session gave the account
    instruction in 1 of 4 runs; with this rule, both instructions (the contact's hours and Clara's) in 6 of
    6, and none in 6 runs of a session of the same lawyer that gave none."""
    p = build_prompt(material([]), legal_rules(), flagged=frozenset(), spans={})
    assert "An instruction or contact preference a person states stands as they said it" in p.system
    assert "cannot or does not record it cancels nothing, and each one is still written" in p.system


def test_a_promise_due_on_a_day_written_beside_a_relative_one_keeps_the_written_day() -> None:
    """juridico-sandbox, 06/10/2026: "enviar a cópia amanhã, 06/10", written on 06/10, left the copy of the
    decision undated. The written date is the due; a turn writing two such days, or none, gives none."""
    said = event(
        Speaker.CUSTOMER, "Vou avisar o cliente hoje e enviar a cópia da decisão amanhã, 24/09/2026.", 1
    )
    m = material([said])
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    assert "a turn that writes a relative day and a date that disagree" in p.system
    raw = answer(
        open_items=[
            {"description": "Avisar o cliente", "kind": "promise", "owner": "company", "evidence": ["e1"],
             "due_at": "2026-09-22T23:59:00-03:00"},
            {"description": "Enviar a cópia da decisão", "kind": "promise", "owner": "company",
             "evidence": ["e1"]},
        ]
    )  # fmt: skip
    told, copy = validate_one(raw, p, m, legal_rules()).open_items
    assert told.due_at is not None
    assert told.due_at.isoformat() == "2026-09-22T23:59:00-03:00"
    assert copy.due_at is not None
    assert copy.due_at.date().isoformat() == "2026-09-24"
    # A day, as the model's own dates are: never "até 24/09/2026 23:59" (round 8).
    assert copy.due_at.time() == time()
    two = event(Speaker.CUSTOMER, "Hoje, 22/09, aviso o cliente; amanhã, 23/09, envio a cópia.", 1)
    m = material([two])
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    raw = answer(open_items=[{"description": "Enviar a cópia", "kind": "promise", "owner": "company",
                              "evidence": ["e1"]}])  # fmt: skip
    [undated] = validate_one(raw, p, m, legal_rules()).open_items
    assert undated.due_at is None


def test_a_day_only_an_agents_unbacked_date_gives_never_becomes_an_items_due() -> None:
    """juridico-zero, 07/10/2026: asked until when, the client's attendant said "até 27/10", a day no memory
    held (the lawyer had asked for 06/10); the SDK's check of that turn found the date unbacked, and the item
    was opened due on the 27th. The item stays, undated; a day the turn's check backs, or that the customer
    wrote, or a relative day, stays the due."""
    unbacked = {"backing": {"checked": 1, "unbacked_values": [{"kind": "date"}]}}
    asked = event(Speaker.CUSTOMER, "Até quando a gente manda os extratos?", 1)
    told = replace(
        event(Speaker.AI_AGENT, "Vocês precisam enviar os extratos do sistema de vendas até 27/09.", 2),
        content_meta=unbacked,
    )
    item = {"description": "Enviar extratos do sistema de vendas", "kind": "request", "owner": "customer",
            "evidence": ["e1", "e2"], "due_at": "2026-09-27T00:00:00-03:00"}  # fmt: skip

    def due(*turns: Any) -> Any:
        m = material(list(turns))
        p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
        [said] = validate_one(answer(open_items=[item]), p, m, legal_rules()).open_items
        return said.due_at

    assert due(asked, told) is None
    # The check backed it: the day stays.
    backed = {"backing": {"checked": 1, "unbacked_values": []}}
    assert due(asked, replace(told, content_meta=backed)) is not None
    # The customer wrote the day: it stays.
    wrote = event(Speaker.CUSTOMER, "Combinado, até 27/09 eu mando os extratos.", 1)
    assert due(wrote, told) is not None
    # An unbacked date of the turn that is not the item's day does not void it.
    other = replace(told, text="Os extratos até 27/09; a audiência foi em 15/09.")
    assert due(asked, other) is None
    elsewhere = replace(told, text="Vocês precisam enviar os extratos; a audiência foi em 15/09.")
    assert due(asked, elsewhere) is not None
    # The agent's own new commitment, a relative day the check never reads as a date, keeps its day.
    call = {"description": "Advogado liga para o cliente", "kind": "promise", "owner": "company",
            "evidence": ["e2"], "due_at": "2026-09-23T00:00:00-03:00"}  # fmt: skip
    tomorrow = replace(event(Speaker.AI_AGENT, "O advogado liga para você amanhã.", 2), content_meta=unbacked)
    m = material([asked, tomorrow])
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    [promised] = validate_one(answer(open_items=[call]), p, m, legal_rules()).open_items
    assert promised.due_at is not None


def test_a_day_the_customer_facing_agent_alone_wrote_is_marked_for_memory_to_check() -> None:
    """juridico-zero, 07/10/2026: the attendant's "até 27/10" was backed by the block (the case's deadline to
    contest), so the SDK's check found nothing; the item is marked so memory keeps the day only when an item
    is due on it (`app.memory`). A day the customer wrote, or a staff source's item, is not marked."""
    backed = {"backing": {"checked": 1, "unbacked_values": []}}
    asked = event(Speaker.CUSTOMER, "Até quando a gente manda os extratos?", 1)
    told = replace(
        event(Speaker.AI_AGENT, "Vocês precisam enviar os extratos do sistema de vendas até 27/09.", 2),
        content_meta=backed,
    )
    item = {"description": "Enviar extratos do sistema de vendas", "kind": "request", "owner": "customer",
            "evidence": ["e1", "e2"], "due_at": "2026-09-27T00:00:00-03:00"}  # fmt: skip

    def said(*turns: Any, found: Any = None) -> Any:
        m = material(list(turns))
        r = found or legal_rules()
        p = build_prompt(m, r, flagged=frozenset(), spans={})
        [one] = validate_one(answer(open_items=[item]), p, m, r).open_items
        return one

    marked = said(asked, told)
    assert marked.due_at is not None
    assert marked.relayed
    assert marked.agent_day
    wrote = event(Speaker.CUSTOMER, "Combinado, até 27/09 eu mando os extratos.", 1)
    assert not said(wrote, told).agent_day
    staff = said(asked, told, found=replace(legal_rules(), staff=True))
    assert not staff.relayed
    assert not staff.agent_day


def test_a_staff_sources_sessions_are_the_teams_in_what_the_extraction_writes() -> None:
    """juridico-sandbox, 06/10/2026: the firm's assistant is an internal agent, and its sessions read "Cliente
    pede registrar preferências" on the client's account: the lawyer was written as the client."""
    staff = replace(legal_rules(), staff=True)
    turns = [
        event(Speaker.CUSTOMER, "Bom dia", 1),
        event(Speaker.AI_AGENT, "Bom dia, em que posso ajudar?", 2),
    ]
    p = build_prompt(material(turns), staff, flagged=frozenset(), spans={})
    assert "customer turns: the company's staff, not a client" in p.user
    assert "never as the customer or the client" in p.system
    assert (
        "customer turns:"
        not in build_prompt(material(turns), legal_rules(), flagged=frozenset(), spans={}).user
    )
    assert staff.versions_key != legal_rules().versions_key
    assert (
        short_summary(turns, "pt-BR", staff=True) == "Equipe: Bom dia / Agente: Bom dia, em que posso ajudar?"
    )
    assert short_summary(turns, "pt-BR").startswith("Cliente: Bom dia")


def test_a_2000_character_instruction_and_a_long_summary_are_kept_whole() -> None:
    """Memory never cuts what it learned: an instruction stopped at 260 characters, then at 600, mid-sentence,
    and a cut restriction can say something else."""
    reason = "porque a diretoria decidiu levar o processo a julgamento depois de ler o laudo da perícia. "
    rule = "Não propor acordo neste processo, " + reason * 22
    assert len(rule) > 2_000
    m = case_material((Speaker.CUSTOMER, rule))
    p = build_prompt(m, legal_rules(), flagged=frozenset(), spans={})
    long_summary = "O cliente explicou a decisão da diretoria em detalhe. " * 40
    raw = answer(
        episode={"summary": long_summary, "intent": "request", "category": "other", "outcome": "resolved",
                 "resolution": None, "sentiment": 0.0},
        instructions=[
            {"text": rule, "strength": "must_not", "attr": None, "evidence": ["e1"], "object": "o1"}
        ],
    )  # fmt: skip
    result = validate_one(raw, p, m, legal_rules())
    [said] = result.instructions
    assert said.text == rule.strip()
    assert result.episode is not None
    assert result.episode.summary.startswith(long_summary.strip())
    assert "…" not in said.text
