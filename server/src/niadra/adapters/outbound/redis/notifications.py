"""The warm copy of each space's notification feed on the cache store (7.5; 24.2.10).

Per space, under one hash tag: `nf:` a sorted set of the newest events' bodies by sequence number, and `nfh:`
the head, the highest number the feed's table is known to hold, which only ever rises. A poll at the head
answers from here with no statement; so does a page whose events are all here without a gap. Anything else,
an evicted copy included, is the table's to answer. Both keys expire with the feed.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import orjson
from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated
from niadra.domain.notify.feed import FEED_CACHED, FEED_KEEP, FeedEvent, contiguous

_TTL_S = int(FEED_KEEP.total_seconds())


def _keys(space_id: UUID) -> tuple[str, str]:
    return f"nf:{{{space_id}}}", f"nfh:{{{space_id}}}"


class RedisFeedCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, space_id: UUID, events: Sequence[FeedEvent], *, head: int) -> None:
        feed, known = _keys(space_id)
        async with translated("cache", "feed_add"), self._redis.pipeline(transaction=True) as pipe:
            if events:
                pipe.zadd(feed, {orjson.dumps(e.body): e.seq for e in events})
                pipe.zremrangebyrank(feed, 0, -(FEED_CACHED + 1))
                pipe.expire(feed, _TTL_S)
            pipe.zadd(known, {b"h": head}, gt=True)
            pipe.expire(known, _TTL_S)
            await pipe.execute()

    async def since(self, space_id: UUID, after: int, limit: int) -> list[FeedEvent] | None:
        feed, known = _keys(space_id)
        async with translated("cache", "feed_since"), self._redis.pipeline(transaction=False) as pipe:
            pipe.zscore(known, b"h")
            pipe.zrangebyscore(feed, f"({after}", "+inf", start=0, num=limit, withscores=True)
            head, found = await pipe.execute()
        if head is None:
            return None
        if after >= int(head):
            return []
        events = contiguous(after, [FeedEvent(int(score), _body(member)) for member, score in found])
        return events or None


def _body(member: Any) -> dict[str, Any]:
    body: dict[str, Any] = orjson.loads(member)
    return body
