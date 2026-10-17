"""The notification feed's persistence (7.5): the events a space's pollers read, in their commit order."""

from datetime import datetime
from typing import Protocol

from niadra.domain.notify.feed import FeedEvent, NewFeedEvent
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class FeedRepository(Protocol):
    async def append(self, event: NewFeedEvent) -> int | None:
        """The event's sequence number, the next of the space's, taken under the space's feed lock so the
        transactions that append commit in their numbers' order; None when the event is there already."""
        ...

    async def since(self, after: int, limit: int) -> tuple[list[FeedEvent], int]:
        """The events after `after`, oldest first, and the feed's head, its highest number (0 when empty);
        one statement."""
        ...

    async def purge_before(self, before: datetime) -> int: ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): the events whose bodies name the target, as its webhook deliveries."""
        ...
