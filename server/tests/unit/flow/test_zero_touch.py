"""Zero touch: a company connects its court system and never declares a type. Its events make the
space's observed types within a minute: the case with its fields, the deadline with its due date and its case,
and the client's agent reads them on the case's line. A field that looks personal stays held, and no person
changed the configuration."""

from dataclasses import replace
from datetime import timedelta
from typing import Any, ClassVar
from uuid import UUID

import pytest

from niadra.app.observed_config import ObservedConfigService
from niadra.config.models import CategoryRule, FeatureSettings, SpaceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.state import ObjectPush, ObjectPushRequest, Provenance, StateRef
from niadra.domain.common.errors import Unavailable
from niadra.domain.config import OBSERVED_DOC
from niadra.domain.extract.observed import ObservedOperation, ObservedOperations
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import IssueQuery
from niadra.domain.vocabulary import Feature, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

FIRM = {"type": "system_id", "value": "CLIENT-1", "scope": "cases", "subject_kind": "account"}
CLIENT = {"type": "phone_e164", "value": "+5511955550001"}
CASES = [f"case-{n}" for n in range(3)]


def bare(cell: Cell) -> SpaceConfig:
    """A space whose company declared no type at all."""
    config = cell.config.space(SPACE_ID)
    policy = config.policy.model_copy(
        update={
            "categories": [
                *config.policy.categories,
                CategoryRule(name="object", min_verification=Verification.V0),
            ]
        }
    )
    return config.model_copy(
        update={
            "policy": policy,
            "object_types": ObjectTypeRegistry(),
            "features": FeatureSettings(enabled=frozenset({Feature.SIGNALS, Feature.STATE})),
        }
    )


def event(key: str, kind: str, ref: dict[str, str], fields: dict[str, Any], at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": key,
        "channel": "erp",
        "canonical_type": kind,
        "handles": [FIRM],
        "object_refs": [ref],
        "speaker": {"role": "system"},
        "fields": fields,
        "occurred_at": at,
    }


async def connected(cell: Cell) -> Flow:
    cell.config.put(bare(cell))
    flow = Flow.build(cell)
    now = cell.clock.now()
    due = (now + timedelta(days=21)).date().isoformat()
    events = []
    for n, case_id in enumerate(CASES):
        ref = {"type": "legal_case", "namespace": "court", "id": case_id}
        fields = {
            "cnj": f"080123{n}-67.2026.8.26.0100",
            "claim_amount": 45000 + n,
            "telefone_cliente": "+5511999990000",
            "state": "ativo",
        }
        events.append(event(f"case-{n}", "legal_case.opened", ref, fields, now.isoformat()))
    for n, case_id in enumerate(CASES):
        ref = {"type": "deadline", "namespace": "court", "id": f"deadline-{n}"}
        fields = {"case_id": case_id, "due_on": due, "days": 15, "state": "aberto"}
        events.append(event(f"deadline-{n}", "deadline.opened", ref, fields, now.isoformat()))
    assert (await flow.send(BILLING_SOURCE, *events)).errors == []
    await flow.drain()
    await flow.identity.create_link(
        SPACE_ID, Handle.model_validate(CLIENT), Handle.model_validate(FIRM), role="contact"
    )
    return flow


async def test_a_court_system_nobody_declared_is_served_on_the_case_line_after_one_observation(
    cell: Cell,
) -> None:
    flow = await connected(cell)
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    served = cell.config.space(SPACE_ID).object_types
    case = served.get("legal_case")
    deadline = served.get("deadline")
    assert case is not None
    assert deadline is not None
    # The identifier is declared, so it names the case and finds it in a conversation; every other field
    # is served undeclared, by its name.
    assert set(case.fields) == {"cnj"}
    assert case.observed_fields == {"cnj"}
    assert deadline.relations["legal_case"].via == "case_id"
    assert "due_on" in served.date_fields()["deadline"]

    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
    )
    text = (await flow.serve.context(caller, request)).text
    assert "0801230-67.2026.8.26.0100" in text
    assert "vence em" in text
    # The phone the system sent undeclared stays held: inference never declares what looks personal.
    assert "999990000" not in text
    read = await flow.serve.context(caller, request.model_copy(update={"include": ["state"]}))
    assert read.state is not None
    stated = read.state.text or ""
    due = (cell.clock.now() + timedelta(days=21)).strftime("%d/%m/%Y")
    # The deadline goes by its case's number, right under it, and says when it is due.
    assert (
        f"- deadline court:deadline-0 (de Cnj 0801230-67.2026.8.26.0100): situação aberto; vence em {due}"
        in (stated)
    )
    assert "999990000" not in stated


