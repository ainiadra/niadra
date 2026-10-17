"""The notification feed (7.5; 24.1.1, pull first): every event a webhook carries, for the company that polls
`GET /v1/notifications` instead of running a receiver, in a space that turned the feed on.

`enqueue_event` leaves each event's body in a task inside the announcing transaction; the task appends it
here after that transaction committed, so the feed never shows an event that did not happen. A poll reads
the cache store's warm copy with no statement, and the space's table only when the copy cannot answer (one
statement a page), which warms it again.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from niadra.contracts.turns import Notification, NotificationPage
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.notify.feed import FEED_CACHED, FeedEvent, NewFeedEvent, decode_seq
from niadra.domain.tasks import Task
from niadra.ports.infra import CacheUnavailable
from niadra.ports.notifications import FeedCache
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)


class NotificationsService:
    def __init__(self, uow: UnitOfWorkFactory, cache: FeedCache) -> None:
        self._uow = uow
        self._cache = cache

    async def append(self, task: Task) -> None:
        payload = task.payload
        event = NewFeedEvent(
            event_id=UUID(payload["event_id"]),
            type=payload["type"],
            created_at=datetime.fromisoformat(payload["at"]),
            body=payload["body"],
        )
        async with self._uow(task.space_id) as uow:
            seq = await uow.feed.append(event)
        if seq is not None:
            await self._warm(task.space_id, [FeedEvent(seq, event.body)], seq)
            # How long an event waited to reach the feed: the queue's wait for a delivery worker shows here.
            lag = (datetime.now(UTC) - event.created_at).total_seconds()
            log.info(
                "notification in the feed", extra={"type": event.type, "seq": seq, "lag_s": round(lag, 1)}
            )

    async def page(self, principal: SourcePrincipal, cursor: str | None, limit: int) -> NotificationPage:
        """The events after `cursor`, oldest first; `next_cursor` is where the next poll starts."""
        space_id = principal.space.space_id
        after = decode_seq(cursor)
        try:
            events = await self._cache.since(space_id, after, limit)
        except CacheUnavailable:
            events = None
        if events is None:
            async with self._uow(space_id) as uow:
                events, head = await uow.feed.since(after, limit)
            await self._warm(space_id, events[-FEED_CACHED:], head)
        return NotificationPage(
            items=[Notification.model_validate(e.body) for e in events],
            next_cursor=str(events[-1].seq if events else after),
        )

    async def _warm(self, space_id: UUID, events: list[FeedEvent], head: int) -> None:
        # The copy is only a copy: without it a poll costs one statement, never a wrong answer.
        try:
            await self._cache.add(space_id, events, head=head)
        except CacheUnavailable:
            log.warning(
                "the notification feed's warm copy was not written", extra={"space_id": str(space_id)}
            )
