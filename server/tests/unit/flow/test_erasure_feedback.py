"""The company's feedback never keeps what the customer said (8.4): a retracted, corrected or restored fact
goes with the last event of the customer that stated it, the corrections written for it go too, and the
feedback events stay as audit records with ids and kinds only, on every path an erasure takes."""

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.backups import BackupService
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.privacy import ErasureOut, ForgetRequest
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.records import EventRecord, Fact
from niadra.domain.vocabulary import FactStatus, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, remembered, said
from tests.unit.flow.test_restore_fact import Console, customer

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
EMAIL = "rafa@example.com"
CRM = {"type": "system_id", "value": "48213", "scope": "crm"}
REASON = "a cliente disse Rafa, mas o cadastro diz outra coisa"


def ended(conversation: str, at: str) -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at,
    }


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    return flow


async def every_fact(flow: Flow, *handles: UUID) -> dict[str, Fact]:
    async with flow.cell.uow(SPACE_ID) as uow:
        return {f.value: f for f in await uow.memory.facts_for(handles, tuple(FactStatus))}


async def feedback_of(flow: Flow, *handles: UUID) -> dict[str, EventRecord]:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await uow.ingest.system_events_for_handles(handles, EPOCH, 100)
    return {e.canonical_type or "": e for e in found if (e.canonical_type or "").startswith("feedback.")}


async def forget(flow: Flow, request: ForgetRequest, key: str = "f1") -> ErasureOut:
    accepted = await flow.privacy.forget(SPACE_ID, request, idempotency_key=key, requested_by="user:dpo")
    await flow.drain()
    out = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert out.status == ErasureStatus.COMPLETED.value
    return out


async def retract(console: Console, profile_id: UUID, fact: Fact, key: str = "r1") -> None:
    body = {
        "profile_id": str(profile_id),
        "action": "retract_fact",
        "fact_id": str(fact.fact_id),
        "reason": REASON,
    }
    assert (await console.post("/v1/corrections", body, key=key)).status_code == 202
    await console.flow.drain()


