"""In-memory `TriggerRepository` for unit tests."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.triggers.firing import Firing, erased_by


class InMemoryTriggerRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("triggers")
        self._firings: dict[UUID, Firing] = self._s.setdefault("firings", {})

    async def add_firing(self, firing: Firing) -> bool:
        taken = any(
            (f.rule_id, f.profile_id, f.window_key) == (firing.rule_id, firing.profile_id, firing.window_key)
            for f in self._firings.values()
        )
        if taken:
            return False
        self._firings[firing.firing_id] = firing
        return True

    async def count_since(self, since: datetime) -> int:
        return sum(1 for f in self._firings.values() if f.fired_at >= since)

    async def firings_for(self, profile_id: UUID, *, since: datetime) -> list[Firing]:
        found = [
            f
            for f in self._firings.values()
            if f.profile_id == profile_id and f.retracted_at is None and f.fired_at >= since
        ]
        return _newest_first(found)

    async def retract(self, firing_id: UUID, at: datetime) -> None:
        if firing_id in self._firings:
            self._firings[firing_id] = replace(self._firings[firing_id], retracted_at=at)

    async def list_firings(
        self,
        *,
        rule_id: str | None = None,
        profile_id: UUID | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Firing]:
        found = [
            f
            for f in self._firings.values()
            if (rule_id is None or f.rule_id == rule_id)
            and (profile_id is None or f.profile_id == profile_id)
            and (before is None or (f.fired_at, f.firing_id) < before)
        ]
        return _newest_first(found)[:limit]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        doomed = sorted(f.firing_id for f in self._firings.values() if erased_by(f, scope))[:limit]
        for firing_id in doomed:
            del self._firings[firing_id]
        return [ErasedRow("trigger_firings", {"firing_id": str(firing_id)}) for firing_id in doomed]

    async def purge_before(self, before: datetime) -> int:
        doomed = [f.firing_id for f in self._firings.values() if f.fired_at < before]
        for firing_id in doomed:
            del self._firings[firing_id]
        return len(doomed)


def _newest_first(firings: list[Firing]) -> list[Firing]:
    return sorted(firings, key=lambda f: (f.fired_at, f.firing_id), reverse=True)
