"""The hot copy of the shared objects' working set on the cache store (24.3.12; 06, 15.3).

One hash per object in the working set: `#id` its id, `#in` its membership, `#t` its typed stamps (the values'
versions, the timers, the latches; never a value) and `#tn` their sequence, and per field and source
(`<field>|<source>`) `v:` the source's version and `p:` the observation (the value sealed, the stamps in the
clear). Every key of a space shares its hash tag, so one script decides a whole push request atomically and
marks the objects it moved in the space's dirty set, which the consolidator takes from. The script never opens
a value: the versions decide.

The keys carry no cache epoch: the ingest, worker and loops processes all write here, and only the read
process follows the epoch. A store that comes back empty loses the loaded mark, and the consolidator loads the
working set again; the database never moves back, since it too keeps only greater versions.

The objects of typed shared types the consolidator wrote wait for the typed pass in a set on the state store,
which evicts nothing (`tp:`): a mark the cache lost would leave a timer unarmed.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated

# KEYS[1]: the dirty set; KEYS[2..]: the objects. ARGV per object: digest, version, n, then n pairs of
# (`field|source`, observation). A field of a source moves when the version is greater than the one that last
# wrote it (object-state 10.1); an object not in the working set keeps nothing.
PUSH = """
local out = {}
local a = 1
for k = 2, #KEYS do
  local key = KEYS[k]
  local digest, version, n = ARGV[a], ARGV[a + 1], tonumber(ARGV[a + 2])
  local base = a + 3
  a = base + 2 * n
  if redis.call('HEXISTS', key, '#in') == 0 then
    out[#out + 1] = 'out_of_set'
  else
    local moved = false
    for j = 0, n - 1 do
      local slot = ARGV[base + 2 * j]
      local current = redis.call('HGET', key, 'v:' .. slot)
      if (not current) or tonumber(version) > tonumber(current) then
        redis.call('HSET', key, 'v:' .. slot, version, 'p:' .. slot, ARGV[base + 2 * j + 1])
        moved = true
      end
    end
    if moved then
      redis.call('SADD', KEYS[1], digest)
      out[#out + 1] = 'applied'
    else
      out[#out + 1] = 'stale_version'
    end
  end
end
return out
"""

# KEYS: the objects. ARGV per object: id, ttl_s, typed stamps (empty for none), their sequence, n, then n
# triples of (`field|source`, version, observation): seeded only into an object not held; a held one keeps its
# own fields and stamps, which are as new or newer.
ENTER = """
local a = 1
for k = 1, #KEYS do
  local key = KEYS[k]
  local id, ttl, typed, seq, n = ARGV[a], ARGV[a + 1], ARGV[a + 2], ARGV[a + 3], tonumber(ARGV[a + 4])
  local base = a + 5
  a = base + 3 * n
  if redis.call('EXISTS', key) == 0 then
    redis.call('HSET', key, '#id', id)
    if typed ~= '' then
      redis.call('HSET', key, '#t', typed, '#tn', seq)
    end
    for j = 0, n - 1 do
      local slot = ARGV[base + 3 * j]
      redis.call('HSET', key, 'v:' .. slot, ARGV[base + 3 * j + 1], 'p:' .. slot, ARGV[base + 3 * j + 2])
    end
  end
  redis.call('HSET', key, '#in', '1')
  redis.call('EXPIRE', key, ttl)
end
return 1
"""


# KEYS: the objects. ARGV: per object, its typed stamps and their sequence; an object the cache no longer
# holds is left alone, and stamps of a sequence not greater than the held one are older: two writers publish
# after their commits in either order.
SET_TYPED = """
for k = 1, #KEYS do
  if redis.call('EXISTS', KEYS[k]) == 1 then
    local held = tonumber(redis.call('HGET', KEYS[k], '#tn') or '-1')
    if tonumber(ARGV[2 * k]) > held then
      redis.call('HSET', KEYS[k], '#t', ARGV[2 * k - 1], '#tn', ARGV[2 * k])
    end
  end
end
return 1
"""


def object_key(space_id: UUID, digest: str) -> str:
    return f"so:{{{space_id}}}:{digest}"


def dirty_key(space_id: UUID) -> str:
    return f"sd:{{{space_id}}}"


def typed_key(space_id: UUID) -> str:
    return f"tp:{{{space_id}}}"


def drift_key(space_id: UUID) -> str:
    return f"dr:{{{space_id}}}"


def loaded_key(space_id: UUID) -> str:
    """Set once the working set was loaded from the database; a store that came back empty lacks it."""
    return f"sm:{{{space_id}}}"


class RedisSharedObjects:
    """`state` is the store that evicts nothing, for the typed marks; the cache store's own client when the
    cell runs one store."""

    def __init__(self, redis: Redis, *, state: Redis | None = None, store: str = "cache") -> None:
        self._redis = redis
        self._state = state or redis
        self._store = store
        self._push = redis.register_script(PUSH)
        self._enter = redis.register_script(ENTER)
        self._set_typed = redis.register_script(SET_TYPED)

    async def push(self, space_id: UUID, items: Sequence[tuple[str, int, Mapping[str, bytes]]]) -> list[str]:
        if not items:
            return []
        keys = [dirty_key(space_id)]
        args: list[Any] = []
        for digest, version, fields in items:
            keys.append(object_key(space_id, digest))
            args += [digest, version, len(fields)]
            for slot, payload in sorted(fields.items()):
                args += [slot, payload]
        async with translated(self._store, "shared_push"):
            found = await self._push(keys=keys, args=args)
        return [_text(outcome) for outcome in found]

    async def enter(
        self,
        space_id: UUID,
        entries: Sequence[tuple[str, UUID, Mapping[str, tuple[int, bytes]], int, bytes | None, int]],
    ) -> None:
        if not entries:
            return
        keys: list[str] = []
        args: list[Any] = []
        for digest, object_id, fields, ttl_s, typed, seq in entries:
            keys.append(object_key(space_id, digest))
            args += [str(object_id), ttl_s, typed or b"", seq, len(fields)]
            for slot, (version, payload) in sorted(fields.items()):
                args += [slot, version, payload]
        async with translated(self._store, "shared_enter"):
            await self._enter(keys=keys, args=args)

    async def load(
        self, space_id: UUID, digests: Sequence[str]
    ) -> list[tuple[UUID, dict[str, tuple[int, bytes]], bytes | None] | None]:
        if not digests:
            return []
        async with translated(self._store, "shared_load"), self._redis.pipeline(transaction=False) as pipe:
            for digest in digests:
                pipe.hgetall(object_key(space_id, digest))
            raw = await pipe.execute()
        return [_held(found) for found in raw]

    async def take_dirty(self, space_id: UUID, limit: int) -> list[str]:
        async with translated(self._store, "shared_take_dirty"):
            found = await self._redis.spop(dirty_key(space_id), limit)
        return [_text(digest) for digest in found or ()]

    async def mark_dirty(self, space_id: UUID, digests: Sequence[str]) -> None:
        if digests:
            async with translated(self._store, "shared_mark_dirty"):
                await self._redis.sadd(dirty_key(space_id), *digests)

    async def loaded(self, space_id: UUID) -> bool:
        async with translated(self._store, "shared_loaded"):
            return bool(await self._redis.exists(loaded_key(space_id)))

    async def mark_loaded(self, space_id: UUID) -> None:
        async with translated(self._store, "shared_mark_loaded"):
            await self._redis.set(loaded_key(space_id), b"1")

    async def set_typed(self, space_id: UUID, entries: Sequence[tuple[str, bytes, int]]) -> None:
        if entries:
            async with translated(self._store, "shared_set_typed"):
                await self._set_typed(
                    keys=[object_key(space_id, digest) for digest, _, _ in entries],
                    args=[arg for _, typed, seq in entries for arg in (typed, seq)],
                )

    async def mark_typed(self, space_id: UUID, object_ids: Sequence[UUID]) -> None:
        if object_ids:
            async with translated("state", "shared_mark_typed"):
                await self._state.sadd(typed_key(space_id), *[str(i) for i in object_ids])

    async def take_typed(self, space_id: UUID, limit: int) -> list[UUID]:
        async with translated("state", "shared_take_typed"):
            found = await self._state.spop(typed_key(space_id), limit)
        return [UUID(_text(i)) for i in found or ()]

    async def note_drift(self, space_id: UUID, entries: Sequence[bytes]) -> None:
        if entries:
            async with translated("state", "shared_note_drift"):
                await self._state.sadd(drift_key(space_id), *entries)

    async def take_drift(self, space_id: UUID, limit: int) -> list[bytes]:
        async with translated("state", "shared_take_drift"):
            found = await self._state.spop(drift_key(space_id), limit)
        return [_text(i).encode() for i in found or ()]

    async def forget(self, space_id: UUID, digests: Sequence[str]) -> None:
        if digests:
            async with translated(self._store, "shared_forget"):
                await self._redis.delete(*[object_key(space_id, d) for d in digests])


def _held(raw: Mapping[Any, Any]) -> tuple[UUID, dict[str, tuple[int, bytes]], bytes | None] | None:
    pairs = {_text(k): v for k, v in raw.items()}
    if "#in" not in pairs or "#id" not in pairs:
        return None
    fields = {
        name[2:]: (int(pairs[name]), bytes(pairs[f"p:{name[2:]}"]))
        for name in pairs
        if name.startswith("v:") and f"p:{name[2:]}" in pairs
    }
    typed = pairs.get("#t")
    return UUID(_text(pairs["#id"])), fields, bytes(typed) if typed else None


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
