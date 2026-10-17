"""The notification feed (7.5, 24.3.8): the webhooks' bodies for a company that pulls instead of receiving
them.

An event joins the feed after the transaction that announced it committed, one at a time per space, so its
sequence number is the order the feed's readers see: a reader at sequence n never finds n + 1 missing and
n + 2 there. The cursor is that number. Pure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import InvalidInput

FEED_KEEP = timedelta(days=7)
"""How long an event stays in the feed: a poller that stopped longer reads the gap from the webhooks' log."""
FEED_CACHED = 1000
"""The newest events per space the read path keeps warm."""


@dataclass(frozen=True, slots=True)
class NewFeedEvent:
    event_id: UUID
    type: str
    created_at: datetime
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FeedEvent:
    seq: int
    body: dict[str, Any]


def contiguous(after: int, found: Sequence[FeedEvent]) -> list[FeedEvent]:
    """The events that follow `after` without a gap: one appended later but committed first waits for the
    one before it."""
    out: list[FeedEvent] = []
    for event in sorted(found, key=lambda e: e.seq):
        if event.seq != after + len(out) + 1:
            break
        out.append(event)
    return out


def decode_seq(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdigit() or len(cursor) > 18:
        raise InvalidInput("the cursor is not one this feed returned")
    return int(cursor)
