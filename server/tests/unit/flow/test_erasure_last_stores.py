"""The last stores an erasure reaches in the memory of others (5.14.0): an agent action's result, a business
object mirrored from a company system and its computed values, a settled value and an agent's note proposal
that name the person are redacted in place; the patterns are derived again from memory already redacted. The
object's system of record keeps its own copy, which the request reports, and the next push of the record does
not bring the person back."""

from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.holds import LegalHoldsService
from niadra.config.models import TraitRuleConfig
from niadra.contracts.common import Handle
from niadra.contracts.signals import LegalHoldCreate
from niadra.domain.agent_memory.model import NoteDraft, NoteKind, NoteOrigin, ProposalRecord, ProposalStatus
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.privacy.raw_index import ERASED
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.state.typed import NewValue
from niadra.domain.vocabulary import ActionStatus, HandleType, Speaker
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_erasure_derived_copies import (
    CLARA,
    CLARA_HANDLES,
    CLARA_TEXT,
    EMAIL,
    erase,
    extraction,
    from_clara,
    marcos,
    remember,
)
from tests.unit.flow.test_first_read import PHONE

INVOICE = ObjectKey("invoice", "erp", "0901")


def invoice(flow: Flow, key: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": key,
        "channel": "erp",
        "canonical_type": "invoice.updated",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": INVOICE.type, "namespace": INVOICE.namespace, "id": INVOICE.external_id}],
        "speaker": {"role": "system"},
        "fields": fields,
        "occurred_at": flow.cell.clock.now().isoformat(),
    }


async def stored(cell: Cell) -> BusinessObject:
    async with cell.uow(SPACE_ID) as uow:
        found = await uow.objects.get_object(INVOICE)
    assert found is not None
    return found


def everything(obj: BusinessObject) -> str:
    return str((obj.state, obj.fields))


