"""In-memory `AgentMemoryRepository` for unit tests."""

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.agent_memory.model import AgentNoteRecord, NoteStatus, ProposalRecord, ProposalStatus
from niadra.domain.privacy.erasure import AGENT_NOTE_EVIDENCE, ErasedRow


class InMemoryAgentMemoryRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("agent_memory")
        self._notes: dict[UUID, AgentNoteRecord] = self._s.setdefault("notes", {})
        self._proposals: dict[UUID, ProposalRecord] = self._s.setdefault("proposals", {})
        self._seq: dict[UUID, int] = self._s.setdefault("seq", {})
        self._counter: list[int] = self._s.setdefault("counter", [0])

    def _bump(self, note_id: UUID) -> None:
        self._counter[0] += 1
        self._seq[note_id] = self._counter[0]

    async def add_note(self, note: AgentNoteRecord) -> None:
        self._notes[note.note_id] = note
        self._bump(note.note_id)

    async def get_note(self, note_id: UUID) -> AgentNoteRecord | None:
        return self._notes.get(note_id)

    async def rewrite_note(self, note: AgentNoteRecord) -> None:
        if note.note_id in self._notes:
            self._notes[note.note_id] = note

    async def rewrite_proposal(self, proposal: ProposalRecord) -> None:
        if proposal.proposal_id in self._proposals:
            self._proposals[proposal.proposal_id] = dataclasses.replace(
                self._proposals[proposal.proposal_id], draft=proposal.draft
            )

    async def retire_note(self, note_id: UUID, at: datetime) -> bool:
        note = self._notes.get(note_id)
        if note is None or note.status is not NoteStatus.ACTIVE:
            return False
        self._notes[note_id] = dataclasses.replace(note, status=NoteStatus.RETIRED, retired_at=at)
        self._bump(note_id)
        return True

    async def lineage(self, lineage_id: UUID) -> list[AgentNoteRecord]:
        return sorted(
            (n for n in self._notes.values() if n.lineage_id == lineage_id), key=lambda n: n.version
        )

    async def active_notes(self) -> list[AgentNoteRecord]:
        return [self._notes[i] for i in sorted(self._notes) if self._notes[i].status is NoteStatus.ACTIVE]

    async def list_notes(
        self, *, source_id: UUID | None, status: NoteStatus | None, before_id: UUID | None, limit: int
    ) -> list[AgentNoteRecord]:
        found = [
            n
            for n in self._notes.values()
            if (source_id is None or n.source_id == source_id)
            and (status is None or n.status is status)
            and (before_id is None or n.note_id < before_id)
        ]
        return sorted(found, key=lambda n: n.note_id, reverse=True)[:limit]

    async def notes_of_source(self, source_id: UUID) -> list[AgentNoteRecord]:
        return [self._notes[i] for i in sorted(self._notes) if self._notes[i].source_id == source_id]

    async def count_active(self, source_id: UUID) -> int:
        return sum(
            1 for n in self._notes.values() if n.source_id == source_id and n.status is NoteStatus.ACTIVE
        )

    async def writes_since(self, source_id: UUID, at: datetime) -> int:
        by = f"source:{source_id}"
        rows: list[AgentNoteRecord | ProposalRecord] = [*self._notes.values(), *self._proposals.values()]
        return sum(1 for r in rows if r.source_id == source_id and r.created_at >= at and r.created_by == by)

    async def delete_source(self, source_id: UUID) -> list[ErasedRow]:
        gone = [n for n in self._notes.values() if n.source_id == source_id]
        for note in gone:
            del self._notes[note.note_id]
        for p in [p for p in self._proposals.values() if p.source_id == source_id]:
            del self._proposals[p.proposal_id]
        return [
            ErasedRow("agent_notes", {"note_id": str(n.note_id)}, change_seq=self._seq.pop(n.note_id, None))
            for n in gone
        ]

    async def forget_conversations(self, names: Sequence[str]) -> list[ErasedRow]:
        wanted = set(names)
        erased: list[ErasedRow] = []
        for note in [n for n in self._notes.values() if n.evidence_conversation_id in wanted]:
            self._notes[note.note_id] = dataclasses.replace(note, evidence_conversation_id=None)
            self._bump(note.note_id)
            erased.append(ErasedRow(AGENT_NOTE_EVIDENCE, {"note_id": str(note.note_id)}))
        for p in [p for p in self._proposals.values() if p.evidence_conversation_id in wanted]:
            self._proposals[p.proposal_id] = dataclasses.replace(p, evidence_conversation_id=None)
            erased.append(ErasedRow(AGENT_NOTE_EVIDENCE, {"proposal_id": str(p.proposal_id)}))
        return erased

    async def add_proposal(self, proposal: ProposalRecord) -> None:
        self._proposals[proposal.proposal_id] = proposal

    async def get_proposal(self, proposal_id: UUID) -> ProposalRecord | None:
        return self._proposals.get(proposal_id)

    async def decide_proposal(self, proposal: ProposalRecord) -> bool:
        current = self._proposals.get(proposal.proposal_id)
        if current is None or current.status not in (ProposalStatus.PENDING, ProposalStatus.DRAFTING):
            return False
        self._proposals[proposal.proposal_id] = proposal
        return True

    async def save_draft(self, proposal: ProposalRecord) -> bool:
        current = self._proposals.get(proposal.proposal_id)
        if current is None or current.status is not ProposalStatus.DRAFTING:
            return False
        self._proposals[proposal.proposal_id] = proposal
        return True

    async def list_proposals(
        self, *, source_id: UUID | None, status: ProposalStatus | None, before_id: UUID | None, limit: int
    ) -> list[ProposalRecord]:
        found = [
            p
            for p in self._proposals.values()
            if (source_id is None or p.source_id == source_id)
            and (status is None or p.status is status)
            and (before_id is None or p.proposal_id < before_id)
        ]
        return sorted(found, key=lambda p: p.proposal_id, reverse=True)[:limit]

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "agent_notes":
            raise ValueError(f"agent_memory does not export {table}")
        rows = sorted(
            (seq, i) for i, seq in self._seq.items() if after_seq < seq <= upto_seq and i in self._notes
        )
        out = []
        for seq, note_id in rows[:limit]:
            note = self._notes[note_id]
            flat = {k: v for k, v in dataclasses.asdict(note).items() if k != "draft"}
            flat |= {
                "kind": note.draft.kind.value,
                "title": note.draft.title,
                "body": note.draft.body,
                "tags": list(note.draft.tags),
                "visibility": note.draft.visibility.value,
                "valid_until": note.draft.valid_until,
                "change_seq": seq,
            }
            out.append(flat)
        return out
