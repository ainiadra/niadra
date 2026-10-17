"""What erasures reached, and when, on the state store, which never evicts (`ports.erasure.ErasureMarks`).

One string per mark, `erased:<space>:<mark hex>`, holding the erasure time in microseconds since the epoch, as
exact as the records' own times; it expires with the fence. Every module's fence shares them: the agents'
working state, the objects the tools observed and coordination read the same marks.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# KEYS: the marks; ARGV: the erasure time in microseconds, the time to live in ms. The newer time wins.
FENCE = """
for _, key in ipairs(KEYS) do
  local held = tonumber(redis.call('GET', key) or '-1')
  if tonumber(ARGV[1]) >= held then redis.call('SET', key, ARGV[1], 'PX', ARGV[2]) end
end
return #KEYS
"""


class RedisErasureMarks:
    def __init__(self, redis: Redis, *, store: str = "state") -> None:
        self._redis = redis
        self._store = store
        self._fence = redis.register_script(FENCE)

    async def fence(self, space_id: UUID, marks: Sequence[bytes], at: datetime, ttl_s: int) -> None:
        if not marks:
            return
        keys = sorted({mark_key(space_id, m) for m in marks})
        async with translated(self._store, "erasure_fence"):
            await self._fence(keys=keys, args=[_us(at), ttl_s * 1000])

    async def fenced(self, space_id: UUID, marks: Sequence[bytes]) -> dict[bytes, datetime]:
        unique = list(dict.fromkeys(marks))
        if not unique:
            return {}
        async with translated(self._store, "erasure_fenced"):
            found = await self._redis.mget([mark_key(space_id, m) for m in unique])
        return {m: _at(raw) for m, raw in zip(unique, found, strict=True) if raw is not None}


def mark_key(space_id: UUID, mark: bytes) -> str:
    return f"erased:{space_id}:{mark.hex()}"


def _us(at: datetime) -> int:
    return (at - EPOCH) // timedelta(microseconds=1)


def _at(raw: Any) -> datetime:
    return EPOCH + timedelta(microseconds=int(raw))
