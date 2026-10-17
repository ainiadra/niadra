"""The records of agent memory: notes, in versions, and proposals waiting for a person."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class NoteKind(StrEnum):
    PROCEDURE = "procedure"
    TOOL_NOTE = "tool_note"
    PROCESS_NOTE = "process_note"
    PITFALL = "pitfall"


class NoteVisibility(StrEnum):
    SOURCE = "source"
    VENDOR = "vendor"
    SPACE = "space"


class NoteOrigin(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    DISTILLED = "distilled"


class NoteStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class ProposalStatus(StrEnum):
    DRAFTING = "drafting"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NoteDraft:
    """What a note says; the same shape for a note, a new version and a proposal."""

    kind: NoteKind
    title: str
    body: str
    tags: tuple[str, ...] = ()
    visibility: NoteVisibility = NoteVisibility.SOURCE
    valid_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentNoteRecord:
    note_id: UUID
    source_id: UUID
    draft: NoteDraft
    origin: NoteOrigin
    version: int
    status: NoteStatus
    created_at: datetime
    created_by: str
    lineage_id: UUID
    """The first version's id: every version of one note shares it."""
    supersedes_note_id: UUID | None = None
    evidence_conversation_id: str | None = None
    evidence_task_id: str | None = None
    retired_at: datetime | None = None

    def active_at(self, now: datetime) -> bool:
        until = self.draft.valid_until
        return self.status is NoteStatus.ACTIVE and (until is None or until > now)


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    proposal_id: UUID
    source_id: UUID
    origin: NoteOrigin
    draft: NoteDraft
    status: ProposalStatus
    created_at: datetime
    created_by: str
    evidence_conversation_id: str | None = None
    evidence_task_id: str | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    note_id: UUID | None = None
    problem: str | None = None
