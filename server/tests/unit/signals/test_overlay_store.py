"""The session overlay's scripts on the state store (24.3.2): entries added once and kept out once folded, and
the marker a rebuild claims and seals after the store came back empty. On fakeredis with Lua, and again on a
real Redis-compatible store when NIADRA_TEST_REDIS names one."""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import fakeredis
import pytest
from redis.asyncio import Redis

from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.signals import MARKER_KEY, RedisSignalOverlay

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")


@pytest.fixture(params=["fakeredis", "redis"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Redis]:
    if request.param == "redis" and not REDIS:
        pytest.skip("needs NIADRA_TEST_REDIS")
    client: Redis = fakeredis.FakeAsyncRedis() if request.param == "fakeredis" else connect(REDIS)
    await client.delete(MARKER_KEY)
    yield client
    await client.delete(MARKER_KEY)
    await client.aclose()


async def test_an_entry_folded_already_is_not_added_again(store: Redis) -> None:
    overlay, space, handle = RedisSignalOverlay(store), uuid4(), uuid4()
    await overlay.add(space, {handle: {"t:1:0": b"one", "t:1:1": b"two"}}, 60)
    await overlay.drop(space, handle, ["t:1:0"], 60)
    await overlay.add(space, {handle: {"t:1:0": b"one again"}}, 60)
    assert await overlay.read(space, [handle]) == {handle: {"t:1:1": b"two"}}
    await overlay.purge(space, [handle])
    assert await overlay.read(space, [handle]) == {}


async def test_one_rebuild_at_a_time_and_only_its_own_seal(store: Redis) -> None:
    overlay = RedisSignalOverlay(store)
    assert not await overlay.whole()
    assert await overlay.claim_rebuild("a", 60_000)
    assert not await overlay.claim_rebuild("b", 60_000)  # another rebuild holds it
    assert not await overlay.seal("b")
    assert await overlay.seal("a")
    assert await overlay.whole()
    assert not await overlay.claim_rebuild("c", 60_000)  # whole: nothing to rebuild


async def test_a_rebuild_that_dies_lets_another_take_over_when_its_lease_runs_out(store: Redis) -> None:
    overlay = RedisSignalOverlay(store)
    assert await overlay.claim_rebuild("a", 1)
    await store.delete(MARKER_KEY)  # what the lease's expiry does
    assert await overlay.claim_rebuild("b", 60_000)
    assert not await overlay.seal("a")
