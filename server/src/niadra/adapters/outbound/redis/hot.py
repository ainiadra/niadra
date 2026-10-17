"""The hot layer on a Redis-compatible store (5.4; 06, 6.4).

One sorted set per handle, scored by the turn's time. The hash tag goes on the handle, never
on the space, so in cluster mode a large tenant spreads over every slot (06, 3). A member is
the event id (16 bytes) followed by the sealed envelope.

`MARKER_KEY` says whether the store still holds every turn written to it: `whole`, or
`rebuilding:<token>` while one rebuilder re-adds turns from the event log, or missing after the store
came back empty. It names no space, so a space purge never touches it.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated
from niadra.domain.vocabulary import Verification
from niadra.ports.infra import Clock, HotLayerState, HotRead, HotTurn

ID_BYTES = 16
MARKER_KEY = "hot:marker"
WHOLE = b"whole"
REBUILDING = b"rebuilding:"
LEVELS = frozenset(v.value for v in Verification if v is not Verification.NO_CUSTOMER)

# Moves a rebuild's marker on only while it is still that rebuild's: a store that restarted again,
# or a lease that ran out, makes the rebuilder stop instead of sealing turns it no longer holds.
MARKER_IF_MINE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if ARGV[2] == 'seal' then
  redis.call('SET', KEYS[1], ARGV[3])
else
  redis.call('PEXPIRE', KEYS[1], ARGV[3])
end
return 1
"""

# Lowers the key's expiry to `covered_until + keep` only when no turn newer than the one the
# pack covers is in the set, and never raises it: a turn that arrived after compilation keeps
# the key alive for the full cap (06, 6.4). The deadline is passed as a relative TTL computed
# from the application clock, so the store's own clock never enters the comparison.
SHORTEN = """
local newest = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
if #newest == 0 then return 0 end
if tonumber(newest[2]) > tonumber(ARGV[1]) then return 0 end
local remaining = tonumber(ARGV[1]) + tonumber(ARGV[2]) - tonumber(ARGV[3])
local ttl = redis.call('PTTL', KEYS[1])
if ttl >= 0 and ttl <= remaining then return 0 end
if remaining <= 0 then
  redis.call('DEL', KEYS[1])
else
  redis.call('PEXPIRE', KEYS[1], remaining)
end
return 1
"""


# The members at the turn's score whose first 16 bytes are its event id: the envelope's nonce makes each
# write of the same turn a different member.
RETRACT = """
local n = 0
for _, member in ipairs(redis.call('ZRANGEBYSCORE', KEYS[1], ARGV[1], ARGV[1])) do
  if string.sub(member, 1, 16) == ARGV[2] then
    redis.call('ZREM', KEYS[1], member)
    n = n + 1
  end
end
return n
"""


