"""The warm copy of the notification feed the read path answers from (24.2.10): no statement for a poll."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from niadra.domain.notify.feed import FeedEvent


class FeedCache(Protocol):
    async def add(self, space_id: UUID, events: Sequence[FeedEvent], *, head: int) -> None:
        """Keeps the events, the newest `FEED_CACHED`, and raises the space's known head to `head`; never
        lowers it. `head` is a sequence the feed's table holds already."""
        ...

    async def since(self, space_id: UUID, after: int, limit: int) -> list[FeedEvent] | None:
        """The events after `after` without a gap, or none when the head is at `after`; None when the copy
        cannot tell (evicted, or older than what it keeps), and the table answers."""
        ...