async def test_what_the_cell_inferred_is_kept_with_its_notes_and_a_quiet_run_writes_nothing(
    cell: Cell,
) -> None:
    flow = await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await flow.observed_config.observe(task)
    async with cell.uow.cell() as uow:
        first = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert first is not None
    assert first.version == 1
    kinds = {(n["kind"], n.get("type")) for n in first.document["unreported"]}
    assert ("type", "legal_case") in kinds
    assert ("relation", "deadline") in kinds
    assert "999990000" not in repr(first.document)
    await flow.observed_config.observe(task)
    async with cell.uow.cell() as uow:
        again = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert again == first


async def test_a_personal_value_sent_later_in_an_observed_identifier_never_reaches_the_agent(
    cell: Cell,
) -> None:
    """What the inferrer declared no person ruled not personal: a value that may be personal, sent after the
    field was declared, is held as an undeclared field's is."""
    flow = await connected(cell)
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    ref = {"type": "legal_case", "namespace": "court", "id": "case-0"}
    cell.clock.advance(minutes=1)
    later = event(
        "case-0-fix", "legal_case.updated", ref, {"cnj": "maria@exemplo.com"}, cell.clock.now().isoformat()
    )
    assert (await flow.send(BILLING_SOURCE, later)).errors == []
    await flow.drain()
    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
        include=["state"],
    )
    read = await flow.serve.context(caller, request)
    assert read.state is not None
    # The update arrived: the case still holds its other fields, and the new value stays held.
    assert "Claim amount" in (read.text or "")
    assert "maria@exemplo.com" not in (read.text or "")
    assert "maria@exemplo.com" not in (read.state.text or "")


class Recorded:
    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.calls: list[tuple[str, Any]] = []
        self.reasons: list[str] = []

    async def record(self, space_id: UUID, config_type: str, document: Any, reason: str) -> str:
        if self.down:
            raise Unavailable("control plane unreachable")
        self.calls.append((config_type, document))
        self.reasons.append(reason)
        return "d1"


class Operations:
    async def snapshot(self, space_id: UUID) -> ObservedOperations:
        return ObservedOperations((ObservedOperation("notify_client", 3, "Avisar o cliente"),))


async def test_what_the_cell_inferred_is_recorded_as_observed_once_and_again_after_the_control_plane_was_down(
    cell: Cell,
) -> None:
    await connected(cell)
    records = Recorded(down=True)
    service = ObservedConfigService(
        cell.uow, cell.config, cell.clock, records=records, operations=Operations()
    )
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await service.observe(task)
    assert records.calls == []
    records.down = False
    await service.observe(task)
    assert [name for name, _ in records.calls] == ["object-types", "schemas"]
    types = {t["type"] for t in records.calls[0][1]["types"]}
    assert types == {"legal_case", "deadline"}
    assert records.calls[1][1] == {"operations": [{"name": "notify_client", "meaning": "Avisar o cliente"}]}
    await service.observe(task)
    assert len(records.calls) == 2


