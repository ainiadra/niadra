"""What an erasure must not leave behind (8.4): what a conversation's events produced goes whether those
events are still in the hot window or already in the cold archive, an agent note never keeps the erased
conversation's id, and the company's resolution of an erased open item keeps ids and kinds only."""

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.agent_memory.model import (
    AgentNoteRecord,
    NoteDraft,
    NoteKind,
    NoteOrigin,
    NoteStatus,
    ProposalRecord,
    ProposalStatus,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.files import from_parquet
from niadra.domain.privacy.retention import month_key
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, HandleType, OpenItemStatus
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_erasure_feedback import ended, forget
from tests.unit.flow.test_first_read import PHONE, remembered, said

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
REASON = "a cliente pediu pelo telefone +5511955554444"


def with_item_and_value(answer: dict[str, Any]) -> dict[str, Any]:
    """The first conversation's extraction: a fact, an open item, a settled value and the episode."""
    answer = copy.deepcopy(answer)
    answer["open_items"] = [
        {
            "description": "Enviar a segunda via da fatura",
            "owner": "company",
            "due_at": None,
            "evidence": ["e1"],
            "object": None,
            "expected_operation": None,
            "about": None,
        }
    ]
    answer["values"] = ["postal_code 88221-615"]
    return answer


def later() -> dict[str, Any]:
    """A second conversation that states nothing of the first one."""
    answer = copy.deepcopy(remembered())
    answer["episode"]["summary"] = "Perguntou o horário da loja"
    answer["facts"][0]["value"] = "Bia"
    return answer


async def phone_handle(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
    assert found is not None
    return found.handle_id


async def drawn(flow: Flow, handle: UUID) -> dict[str, list[str]]:
    """Everything memory keeps for the customer, by kind."""
    async with flow.cell.uow(SPACE_ID) as uow:
        facts = await uow.memory.facts_for([handle], tuple(FactStatus))
        items = await uow.memory.open_items_for([handle], tuple(OpenItemStatus))
        values = await uow.memory.values_for([handle])
        episodes = await uow.memory.episodes_for([handle])
    return {
        "facts": sorted(f.value for f in facts),
        "open_items": sorted(i.description for i in items),
        "values": sorted(v.raw for v in values),
        "episodes": sorted(e.conversation_id or "" for e in episodes),
    }


async def archive_month_of(flow: Flow, at: datetime) -> None:
    month = month_key(at.astimezone(UTC).date())
    await flow.privacy.archive_events(Task(TaskName.ARCHIVE_EVENTS, SPACE_ID, {"month": month}))


def archived_rows(flow: Flow) -> list[dict[str, Any]]:
    objects = flow.cell.objects.objects
    return [
        row
        for key, data in objects.items()
        if key.startswith(f"archive/events/{SPACE_ID}/") and key.endswith(".parquet")
        for row in from_parquet(data)
    ]


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(with_item_and_value(remembered()), later()))
    now = cell.clock.now().isoformat()
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via, CEP 88221-615", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    return flow


@pytest.mark.parametrize("archived", [False, True], ids=["live", "archived"])
async def test_a_conversation_erasure_takes_what_it_produced_live_or_archived(
    flow: Flow, archived: bool
) -> None:
    handle = await phone_handle(flow)
    started = flow.cell.clock.now()
    assert await drawn(flow, handle) == {
        "facts": ["Rafa"],
        "open_items": ["Enviar a segunda via da fatura"],
        "values": ["88221-615"],
        "episodes": ["wa-1"],
    }
    # A later conversation in another month, which stays.
    flow.cell.clock.advance(days=45)
    now = flow.cell.clock.now().isoformat()
    second = said("wa-2", "Me chama de Bia, qual o horário da loja?", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, second, ended("wa-2", now))).errors == []
    await flow.drain()
    if archived:
        # Past the hot window the first conversation's events live only in the cold archive (5.14.0).
        await archive_month_of(flow, started)
        async with flow.cell.uow(SPACE_ID) as uow:
            live = await uow.ingest.events_between(
                conversation_id="wa-1", since=EPOCH, until=flow.cell.clock.now()
            )
        assert live == []
        assert {r["conversation_id"] for r in archived_rows(flow)} == {"wa-1"}

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    # The same result either way: only what the second conversation produced is left.
    assert await drawn(flow, handle) == {
        "facts": ["Bia"],
        "open_items": [],
        "values": [],
        "episodes": ["wa-2"],
    }
    assert (out.erased["facts"], out.erased["open_items"], out.erased["episodes"]) == (1, 1, 1)
    assert [r for r in archived_rows(flow) if r["conversation_id"] == "wa-1"] == []