async def test_an_agent_actions_result_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    handle, _ = await marcos(cell)
    action = AgentAction(
        action_id=uuid7(),
        event_id=uuid7(),
        source_id=BILLING_SOURCE,
        operation="notify",
        status=ActionStatus.CONFIRMED,
        occurred_at=cell.clock.now(),
        result=f"aviso enviado para a Clara em {EMAIL}",
        closes_handle_id=handle,
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.objects.add_action(action)

    counts = await erase(flow, *CLARA_HANDLES)

    kept = cell.uow.store.module("objects")["actions"][action.action_id]
    assert kept.result == f"aviso enviado para a Clara em {ERASED}"
    assert counts[HandleType.PHONE_E164]["named_rows_redacted"] >= 1


async def test_a_mirrored_business_object_is_redacted_and_its_system_of_record_reported(cell: Cell) -> None:
    flow = await remember(cell)
    pushed = invoice(flow, "erp-1", amount="990.00", notify=EMAIL, note=f"avisar a Clara no {CLARA_TEXT}")
    assert (await flow.send(BILLING_SOURCE, pushed)).errors == []
    await flow.drain()
    assert EMAIL in everything(await stored(cell))

    counts = await erase(flow, *CLARA_HANDLES)

    obj = await stored(cell)
    assert EMAIL not in everything(obj)
    assert CLARA_TEXT not in everything(obj)
    assert obj.state["notify"] == ERASED
    assert obj.state["note"] == f"avisar a Clara no {ERASED}"
    assert obj.state["amount"] == "990.00"
    assert counts[HandleType.PHONE_E164]["named_objects_in_source"] == 1


async def test_the_read_paths_copies_of_the_redacted_object_are_read_again(cell: Cell) -> None:
    flow = await remember(cell)
    handle, _ = await marcos(cell)
    assert (await flow.send(BILLING_SOURCE, invoice(flow, "erp-1", notify=EMAIL))).errors == []
    await flow.drain()
    guard = PrivacyGuard()

    await erase(flow, *CLARA_HANDLES)

    guard.apply(cell.uow.store.module("privacy")["feed"], cell.clock.now())
    mark = guard.handle_mark(handle)
    assert mark is not None, "a copy of his object made before the erasure is read from the database"
    assert mark.kind != "erasure", "his own live turns stay served"


async def test_the_next_push_of_the_record_does_not_bring_the_person_back(cell: Cell) -> None:
    flow = await remember(cell)
    note = f"ligar para a Clara no {CLARA_TEXT}"
    first = invoice(flow, "erp-1", amount="990.00", notify=EMAIL, note=note)
    assert (await flow.send(BILLING_SOURCE, first)).errors == []
    await flow.drain()
    await erase(flow, *CLARA_HANDLES)
    cell.clock.advance(minutes=5)

    # The company's system did not erase its record: it sends the old values again with the new amount.
    again = invoice(flow, "erp-2", amount="1090.00", notify=EMAIL, note=note)
    assert (await flow.send(BILLING_SOURCE, again)).errors == []
    await flow.drain()

    obj = await stored(cell)
    assert obj.state["amount"] == "1090.00"
    assert obj.state["notify"] == ERASED
    assert obj.state["note"] == f"ligar para a Clara no {ERASED}"
    assert EMAIL not in everything(obj)
    assert CLARA_TEXT not in everything(obj)


async def test_a_computed_value_of_someone_elses_object_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    assert (await flow.send(BILLING_SOURCE, invoice(flow, "erp-1", amount="990.00"))).errors == []
    await flow.drain()
    obj = await stored(cell)
    now = cell.clock.now()
    value = NewValue(
        name="contact",
        version=1,
        value={"to": EMAIL, "until": "2026-11-01"},
        absent_as=None,
        rule="contact@1",
        inputs_hash=b"h",
        inputs=("notify",),
        computed_at=now,
        valid_until=None,
        declared_gaps=(),
        gap_effect="unknown",
        supersedes=None,
        recorded_at=now,
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.typed.add_values({obj.object_id: [value]}, None)

    await erase(flow, *CLARA_HANDLES)

    kept = cell.uow.store.module("objects")["values"][(obj.object_id, "contact", 1)]
    assert kept.value == {"to": ERASED, "until": "2026-11-01"}


async def test_a_settled_value_of_someone_else_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    handle, _ = await marcos(cell)
    now = cell.clock.now()
    value = SettledValue(
        value_id=uuid7(),
        origin_handle_id=handle,
        type=ValueType.RECORD,
        raw=CLARA_TEXT,
        said_at=now,
        speaker=Speaker.CUSTOMER,
        session_id=uuid7(),
        channel="whatsapp",
        source_id=WHATSAPP_SOURCE,
        created_at=now,
        conversation_id="wa-marcos",
    )
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.memory.insert_values([value]) == 1

    await erase(flow, *CLARA_HANDLES)

    async with cell.uow(SPACE_ID) as uow:
        assert await uow.memory.values_by_keys([handle], [value.key]) == [], "no longer found by her phone"
    assert cell.uow.store.data["memory"]["values"][(handle, value.value_id)].raw == ERASED


async def test_an_agents_note_proposal_that_names_the_person_is_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    proposal = ProposalRecord(
        proposal_id=uuid7(),
        source_id=WHATSAPP_SOURCE,
        origin=NoteOrigin.AGENT,
        draft=NoteDraft(kind=NoteKind.PROCEDURE, title="Contato", body=f"Escrever para {EMAIL}."),
        status=ProposalStatus.PENDING,
        created_at=cell.clock.now(),
        created_by=f"source:{WHATSAPP_SOURCE}",
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.agent_memory.add_proposal(proposal)

    await erase(flow, *CLARA_HANDLES)

    async with cell.uow(SPACE_ID) as uow:
        kept = await uow.agent_memory.get_proposal(proposal.proposal_id)
    assert kept is not None
    assert kept.draft.body == f"Escrever para {ERASED}."


async def test_a_legal_hold_keeps_the_object_as_it_is_and_is_reported(cell: Cell) -> None:
    flow = await remember(cell)
    assert (await flow.send(BILLING_SOURCE, invoice(flow, "erp-1", notify=EMAIL))).errors == []
    await flow.drain()
    body = LegalHoldCreate(handle=Handle(type=HandleType.PHONE_E164, value=PHONE), reason="processo em curso")
    await LegalHoldsService(cell.uow, cell.keys, cell.clock).create(
        principal(WHATSAPP_SOURCE), body, idempotency_key="hold-marcos", by="user:dpo"
    )

    counts = await erase(flow, CLARA_HANDLES[1])

    assert (await stored(cell)).state["notify"] == EMAIL
    assert counts[HandleType.EMAIL]["named_held"] >= 1
    assert "named_objects_in_source" not in counts[HandleType.EMAIL]


async def test_patterns_are_derived_again_from_redacted_memory_and_never_name_the_person(cell: Cell) -> None:
    rule = TraitRuleConfig(rule_id="rc", catalog="recurring_complaint", version="1", params={"min_count": 2})
    cell.config.put(sample_config().model_copy(update={"trait_rules": [rule]}))
    complaint = extraction()
    complaint["episode"] = {**complaint["episode"], "intent": "complaint", "outcome": "pending"}
    flow = Flow.build(cell, ScriptedLlm(complaint, complaint))
    for n in range(2):
        cell.clock.advance(days=1)
        now = cell.clock.now().isoformat()
        name = f"wa-marcos-{n}"
        said = {
            "type": "event",
            "idempotency_key": f"{name}-1",
            "channel": "whatsapp",
            "conversation_id": name,
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": f"a fatura veio errada; avisem a Clara em {EMAIL}"},
            "occurred_at": now,
        }
        ended = {"type": "conversation.ended", "idempotency_key": f"{name}-end", "conversation_id": name}
        assert (await flow.send(WHATSAPP_SOURCE, said, {**ended, "occurred_at": now})).errors == []
        await flow.drain()
    assert (await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi", now))).errors == []
    await flow.drain()
    _, profile = await marcos(cell)
    before = await traits_of(cell, profile)
    assert [t.value for t in before] == ["billing"]

    await erase(flow, *CLARA_HANDLES)

    after = await traits_of(cell, profile)
    assert [t.value for t in after] == ["billing"]
    async with cell.uow(SPACE_ID) as uow:
        episodes = await uow.memory.episodes_by_ids([e for _, e in after[0].evidence])
    assert len(episodes) == 2
    assert all(EMAIL not in e.summary and ERASED in e.summary for e in episodes), "its redacted evidence"
    assert all(EMAIL not in str(t) and CLARA not in str(t) and CLARA_TEXT not in str(t) for t in after)


async def traits_of(cell: Cell, profile: UUID) -> list[Any]:
    async with cell.uow(SPACE_ID) as uow:
        return await uow.traits.traits_of(profile)