# A held proof and its expiry in one step: a name never outlives `ttl_ms` without a field in it.
HOLD_PROOF = """
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""


def hot_key(space_id: UUID, handle_id: UUID) -> str:
    return f"hot:{space_id}:{{{handle_id}}}"


class RedisHotLayer:
    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock
        self._shorten = redis.register_script(SHORTEN)
        self._marker_if_mine = redis.register_script(MARKER_IF_MINE)
        self._retract = redis.register_script(RETRACT)
        self._hold_proof = redis.register_script(HOLD_PROOF)

    async def add(self, space_id: UUID, handle_id: UUID, turns: Sequence[HotTurn], cap_ms: int) -> None:
        if not turns:
            return
        key = hot_key(space_id, handle_id)
        members: dict[Any, float] = {t.event_id.bytes + t.envelope_ct: t.occurred_at_ms for t in turns}
        async with translated("hot", "add"), self._redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, members)
            pipe.zremrangebyscore(key, "-inf", f"({self._now_ms() - cap_ms}")
            pipe.pexpire(key, cap_ms)
            await pipe.execute()

    async def read(
        self, space_id: UUID, handle_ids: Sequence[UUID], since_ms: int, *, newest: int | None = None
    ) -> HotRead:
        if not handle_ids:
            return HotRead([])
        # One pipeline: a cluster client splits it per node, and the union happens here (06, 3). The
        # marker rides in it, so the marker and the turns come from the same life of the store.
        async with translated("hot", "read"), self._redis.pipeline(transaction=False) as pipe:
            pipe.get(MARKER_KEY)
            for handle_id in handle_ids:
                key = hot_key(space_id, handle_id)
                if newest is None:
                    pipe.zrangebyscore(key, since_ms, "+inf", withscores=True)
                else:
                    pipe.zrevrangebyscore(key, "+inf", since_ms, start=0, num=newest, withscores=True)
            marker, *results = await pipe.execute()
        turns: dict[UUID, HotTurn] = {}
        by_handle: dict[UUID, list[HotTurn]] = {}
        for handle_id, rows in zip(handle_ids, results, strict=True):
            own = [
                HotTurn(UUID(bytes=bytes(m[:ID_BYTES])), int(score), bytes(m[ID_BYTES:])) for m, score in rows
            ]
            own.sort(key=_order)
            by_handle[handle_id] = own
            for turn in own:
                turns.setdefault(turn.event_id, turn)
        return HotRead(sorted(turns.values(), key=_order), whole=marker == WHOLE, by_handle=by_handle)

    async def state(self) -> HotLayerState:
        async with translated("hot", "state"):
            marker = await self._redis.get(MARKER_KEY)
        if marker == WHOLE:
            return HotLayerState.WHOLE
        return HotLayerState.LOST if marker is None else HotLayerState.REBUILDING

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        async with translated("hot", "claim_rebuild"):
            return bool(await self._redis.set(MARKER_KEY, REBUILDING + token.encode(), px=lease_ms, nx=True))

    async def extend_rebuild(self, token: str, lease_ms: int) -> bool:
        async with translated("hot", "extend_rebuild"):
            mine = REBUILDING + token.encode()
            return bool(await self._marker_if_mine(keys=[MARKER_KEY], args=[mine, "extend", lease_ms]))

    async def seal(self, token: str) -> bool:
        async with translated("hot", "seal"):
            mine = REBUILDING + token.encode()
            return bool(await self._marker_if_mine(keys=[MARKER_KEY], args=[mine, "seal", WHOLE]))

    async def shorten(self, space_id: UUID, handle_id: UUID, covered_until_ms: int, keep_ms: int) -> None:
        async with translated("hot", "shorten"):
            await self._shorten(
                keys=[hot_key(space_id, handle_id)], args=[covered_until_ms, keep_ms, self._now_ms()]
            )

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None:
        # Single-key deletes: a multi-key DEL across slots fails in cluster mode.
        async with translated("hot", "purge"), self._redis.pipeline(transaction=False) as pipe:
            for handle_id in handle_ids:
                pipe.delete(hot_key(space_id, handle_id))
            await pipe.execute()

    async def retract(self, space_id: UUID, handle_id: UUID, event_id: UUID, occurred_at_ms: int) -> None:
        async with translated("hot", "retract"):
            await self._retract(keys=[hot_key(space_id, handle_id)], args=[occurred_at_ms, event_id.bytes])

    async def hold_proofs(self, held: Sequence[tuple[str, UUID, Verification]], ttl_ms: int) -> None:
        if not held:
            return
        async with translated("hot", "hold_proofs"), self._redis.pipeline(transaction=False) as pipe:
            for name, event_id, level in held:
                await self._hold_proof(keys=[name], args=[event_id.bytes, level.value, ttl_ms], client=pipe)
            await pipe.execute()

    async def held_proof(self, name: str) -> Verification | None:
        async with translated("hot", "held_proof"):
            values = await self._redis.hvals(name)
        found = [v.decode() if isinstance(v, bytes) else str(v) for v in values]
        levels = [Verification(v) for v in found if v in LEVELS]
        return max(levels, key=lambda level: level.rank, default=None)

    async def release_proofs(self, held: Sequence[tuple[str, UUID]]) -> None:
        if not held:
            return
        fields: dict[str, list[bytes]] = {}
        for name, event_id in held:
            fields.setdefault(name, []).append(event_id.bytes)
        async with translated("hot", "release_proofs"), self._redis.pipeline(transaction=False) as pipe:
            for name, ids in fields.items():
                pipe.hdel(name, *ids)
            await pipe.execute()

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)


def _order(turn: HotTurn) -> tuple[int, str]:
    return turn.occurred_at_ms, str(turn.event_id)
