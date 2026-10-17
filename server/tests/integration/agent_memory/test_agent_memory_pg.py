"""Agent memory over PostgreSQL: sealed notes in versions, proposals, erasure with tombstones and the
continuous export's rows (10, C)."""

from dataclasses import replace
from datetime import UTC, datetime

from niadra.adapters.memory.fakes import DeterministicModels, FixedClock, ScriptedLlm, StaticConfig
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.agent_memory import AgentMemoryService
from niadra.config.models import AgentMemorySettings
from niadra.contracts.agent_memory import CreateAgentNoteRequest, UpdateAgentNoteRequest
from niadra.domain.agent_memory.model import NoteDraft, NoteKind, NoteOrigin, ProposalRecord, ProposalStatus
from niadra.domain.common.ids import uuid7
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.latency import within
from tests.unit.audit.fakes import RecordingAdminAudit

T0 = datetime(2026, 9, 24, 21, tzinfo=UTC)
NOTE = CreateAgentNoteRequest(
    kind="procedure",
    title="Credit shows after a refresh",
    body="Call post_credit, then refresh_invoice.",
    tags=["credit", "invoice"],
)


def _service(pg_uow: PgUnitOfWorkFactory, space_id):  # type: ignore[no-untyped-def]
    config = sample_config(space_id).model_copy(update={"agent_memory": AgentMemorySettings(enabled=True)})
    service = AgentMemoryService(
        pg_uow,
        StaticConfig(config),
        FixedClock(T0),
        DeterministicModels(),
        RecordingAdminAudit(),  # type: ignore[arg-type]
        LocalKeyVault(b"k" * 32),
        llm=ScriptedLlm(),
    )
    return service, principal(WHATSAPP_SOURCE, config)


async def test_notes_round_trip_sealed_with_versions_and_erasure(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    service, agent = _service(pg_uow, space)
    first = (await service.create(agent, NOTE)).note
    assert first is not None
    second = await service.update(agent, first.note_id, UpdateAgentNoteRequest(body="post_credit, refresh"))
    block = await service.block(agent)
    assert block.notes == [second.note_id]
    assert "post_credit, refresh" in block.text
    versions = await service.versions(agent, second.note_id)
    assert [(v.version, v.status) for v in versions] == [(1, "retired"), (2, "active")]
    async with pg_uow(space) as uow:
        assert await uow.agent_memory.count_active(WHATSAPP_SOURCE) == 1
        assert await uow.agent_memory.writes_since(WHATSAPP_SOURCE, T0) == 2
        rows = await uow.agent_memory.export_rows("agent_notes", after_seq=0, upto_seq=10**12, limit=10)
    # The body is sealed at rest and opened for the export; the update of v1 bumped its sequence.
    assert {r["title"] for r in rows} == {NOTE.title}
    assert sorted(r["version"] for r in rows) == [1, 2]
    assert rows[-1]["change_seq"] > rows[0]["change_seq"]
    erased = await service.erase_source(agent, WHATSAPP_SOURCE)
    assert erased.deleted == 2
    async with pg_uow(space) as uow:
        assert await uow.agent_memory.notes_of_source(WHATSAPP_SOURCE) == []
        tombstones = await uow.privacy.tombstones_between(space, 0, None)
    assert sorted(t.key["note_id"] for t in tombstones) == sorted([str(first.note_id), str(second.note_id)])
    async with pg_uow(uuid7()) as other:
        assert await other.agent_memory.active_notes() == []


async def test_proposals_move_from_drafting_to_pending_to_approved(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    proposal = ProposalRecord(
        proposal_id=uuid7(),
        source_id=WHATSAPP_SOURCE,
        origin=NoteOrigin.DISTILLED,
        draft=NoteDraft(kind=NoteKind.PROCEDURE, title="", body=""),
        status=ProposalStatus.DRAFTING,
        created_at=T0,
        created_by="user:x",
        evidence_conversation_id="wa-1",
    )
    ready = replace(
        proposal,
        draft=NoteDraft(
            kind=NoteKind.PITFALL, title="Refresh", body="Refresh the invoice", tags=("invoice",)
        ),
        status=ProposalStatus.PENDING,
    )
    async with pg_uow(space) as uow:
        await uow.agent_memory.add_proposal(proposal)
        assert await uow.agent_memory.save_draft(ready)
        assert not await uow.agent_memory.save_draft(ready)
    decided = replace(
        ready, status=ProposalStatus.APPROVED, decided_at=T0, decided_by="user:y", note_id=uuid7()
    )
    async with pg_uow(space) as uow:
        assert await uow.agent_memory.decide_proposal(decided)
        assert not await uow.agent_memory.decide_proposal(decided)
        assert await uow.agent_memory.get_proposal(proposal.proposal_id) == decided
        listed = await uow.agent_memory.list_proposals(
            source_id=WHATSAPP_SOURCE, status=ProposalStatus.APPROVED, before_id=None, limit=5
        )
    assert listed == [decided]


async def test_a_block_read_runs_one_statement_whatever_the_notes(pg_uow: PgUnitOfWorkFactory) -> None:
    """The block an agent reads before each conversation (09/10/2026, two reads past the client's 0.6 s):
    the notes in one statement, opened in memory; its receipt never waits past a read's budget
    (`AdminAudit.READ_EMIT_TIMEOUT_S`)."""
    import asyncio
    import time

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    space = uuid7()
    service, agent = _service(pg_uow, space)
    for n in range(20):  # the writes an agent may make in an hour
        await service.create(
            agent, NOTE.model_copy(update={"title": f"Procedure {n}", "body": f"Step {n}: refresh."})
        )
    ran: list[str] = []

    def count(conn: object, cursor: object, statement: str, *args: object) -> None:
        if asyncio.current_task() is mine:
            ran.append(statement)

    mine = asyncio.current_task()
    event.listen(Engine, "before_cursor_execute", count)
    try:
        started = time.perf_counter()
        block = await service.block(agent, max_tokens=2000)
        took = time.perf_counter() - started
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    assert block.notes
    assert len([s for s in ran if not s.startswith("SELECT set_config")]) == 1, ran
    within("an agent's block of notes", took, 0.05)
    print(f"block of {len(block.notes)} notes: {len(ran)} statements, {took * 1000:.1f} ms")
