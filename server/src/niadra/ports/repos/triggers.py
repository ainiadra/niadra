"""Trigger firings (22.1)."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.triggers.firing import Firing


class TriggerRepository(Protocol):
    """Owned by the `triggers` module; no other module reads or writes these tables (4.3)."""

    async def add_firing(self, firing: Firing) -> bool:
        """False when the rule already fired for the subject in that window (unique key)."""
        ...

    async def count_since(self, since: datetime) -> int: ...

    async def firings_for(self, profile_id: UUID, *, since: datetime) -> list[Firing]:
        """Firings of one subject not yet retracted, newest first."""
        ...

    async def retract(self, firing_id: UUID, at: datetime) -> None: ...

    async def list_firings(
        self,
        *,
        rule_id: str | None = None,
        profile_id: UUID | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Firing]:
        """Newest first; `before` is the (fired_at, firing_id) of the last row of the previous page."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): deletes at most `limit` rows of the target, idempotent."""
        ...

    async def purge_before(self, before: datetime) -> int:
        """Retention (22.5): firings fired before `before` go."""
        ...
