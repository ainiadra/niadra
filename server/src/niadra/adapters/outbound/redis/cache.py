"""`KeyValueCache` on a Redis-compatible store (5.11; 06, 3).

Multi-key reads and deletes go through pipelines of single-key commands, because keys of one
call may live in different cluster slots. The pack and its generation share a hash tag, so
the atomic scripts below touch one slot.
"""

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated

# Stores the pack only if the generation exists and the caller's is not older (06, 3:
# "the T0 only accepts what it can prove"); a missing generation means do not write.
PUT_IF_GENERATION = """
local current = redis.call('GET', KEYS[2])
if not current then return 0 end
if tonumber(ARGV[2]) < tonumber(current) then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""

# Generations only move forward, whatever the order the writers arrive in.
BUMP_GENERATION = """
local current = redis.call('GET', KEYS[1])
if (not current) or tonumber(current) < tonumber(ARGV[1]) then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
else
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 1
"""

# The expiry is set with the first increment, atomically, so a crash never leaves a window
# counter without a TTL.
INCR_WINDOW = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


class RedisCache:
    """`store` names the failures in `niadra_cache_failures_total`: `cache` for the evicting store,
    `state` for the one that never evicts. `also_purged` are the other stores a space or profile
    purge must clear, so the hot layer's `hot:<space>:` keys go with the space wherever they live."""

    def __init__(self, redis: Redis, *, store: str = "cache", also_purged: Sequence[Redis] = ()) -> None:
        self._redis = redis
        self._store = store
        self._also_purged = tuple(also_purged)
        self._put_if_generation = redis.register_script(PUT_IF_GENERATION)
        self._bump_generation = redis.register_script(BUMP_GENERATION)
        self._incr_window = redis.register_script(INCR_WINDOW)

    @property
    def client(self) -> Redis:
        """The store's client, for a module's own scripts on it (the working set's hot copy, 24.3.12)."""
        return self._redis

    async def get(self, key: str) -> bytes | None:
        async with translated(self._store, "get"):
            return cast(bytes | None, await self._redis.get(key))

    async def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        if not keys:
            return []
        async with translated(self._store, "get_many"), self._redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.get(key)
            return list(await pipe.execute())

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        async with translated(self._store, "set"):
            await self._redis.set(key, value, ex=ttl_s)

    async def delete(self, keys: Sequence[str]) -> None:
        if not keys:
            return
        async with translated(self._store, "delete"), self._redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.delete(key)
            await pipe.execute()

    async def put_and_get_many(
        self, key: str, value: bytes | None, ttl_s: int, keys: Sequence[str]
    ) -> tuple[bool, list[bytes | None]]:
        operation = "put_and_get_many"
        async with translated(self._store, operation), self._redis.pipeline(transaction=False) as pipe:
            if value is None:
                pipe.expire(key, ttl_s)
            else:
                pipe.set(key, value, ex=ttl_s)
            for other in keys:
                pipe.get(other)
            stored, *found = await pipe.execute()
        return bool(stored), list(found)

    async def put_if_generation(
        self, pack_key: str, gen_key: str, value: bytes, generation: int, ttl_s: int
    ) -> bool:
        async with translated(self._store, "put_if_generation"):
            return bool(
                await self._put_if_generation(keys=[pack_key, gen_key], args=[value, generation, ttl_s])
            )

    async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None:
        async with translated(self._store, "bump_generation"):
            await self._bump_generation(keys=[gen_key], args=[generation, ttl_s])

    async def incr_window(self, key: str, window_s: int) -> int:
        async with translated(self._store, "incr_window"):
            return int(await self._incr_window(keys=[key], args=[window_s]))

    async def purge_profile(self, space_id: UUID, profile_id: UUID) -> int:
        return await self._purge(f"*:{space_id}:{{{profile_id}}}*", "purge_profile")

    async def purge_space(self, space_id: UUID) -> int:
        """SCAN, not KEYS: the scan walks the keyspace in pages and never blocks the server. The hot
        layer's `hot:<space>:` keys go too, from this store or from `also_purged`."""
        return await self._purge(f"*:{space_id}:*", "purge_space")

    async def _purge(self, pattern: str, operation: str) -> int:
        purged = 0
        async with translated(self._store, operation):
            for redis in (self._redis, *self._also_purged):
                batch: list[bytes] = []
                async for key in redis.scan_iter(match=pattern, count=1000):
                    batch.append(key)
                    if len(batch) >= 1000:
                        purged += await redis.unlink(*batch)
                        batch = []
                if batch:
                    purged += await redis.unlink(*batch)
        return purged