async def test_what_a_customer_facing_agent_or_coordination_made_is_never_a_systems_type(cell: Cell) -> None:
    """Only what a company system (or an internal agent) wrote is inferred as a type that mirrors a system: an
    object the client's agent named stays the agent's, and the next one the
    agent names is never refused as unknown (e2e, 07/10/2026)."""
    flow = await connected(cell)
    now = cell.clock.now().isoformat()

    def act(key: str, ref: dict[str, str]) -> dict[str, Any]:
        return {
            "type": "event", "kind": "action", "idempotency_key": key, "channel": "whatsapp",
            "handles": [CLIENT], "object_refs": [ref], "speaker": {"role": "ai_agent", "id": "atendente"},
            "action": {"operation": "notify_client", "result": "avisado"}, "occurred_at": now,
        }  # fmt: skip

    ticket = {"type": "ticket", "namespace": "helpdesk", "id": "t-1"}
    assert (await flow.send(WHATSAPP_SOURCE, act("a-1", ticket))).errors == []
    await flow.drain()
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    served = cell.config.space(SPACE_ID).object_types
    assert served.get("legal_case") is not None
    assert served.get("ticket") is None
    assert served.get("handoff") is None
    again = {"type": "ticket", "namespace": "helpdesk", "id": "t-2"}
    assert (await flow.send(WHATSAPP_SOURCE, act("a-2", again))).errors == []


async def test_a_systems_push_of_a_type_nobody_declared_is_kept_and_served_after_one_observation(
    cell: Cell,
) -> None:
    """Zero touch (juridico-zero, 07/10/2026): the court system pushes its cases' state with no type declared.
    The push was refused (`object_type_unknown`) and the state was lost; it is kept as a system event's fields
    are, the inference reads it, and the client's agent reads the pushed fields on the case's line."""
    flow = await connected(cell)
    now = cell.clock.now()
    held_on = (now - timedelta(days=1)).date().isoformat()
    pushes = [
        ObjectPush(
            ref=StateRef(type="legal_case", namespace="court", id=case_id),
            version=1,
            fields={"client_side": "passivo", "held_on": held_on},
            provenance=Provenance(source="live", source_observed_at=now),
        )
        for case_id in CASES
    ] + [
        ObjectPush(
            ref=StateRef(type="hearing", namespace="court", id="hearing-0"),
            version=1,
            fields={"case_id": "case-0", "held_on": held_on, "state": "realizada"},
            provenance=Provenance(source="live", source_observed_at=now),
        )
    ]
    pusher = principal(BILLING_SOURCE, cell.config.space(SPACE_ID))
    answer = await flow.shared.push(pusher, ObjectPushRequest(objects=pushes))
    assert answer.recorded == len(pushes)
    await flow.drain()
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    served = cell.config.space(SPACE_ID).object_types
    assert served.get("legal_case") is not None
    assert served.get("hearing") is not None

    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
        include=["state"],
    )
    read = await flow.serve.context(caller, request)
    assert read.state is not None
    stated = read.state.text or ""
    # What only the push said reaches the case's line, beside what the events said.
    assert "passivo" in stated
    assert "0801230-67.2026.8.26.0100" in stated


async def test_what_the_systems_confirm_later_is_inferred_on_the_next_run_and_offered_to_the_extraction(
    cell: Cell,
) -> None:
    """Production, 07/10/2026: every run after a space's first died on its own evidence, so a type and the
    operations its systems confirm later were never inferred; and an item said before the event that does it
    waited for no operation, since only the agents' recorded actions were offered."""
    from niadra.app.extract import rules_for

    flow = await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await flow.observed_config.observe(task)
    cell.clock.advance(minutes=1)
    now = cell.clock.now().isoformat()
    docs = [
        event(
            f"doc-{n}",
            "case_document.attached",
            {"type": "case_document", "namespace": "court", "id": f"doc-{n}"},
            {"case_id": case_id, "kind": "procuracao", "operation": "attach_document", "state": "anexado"},
            now,
        )
        for n, case_id in enumerate(CASES)
    ]
    assert (await flow.send(BILLING_SOURCE, *docs)).errors == []
    await flow.drain()
    await flow.observed_config.observe(task)
    space = cell.config.space(SPACE_ID)
    document = space.object_types.get("case_document")
    assert document is not None
    assert document.relations["legal_case"].via == "case_id"
    assert "attach_document" in space.extraction.operation_names()
    assert "attach_document" in rules_for(space, BILLING_SOURCE).operations
    async with cell.uow.cell() as uow:
        stored = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert stored is not None
    assert stored.document["operations"] == ["attach_document"]
    assert any(n.get("operation") == "attach_document" for n in stored.document["unreported"])


