"""Redis adapters on fakeredis with Lua (06, 3 and 6.4)."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import fakeredis
import pytest

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.adapters.outbound.redis.errors import CACHE_FAILURES
from niadra.adapters.outbound.redis.hot import MARKER_KEY, RedisHotLayer, hot_key
from niadra.domain.common.ids import uuid7
from niadra.domain.serve.cache_keys import gen_key, pin_key, t0_key
from niadra.domain.vocabulary import Verification
from niadra.entrypoints.cell import open_redis
from niadra.entrypoints.settings import Settings
from niadra.ports.infra import CacheUnavailable, HotLayerState, HotTurn

HOUR_MS = 3600 * 1000
SPACE = UUID("0192f5a0-0000-7000-8000-00000000a001")


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis()
    yield client
    await client.aclose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


def now_ms(clock: FixedClock) -> int:
    return int(clock.now().timestamp() * 1000)


def turn(at_ms: int, body: bytes = b"sealed") -> HotTurn:
    return HotTurn(uuid7(at_ms), at_ms, body)


async def test_hot_layer_reads_the_union_of_handles_in_time_order(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    t0 = now_ms(clock)
    a, b = uuid7(), uuid7()
    shared = turn(t0 - 3000, b"both")
    await hot.add(SPACE, a, [turn(t0 - 1000, b"\x00binary\xff"), shared], 72 * HOUR_MS)
    await hot.add(SPACE, b, [shared, turn(t0 - 2000)], 72 * HOUR_MS)
    turns = (await hot.read(SPACE, [a, b], since_ms=t0 - 2500)).turns
    assert [t.occurred_at_ms for t in turns] == [t0 - 2000, t0 - 1000]
    assert turns[1].envelope_ct == b"\x00binary\xff"
    everything = (await hot.read(SPACE, [a, b], since_ms=0)).turns
    assert len(everything) == 3  # the turn under both handles comes once


async def test_hot_layer_trims_beyond_the_cap_and_extends_the_ttl(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    handle = uuid7()
    t0 = now_ms(clock)
    await hot.add(SPACE, handle, [turn(t0 - 73 * HOUR_MS), turn(t0 - HOUR_MS)], 72 * HOUR_MS)
    assert len((await hot.read(SPACE, [handle], 0)).turns) == 1
    ttl = await redis.pttl(hot_key(SPACE, handle))
    assert 71 * HOUR_MS < ttl <= 72 * HOUR_MS


async def test_shorten_lowers_the_ttl_only_when_every_turn_is_covered(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    handle = uuid7()
    key = hot_key(SPACE, handle)
    t0 = now_ms(clock)
    await hot.add(SPACE, handle, [turn(t0 - 2000), turn(t0 - 1000)], 72 * HOUR_MS)

    await hot.shorten(SPACE, handle, covered_until_ms=t0 - 2000, keep_ms=24 * HOUR_MS)
    assert await redis.pttl(key) > 71 * HOUR_MS  # a newer turn is not covered yet

    await hot.shorten(SPACE, handle, covered_until_ms=t0 - 1000, keep_ms=24 * HOUR_MS)
    assert 23 * HOUR_MS < await redis.pttl(key) <= 24 * HOUR_MS

    await hot.shorten(SPACE, handle, covered_until_ms=t0, keep_ms=48 * HOUR_MS)
    assert await redis.pttl(key) <= 24 * HOUR_MS  # never raised

    await hot.add(SPACE, handle, [turn(t0)], 72 * HOUR_MS)
    assert await redis.pttl(key) > 71 * HOUR_MS  # a new turn extends it back to the cap


async def test_hot_layer_uses_the_handle_as_hash_tag_and_purges(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    handle = uuid7()
    assert hot_key(SPACE, handle) == f"hot:{SPACE}:{{{handle}}}"
    hot = RedisHotLayer(redis, clock)
    await hot.add(SPACE, handle, [turn(now_ms(clock))], HOUR_MS)
    await hot.purge(SPACE, [handle, uuid7()])
    assert (await hot.read(SPACE, [handle], 0)).turns == []


async def test_put_if_generation_needs_an_existing_generation_that_is_not_newer(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    cache = RedisCache(redis)
    pack, gen = "t0:1:s:{p}:v", "gen:1:s:{p}"
    assert await cache.put_if_generation(pack, gen, b"pack-3", 3, 60) is False  # no generation: never write
    await cache.bump_generation(gen, 3, 60)
    assert await cache.put_if_generation(pack, gen, b"pack-2", 2, 60) is False
    assert await cache.get(pack) is None
    assert await cache.put_if_generation(pack, gen, b"pack-3", 3, 60) is True
    assert await cache.get(pack) == b"pack-3"
    assert 0 < await redis.ttl(pack) <= 60


async def test_generations_only_move_forward(redis: fakeredis.FakeAsyncRedis) -> None:
    cache = RedisCache(redis)
    await cache.bump_generation("gen", 5, 60)
    await cache.bump_generation("gen", 4, 120)
    assert await cache.get("gen") == b"5"
    assert await redis.ttl("gen") > 60  # the TTL is refreshed even when the value stays
    await cache.bump_generation("gen", 6, 60)
    assert await cache.get("gen") == b"6"


async def test_window_counter_expires_with_its_window(redis: fakeredis.FakeAsyncRedis) -> None:
    cache = RedisCache(redis)
    assert [await cache.incr_window("rl:k", 10) for _ in range(3)] == [1, 2, 3]
    assert 0 < await redis.ttl("rl:k") <= 10


async def test_multi_key_calls_work_key_by_key(redis: fakeredis.FakeAsyncRedis) -> None:
    cache = RedisCache(redis)
    await cache.set("a", b"1", ttl_s=30)
    await cache.set("b", b"2")
    assert await cache.get_many(["a", "missing", "b"]) == [b"1", None, b"2"]
    await cache.delete(["a", "b"])
    assert await cache.get_many(["a", "b"]) == [None, None]
    assert await cache.get_many([]) == []


async def test_a_profiles_compiled_context_leaves_the_cache_on_erasure(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # Packs, pins, the generation and the watermarks of one profile, in any cache epoch.
    cache = RedisCache(redis)
    profile, other = uuid7(), uuid7()
    for epoch in (0, 3):
        await cache.set(t0_key(epoch, SPACE, profile, "chat|V1"), b"pack")
        await cache.set(gen_key(epoch, SPACE, profile), b"4")
        await cache.set(pin_key(epoch, SPACE, profile, "c1", "chat", uuid7()), b"pin")
        await cache.set(t0_key(epoch, SPACE, other, "chat|V1"), b"theirs")
    assert await cache.purge_profile(SPACE, profile) == 6
    assert await cache.get(t0_key(0, SPACE, other, "chat|V1")) == b"theirs"
    assert await cache.get(gen_key(3, SPACE, profile)) is None


async def test_the_newest_turns_come_from_the_end_of_each_handle(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    # A busy number: the read asks for the newest turns only, still oldest first in the answer.
    hot = RedisHotLayer(redis, clock)
    handle, t0 = uuid7(), now_ms(clock)
    await hot.add(SPACE, handle, [turn(t0 - i * 1000) for i in range(10)], cap_ms=HOUR_MS)
    newest = (await hot.read(SPACE, [handle], 0, newest=3)).turns
    assert [t.occurred_at_ms for t in newest] == [t0 - 2000, t0 - 1000, t0]


async def test_a_space_purge_clears_the_state_store_too(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    state = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())
    try:
        other = uuid7()
        handle = uuid7()
        await RedisHotLayer(state, clock).add(SPACE, handle, [turn(now_ms(clock))], HOUR_MS)
        await RedisHotLayer(state, clock).add(other, handle, [turn(now_ms(clock))], HOUR_MS)
        await redis.set(t0_key(1, SPACE, uuid7(), "chat"), b"pack")
        cache = RedisCache(redis, also_purged=[state])
        assert await cache.purge_space(SPACE) == 2
        assert await state.exists(hot_key(SPACE, handle)) == 0
        assert await state.exists(hot_key(other, handle)) == 1  # another space keeps its turns
    finally:
        await state.aclose()


async def test_failures_are_counted_under_the_store_they_happened_in() -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    down = fakeredis.FakeAsyncRedis(server=server)
    try:
        failed = CACHE_FAILURES.labels("state", "incr_window")
        before = failed._value.get()
        with pytest.raises(CacheUnavailable):
            await RedisCache(down, store="state").incr_window("rate:k:read:1", 1)
        assert failed._value.get() == before + 1
    finally:
        await down.aclose()


@pytest.mark.parametrize(
    ("state_url", "separate"),
    [(None, False), ("redis://valkey:6379/0", False), ("redis://valkey-state:6379/0", True)],
)
async def test_the_state_store_is_its_own_client_only_when_it_has_its_own_address(
    state_url: str | None, separate: bool
) -> None:
    settings = cast(Settings, SimpleNamespace(redis_url="redis://valkey:6379/0", state_redis_url=state_url))
    async with AsyncExitStack() as stack:
        cache, state = open_redis(settings, stack)
        assert (state is not cache) is separate


async def test_the_marker_is_lost_when_the_store_comes_back_empty(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    handle = uuid7()
    assert await hot.state() is HotLayerState.LOST  # a store never sealed says so too
    assert await hot.claim_rebuild("a", 60_000)
    assert not await hot.claim_rebuild("b", 60_000)  # one rebuilder at a time
    assert await hot.state() is HotLayerState.REBUILDING
    await hot.add(SPACE, handle, [turn(now_ms(clock))], HOUR_MS)
    assert not (await hot.read(SPACE, [handle], 0)).whole  # rebuilt turns are not whole until sealed
    assert await hot.extend_rebuild("a", 60_000)
    assert not await hot.seal("b")
    assert await hot.seal("a")
    assert await redis.pttl(MARKER_KEY) == -1  # sealed for good: the lease is gone
    read = await hot.read(SPACE, [handle], 0)
    assert (len(read.turns), read.whole) == (1, True)

    await redis.flushall()  # a restart without persistence

    read = await hot.read(SPACE, [handle], 0)
    assert (read.turns, read.whole) == ([], False)
    assert await hot.state() is HotLayerState.LOST


async def test_a_rebuild_cannot_seal_a_store_that_restarted_under_it(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    assert await hot.claim_rebuild("a", 60_000)
    await redis.flushall()
    assert not await hot.extend_rebuild("a", 60_000)
    assert not await hot.seal("a")
    assert await hot.state() is HotLayerState.LOST


async def test_a_space_purge_leaves_the_marker(redis: fakeredis.FakeAsyncRedis, clock: FixedClock) -> None:
    hot = RedisHotLayer(redis, clock)
    assert await hot.claim_rebuild("a", 60_000)
    assert await hot.seal("a")
    await RedisCache(redis).purge_space(SPACE)
    assert await hot.state() is HotLayerState.WHOLE


async def test_held_proofs_give_the_highest_level_until_their_turns_are_stored(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    name, other = f"hp:{SPACE}:{{abc}}", f"hp:{SPACE}:{{def}}"
    first, second = uuid7(), uuid7()
    await hot.hold_proofs([(name, first, Verification.V1), (name, second, Verification.V2)], 60_000)
    assert await hot.held_proof(name) is Verification.V2
    assert await hot.held_proof(other) is None
    assert 0 < await redis.pttl(name) <= 60_000

    await hot.release_proofs([(name, second)])
    assert await hot.held_proof(name) is Verification.V1
    await hot.release_proofs([(name, first)])
    assert await redis.exists(name) == 0


async def test_a_space_purge_drops_its_held_proofs(
    redis: fakeredis.FakeAsyncRedis, clock: FixedClock
) -> None:
    hot = RedisHotLayer(redis, clock)
    name = f"hp:{SPACE}:{{abc}}"
    await hot.hold_proofs([(name, uuid7(), Verification.V1)], 60_000)
    await RedisCache(redis).purge_space(SPACE)
    assert await hot.held_proof(name) is None
