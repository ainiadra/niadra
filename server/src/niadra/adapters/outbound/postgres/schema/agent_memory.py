"""Agent memory (10, C): the agents' own working notes and the proposals a person approves.

No column holds a customer: a write with personal data is refused before it gets here, so erasure of a
data subject never looks at these tables. Title and body are still sealed like every tenant text.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version


class AgentNote(SpaceBase):
    __tablename__ = "agent_notes"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    note_id: Mapped[UUID] = mapped_column(primary_key=True)
    lineage_id: Mapped[UUID]
    source_id: Mapped[UUID]
    visibility: Mapped[str]
    kind: Mapped[str]
    title_ct: Mapped[bytes]
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    tags: Mapped[list[str]]
    origin: Mapped[str]
    version: Mapped[int] = mapped_column(Integer)
    supersedes_note_id: Mapped[UUID | None]
    status: Mapped[str]
    valid_until: Mapped[datetime | None]
    evidence_conversation_id: Mapped[str | None]
    evidence_task_id: Mapped[str | None]
    created_at: Mapped[datetime]
    created_by: Mapped[str]
    retired_at: Mapped[datetime | None]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("visibility IN ('source', 'vendor', 'space')", name="visibility"),
        CheckConstraint("kind IN ('procedure', 'tool_note', 'process_note', 'pitfall')", name="kind"),
        CheckConstraint("origin IN ('agent', 'human', 'distilled')", name="origin"),
        CheckConstraint("status IN ('active', 'retired')", name="status"),
        Index(None, "space_id", "source_id", "status"),
        Index(None, "space_id", "lineage_id"),
        Index(None, "space_id", "change_seq"),
    )


class AgentNoteProposal(SpaceBase):
    __tablename__ = "agent_note_proposals"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    proposal_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    origin: Mapped[str]
    kind: Mapped[str]
    title_ct: Mapped[bytes]
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    tags: Mapped[list[str]]
    visibility: Mapped[str]
    valid_until: Mapped[datetime | None]
    status: Mapped[str]
    evidence_conversation_id: Mapped[str | None]
    evidence_task_id: Mapped[str | None]
    created_at: Mapped[datetime]
    created_by: Mapped[str]
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None]
    note_id: Mapped[UUID | None]
    problem: Mapped[str | None]
    __table_args__ = (
        CheckConstraint("status IN ('drafting', 'pending', 'approved', 'rejected', 'failed')", name="status"),
        CheckConstraint("origin IN ('agent', 'distilled')", name="origin"),
        Index(None, "space_id", "source_id", "status"),
    )
