"""The erasure cascade on PostgreSQL against what it used to leave (8.4): a conversation whose events all
left for the cold archive is still found and loses what those events produced, an agent note loses the
id (or alias) of the erased conversation, and the company's resolution of an erased open item keeps ids
and kinds only."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

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
from niadra.domain.extract.result import MemoryRef
from niadra.domain.ingest.events import ConversationTouch
from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.privacy.erasure import ErasureScope, ErasureStatus, TargetKind
from niadra.domain.records import OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, Speaker
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_erasure_feedback_pg import (
    NOW,
    SOURCE,
    _erase,
    _fact,
    _feedback,
    _said,
    _service,
)

PHONE_THREAD = "+5511955554444"
EMAIL_ALIAS = "rafa@example.com"
REASON = "a cliente pediu pelo telefone"


def _item(origin: UUID, event_id: UUID) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=origin,
        description="Enviar a segunda via",
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=NOW,
        origin={"event_ids": [str(event_id)]},
    )


def _value(origin: UUID, event_id: UUID, conversation: str) -> SettledValue:
    return SettledValue(
        value_id=uuid7(),
        origin_handle_id=origin,
        type=ValueType.POSTAL_CODE,
        raw="88221-615",
        said_at=NOW,
        speaker=Speaker.CUSTOMER,
        session_id=uuid7(),
        channel="whatsapp",
        source_id=SOURCE,
        created_at=NOW,
        evidence_event_ids=(event_id,),
        conversation_id=conversation,
    )


async def test_a_conversation_whose_events_are_all_archived_loses_what_they_produced(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, rafa = uuid4(), uuid7()
    said = _said(space, rafa, PHONE_THREAD)
    fact = _fact(rafa, "Rafa")
    item = _item(rafa, said.record.event_id)
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([said])
        await uow.ingest.upsert_conversation(
            space, ConversationTouch(PHONE_THREAD, "whatsapp", NOW, NOW, primary_handle=rafa)
        )
        assert await uow.memory.insert_fact(fact, normalize_value(fact.value))
        await uow.memory.add_mentions(MemoryRef(rafa, fact.fact_id), [said.record.event_id])
        assert await uow.memory.insert_open_item(item)
        assert await uow.memory.insert_values([_value(rafa, said.record.event_id, PHONE_THREAD)]) == 1
    service = _service(pg_uow, space)
    month = f"{NOW:%Y-%m}"
    await service.archive_events(Task(TaskName.ARCHIVE_EVENTS, space, {"month": month}))
    async with pg_uow(space) as uow:
        assert await uow.ingest.get_events([said.record.event_id]) == []

    # Before, `forget` answered 404: no live event named the conversation.
    accepted = await service.forget(
        space,
        ForgetRequest(target="conversation", conversation_id=PHONE_THREAD),
        idempotency_key="k1",
        requested_by="user:dpo",
    )
    request_id = UUID(accepted.request_id)
    await service.erase(Task(TaskName.ERASE, space, {"request_id": str(request_id)}))
    await service.erase_cold(Task(TaskName.ERASE_COLD, space, {"request_id": str(request_id)}))

    async with pg_uow(space) as uow:
        assert await uow.memory.facts_for([rafa], tuple(FactStatus)) == []
        assert await uow.memory.open_items_for([rafa], tuple(OpenItemStatus)) == []
        assert await uow.memory.values_for([rafa]) == []
        done = await uow.privacy.get_erasure(space, request_id)
    assert done is not None
    assert done.status is ErasureStatus.COMPLETED
    assert (done.erased["facts"], done.erased["open_items"], done.erased["settled_values"]) == (1, 1, 1)
    # The ids stay with the request, so a restored dump loses the same rows again.
    assert done.scope.archived_event_ids == (said.record.event_id,)
    assert done.counts["cold_rows"] == 1


async def test_notes_lose_the_conversation_and_the_resolution_keeps_ids_only(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, rafa = uuid4(), uuid7()
    said = _said(space, rafa, PHONE_THREAD)
    item = _item(rafa, said.record.event_id)
    fields = {"action": "resolve_open_item", "open_item_id": str(item.item_id), "reason": REASON}
    resolution = _feedback(space, rafa, fields)
    draft = NoteDraft(kind=NoteKind.PROCEDURE, title="Second copy", body="Send it from the billing tool.")

    def note(evidence: str) -> AgentNoteRecord:
        note_id = uuid7()
        return AgentNoteRecord(
            note_id,
            SOURCE,
            draft,
            NoteOrigin.AGENT,
            1,
            NoteStatus.ACTIVE,
            NOW,
            "agent",
            note_id,
            evidence_conversation_id=evidence,
        )

    by_id, by_alias, other = note(PHONE_THREAD), note(EMAIL_ALIAS), note("wa-other")
    proposal = ProposalRecord(
        uuid7(),
        SOURCE,
        NoteOrigin.AGENT,
        draft,
        ProposalStatus.PENDING,
        NOW,
        "agent",
        evidence_conversation_id=PHONE_THREAD,
    )
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([said, resolution])
        await uow.ingest.upsert_conversation(
            space,
            ConversationTouch(
                PHONE_THREAD, "whatsapp", NOW, NOW, aliases=frozenset({EMAIL_ALIAS}), primary_handle=rafa
            ),
        )
        assert await uow.memory.insert_open_item(item)
        resolved = replace(item, status=OpenItemStatus.RESOLVED, closed_at=NOW + timedelta(minutes=1))
        await uow.memory.update_open_item(resolved, closed_by_event=resolution.record.event_id)
        for row in (by_id, by_alias, other):
            await uow.agent_memory.add_note(row)
        await uow.agent_memory.add_proposal(proposal)
        seq_before = int(
            (
                await uow.session.execute(
                    text("SELECT change_seq FROM agent_notes WHERE note_id = :n"), {"n": by_id.note_id}
                )
            ).scalar_one()
        )

    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (rafa,), None, PHONE_THREAD, NOW)
    done = await _erase(pg_uow, space, scope)

    async with pg_uow(space) as uow:
        assert await uow.memory.open_items_for([rafa], tuple(OpenItemStatus)) == []
        [kept] = await uow.ingest.get_events([resolution.record.event_id])
        notes = {n.note_id: n for n in await uow.agent_memory.active_notes()}
        pending = await uow.agent_memory.get_proposal(proposal.proposal_id)
        seq_after = int(
            (
                await uow.session.execute(
                    text("SELECT change_seq FROM agent_notes WHERE note_id = :n"), {"n": by_id.note_id}
                )
            ).scalar_one()
        )
    assert kept.fields == {"action": "resolve_open_item", "open_item_id": str(item.item_id)}
    assert kept.text is None
    assert notes[by_id.note_id].evidence_conversation_id is None
    assert notes[by_alias.note_id].evidence_conversation_id is None
    assert notes[other.note_id].evidence_conversation_id == "wa-other"
    assert pending is not None
    assert pending.evidence_conversation_id is None
    # The export sends the note again, without the id.
    assert seq_after > seq_before
    assert (done.erased["feedback_values"], done.erased["agent_note_evidence"]) == (1, 3)
