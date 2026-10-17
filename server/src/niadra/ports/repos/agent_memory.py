"""Agent notes and their proposals (10, C)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.agent_memory.model import AgentNoteRecord, NoteStatus, ProposalRecord, ProposalStatus
from niadra.domain.privacy.erasure import ErasedRow


class AgentMemoryRepository(Protocol):
    """Owned by the `agent_memory` module; no other module reads or writes these tables (4.3)."""

    async def add_note(self, note: AgentNoteRecord) -> None: ...

    async def get_note(self, note_id: UUID) -> AgentNoteRecord | None: ...

    async def rewrite_proposal(self, proposal: ProposalRecord) -> None:
        """Title and body of a proposal, in place, whatever its status: an erasure redacts someone named in
        them."""
        ...

    async def rewrite_note(self, note: AgentNoteRecord) -> None:
        """Title and body of that version, in place: an erasure redacts someone named in them."""
        ...

    async def retire_note(self, note_id: UUID, at: datetime) -> bool:
        """Active to retired; False when it was not active (another writer won)."""
        ...

    async def lineage(self, lineage_id: UUID) -> list[AgentNoteRecord]:
        """Every version of one note, oldest first."""
        ...

    async def active_notes(self) -> list[AgentNoteRecord]:
        """Every active note of the space: the block and the search filter by reader in memory, since a
        space holds hundreds of notes per agent at most."""
        ...

    async def list_notes(
        self,
        *,
        source_id: UUID | None,
        status: NoteStatus | None,
        before_id: UUID | None,
        limit: int,
    ) -> list[AgentNoteRecord]:
        """Newest first by `note_id` (UUIDv7)."""
        ...

    async def notes_of_source(self, source_id: UUID) -> list[AgentNoteRecord]:
        """Every version of every note of one agent, oldest first: the export."""
        ...

    async def count_active(self, source_id: UUID) -> int: ...

    async def writes_since(self, source_id: UUID, at: datetime) -> int:
        """Notes and proposals an agent created since `at`: the hourly write limit."""
        ...

    async def delete_source(self, source_id: UUID) -> list[ErasedRow]:
        """Deletes every note and proposal of one agent; the notes' keys go to the export's tombstones."""
        ...

    async def forget_conversations(self, names: Sequence[str]) -> list[ErasedRow]:
        """Clears `evidence_conversation_id` on the notes and proposals that name one of `names`, the
        conversations an erasure takes (8.4): the note stays, the id (often a phone or an e-mail) goes. One
        `AGENT_NOTE_EVIDENCE` row per note or proposal changed; a note's `change_seq` moves, so the export
        sends it again without the id."""
        ...

    async def add_proposal(self, proposal: ProposalRecord) -> None: ...

    async def get_proposal(self, proposal_id: UUID) -> ProposalRecord | None: ...

    async def decide_proposal(self, proposal: ProposalRecord) -> bool:
        """Pending (or drafting, for a failed distillation) to decided; False when it was already decided."""
        ...

    async def save_draft(self, proposal: ProposalRecord) -> bool:
        """What a distillation proposed, while the proposal is still drafting."""
        ...

    async def list_proposals(
        self,
        *,
        source_id: UUID | None,
        status: ProposalStatus | None,
        before_id: UUID | None,
        limit: int,
    ) -> list[ProposalRecord]: ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]: ...