class PortugueseWords:
    def __init__(self) -> None:
        self.asked: list[tuple[str, list[str], list[str]]] = []

    async def write(self, space_id: UUID, locale: str, names: Any, codes: Any, context: Any = ()) -> Any:
        from niadra.domain.objects.words import Words

        self.asked.append((locale, list(names), list(codes)))
        return Words({"claim_amount": "Valor da causa", "cnj": "Número CNJ"}, {"ativo": "em andamento"})


async def test_a_line_says_what_the_systems_send_in_the_spaces_words_with_no_label_declared(
    cell: Cell,
) -> None:
    """juridico-zero, 07/10/2026: a Portuguese space read "Claim amount 40000.00; situação ingested". The
    space's words for the names and codes its systems send are written once, from the names alone, and the
    lines say them."""
    flow = await connected(cell)
    words = PortugueseWords()
    service = ObservedConfigService(cell.uow, cell.config, cell.clock, words=words)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await service.observe(task)
    locale, names, codes = words.asked[0]
    assert locale == "pt-BR"
    assert "claim_amount" in names
    assert "ativo" in codes
    # The phone the system sent is held, so its name is never asked about.
    assert "telefone_cliente" not in names
    await service.observe(task)
    assert len(words.asked) == 1
    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
        include=["state"],
    )
    read = await flow.serve.context(caller, request)
    assert read.state is not None
    stated = read.state.text or ""
    assert "Valor da causa 45000" in stated
    assert "situação em andamento" in stated
    assert "Claim amount" not in stated
    assert "[Número CNJ 0801230-67.2026.8.26.0100]" in stated


async def test_the_fields_the_inference_declared_are_said_in_the_spaces_words_too(cell: Cell) -> None:
    """juridico-zero, 08/10/2026: the case's own key read "[Cnj 1012345-...]" and "Case id", "Assigned
    lawyer" on a pt-BR space. The inference declares those fields with their names made readable, and the
    space's words were asked only for names nothing labelled, so the readable English stood. A label a
    person declared stands; the one the inference wrote is replaced."""
    flow = await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    # The types are inferred on a run that writes no words (none could be written then).
    await ObservedConfigService(cell.uow, cell.config, cell.clock).observe(task)
    words = PortugueseWords()
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=words).observe(task)
    assert words.asked, "the next run asks the space's words"
    assert "cnj" in words.asked[0][1]
    await flow.prove(WHATSAPP_SOURCE, "wa-client", CLIENT["value"], "V2")
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    request = ContextRequest(
        subject=Handle.model_validate(CLIENT),
        about=Handle.model_validate(FIRM),
        conversation_id="wa-client",
        verification=Verification.V2,
        include=["state"],
    )
    read = await flow.serve.context(caller, request)
    stated = (read.state.text if read.state else "") or ""
    assert "[Número CNJ 0801230-67.2026.8.26.0100]" in stated
    assert "[Cnj " not in stated