async def test_a_conversation_erasure_takes_the_fact_the_company_retracted(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    fact = (await every_fact(flow, phone))["Rafa"]
    await retract(console, profile_id, fact)

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    # The retraction's mention held the row and its value before (WP-G's report); now both go.
    assert await every_fact(flow, phone) == {}
    retraction = (await feedback_of(flow, phone))["feedback.retract_fact"]
    assert set(retraction.fields) == {"action", "fact_id", "requested_by"}
    assert retraction.fields["fact_id"] == str(fact.fact_id)
    assert REASON not in str(retraction.fields)
    assert (out.erased["facts"], out.erased["feedback_values"]) == (1, 1)
    missing = await console.restore(profile_id, fact)
    assert missing.status_code == 404


async def test_the_correction_of_an_erased_fact_goes_with_it(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    original = (await every_fact(flow, phone))["Rafa"]
    await console.correct(profile_id, original, "Rafaela")
    assert set(await every_fact(flow, phone)) == {"Rafa", "Rafaela"}

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    # The corrected row copies the conversation, the speaker and the slot of the fact it replaces, and
    # only the company's event holds it: it states nothing the customer still has on record.
    assert await every_fact(flow, phone) == {}
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await uow.memory.relations_for([phone]) == []
    correction = (await feedback_of(flow, phone))["feedback.correct_fact"]
    assert correction.fields == {
        "action": "correct_fact",
        "fact_id": str(original.fact_id),
        "requested_by": correction.fields["requested_by"],
    }
    assert "Rafaela" not in str(correction.fields)
    assert out.erased["facts"] == 2


async def test_a_restored_fact_goes_and_the_restore_keeps_ids_only(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    fact = (await every_fact(flow, phone))["Rafa"]
    await retract(console, profile_id, fact)
    flow.cell.clock.advance(minutes=5)
    assert (await console.restore(profile_id, fact)).status_code == 202
    await flow.drain()
    assert (await every_fact(flow, phone))["Rafa"].status is FactStatus.ACTIVE

    await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    assert await every_fact(flow, phone) == {}
    events = await feedback_of(flow, phone)
    assert set(events) == {"feedback.retract_fact", "feedback.restore_fact"}
    for event in events.values():
        assert set(event.fields) <= {"action", "fact_id", "requested_by"}
        assert event.fields["fact_id"] == str(fact.fact_id)


async def test_a_correction_the_customer_also_said_stays(cell: Cell) -> None:
    answer = remembered()
    again = copy.deepcopy(answer)
    again["facts"][0]["value"] = "Rafaela"
    flow = Flow.build(cell, ScriptedLlm(answer, again))
    now = cell.clock.now().isoformat()
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    console = Console(flow)
    profile_id, phone = await customer(flow)
    await console.correct(profile_id, (await every_fact(flow, phone))["Rafa"], "Rafaela")
    cell.clock.advance(hours=1)
    later = cell.clock.now().isoformat()
    second = said("wa-2", "Na verdade é Rafaela", later, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, second, ended("wa-2", later))).errors == []
    await flow.drain()

    await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    # The customer said the corrected value in another conversation: that evidence keeps the row.
    left = await every_fact(flow, phone)
    assert set(left) == {"Rafaela"}
    assert left["Rafaela"].status is FactStatus.ACTIVE
    correction = (await feedback_of(flow, phone))["feedback.correct_fact"]
    assert "value" not in correction.fields


async def test_a_handle_erasure_clears_the_feedback_sent_through_another_handle(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    handles = [{"type": "phone_e164", "value": PHONE}, {"type": "email", "value": EMAIL}, CRM]
    first = {
        **said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp"),
        "handles": handles,
        "verification_hint": "V1",
    }
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    by_id: dict[UUID, Handle] = {}
    async with flow.cell.uow(SPACE_ID) as uow:
        for h in handles:
            found = await uow.identity.find_handle(HandleType(h["type"]), h.get("scope", ""), h["value"])
            assert found is not None
            by_id[found.handle_id] = Handle.model_validate(h)
        profile = await uow.identity.profile_of(next(iter(by_id)))
    assert profile is not None
    assert len(profile.handle_ids) > 1
    by_id = {i: by_id[i] for i in profile.handle_ids}
    [fact] = (await every_fact(flow, *by_id)).values()
    subject = next(h for i, h in by_id.items() if i != fact.origin_handle_id)
    # An agent corrects the fact naming the customer by a handle the fact does not live on.
    feedback = FeedbackRequest(
        idempotency_key="fb-1",
        subject=subject,
        action="correct_fact",
        fact_id=str(fact.fact_id),
        value="Rafaela",
        reason=REASON,
    )
    config = flow.cell.config.space(SPACE_ID)
    assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), feedback)).accepted == 1
    await flow.drain()
    assert set(await every_fact(flow, *by_id)) == {"Rafa", "Rafaela"}

    out = await forget(flow, ForgetRequest(target="handle", handle=by_id[fact.origin_handle_id]))

    assert await every_fact(flow, *by_id) == {}
    kept = [i for i, h in by_id.items() if h == subject]
    correction = (await feedback_of(flow, *kept))["feedback.correct_fact"]
    assert correction.fields == {"action": "correct_fact", "fact_id": str(fact.fact_id)}
    assert out.erased["feedback_values"] == 1


async def test_a_restored_dump_runs_the_erasure_again_without_the_retracted_fact(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    fact = (await every_fact(flow, phone))["Rafa"]
    await retract(console, profile_id, fact)
    dump = copy.deepcopy(flow.cell.uow.store.data)  # the nightly dump, before the request

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))
    assert await every_fact(flow, phone) == {}

    flow.cell.uow.store.data = dump
    assert (await every_fact(flow, phone))["Rafa"].status is FactStatus.RETRACTED
    admin: Any = None
    backups = BackupService(
        flow.cell.uow, admin, flow.cell.objects, flow.cell.clock, cell_database="cell", keys=flow.cell.keys
    )
    assert await backups.reapply_erasures(SPACE_ID, "backups/s_x/dump") == [UUID(out.request_id)]
    await flow.drain()

    assert await every_fact(flow, phone) == {}
    retraction = (await feedback_of(flow, phone))["feedback.retract_fact"]
    assert REASON not in str(retraction.fields)
