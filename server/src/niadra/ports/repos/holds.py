"""Legal holds (`legal_holds`, 6.7), owned by the privacy module. Reasons arrive sealed."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.holds import LegalHoldRecord


class LegalHoldRepository(Protocol):
    async def add(self, hold: LegalHoldRecord) -> LegalHoldRecord:
        """The hold as stored: an id already there keeps what it held (a retried request)."""
        ...

    async def get(self, hold_id: UUID) -> LegalHoldRecord | None: ...

    async def release(
        self, hold_id: UUID, *, at: datetime, by: str, reason_ct: bytes
    ) -> LegalHoldRecord | None:
        """Releases an active hold and returns it; None when it is unknown or released already."""
        ...

    async def active(self) -> list[LegalHoldRecord]:
        """Every active hold of the space, in one statement: a space holds few."""
        ...

    async def holds_on(
        self, handle_ids: Sequence[UUID], conversation_hmacs: Sequence[bytes]
    ) -> list[LegalHoldRecord]:
        """Every hold, active or released, on these handles or conversations: the subject's package."""
        ...
