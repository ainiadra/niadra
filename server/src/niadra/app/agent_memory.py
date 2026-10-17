"""Agent memory (10, C): the agents' own working notes, never about a person.

A note is refused, never masked, when it carries personal data: the models server's detectors and a local
layer both look at every write, and a person's edit of a proposal passes them again. Reads leave an
`agent_memory` receipt, writes an `admin` one, on the governance recorder's chain.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.app.admin_audit import AdminAudit
from niadra.app.notify import enqueue_event
from niadra.config.models import SpaceConfig
from niadra.contracts.agent_memory import (
    AgentMemoryBlock,
    AgentMemoryExport,
    AgentMemorySearchRequest,
    AgentMemorySearchResponse,
    AgentNote,
    AgentNotePage,
    AgentNoteProposal,
    AgentNoteProposalPage,
    AgentNoteStatus,
    AgentNoteVisibility,
    ApproveProposalRequest,
    CreateAgentNoteRequest,
    DistillRequest,
    ErasedAgentNotes,
    Evidence,
    RememberResult,
    UpdateAgentNoteRequest,
)
from niadra.contracts.agent_memory import ProposalStatus as ProposalStatusOut
from niadra.contracts.context import ToolDefinition
from niadra.domain.agent_memory.distill import (
    ANSWER_SCHEMA,
    MERGE_SYSTEM,
    SYSTEM,
    carries_placeholder,
    merge_input,
    read_answer,
    transcript_windows,
)
from niadra.domain.agent_memory.model import (
    AgentNoteRecord,
    NoteDraft,
    NoteKind,
    NoteOrigin,
    NoteStatus,
    NoteVisibility,
    ProposalRecord,
    ProposalStatus,
)
from niadra.domain.agent_memory.rules import (
    WRITE_WINDOW,
    WRITES_PER_HOUR,
    Reader,
    may_read,
    personal_kinds,
    render_block,
    search,
    still_valid,
)
from niadra.domain.common.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    PersonalDataInAgentMemory,
    RateLimited,
    ScopeMissing,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.compile.templates import Formatter
from niadra.domain.configure.masking import mask_text
from niadra.domain.extract.redaction import Redactor
from niadra.domain.identity.provenance import only_unconfirmed
from niadra.domain.privacy.personal import local_personal_kinds
from niadra.domain.serve.tools import agent_memory_tools
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, ReceiptKind, ReceiptSurface, Scope
from niadra.ports.infra import Clock, ConfigProvider, KeyVault, LlmClient, ModelsClient, Reasoning
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

PII_TIMEOUT_S = 3.0
REASONING: Reasoning = "low"
"""The extraction model's effort for a note distilled from one conversation: the shape of an extraction, at
the effort measured for it (`app/extract.py`)."""
DISTILL_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
"""The whole conversation is read: memory never cuts what it was told."""
PAGE_SCAN = 500
"""Notes a listing reads per statement before filtering by what the reader may see."""
PAGE_SCANS = 20
"""Statements one page may spend reading: 10,000 notes, past which the page ends with a cursor."""


class AgentMemoryService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        config: ConfigProvider,
        clock: Clock,
        models: ModelsClient,
        audit: AdminAudit,
        keys: KeyVault,
        llm: LlmClient | None = None,
    ) -> None:
        self._uow = uow
        self._config = config
        self._clock = clock
        self._models = models
        self._audit = audit
        self._keys = keys
        self._llm = llm

    # Reads.

    async def block(
        self,
        caller: Principal,
        *,
        max_tokens: int = 300,
        tags: Sequence[str] = (),
        view: str | None = None,
        source_id: UUID | None = None,
    ) -> AgentMemoryBlock:
        _require_read(caller)
        config = self._config.space(caller.space.space_id)
        reader = self._reader(caller, config, source_id)
        if not config.agent_memory.enabled:
            return AgentMemoryBlock(text="", etag="am-off", enabled=False)
        wanted = {*tags, *_view_tags(config, view)}
        notes = await self._visible(caller, config, reader)
        block = render_block(notes, language=_language(config), max_tokens=max_tokens, wanted=wanted)
        await self._audit.record_read(
            caller,
            kind=ReceiptKind.AGENT_MEMORY,
            surface=ReceiptSurface.AGENT_BLOCK,
            item_ids=tuple(f"agent_note:{i}" for i in block.note_ids),
        )
        return AgentMemoryBlock(
            text=block.text,
            notes=list(block.note_ids),
            etag=block.etag,
            tokens=block.tokens,
            left_out=block.left_out,
        )

    async def search(self, caller: Principal, request: AgentMemorySearchRequest) -> AgentMemorySearchResponse:
        _require_read(caller)
        config = self._config.space(caller.space.space_id)
        reader = self._reader(caller, config, None)
        if not config.agent_memory.enabled:
            return AgentMemorySearchResponse(notes=[])
        notes = await self._visible(caller, config, reader)
        found = search(notes, request.query, request.tags, request.limit)
        query_hash = await self._keys.hmac(
            caller.space.space_id, "agent_memory_query", request.query.encode()
        )
        await self._audit.record_read(
            caller,
            kind=ReceiptKind.AGENT_MEMORY,
            surface=ReceiptSurface.AGENT_SEARCH,
            item_ids=tuple(f"agent_note:{n.note_id}" for n in found),
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            query_hash=query_hash,
        )
        return AgentMemorySearchResponse(notes=[_note_out(n) for n in found])

    def tools_definitions(self, caller: Principal) -> list[ToolDefinition]:
        _require_read(caller)
        can_write = not isinstance(caller, SourcePrincipal) or caller.allows(Scope.AGENT_MEMORY_WRITE)
        return [ToolDefinition(function=t.as_function()) for t in agent_memory_tools(can_write)]

    async def list_notes(
        self,
        caller: Principal,
        *,
        source_id: UUID | None,
        status: AgentNoteStatus | None,
        visibility: AgentNoteVisibility | None,
        cursor: str | None,
        limit: int,
    ) -> AgentNotePage:
        _require_read(caller)
        config = self._config.space(caller.space.space_id)
        reader = self._reader(caller, config, None)
        if source_id is not None:
            self._check_source(caller, config, source_id, own_only=False)
        vendors = _vendors(config)

        def visible(n: AgentNoteRecord) -> bool:
            return (
                may_read(n, reader, vendors)
                and self._person_sees(caller, n.source_id)
                and (visibility is None or n.draft.visibility.value == visibility.value)
            )

        # The notes are read in batches until the page and one more are visible, so a reader who sees few
        # of the space's notes still gets every one of them; past `PAGE_SCANS` batches the
        # page ends where the reading stopped and its cursor goes on from there, never a silent end.
        shown: list[AgentNoteRecord] = []
        before = _cursor(cursor)
        stopped: UUID | None = None
        async with self._uow(caller.space.space_id) as uow:
            for _ in range(PAGE_SCANS):
                rows = await uow.agent_memory.list_notes(
                    source_id=source_id,
                    status=NoteStatus(status.value) if status else None,
                    before_id=before,
                    limit=PAGE_SCAN,
                )
                shown += [n for n in rows if visible(n)]
                if len(shown) > limit or len(rows) < PAGE_SCAN:
                    break
                before = rows[-1].note_id
            else:
                stopped = before
        page = shown[:limit]
        if len(shown) > limit:
            next_cursor: str | None = str(page[-1].note_id)
        elif stopped is not None:
            next_cursor = str(stopped)
        else:
            next_cursor = None
        return AgentNotePage(items=[_note_out(n) for n in page], next_cursor=next_cursor)

    async def get(self, caller: Principal, note_id: UUID) -> AgentNote:
        _require_read(caller)
        note = await self._readable(caller, note_id)
        return _note_out(note)

    async def versions(self, caller: Principal, note_id: UUID) -> list[AgentNote]:
        _require_read(caller)
        note = await self._readable(caller, note_id)
        async with self._uow(caller.space.space_id) as uow:
            lineage = await uow.agent_memory.lineage(note.lineage_id)
        return [_note_out(n) for n in lineage]

    async def export_source(self, caller: Principal, source_id: UUID) -> AgentMemoryExport:
        _require_read(caller)
        config = self._config.space(caller.space.space_id)
        self._check_source(caller, config, source_id, own_only=True)
        async with self._uow(caller.space.space_id) as uow:
            notes = await uow.agent_memory.notes_of_source(source_id)
        await self._audit.record(caller, action="agent_memory.export", target=f"source:{source_id}")
        return AgentMemoryExport(
            source_id=source_id, exported_at=self._clock.now(), notes=[_note_out(n) for n in notes]
        )

    # Writes.

    async def create(self, caller: Principal, request: CreateAgentNoteRequest) -> RememberResult:
        _require_write(caller)
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        owner = self._owner(caller, config, request.source_id)
        draft = NoteDraft(
            kind=NoteKind(request.kind.value),
            title=request.title,
            body=request.body,
            tags=tuple(dict.fromkeys(request.tags)),
            visibility=NoteVisibility(request.visibility.value),
            valid_until=request.valid_until,
        )
        await self._refuse_personal(draft)
        now = self._clock.now()
        evidence = request.evidence
        by = _actor(caller)
        agent_write = isinstance(caller, SourcePrincipal)
        if agent_write and config.agent_memory.writes == "human_only":
            proposal = ProposalRecord(
                proposal_id=uuid7(),
                source_id=owner,
                origin=NoteOrigin.AGENT,
                draft=draft,
                status=ProposalStatus.PENDING,
                created_at=now,
                created_by=by,
                evidence_conversation_id=evidence.conversation_id if evidence else None,
                evidence_task_id=evidence.task_id if evidence else None,
            )
            async with self._uow(space_id) as uow:
                await self._check_rate(uow, caller, owner, now)
                await uow.agent_memory.add_proposal(proposal)
                await _announce(uow, config, proposal=proposal)
            await self._audit.record(
                caller,
                action="agent_memory.proposal_created",
                target=f"agent_note_proposal:{proposal.proposal_id}",
            )
            return RememberResult(proposal_id=proposal.proposal_id)
        note_id = uuid7()
        note = AgentNoteRecord(
            note_id=note_id,
            source_id=owner,
            draft=draft,
            origin=NoteOrigin.AGENT if agent_write else NoteOrigin.HUMAN,
            version=1,
            status=NoteStatus.ACTIVE,
            created_at=now,
            created_by=by,
            lineage_id=note_id,
            evidence_conversation_id=evidence.conversation_id if evidence else None,
            evidence_task_id=evidence.task_id if evidence else None,
        )
        async with self._uow(space_id) as uow:
            await self._check_rate(uow, caller, owner, now)
            await uow.agent_memory.add_note(note)
            await _announce(uow, config, note=note)
        await self._audit.record(caller, action="agent_memory.note_created", target=f"agent_note:{note_id}")
        return RememberResult(note=_note_out(note))

    async def update(self, caller: Principal, note_id: UUID, request: UpdateAgentNoteRequest) -> AgentNote:
        _require_write(caller)
        space_id = caller.space.space_id
        current = await self._writable(caller, note_id)
        changes: dict[str, Any] = {}
        for name in request.model_fields_set:
            value = getattr(request, name)
            if name == "kind":
                value = NoteKind(value.value)
            elif name == "visibility":
                value = NoteVisibility(value.value)
            elif name == "tags":
                value = tuple(dict.fromkeys(value))
            changes[name] = value
        draft = replace(current.draft, **changes)
        await self._refuse_personal(draft)
        now = self._clock.now()
        new = replace(
            current,
            note_id=uuid7(),
            draft=draft,
            origin=NoteOrigin.AGENT if isinstance(caller, SourcePrincipal) else NoteOrigin.HUMAN,
            version=current.version + 1,
            supersedes_note_id=current.note_id,
            status=NoteStatus.ACTIVE,
            created_at=now,
            created_by=_actor(caller),
            retired_at=None,
        )
        async with self._uow(space_id) as uow:
            await self._check_rate(uow, caller, current.source_id, now)
            if not await uow.agent_memory.retire_note(current.note_id, now):
                raise Conflict("the note changed meanwhile: read it again")
            await uow.agent_memory.add_note(new)
        await self._audit.record(
            caller, action="agent_memory.note_revised", target=f"agent_note:{new.note_id}"
        )
        return _note_out(new)

    async def retire(self, caller: Principal, note_id: UUID) -> AgentNote:
        _require_write(caller)
        current = await self._writable(caller, note_id)
        now = self._clock.now()
        async with self._uow(caller.space.space_id) as uow:
            if not await uow.agent_memory.retire_note(current.note_id, now):
                raise Conflict("the note is no longer active")
        await self._audit.record(caller, action="agent_memory.note_retired", target=f"agent_note:{note_id}")
        return _note_out(replace(current, status=NoteStatus.RETIRED, retired_at=now))

    async def erase_source(self, caller: Principal, source_id: UUID) -> ErasedAgentNotes:
        """Every note and proposal of one agent, every version; the continuous export gets tombstones."""
        space_id = caller.space.space_id
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            erased = await uow.agent_memory.delete_source(source_id)
            if erased:
                await uow.privacy.add_tombstones(space_id, erased, now)
        await self._audit.record(caller, action="agent_memory.erase", target=f"source:{source_id}")
        return ErasedAgentNotes(source_id=source_id, deleted=len(erased))

    # Proposals and distillation.

    async def distill(self, caller: Principal, request: DistillRequest) -> AgentNoteProposal:
        _require_write(caller)
        if self._llm is None:
            raise Unavailable("distillation runs in the workers only")
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        self._check_source(caller, config, request.source_id, own_only=True)
        now = self._clock.now()
        proposal = ProposalRecord(
            proposal_id=uuid7(),
            source_id=request.source_id,
            origin=NoteOrigin.DISTILLED,
            draft=NoteDraft(kind=NoteKind.PROCEDURE, title="", body=""),
            status=ProposalStatus.DRAFTING,
            created_at=now,
            created_by=_actor(caller),
            evidence_conversation_id=request.conversation_id,
            evidence_task_id=request.task_id,
        )
        async with self._uow(space_id) as uow:
            await self._check_rate(uow, caller, request.source_id, now)
            await uow.agent_memory.add_proposal(proposal)
            await uow.pipeline.enqueue(
                [Task(TaskName.DISTILL_NOTE, space_id, {"proposal_id": str(proposal.proposal_id)})]
            )
        await self._audit.record(
            caller, action="agent_memory.distill", target=f"agent_note_proposal:{proposal.proposal_id}"
        )
        return _proposal_out(proposal)

    async def distill_task(self, task: Task) -> None:
        """The worker side: masked turns in, one proposed note (or a reason why not) out. Runs the LLM call
        outside any transaction (L3)."""
        assert self._llm is not None
        space_id = task.space_id
        proposal_id = UUID(task.payload["proposal_id"])
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            proposal = await uow.agent_memory.get_proposal(proposal_id)
            if proposal is None or proposal.status is not ProposalStatus.DRAFTING:
                return
            erased = proposal.evidence_conversation_id is None and proposal.evidence_task_id is None
            # An erasure took the conversation while the proposal waited: never read the whole log instead.
            events = (
                []
                if erased
                else await uow.ingest.events_between(
                    conversation_id=proposal.evidence_conversation_id,
                    task_id=proposal.evidence_task_id,
                    since=DISTILL_SINCE,
                    until=now,
                )
            )
            # Only what the agent's own source sent: a conversation id is not tied to a source, and another
            # agent's conversation (another vendor's, or one the policy keeps from this agent) must never
            # reach the prompt, nor come back to this agent as a proposal it reads (10, C.5).
            events = [e for e in events if e.source_id == proposal.source_id]
            named = sorted({h for e in events for h in e.handle_ids})
            provisional = [h.handle_id for h in await uow.identity.get_handles(named) if h.provisional]
        if only_unconfirmed(named, provisional):
            # A conversation that spoke only through a link a claim made and nobody confirmed teaches no
            # note until the link is confirmed (5.3): the agent asks again then.
            await self._fail(space_id, proposal, "unconfirmed_link")
            return
        lines = [
            f"{e.speaker.value}: {e.text}"
            for e in events
            if e.text and e.kind in (EventKind.MESSAGE, EventKind.ACTION, EventKind.SYSTEM_EVENT)
        ]
        if not lines:
            await self._fail(space_id, proposal, "no_turns")
            return
        masked = await self._masked(lines)
        drafts = [
            draft
            for window in transcript_windows(masked)
            if (draft := await self._propose(space_id, SYSTEM, window)) is not None
        ]
        draft = drafts[0] if len(drafts) == 1 else None
        if len(drafts) > 1:
            # One note per conversation: the windows' proposals put together, the last one's if that fails.
            draft = await self._propose(space_id, MERGE_SYSTEM, merge_input(drafts)) or drafts[-1]
        if draft is None:
            await self._fail(space_id, proposal, "nothing_to_propose")
            return
        if carries_placeholder(draft) or await self._personal(draft):
            await self._fail(space_id, proposal, "personal_data")
            return
        ready = replace(proposal, draft=draft, status=ProposalStatus.PENDING)
        config = self._config.space(space_id)
        async with self._uow(space_id) as uow:
            if await uow.agent_memory.save_draft(ready):
                await _announce(uow, config, proposal=ready)

    async def _propose(self, space_id: UUID, system: str, user: str) -> NoteDraft | None:
        """One distillation call: the model's note, or None when it proposed nothing usable."""
        assert self._llm is not None
        result = await self._llm.complete_json(
            system=system,
            user=user,
            json_schema=ANSWER_SCHEMA,
            reasoning=REASONING,
            max_output_tokens=800,
            space_id=space_id,
            purpose="agent_memory",
        )
        try:
            answer = json.loads(result.content)
        except ValueError:
            answer = {}
        return read_answer(answer if isinstance(answer, dict) else {})

    async def list_proposals(
        self,
        caller: Principal,
        *,
        source_id: UUID | None,
        status: ProposalStatusOut | None,
        cursor: str | None,
        limit: int,
    ) -> AgentNoteProposalPage:
        _require_read(caller)
        config = self._config.space(caller.space.space_id)
        if isinstance(caller, SourcePrincipal):
            source_id = caller.source_id  # an agent sees only its own proposals
        elif source_id is not None:
            self._check_source(caller, config, source_id, own_only=False)
        async with self._uow(caller.space.space_id) as uow:
            rows = await uow.agent_memory.list_proposals(
                source_id=source_id,
                status=ProposalStatus(status.value) if status else None,
                before_id=_cursor(cursor),
                limit=limit + 1,
            )
        rows = [p for p in rows if self._person_sees(caller, p.source_id)]
        page = rows[:limit]
        more = len(rows) > limit
        return AgentNoteProposalPage(
            items=[_proposal_out(p) for p in page], next_cursor=str(page[-1].proposal_id) if more else None
        )

    async def approve(self, caller: Principal, proposal_id: UUID, edits: ApproveProposalRequest) -> AgentNote:
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        async with self._uow(space_id) as uow:
            proposal = await uow.agent_memory.get_proposal(proposal_id)
        if proposal is None or not self._person_sees(caller, proposal.source_id):
            raise NotFound("proposal not found")
        if proposal.status is not ProposalStatus.PENDING:
            raise Conflict(f"the proposal is {proposal.status.value}")
        changes: dict[str, Any] = {}
        for name in edits.model_fields_set:
            value = getattr(edits, name)
            if value is None:
                continue
            if name == "kind":
                value = NoteKind(value.value)
            elif name == "visibility":
                value = NoteVisibility(value.value)
            elif name == "tags":
                value = tuple(dict.fromkeys(value))
            changes[name] = value
        draft = replace(proposal.draft, **changes)
        await self._refuse_personal(draft)
        now = self._clock.now()
        note_id = uuid7()
        note = AgentNoteRecord(
            note_id=note_id,
            source_id=proposal.source_id,
            draft=draft,
            origin=proposal.origin,
            version=1,
            status=NoteStatus.ACTIVE,
            created_at=now,
            created_by=_actor(caller),
            lineage_id=note_id,
            evidence_conversation_id=proposal.evidence_conversation_id,
            evidence_task_id=proposal.evidence_task_id,
        )
        decided = replace(
            proposal,
            status=ProposalStatus.APPROVED,
            decided_at=now,
            decided_by=_actor(caller),
            note_id=note_id,
        )
        async with self._uow(space_id) as uow:
            if not await uow.agent_memory.decide_proposal(decided):
                raise Conflict("the proposal was decided meanwhile")
            await uow.agent_memory.add_note(note)
            await _announce(uow, config, note=note)
        await self._audit.record(
            caller, action="agent_memory.proposal_approved", target=f"agent_note_proposal:{proposal_id}"
        )
        return _note_out(note)

    async def reject(self, caller: Principal, proposal_id: UUID) -> AgentNoteProposal:
        space_id = caller.space.space_id
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            proposal = await uow.agent_memory.get_proposal(proposal_id)
            if proposal is None or not self._person_sees(caller, proposal.source_id):
                raise NotFound("proposal not found")
            decided = replace(
                proposal, status=ProposalStatus.REJECTED, decided_at=now, decided_by=_actor(caller)
            )
            if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.DRAFTING) or not (
                await uow.agent_memory.decide_proposal(decided)
            ):
                raise Conflict(f"the proposal is {proposal.status.value}")
        await self._audit.record(
            caller, action="agent_memory.proposal_rejected", target=f"agent_note_proposal:{proposal_id}"
        )
        return _proposal_out(decided)

    # Internals.

    def _reader(self, caller: Principal, config: SpaceConfig, source_id: UUID | None) -> Reader:
        if isinstance(caller, SourcePrincipal):
            return Reader(caller.source_id, caller.vendor)
        if source_id is None:
            return Reader(None, None, person=True)
        self._check_source(caller, config, source_id, own_only=False)
        source = config.source(source_id)
        return Reader(source_id, source.vendor if source else None)

    async def _visible(self, caller: Principal, config: SpaceConfig, reader: Reader) -> list[AgentNoteRecord]:
        async with self._uow(caller.space.space_id) as uow:
            notes = await uow.agent_memory.active_notes()
        vendors = _vendors(config)
        return [
            n
            for n in still_valid(notes, self._clock.now())
            if may_read(n, reader, vendors) and self._person_sees(caller, n.source_id)
        ]

    async def _readable(self, caller: Principal, note_id: UUID) -> AgentNoteRecord:
        config = self._config.space(caller.space.space_id)
        async with self._uow(caller.space.space_id) as uow:
            note = await uow.agent_memory.get_note(note_id)
        reader = self._reader(caller, config, None)
        # A note the caller may not read and a missing one look the same (L7).
        if (
            note is None
            or not may_read(note, reader, _vendors(config))
            or not self._person_sees(caller, note.source_id)
        ):
            raise NotFound("note not found")
        return note

    async def _writable(self, caller: Principal, note_id: UUID) -> AgentNoteRecord:
        note = await self._readable(caller, note_id)
        if isinstance(caller, SourcePrincipal) and note.source_id != caller.source_id:
            raise Forbidden("an agent changes only its own notes")
        if note.status is not NoteStatus.ACTIVE:
            raise Conflict("a retired version cannot change; revise the current one")
        return note

    def _owner(self, caller: Principal, config: SpaceConfig, source_id: UUID | None) -> UUID:
        if isinstance(caller, SourcePrincipal):
            if not config.agent_memory.enabled:
                raise Forbidden("agent memory is off in this space")
            if source_id is not None and source_id != caller.source_id:
                raise Forbidden("an agent writes only its own notes")
            return caller.source_id
        if source_id is None:
            raise InvalidInput("name the agent: `source_id`")
        self._check_source(caller, config, source_id, own_only=False)
        return source_id

    def _check_source(
        self, caller: Principal, config: SpaceConfig, source_id: UUID, *, own_only: bool
    ) -> None:
        if config.source(source_id) is None:
            raise NotFound("source not found")
        if isinstance(caller, SourcePrincipal):
            if own_only and source_id != caller.source_id:
                raise Forbidden("an agent reaches only its own notes")
        elif not self._person_sees(caller, source_id):
            raise NotFound("source not found")

    @staticmethod
    def _person_sees(caller: Principal, source_id: UUID) -> bool:
        """Vendor people see only the sources their binding names (5.22)."""
        if isinstance(caller, PersonPrincipal) and caller.allowed_sources is not None:
            return source_id in caller.allowed_sources
        return True

    async def _check_rate(self, uow: Any, caller: Principal, owner: UUID, now: datetime) -> None:
        if not isinstance(caller, SourcePrincipal):
            return
        if await uow.agent_memory.writes_since(owner, now - WRITE_WINDOW) >= WRITES_PER_HOUR:
            raise RateLimited(int(WRITE_WINDOW.total_seconds() // 4), "20 agent memory writes per hour")

    async def _refuse_personal(self, draft: NoteDraft) -> None:
        if found := await self._personal(draft):
            # Kinds only: echoing the value would put the personal data in a log or an answer (7.0).
            raise PersonalDataInAgentMemory(
                "agent notes never hold personal data (found: " + ", ".join(found) + "); rewrite it "
                "so it helps with any customer"
            )

    async def _personal(self, draft: NoteDraft) -> list[str]:
        texts = [draft.title, draft.body, " ".join(draft.tags)]
        local = local_personal_kinds(texts)
        try:
            async with asyncio.timeout(PII_TIMEOUT_S):
                spans = await self._models.pii(texts)
        except Exception:
            # Fail closed: without the detectors nothing proves the note is clean.
            log.warning("the PII detector is unavailable; agent memory write refused for now")
            raise Unavailable("the personal data check is unavailable", retry_after_s=5) from None
        detected = personal_kinds({s.kind for found in spans for s in found})
        return sorted({*local, *detected})

    async def _masked(self, lines: list[str]) -> list[str]:
        try:
            spans = await self._models.pii(lines)
        except Exception:
            raise Unavailable("the personal data check is unavailable", retry_after_s=30) from None
        redactor = Redactor()
        return [mask_text(redactor.redact(line, found)) for line, found in zip(lines, spans, strict=True)]

    async def _fail(self, space_id: UUID, proposal: ProposalRecord, problem: str) -> None:
        failed = replace(
            proposal, status=ProposalStatus.FAILED, problem=problem, decided_at=self._clock.now()
        )
        async with self._uow(space_id) as uow:
            await uow.agent_memory.decide_proposal(failed)


def _require_read(caller: Principal) -> None:
    if isinstance(caller, SourcePrincipal) and not (
        caller.allows(Scope.AGENT_MEMORY) or caller.allows(Scope.CONTEXT)
    ):
        raise ScopeMissing("the key lacks the scope agent_memory")


def _require_write(caller: Principal) -> None:
    if isinstance(caller, SourcePrincipal) and not caller.allows(Scope.AGENT_MEMORY_WRITE):
        raise ScopeMissing("the key lacks the scope agent_memory:write")


def _actor(caller: Principal) -> str:
    return f"source:{caller.source_id}" if isinstance(caller, SourcePrincipal) else f"user:{caller.user_id}"


def _vendors(config: SpaceConfig) -> dict[UUID, str | None]:
    return {s.source_id: s.vendor for s in config.sources}


def _language(config: SpaceConfig) -> str:
    return Formatter(config.settings.timezone, config.settings.locale).lang


def _view_tags(config: SpaceConfig, view: str | None) -> set[str]:
    if not view:
        return set()
    task = next((v for v in config.views if v.name == view), None)
    return {view, *(task.object_types if task else ())}


def _cursor(cursor: str | None) -> UUID | None:
    if not cursor:
        return None
    try:
        return UUID(cursor)
    except ValueError:
        raise InvalidInput("unknown cursor") from None


async def _announce(
    uow: Any,
    config: SpaceConfig,
    *,
    note: AgentNoteRecord | None = None,
    proposal: ProposalRecord | None = None,
) -> None:
    """`agent_memory.note_created` for endpoints that review agent notes: ids and kind, never the text."""
    if note is not None:
        data = {
            "note_id": str(note.note_id),
            "source_id": str(note.source_id),
            "kind": note.draft.kind.value,
            "origin": note.origin.value,
            "version": note.version,
            "status": "active",
        }
    else:
        assert proposal is not None
        data = {
            "proposal_id": str(proposal.proposal_id),
            "source_id": str(proposal.source_id),
            "kind": proposal.draft.kind.value,
            "origin": proposal.origin.value,
            "status": "pending",
        }
    await enqueue_event(uow, config, "agent_memory.note_created", None, data)


def _evidence(conversation_id: str | None, task_id: str | None) -> Evidence | None:
    if conversation_id is None and task_id is None:
        return None
    return Evidence(conversation_id=conversation_id, task_id=None if conversation_id else task_id)


def _note_out(note: AgentNoteRecord) -> AgentNote:
    return AgentNote(
        note_id=note.note_id,
        source_id=note.source_id,
        visibility=AgentNoteVisibility(note.draft.visibility.value),
        kind=note.draft.kind.value,
        title=note.draft.title,
        body=note.draft.body,
        tags=list(note.draft.tags),
        evidence=_evidence(note.evidence_conversation_id, note.evidence_task_id),
        origin=note.origin.value,
        version=note.version,
        supersedes_note_id=note.supersedes_note_id,
        status=AgentNoteStatus(note.status.value),
        valid_until=note.draft.valid_until,
        created_at=note.created_at,
        created_by=note.created_by,
    )


def _proposal_out(proposal: ProposalRecord) -> AgentNoteProposal:
    return AgentNoteProposal(
        proposal_id=proposal.proposal_id,
        source_id=proposal.source_id,
        origin="distilled" if proposal.origin is NoteOrigin.DISTILLED else "agent",
        kind=proposal.draft.kind.value,
        title=proposal.draft.title,
        body=proposal.draft.body,
        tags=list(proposal.draft.tags),
        visibility=AgentNoteVisibility(proposal.draft.visibility.value),
        evidence=_evidence(proposal.evidence_conversation_id, proposal.evidence_task_id),
        status=ProposalStatusOut(proposal.status.value),
        created_at=proposal.created_at,
        decided_at=proposal.decided_at,
        decided_by=proposal.decided_by,
        note_id=proposal.note_id,
        problem=proposal.problem,
    )