async def test_a_limit_inference_reaches_is_an_issue_and_no_note_is_cut(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory never cuts: what inference did not do is an issue the Console lists, and the notes
    of what it did stay whole until a record names them, the record saying how many it did not name one by
    one. They were kept in the observed document cut to the last 50."""
    monkeypatch.setattr("niadra.domain.objects.infer.MAX_TYPES", 1)
    monkeypatch.setattr("niadra.app.observed_config.REASON_NAMED", 2)
    flow = await connected(cell)
    records = Recorded(down=True)
    service = ObservedConfigService(
        cell.uow, cell.config, cell.clock, records=records, operations=Operations(), issues=flow.data_issues
    )
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)

    await service.observe(task)
    async with cell.uow(SPACE_ID) as uow:
        [issue] = await uow.turns.issues(IssueQuery(kind="inference_limit"))
    assert issue.field == "types"
    async with cell.uow.cell() as uow:
        kept = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert kept is not None
    notes = kept.document["unreported"]
    assert len(notes) > 2
    assert "notes" not in kept.document

    records.down = False
    await service.observe(task)
    assert records.reasons[0].endswith(f"; and {len(notes) - 2} more")
    async with cell.uow.cell() as uow:
        told = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert told is not None
    assert told.document["unreported"] == []


class ContextWords(PortugueseWords):
    """Records the record types each call gave as context."""

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[list[dict[str, Any]]] = []

    async def write(self, space_id: UUID, locale: str, names: Any, codes: Any, context: Any = ()) -> Any:
        self.contexts.append(list(context))
        return await super().write(space_id, locale, names, codes)


async def stored_words(cell: Cell) -> dict[str, Any]:
    async with cell.uow.cell() as uow:
        stored = await uow.config.document(SPACE_ID, OBSERVED_DOC)
    assert stored is not None
    return dict(stored.document["words"])


async def test_the_words_are_asked_with_the_record_types_they_come_from_by_name_only(cell: Cell) -> None:
    """juridico-zero, 08/10/2026: from the names alone, a hearing's state "held" was worded "retido" and a
    case's "claim_amount" "Valor da reclamação". The model gets the types the names come from, with their
    fields and states, names only: never a value a customer's record holds."""
    await connected(cell)
    words = ContextWords()
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=words).observe(
        Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    )
    [context] = words.contexts
    case = next(t for t in context if t["type"] == "legal_case")
    assert {"claim_amount", "cnj"} <= set(case["fields"])
    assert "ativo" in case["states"]
    assert "telefone_cliente" not in case["fields"]  # held: personal
    said = repr(context)
    assert "45000" not in said
    assert "080123" not in said
    assert "999990000" not in said


async def test_a_word_asked_with_less_context_is_asked_once_more(cell: Cell) -> None:
    """A word of the earlier asking, from the names alone, is asked again once with the context; a word
    whose record type grew by `REWORD_GROWTH` names is asked again once; nothing is asked on every run."""
    from niadra.domain.objects.words import REWORD_GROWTH

    flow = await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    words = ContextWords()
    service = ObservedConfigService(cell.uow, cell.config, cell.clock, words=words)
    await service.observe(task)
    assert len(words.asked) == 1
    # The words a space kept from before the context: no marks.
    async with cell.uow.cell() as uow:
        stored = await uow.config.document(SPACE_ID, OBSERVED_DOC)
        assert stored is not None
        document = {**stored.document, "words": {**stored.document["words"], "marks": {}, "asked": {}}}
        await uow.config.save_document(replace(stored, document=document))
    await service.observe(task)
    assert len(words.asked) == 2
    assert "claim_amount" in words.asked[1][1]
    await service.observe(task)
    assert len(words.asked) == 2
    # The case type gains fields: its words are asked once more, with them, and never again for that.
    cell.clock.advance(days=2)
    fields = {f"extra_{n}": n for n in range(REWORD_GROWTH)} | {"cnj": "0801239-67.2026.8.26.0100"}
    ref = {"type": "legal_case", "namespace": "court", "id": "case-9"}
    changed = {**fields, "claim_amount": 1, "state": "ativo"}
    grown = event("case-9", "legal_case.updated", ref, changed, cell.clock.now().isoformat())
    assert (await flow.send(BILLING_SOURCE, grown)).errors == []
    await flow.drain()
    await service.observe(task)
    assert len(words.asked) == 3
    assert "claim_amount" in words.asked[2][1]
    cell.clock.advance(days=2)
    await service.observe(task)
    assert "claim_amount" not in [n for _, names, _ in words.asked[3:] for n in names]
    assert (await stored_words(cell))["marks"]["f:claim_amount"][2] is True


