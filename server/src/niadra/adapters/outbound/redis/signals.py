"""The session overlay of subject signals on the state store (24.3.2): one hash per handle, entry key to
sealed entry, and beside it the set of entries already folded, so a turn group applied again after its
derivation does not bring them back.

`MARKER_KEY` says whether the store still holds every entry: `whole`, `rebuilding:<token>` while one
rebuilder re-adds the pending interactions from the database, or missing after the store came back empty.
It names no space, so a space's purge never touches it."""

from collections.abc import Mapping, Sequence
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated
from niadra.domain.signals.storage import folded_key, overlay_key

# Adds the entries not folded yet and renews both keys' expiry.
ADD = """
local added = 0
for i = 2, #ARGV, 2 do
  if redis.call('SISMEMBER', KEYS[2], ARGV[i]) == 0 then
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
    added = added + 1
  end
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return added
"""

MARKER_KEY = "ov:marker"
WHOLE = b"whole"
REBUILDING = "rebuilding:"

CLAIM = """
local held = redis.call('GET', KEYS[1])
if held == ARGV[2] then return 0 end
if held and string.sub(held, 1, 11) == 'rebuilding:' then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[3])
return 1
"""

SEAL = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
return 1
"""

DROP = """
for i = 2, #ARGV do
  redis.call('HDEL', KEYS[1], ARGV[i])
  redis.call('SADD', KEYS[2], ARGV[i])
end
redis.call('EXPIRE', KEYS[2], ARGV[1])
return 1
"""


class RedisSignalOverlay:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._add = redis.register_script(ADD)
        self._drop = redis.register_script(DROP)
        self._claim = redis.register_script(CLAIM)
        self._seal = redis.register_script(SEAL)

    async def add(self, space_id: UUID, entries: Mapping[UUID, Mapping[str, bytes]], ttl_s: int) -> None:
        async with translated("state", "signals_add"):
            for handle_id, by_key in entries.items():
                if by_key:
                    args: list[str | bytes | int] = [ttl_s]
                    for key, sealed in by_key.items():
                        args += [key, sealed]
                    await self._add(
                        keys=[overlay_key(space_id, handle_id), folded_key(space_id, handle_id)], args=args
                    )

    async def read(self, space_id: UUID, handle_ids: Sequence[UUID]) -> dict[UUID, dict[str, bytes]]:
        async with translated("state", "signals_read"), self._redis.pipeline(transaction=False) as pipe:
            for handle_id in handle_ids:
                pipe.hgetall(overlay_key(space_id, handle_id))
            found = await pipe.execute()
        return {
            handle_id: {k.decode() if isinstance(k, bytes) else k: v for k, v in entries.items()}
            for handle_id, entries in zip(handle_ids, found, strict=True)
            if entries
        }

    async def drop(self, space_id: UUID, handle_id: UUID, entry_keys: Sequence[str], ttl_s: int) -> None:
        if not entry_keys:
            return
        async with translated("state", "signals_drop"):
            await self._drop(
                keys=[overlay_key(space_id, handle_id), folded_key(space_id, handle_id)],
                args=[ttl_s, *entry_keys],
            )

    async def whole(self) -> bool:
        async with translated("state", "signals_whole"):
            return bool(await self._redis.get(MARKER_KEY) == WHOLE)

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        async with translated("state", "signals_claim_rebuild"):
            return bool(await self._claim(keys=[MARKER_KEY], args=[REBUILDING + token, WHOLE, lease_ms]))

    async def seal(self, token: str) -> bool:
        async with translated("state", "signals_seal"):
            return bool(await self._seal(keys=[MARKER_KEY], args=[REBUILDING + token, WHOLE]))

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None:
        # Single-key deletes: a multi-key DEL across slots fails in cluster mode.
        async with translated("state", "signals_purge"), self._redis.pipeline(transaction=False) as pipe:
            for handle_id in handle_ids:
                pipe.delete(overlay_key(space_id, handle_id))
                pipe.delete(folded_key(space_id, handle_id))
            await pipe.execute()
