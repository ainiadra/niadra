"""Agent memory: what an agent knows about its own work, never about a person (10, C).

Notes are procedures, tool notes, process notes and pitfalls, owned by a source (the agent). A write that
carries personal data is refused with `personal_data_in_agent_memory`, never masked: a procedure with holes
is worse than none. Nothing here takes a customer, so erasure of a data subject never looks at it.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import IdStr, Model

MAX_TITLE = 300
MAX_BODY = 20_000
MAX_TAGS = 8

Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,63}$")]
Title = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TITLE, strip_whitespace=True)]
Body = Annotated[str, StringConstraints(min_length=1, max_length=MAX_BODY, strip_whitespace=True)]
Query = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class AgentNoteKind(StrEnum):
    PROCEDURE = "procedure"
    TOOL_NOTE = "tool_note"
    PROCESS_NOTE = "process_note"
    PITFALL = "pitfall"


class AgentNoteVisibility(StrEnum):
    SOURCE = "source"  # only the owning agent
    VENDOR = "vendor"  # every source of the owner's vendor
    SPACE = "space"  # every agent of the space


class AgentNoteOrigin(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    DISTILLED = "distilled"


class AgentNoteStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Evidence(Model):
    """Where the note came from: an id only, never the text of the conversation or task."""

    conversation_id: IdStr | None = None
    task_id: IdStr | None = None

    @model_validator(mode="after")
    def _one(self) -> "Evidence":
        if (self.conversation_id is None) == (self.task_id is None):
            raise ValueError("pass `conversation_id` or `task_id`")
        return self


class AgentNote(Model):
    note_id: UUID
    source_id: UUID = Field(description="The agent that owns the note.")
    visibility: AgentNoteVisibility
    kind: AgentNoteKind
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    evidence: Evidence | None = None
    origin: AgentNoteOrigin
    version: int = Field(ge=1)
    supersedes_note_id: UUID | None = Field(
        default=None, description="The version this one replaced; that one is `retired`."
    )
    status: AgentNoteStatus
    valid_until: datetime | None = Field(
        default=None, description="After this moment the note leaves the block and the search."
    )
    created_at: datetime
    created_by: str = Field(description="`source:<id>` or `user:<id>`.")


class CreateAgentNoteRequest(Model):
    kind: AgentNoteKind
    title: Title
    body: Body
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)
    visibility: AgentNoteVisibility = AgentNoteVisibility.SOURCE
    evidence: Evidence | None = None
    valid_until: datetime | None = None
    source_id: UUID | None = Field(
        default=None,
        description="The owning agent. A source key writes its own notes and may omit it; a person names it.",
    )


class UpdateAgentNoteRequest(Model):
    """Changes make a new version; the current one is retired and kept."""

    kind: AgentNoteKind | None = None
    title: Title | None = None
    body: Body | None = None
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TAGS)
    visibility: AgentNoteVisibility | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def _something(self) -> "UpdateAgentNoteRequest":
        if not self.model_fields_set:
            raise ValueError("change at least one field")
        return self


class AgentNotePage(Model):
    items: list[AgentNote]
    next_cursor: str | None = None


class AgentMemoryBlock(Model):
    """The block that goes into the prompt after the agent's instructions and before the customer's
    context: stable between customers, so it stays in the cacheable prefix."""

    text: str = Field(description="Empty when there is nothing to show or agent memory is off.")
    notes: list[UUID] = Field(default_factory=list, description="The notes the text carries, in order.")
    etag: str
    tokens: int = 0
    left_out: int = Field(
        default=0,
        ge=0,
        description="Notes the reader may see that this block's size left out: each is kept, and the note"
        " search reads it.",
    )
    enabled: bool = Field(default=True, description="False when the space has agent memory turned off.")


class AgentMemorySearchRequest(Model):
    query: Query
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)
    limit: int = Field(default=5, ge=1, le=20)
    conversation_id: IdStr | None = Field(
        default=None, description="For the receipt; never stored in a note."
    )
    task_id: IdStr | None = None


class AgentMemorySearchResponse(Model):
    notes: list[AgentNote]


class RememberResult(Model):
    """What `POST /v1/agent-memory/notes` answers: the note, or where it waits when writes need a person."""

    note: AgentNote | None = None
    proposal_id: UUID | None = Field(
        default=None,
        description="Set when the space takes agent notes only through a person (`writes: human_only`).",
    )


class DistillRequest(Model):
    """Asks for a proposed note from one conversation or task, read already masked (10, C.5)."""

    source_id: UUID = Field(description="The agent whose memory the note would join.")
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None

    @model_validator(mode="after")
    def _one(self) -> "DistillRequest":
        if (self.conversation_id is None) == (self.task_id is None):
            raise ValueError("pass `conversation_id` or `task_id`")
        return self


class ProposalStatus(StrEnum):
    DRAFTING = "drafting"  # a distillation still running; title and body are empty
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"  # the distillation found nothing to propose, or proposed personal data


class AgentNoteProposal(Model):
    proposal_id: UUID
    source_id: UUID
    origin: Literal["distilled", "agent"] = Field(
        description="`distilled` from a conversation or task; `agent` when the space holds agent writes for "
        "a person."
    )
    kind: AgentNoteKind
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    visibility: AgentNoteVisibility = AgentNoteVisibility.SOURCE
    evidence: Evidence | None = None
    status: ProposalStatus
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    note_id: UUID | None = Field(default=None, description="The note an approval created.")
    problem: str | None = Field(
        default=None,
        description=(
            "Why a distillation failed: `nothing_to_propose`, `personal_data`, `no_turns`, or "
            "`unconfirmed_link` (the conversation spoke only through a link a claim made and nobody "
            "confirmed yet; ask again once it is confirmed)."
        ),
    )


class ApproveProposalRequest(Model):
    """A person may edit the proposal before approving it; the edit passes the redactor again."""

    kind: AgentNoteKind | None = None
    title: Title | None = None
    body: Body | None = None
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TAGS)
    visibility: AgentNoteVisibility | None = None


class AgentNoteProposalPage(Model):
    items: list[AgentNoteProposal]
    next_cursor: str | None = None


class ErasedAgentNotes(Model):
    source_id: UUID
    deleted: int
    receipt_id: str | None = None


class AgentMemoryExport(Model):
    """Every note of one source, every version, for portability of the agent's own work."""

    source_id: UUID
    exported_at: datetime
    notes: list[AgentNote]