async def keep_words_from_the_first_asking(cell: Cell, values: dict[str, str]) -> None:
    """The space's words as a run before the record types were given left them: `values`, no marks, every
    name and code stamped as asked a minute ago."""
    async with cell.uow.cell() as uow:
        stored = await uow.config.document(SPACE_ID, OBSERVED_DOC)
        assert stored is not None
        words = dict(stored.document["words"])
        stamp = (cell.clock.now() - timedelta(minutes=1)).isoformat()
        asked = {key: stamp for key in [*words.get("asked", {}), *(f"v:{c}" for c in values)]}
        words.update(values={**words.get("values", {}), **values}, marks={}, asked=asked)
        await uow.config.save_document(replace(stored, document={**stored.document, "words": words}))


async def test_a_word_of_the_first_asking_is_asked_again_with_the_types_even_if_asked_that_day(
    cell: Cell,
) -> None:
    """juridico-zero, 09/10/2026, final round: "held" was worded "retido" at 01:08, from the names alone. The
    asking with the record types at 14:42 passed it over as asked less than a day before, and seven blocks
    read a hearing that took place as "situação: retido". A word of the first asking is asked once more at
    once, with the types, however recently it was asked; then never again for that."""
    await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    words = ContextWords()
    service = ObservedConfigService(cell.uow, cell.config, cell.clock, words=words)
    await service.observe(task)
    await keep_words_from_the_first_asking(cell, {"ativo": "retido"})
    await service.observe(task)
    assert len(words.asked) == 2
    assert "ativo" in words.asked[1][2]
    assert any(t["type"] == "legal_case" and "ativo" in t.get("states", []) for t in words.contexts[1])
    assert (await stored_words(cell))["values"]["ativo"] == "em andamento"
    await service.observe(task)
    assert len(words.asked) == 2


class SpentWords(ContextWords):
    """A budget spent: nothing comes back."""

    async def write(self, space_id: UUID, locale: str, names: Any, codes: Any, context: Any = ()) -> Any:
        from niadra.domain.objects.words import Words

        self.asked.append((locale, list(names), list(codes)))
        self.contexts.append(list(context))
        return Words()


async def test_a_word_of_the_first_asking_whose_asking_again_failed_is_kept_and_asked_a_day_later(
    cell: Cell,
) -> None:
    """A spent budget answers nothing: the old word stays on the lines, is never taken as asked with the
    types, waits a day, and is asked again then; never on every run."""
    await connected(cell)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=PortugueseWords()).observe(task)
    await keep_words_from_the_first_asking(cell, {"ativo": "retido"})
    spent = SpentWords()
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=spent).observe(task)
    assert "ativo" in spent.asked[0][2]
    kept = await stored_words(cell)
    assert kept["values"]["ativo"] == "retido"
    assert kept["marks"]["v:ativo"][0] == 1
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=spent).observe(task)
    assert len(spent.asked) == 1
    cell.clock.advance(days=2)
    words = ContextWords()
    await ObservedConfigService(cell.uow, cell.config, cell.clock, words=words).observe(task)
    assert "ativo" in [c for _, _, codes in words.asked for c in codes]
    assert (await stored_words(cell))["values"]["ativo"] == "em andamento"