async def test_an_erased_conversation_leaves_no_agent_note_naming_it(cell: Cell) -> None:
    # A conversation id is often the customer's phone number or e-mail.
    conversation = PHONE
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    first = said(conversation, "Me chama de Rafa", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended(conversation, now))).errors == []
    await flow.drain()
    draft = NoteDraft(kind=NoteKind.PROCEDURE, title="Second copy", body="Send it from the billing tool.")
    note = AgentNoteRecord(
        note_id=uuid7(),
        source_id=WHATSAPP_SOURCE,
        draft=draft,
        origin=NoteOrigin.AGENT,
        version=1,
        status=NoteStatus.ACTIVE,
        created_at=cell.clock.now(),
        created_by=f"source:{WHATSAPP_SOURCE}",
        lineage_id=uuid7(),
        evidence_conversation_id=conversation,
    )
    proposal = ProposalRecord(
        proposal_id=uuid7(),
        source_id=WHATSAPP_SOURCE,
        origin=NoteOrigin.AGENT,
        draft=draft,
        status=ProposalStatus.PENDING,
        created_at=cell.clock.now(),
        created_by=f"source:{WHATSAPP_SOURCE}",
        evidence_conversation_id=conversation,
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.agent_memory.add_note(note)
        await uow.agent_memory.add_proposal(proposal)

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id=conversation))

    async with cell.uow(SPACE_ID) as uow:
        kept = await uow.agent_memory.get_note(note.note_id)
        pending = await uow.agent_memory.get_proposal(proposal.proposal_id)
    assert kept is not None
    assert pending is not None
    # The note stays (it holds no customer), without the id that pointed at the conversation.
    assert (kept.draft, kept.status) == (draft, NoteStatus.ACTIVE)
    assert kept.evidence_conversation_id is None
    assert pending.evidence_conversation_id is None
    assert out.erased["agent_note_evidence"] == 2


async def test_the_resolution_of_an_erased_open_item_keeps_ids_and_kinds_only(flow: Flow) -> None:
    handle = await phone_handle(flow)
    async with flow.cell.uow(SPACE_ID) as uow:
        [item] = await uow.memory.open_items_for([handle], tuple(OpenItemStatus))
    feedback = FeedbackRequest(
        idempotency_key="fb-resolve",
        subject=Handle.model_validate({"type": "phone_e164", "value": PHONE}),
        action="resolve_open_item",
        open_item_id=str(item.item_id),
        reason=REASON,
    )
    config = flow.cell.config.space(SPACE_ID)
    assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), feedback)).accepted == 1
    await flow.drain()
    assert await resolutions(flow, handle) != []

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    assert (await drawn(flow, handle))["open_items"] == []
    [resolution] = await resolutions(flow, handle)
    assert set(resolution.fields) <= {"action", "open_item_id", "requested_by"}
    assert (resolution.fields["action"], resolution.fields["open_item_id"]) == (
        "resolve_open_item",
        str(item.item_id),
    )
    assert REASON not in str(resolution.fields)
    assert resolution.text is None
    assert out.erased["feedback_values"] == 1


async def resolutions(flow: Flow, handle: UUID) -> list[EventRecord]:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await uow.ingest.system_events_for_handles([handle], EPOCH, 100)
    return [e for e in found if e.canonical_type == "feedback.resolve_open_item"]
