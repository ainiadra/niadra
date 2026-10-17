"""Conversation pins and agent watermarks, projected by the receipt writer (5.11)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.serve.records import AgentWatermark, ConversationPin


class ServeRepository(Protocol):
    """Owned by the `serve` module; no other module reads or writes these tables (4.3).

    The read process only reads them, to restore pins and watermarks a cache failover lost; the
    receipt writer projects them, and only ever moves them forward (rev. 11)."""

    async def get_pin(self, conversation_id: str, view: str) -> ConversationPin | None: ...

    async def get_watermark(
        self, source_id: UUID, profile_id: UUID, thread: str = ""
    ) -> AgentWatermark | None:
        """`thread` is the conversation or task id; "" for reads outside one."""
        ...

    async def latest_watermark(self, source_id: UUID, profile_id: UUID) -> AgentWatermark | None:
        """The last thing the source saw of the profile in any conversation, task or outside one: the
        delta base of a conversation the source has not read in yet (2.4)."""
        ...

    async def project_pins(self, pins: Sequence[ConversationPin]) -> None:
        """`ReceiptProjections`, in one statement, one pin per (conversation, view): inserts each pin or moves
        it to a higher generation; an older receipt arriving late never moves it back, and `pinned_at` only
        grows. Each pin's `expires_at` is the one it asks for, and a stored later one stays (5.11)."""
        ...

    async def project_watermarks(self, marks: Sequence[AgentWatermark]) -> None:
        """`ReceiptProjections`, in one statement, one mark per (source, profile, thread): inserts the agent's
        watermark of one thread or moves it to a later (generation, `seen_at`); a read of an older pinned pack
        never moves it back (2.4). Receipts carry the pack's generation in `extra["generation"]` and the
        thread in `conversation_id`/`task_id`."""
        ...

    async def get_assignments(self, profile_id: UUID) -> dict[str, str]:
        """The group or arm the profile first got in each experiment, by experiment, as receipts recorded
        them; one statement."""
        ...

    async def project_assignment(
        self,
        *,
        experiment: str,
        profile_id: UUID,
        group: str,
        assigned_at: datetime,
        kind: str = "memory",
    ) -> None:
        """`ReceiptProjections`: the first group recorded for the profile stays. An element or agent
        experiment's row keeps the arm as its group and in `arm`, with its kind."""
        ...

    async def invalidations_since(self, since: datetime) -> list[dict[str, Any]]:
        """The rows of `read_invalidations` written after `since` (`id`, `handle_ids`, `profile_ids`,
        `threads`), which the database's triggers write; the read processes poll them with the privacy
        feed (`app.read_state`)."""
        ...

    async def purge_invalidations(self, before: datetime) -> int:
        """The retention sweep: rows older than any read process's poll window."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """Pins of the profile's packs (or of the conversation) and, for a profile or a handle, the
        profile's agent watermarks. Called by `compile`'s erasure step, which drops the packs (8.4)."""
        ...
