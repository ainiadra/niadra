"""`Doorbell` on the state store (06, 6.3): one sorted set of space ids scored by the earliest time each one
has work due. A ring keeps the earlier of two times; the loops process takes every due member in one
atomic call per tick, for the whole cell. Losing the set (a restart of the store) costs only latency: every
relay still comes back at its cap and reads the real due times from its database.
"""

from datetime import datetime
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated

KEY = b"loops:due"

# Takes the members due by ARGV[1] off the set and returns them, atomically: a ring that lands after
# this call is kept for the next one.
TAKE_DUE = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if #due > 0 then redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1]) end
return due
"""


class RedisDoorbell:
    def __init__(self, redis: Redis, *, store: str = "state") -> None:
        self._redis = redis
        self._store = store
        self._take_due = redis.register_script(TAKE_DUE)

    async def ring(self, space_id: UUID, at: datetime) -> None:
        async with translated(self._store, "doorbell_ring"):
            # LT: an earlier ring for the same space wins; a new member is always added.
            await self._redis.zadd(KEY, {space_id.hex: at.timestamp()}, lt=True)

    async def due(self, now: datetime) -> list[UUID]:
        async with translated(self._store, "doorbell_due"):
            members = await self._take_due(keys=[KEY], args=[now.timestamp()])
        return [UUID(hex=m.decode() if isinstance(m, bytes) else str(m)) for m in members]
