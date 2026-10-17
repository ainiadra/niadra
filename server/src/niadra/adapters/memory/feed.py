"""In-memory `FeedRepository` for unit tests: the same numbering as PostgreSQL."""

from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.notify.feed import FeedEvent, NewFeedEvent
from niadra.domain.notify.webhooks import erased_by, erasure_needles
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class InMemoryFeedRepository:
    def __init__(self, store: MemoryStore, space_id: UUID | None) -> None:
        self._space_id = space_id
        self._rows: dict[tuple[UUID | None, int], tuple[NewFeedEvent, dict[str, Any]]] = store.module(
            "feed"
        ).setdefault("rows", {})
        self._tombstones: dict[tuple[UUID | None, str], Any] = store.module("tombstones").setdefault(
            "marks", {}
        )

    def _mine(self) -> list[tuple[int, NewFeedEvent]]:
        return sorted((seq, e) for (space, seq), (e, _) in self._rows.items() if space == self._space_id)

    async def append(self, event: NewFeedEvent) -> int | None:
        mine = self._mine()
        if any(e.event_id == event.event_id for _, e in mine):
            return None
        needles = [
            m[7:] for (space, m) in self._tombstones if space == self._space_id and m.startswith("needle:")
        ]
        if erased_by(event.body, needles):
            return None
        seq = (mine[-1][0] if mine else 0) + 1
        self._rows[(self._space_id, seq)] = (event, event.body)
        return seq

    async def since(self, after: int, limit: int) -> tuple[list[FeedEvent], int]:
        mine = self._mine()
        events = [FeedEvent(seq, e.body) for seq, e in mine if seq > after][:limit]
        return events, mine[-1][0] if mine else 0

    async def purge_before(self, before: datetime) -> int:
        doomed = [seq for seq, e in self._mine() if e.created_at < before]
        for seq in doomed:
            del self._rows[(self._space_id, seq)]
        return len(doomed)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        needles = erasure_needles(scope)
        if not needles:
            return []
        doomed = [(seq, e) for seq, e in self._mine() if erased_by(e.body, needles)][:limit]
        for seq, _ in doomed:
            del self._rows[(self._space_id, seq)]
        return [ErasedRow("notification_feed", {"event_id": str(e.event_id)}) for _, e in doomed]
