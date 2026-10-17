"""PostgreSQL implementation of `AgentMemoryRepository` (10, C). Title and body are sealed."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.mentions import with_mentions
from niadra.adapters.outbound.postgres.privacy_rows import changed_rows
from niadra.adapters.outbound.postgres.schema.agent_memory import AgentNote as NoteRow
from niadra.adapters.outbound.postgres.schema.agent_memory import AgentNoteProposal as ProposalRow
from niadra.adapters.outbound.postgres.sealer import Sealer
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
from niadra.domain.exported import export_row
from niadra.domain.privacy.erasure import AGENT_NOTE_EVIDENCE, ErasedRow

N = cast(Table, NoteRow.__table__)
P = cast(Table, ProposalRow.__table__)


class PgAgentMemoryRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def add_note(self, note: AgentNoteRecord) -> None:
        title, body, dek = await self._seal("agent_notes", note.note_id, note.draft)
        await self._db.execute(
            insert(N).values(
                space_id=CURRENT_SPACE,
                note_id=note.note_id,
                lineage_id=note.lineage_id,
                source_id=note.source_id,
                visibility=note.draft.visibility.value,
                kind=note.draft.kind.value,
                title_ct=title,
                body_ct=body,
                dek_version=dek,
                tags=list(note.draft.tags),
                origin=note.origin.value,
                version=note.version,
                supersedes_note_id=note.supersedes_note_id,
                status=note.status.value,
                valid_until=note.draft.valid_until,
                evidence_conversation_id=note.evidence_conversation_id,
                evidence_task_id=note.evidence_task_id,
                created_at=note.created_at,
                created_by=note.created_by,
                retired_at=note.retired_at,
            )
        )

    async def get_note(self, note_id: UUID) -> AgentNoteRecord | None:
        rows = await self._notes(N.c.note_id == note_id)
        return rows[0] if rows else None

    async def retire_note(self, note_id: UUID, at: datetime) -> bool:
        result = await self._db.execute(
            update(N)
            .where(
                N.c.space_id == CURRENT_SPACE, N.c.note_id == note_id, N.c.status == NoteStatus.ACTIVE.value
            )
            .values(status=NoteStatus.RETIRED.value, retired_at=at)
        )
        return bool(getattr(result, "rowcount", 0))

    async def lineage(self, lineage_id: UUID) -> list[AgentNoteRecord]:
        return await self._notes(N.c.lineage_id == lineage_id, order=(N.c.version,))

    async def active_notes(self) -> list[AgentNoteRecord]:
        return await self._notes(N.c.status == NoteStatus.ACTIVE.value, order=(N.c.note_id,))

    async def list_notes(
        self, *, source_id: UUID | None, status: NoteStatus | None, before_id: UUID | None, limit: int
    ) -> list[AgentNoteRecord]:
        conditions = []
        if source_id is not None:
            conditions.append(N.c.source_id == source_id)
        if status is not None:
            conditions.append(N.c.status == status.value)
        if before_id is not None:
            conditions.append(N.c.note_id < before_id)
        return await self._notes(*conditions, order=(N.c.note_id.desc(),), limit=limit)

    async def notes_of_source(self, source_id: UUID) -> list[AgentNoteRecord]:
        return await self._notes(N.c.source_id == source_id, order=(N.c.note_id,))

    async def count_active(self, source_id: UUID) -> int:
        stmt = select(func.count()).where(
            N.c.space_id == CURRENT_SPACE, N.c.source_id == source_id, N.c.status == NoteStatus.ACTIVE.value
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def writes_since(self, source_id: UUID, at: datetime) -> int:
        notes = select(func.count()).where(
            N.c.space_id == CURRENT_SPACE,
            N.c.source_id == source_id,
            N.c.created_at >= at,
            N.c.created_by == f"source:{source_id}",
        )
        proposals = select(func.count()).where(
            P.c.space_id == CURRENT_SPACE,
            P.c.source_id == source_id,
            P.c.created_at >= at,
            P.c.created_by == f"source:{source_id}",
        )
        stmt = select(notes.scalar_subquery() + proposals.scalar_subquery())
        return int((await self._db.execute(stmt)).scalar_one())

    async def delete_source(self, source_id: UUID) -> list[ErasedRow]:
        await self._db.execute(delete(P).where(P.c.space_id == CURRENT_SPACE, P.c.source_id == source_id))
        gone = await self._db.execute(
            delete(N)
            .where(N.c.space_id == CURRENT_SPACE, N.c.source_id == source_id)
            .returning(N.c.note_id, N.c.change_seq)
        )
        return [
            ErasedRow("agent_notes", {"note_id": str(row.note_id)}, change_seq=row.change_seq)
            for row in gone.all()
        ]

    async def forget_conversations(self, names: Sequence[str]) -> list[ErasedRow]:
        if not names:
            return []
        wanted = list(dict.fromkeys(names))
        notes = await self._db.execute(
            update(N)
            .where(N.c.space_id == CURRENT_SPACE, N.c.evidence_conversation_id.in_(wanted))
            .values(evidence_conversation_id=None)
            .returning(N.c.note_id)
        )
        proposals = await self._db.execute(
            update(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.evidence_conversation_id.in_(wanted))
            .values(evidence_conversation_id=None)
            .returning(P.c.proposal_id)
        )
        return [ErasedRow(AGENT_NOTE_EVIDENCE, {"note_id": str(i)}) for i in notes.scalars()] + [
            ErasedRow(AGENT_NOTE_EVIDENCE, {"proposal_id": str(i)}) for i in proposals.scalars()
        ]

    async def add_proposal(self, proposal: ProposalRecord) -> None:
        title, body, dek = await self._seal("agent_note_proposals", proposal.proposal_id, proposal.draft)
        await self._execute_named(
            insert(P).values(
                space_id=CURRENT_SPACE,
                proposal_id=proposal.proposal_id,
                source_id=proposal.source_id,
                origin=proposal.origin.value,
                kind=proposal.draft.kind.value,
                title_ct=title,
                body_ct=body,
                dek_version=dek,
                tags=list(proposal.draft.tags),
                visibility=proposal.draft.visibility.value,
                valid_until=proposal.draft.valid_until,
                status=proposal.status.value,
                evidence_conversation_id=proposal.evidence_conversation_id,
                evidence_task_id=proposal.evidence_task_id,
                created_at=proposal.created_at,
                created_by=proposal.created_by,
                problem=proposal.problem,
            )
        )

    async def get_proposal(self, proposal_id: UUID) -> ProposalRecord | None:
        rows = await self._proposals(P.c.proposal_id == proposal_id)
        return rows[0] if rows else None

    async def decide_proposal(self, proposal: ProposalRecord) -> bool:
        result = await self._db.execute(
            update(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.proposal_id == proposal.proposal_id,
                P.c.status.in_([ProposalStatus.PENDING.value, ProposalStatus.DRAFTING.value]),
            )
            .values(
                status=proposal.status.value,
                decided_at=proposal.decided_at,
                decided_by=proposal.decided_by,
                note_id=proposal.note_id,
                problem=proposal.problem,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def save_draft(self, proposal: ProposalRecord) -> bool:
        title, body, dek = await self._seal("agent_note_proposals", proposal.proposal_id, proposal.draft)
        result = await self._execute_named(
            update(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.proposal_id == proposal.proposal_id,
                P.c.status == ProposalStatus.DRAFTING.value,
            )
            .values(
                kind=proposal.draft.kind.value,
                title_ct=title,
                body_ct=body,
                dek_version=dek,
                tags=list(proposal.draft.tags),
                status=proposal.status.value,
                problem=proposal.problem,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def list_proposals(
        self, *, source_id: UUID | None, status: ProposalStatus | None, before_id: UUID | None, limit: int
    ) -> list[ProposalRecord]:
        conditions = []
        if source_id is not None:
            conditions.append(P.c.source_id == source_id)
        if status is not None:
            conditions.append(P.c.status == status.value)
        if before_id is not None:
            conditions.append(P.c.proposal_id < before_id)
        return await self._proposals(*conditions, limit=limit)

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "agent_notes":
            raise ValueError(f"agent_memory does not export {table}")
        rows = await changed_rows(self._db, N, after_seq=after_seq, upto_seq=upto_seq, limit=limit)
        out = []
        for row in rows:
            note = await self._note(row._mapping)
            flat = export_row(note, row.change_seq, drop=("draft",))
            flat |= {
                "kind": note.draft.kind.value,
                "title": note.draft.title,
                "body": note.draft.body,
                "tags": list(note.draft.tags),
                "visibility": note.draft.visibility.value,
                "valid_until": note.draft.valid_until,
            }
            out.append(flat)
        return out

    # Internals.

    async def _notes(
        self, *conditions: Any, order: tuple[Any, ...] = (), limit: int | None = None
    ) -> list[AgentNoteRecord]:
        stmt = select(N).where(N.c.space_id == CURRENT_SPACE, *conditions).order_by(*order)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [await self._note(r) for r in (await self._db.execute(stmt)).mappings()]

    async def _proposals(self, *conditions: Any, limit: int | None = None) -> list[ProposalRecord]:
        stmt = select(P).where(P.c.space_id == CURRENT_SPACE, *conditions).order_by(P.c.proposal_id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return [await self._proposal(r) for r in (await self._db.execute(stmt)).mappings()]

    async def rewrite_note(self, note: AgentNoteRecord) -> None:
        title, body, dek = await self._seal("agent_notes", note.note_id, note.draft)
        await self._db.execute(
            update(N)
            .where(N.c.space_id == CURRENT_SPACE, N.c.note_id == note.note_id)
            .values(title_ct=title, body_ct=body, dek_version=dek)
        )

    async def rewrite_proposal(self, proposal: ProposalRecord) -> None:
        """A proposal's title and body sealed again as they are now, whatever its status."""
        title, body, dek = await self._seal("agent_note_proposals", proposal.proposal_id, proposal.draft)
        await self._db.execute(
            update(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.proposal_id == proposal.proposal_id)
            .values(title_ct=title, body_ct=body, dek_version=dek)
        )

    async def _execute_named(self, stmt: Any) -> Any:
        """One statement that also indexes what the rows it sealed name a person by
        (`domain.privacy.mentions`)."""
        return await self._db.execute(await with_mentions(stmt, self._sealer))

    async def _seal(self, table: str, row: UUID, draft: NoteDraft) -> tuple[bytes, bytes, int]:
        sealer = self._need_sealer()
        title, dek = await sealer.seal(draft.title, table=table, row=row, column="title")
        body, _ = await sealer.seal(draft.body, table=table, row=row, column="body")
        return title, body, dek

    async def _open(self, table: str, row: UUID, r: Any) -> tuple[str, str]:
        sealer = self._need_sealer()
        title = await sealer.open_text(r["title_ct"], r["dek_version"], table=table, row=row, column="title")
        body = await sealer.open_text(r["body_ct"], r["dek_version"], table=table, row=row, column="body")
        return title, body

    async def _note(self, r: Any) -> AgentNoteRecord:
        title, body = await self._open("agent_notes", r["note_id"], r)
        return AgentNoteRecord(
            note_id=r["note_id"],
            source_id=r["source_id"],
            draft=NoteDraft(
                kind=NoteKind(r["kind"]),
                title=title,
                body=body,
                tags=tuple(r["tags"]),
                visibility=NoteVisibility(r["visibility"]),
                valid_until=r["valid_until"],
            ),
            origin=NoteOrigin(r["origin"]),
            version=r["version"],
            status=NoteStatus(r["status"]),
            created_at=r["created_at"],
            created_by=r["created_by"],
            lineage_id=r["lineage_id"],
            supersedes_note_id=r["supersedes_note_id"],
            evidence_conversation_id=r["evidence_conversation_id"],
            evidence_task_id=r["evidence_task_id"],
            retired_at=r["retired_at"],
        )

    async def _proposal(self, r: Any) -> ProposalRecord:
        title, body = await self._open("agent_note_proposals", r["proposal_id"], r)
        return ProposalRecord(
            proposal_id=r["proposal_id"],
            source_id=r["source_id"],
            origin=NoteOrigin(r["origin"]),
            draft=NoteDraft(
                kind=NoteKind(r["kind"]),
                title=title,
                body=body,
                tags=tuple(r["tags"]),
                visibility=NoteVisibility(r["visibility"]),
                valid_until=r["valid_until"],
            ),
            status=ProposalStatus(r["status"]),
            created_at=r["created_at"],
            created_by=r["created_by"],
            evidence_conversation_id=r["evidence_conversation_id"],
            evidence_task_id=r["evidence_task_id"],
            decided_at=r["decided_at"],
            decided_by=r["decided_by"],
            note_id=r["note_id"],
            problem=r["problem"],
        )

    def _need_sealer(self) -> Sealer:
        assert self._sealer is not None, "agent memory is read and written inside a space transaction"
        return self._sealer
