"""The agents' working state on the state store (24.3.9; the agent-state spec, sections 4 and 5).

One hash per state: `#v` its version, `#t` when it was last written, and per field `f:<name>` the value sealed
on its own, `s:<name>` the size of its fragment in the state's compact JSON and `w:<name>` when it was
written. The write is one Lua script, so the version check and the change are one atomic step: it never reads
the state in one call and writes it in another, and it never opens a value. A state is kept whole, whatever
its size (memory never cuts). Times come from the application's clock as arguments.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated
from niadra.ports.state import HeldField, HeldState, WriteOutcome, WriteResult

# ARGV: replace ('1' or '0'), if_version (-1 for none), now_ms, ttl_s, then one triple per field written:
# name, sealed value ('' removes the field), size of its fragment.
WRITE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return {'missing', 0} end
local version = tonumber(redis.call('HGET', KEYS[1], '#v') or '0')
local wanted = tonumber(ARGV[2])
if wanted >= 0 and wanted ~= version then return {'conflict', version} end
if ARGV[1] == '1' then redis.call('DEL', KEYS[1]) end
for i = 5, #ARGV, 3 do
  local name = ARGV[i]
  if ARGV[i + 1] == '' then
    redis.call('HDEL', KEYS[1], 'f:' .. name, 's:' .. name, 'w:' .. name)
  else
    redis.call('HSET', KEYS[1], 'f:' .. name, ARGV[i + 1], 's:' .. name, ARGV[i + 2], 'w:' .. name, ARGV[3])
  end
end
redis.call('HSET', KEYS[1], '#v', tostring(version + 1), '#t', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return {'stored', version + 1, redis.call('HGETALL', KEYS[1])}
"""

# ARGV: ttl_s, version, updated_ms, then one quadruple per field: name, sealed value, size, written_ms.
RESTORE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], '#v', ARGV[2], '#t', ARGV[3])
for i = 4, #ARGV, 4 do
  local name = ARGV[i]
  redis.call('HSET', KEYS[1], 'f:' .. name, ARGV[i + 1], 's:' .. name, ARGV[i + 2], 'w:' .. name, ARGV[i + 3])
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""

# ARGV: ttl_s, version, updated_ms, then one quadruple per field: name, sealed value, size, written_ms.
# Puts the state only while the key is still at that version.
REWRITE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if tonumber(redis.call('HGET', KEYS[1], '#v') or '0') ~= tonumber(ARGV[2]) then return 0 end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1], '#v', ARGV[2], '#t', ARGV[3])
for i = 4, #ARGV, 4 do
  local name = ARGV[i]
  redis.call('HSET', KEYS[1], 'f:' .. name, ARGV[i + 1], 's:' .. name, ARGV[i + 2], 'w:' .. name, ARGV[i + 3])
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""


class RedisAgentStateStore:
    def __init__(self, redis: Redis, *, store: str = "state") -> None:
        self._redis = redis
        self._store = store
        self._write = redis.register_script(WRITE)
        self._restore = redis.register_script(RESTORE)
        self._rewrite = redis.register_script(REWRITE)

    async def load(self, key: str) -> HeldState | None:
        async with translated(self._store, "agent_state_load"):
            raw = await self._redis.hgetall(key)
        return held(_flat(raw)) if raw else None

    async def restore(self, key: str, state: HeldState, ttl_s: int) -> None:
        args: list[Any] = [ttl_s, state.version, state.updated_ms]
        for name, f in sorted(state.fields.items()):
            args += [name, f.sealed, f.size, f.written_ms]
        async with translated(self._store, "agent_state_restore"):
            await self._restore(keys=[key], args=args)

    async def rewrite(self, key: str, state: HeldState, ttl_s: int) -> bool:
        args: list[Any] = [ttl_s, state.version, state.updated_ms]
        for name, f in sorted(state.fields.items()):
            args += [name, f.sealed, f.size, f.written_ms]
        async with translated(self._store, "agent_state_rewrite"):
            return bool(await self._rewrite(keys=[key], args=args))

    async def write(
        self,
        key: str,
        changes: dict[str, HeldField | None],
        *,
        replace: bool,
        if_version: int | None,
        now_ms: int,
        ttl_s: int,
    ) -> WriteOutcome:
        args: list[Any] = ["1" if replace else "0", -1 if if_version is None else if_version, now_ms, ttl_s]
        for name, change in sorted(changes.items()):
            args += [name, b"", 0] if change is None else [name, change.sealed, change.size]
        async with translated(self._store, "agent_state_write"):
            found = await self._write(keys=[key], args=args)
        result = WriteResult(_text(found[0]))
        state = held(list(found[2])) if result is WriteResult.STORED else None
        return WriteOutcome(result, int(found[1]), state)

    async def forget(self, keys: Sequence[str]) -> None:
        if keys:
            async with translated(self._store, "agent_state_forget"):
                await self._redis.delete(*keys)

    async def track(self, space_id: UUID, marks: Sequence[bytes], tag: str, ttl_s: int) -> None:
        if not marks:
            return
        async with translated(self._store, "agent_state_track"):
            pipe = self._redis.pipeline(transaction=False)
            for mark in marks:
                pipe.sadd(_index(space_id, mark), tag)
                pipe.expire(_index(space_id, mark), ttl_s)
            await pipe.execute()

    async def tracked(self, space_id: UUID, marks: Sequence[bytes]) -> list[str]:
        if not marks:
            return []
        async with translated(self._store, "agent_state_tracked"):
            pipe = self._redis.pipeline(transaction=False)
            for mark in marks:
                pipe.smembers(_index(space_id, mark))
            found = await pipe.execute()
        return sorted({_text(tag) for tags in found for tag in tags})


def _index(space_id: UUID, mark: bytes) -> str:
    return f"asi:{space_id}:{mark.hex()}"


def held(flat: Sequence[Any]) -> HeldState:
    """A state from the hash's fields and values, as `HGETALL` lists them."""
    pairs = {_text(flat[i]): flat[i + 1] for i in range(0, len(flat), 2)}
    names = {name[2:] for name in pairs if name.startswith("f:")}
    fields = {
        name: HeldField(bytes(pairs[f"f:{name}"]), int(pairs[f"s:{name}"]), int(pairs[f"w:{name}"]))
        for name in names
    }
    return HeldState(int(pairs.get("#v", 0)), int(pairs.get("#t", 0)), fields)


def _flat(raw: dict[Any, Any]) -> list[Any]:
    return [item for pair in raw.items() for item in pair]


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