class WordsByType(ContextWords):
    """Words as the model gives them with one record type as context, and its guess with several."""

    SENSE: ClassVar[dict[str, str]] = {"hearing": "realizada", "court_fee": "retida"}

    async def write(self, space_id: UUID, locale: str, names: Any, codes: Any, context: Any = ()) -> Any:
        from niadra.domain.objects.words import Words

        self.asked.append((locale, list(names), list(codes)))
        self.contexts.append(list(context))
        types = [t["type"] for t in context]
        held = self.SENSE.get(types[0], "retido") if len(types) == 1 else "retido"
        return Words({"claim_amount": "Valor da causa"}, {"held": held, "ativo": "em andamento"})

    def per_type(self) -> list[tuple[str, list[str]]]:
        """The calls asked with one type as context: that type and the codes asked."""
        return [
            (context[0]["type"], codes)
            for context, (_, _, codes) in zip(self.contexts, self.asked, strict=True)
            if len(context) == 1
        ]


async def held_hearings_and_fees(flow: Flow, cell: Cell) -> None:
    now = cell.clock.now().isoformat()
    events = []
    for n, case_id in enumerate(CASES):
        hearing = {"type": "hearing", "namespace": "court", "id": f"hearing-{n}"}
        held = {"case_id": case_id, "state": "held"}
        events.append(event(f"hearing-{n}", "hearing.held", hearing, held, now))
        fee = {"type": "court_fee", "namespace": "court", "id": f"fee-{n}"}
        events.append(event(f"fee-{n}", "court_fee.held", fee, held, now))
    assert (await flow.send(BILLING_SOURCE, *events)).errors == []
    await flow.drain()


async def test_a_code_two_types_share_is_worded_for_each_type_and_the_space_word_stays_beneath(
    cell: Cell,
) -> None:
    """A state two record types share has a sense in each: a hearing that was `held` took place
    ("realizada"), a fee that was `held` is kept back ("retida"). One word for the space put one sense on
    both. A name or code two types share is asked once more for each type, with that type alone as context,
    and each type's lines say its own word; the space's word stays for any other type and for the release
    before this one."""
    flow = await connected(cell)
    await held_hearings_and_fees(flow, cell)
    words = WordsByType()
    service = ObservedConfigService(cell.uow, cell.config, cell.clock, words=words)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await service.observe(task)
    per_type = dict(words.per_type())
    assert {"court_fee", "hearing"} <= set(per_type)
    assert "held" in per_type["hearing"]
    assert "held" in per_type["court_fee"]
    # A code only one type has is never asked again for that type.
    assert "ativo" not in per_type.get("legal_case", [])
    served = cell.config.space(SPACE_ID).object_types
    hearing, fee, case = served.get("hearing"), served.get("court_fee"), served.get("legal_case")
    assert hearing is not None
    assert fee is not None
    assert case is not None
    assert hearing.value_word("held") == "realizada"
    assert fee.value_word("held") == "retida"
    assert case.value_word("ativo") == "em andamento"
    assert served.value_words()["hearing"]["held"] == "realizada"
    stored = await stored_words(cell)
    assert stored["types"]["hearing"]["values"]["held"] == "realizada"
    assert stored["types"]["court_fee"]["values"]["held"] == "retida"
    # The space-wide word the release before this one reads is still written.
    assert stored["values"]["held"] == "retido"
    asked = len(words.asked)
    await service.observe(task)
    assert len(words.asked) == asked


async def test_the_type_words_asked_in_one_run_are_bounded(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each type with a shared name or code costs one call: a run asks at most `TYPE_CALLS_PER_RUN` types,
    the next runs ask the rest, and no type is asked twice."""
    monkeypatch.setattr("niadra.app.observed_config.TYPE_CALLS_PER_RUN", 1)
    flow = await connected(cell)
    await held_hearings_and_fees(flow, cell)
    words = WordsByType()
    service = ObservedConfigService(cell.uow, cell.config, cell.clock, words=words)
    task = Task(TaskName.OBSERVE_CONFIG, SPACE_ID)
    await service.observe(task)
    assert len(words.per_type()) == 1
    await service.observe(task)
    assert len(words.per_type()) == 2
    for _ in range(6):
        await service.observe(task)
    asked = [type_name for type_name, _ in words.per_type()]
    assert len(asked) == len(set(asked)), "each type is asked once"
    assert {"court_fee", "hearing"} <= set(asked)
