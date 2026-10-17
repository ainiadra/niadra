"""In-memory `FeedCache` for unit tests: the same answers as the cache store's."""

from collections.abc import Sequence
from uuid import UUID

from niadra.domain.notify.feed import FEED_CACHED, FeedEvent, contiguous


class InMemoryFeedCache:
    def __init__(self) -> None:
        self.events: dict[UUID, dict[int, FeedEvent]] = {}
        self.heads: dict[UUID, int] = {}

    async def add(self, space_id: UUID, events: Sequence[FeedEvent], *, head: int) -> None:
        kept = self.events.setdefault(space_id, {})
        kept.update({e.seq: e for e in events})
        for seq in sorted(kept)[:-FEED_CACHED]:
            del kept[seq]
        self.heads[space_id] = max(self.heads.get(space_id, 0), head)

    async def since(self, space_id: UUID, after: int, limit: int) -> list[FeedEvent] | None:
        head = self.heads.get(space_id)
        if head is None:
            return None
        if after >= head:
            return []
        found = [e for seq, e in sorted(self.events.get(space_id, {}).items()) if seq > after][:limit]
        return contiguous(after, found) or None
