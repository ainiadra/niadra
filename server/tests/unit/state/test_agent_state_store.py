"""The working state's script on the state store (24.3.9; agent-state 4 and 5): the version, the cap and the
change in one atomic step, from the sizes alone. On fakeredis with Lua, and again on a real Redis-compatible
store when NIADRA_TEST_REDIS names one."""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import fakeredis
import pytest
from redis.asyncio import Redis

from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.agent_state import RedisAgentStateStore
from niadra.domain.state.agent_state import fragment_size
from niadra.ports.state import HeldField, HeldState, WriteResult

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
NOW = 1_790_000_000_000


@pytest.fixture(params=["fakeredis", "redis"])
async def client(request: pytest.FixtureRequest) -> AsyncIterator[Redis]:
    if request.param == "redis" and not REDIS:
        pytest.skip("needs NIADRA_TEST_REDIS")
    redis: Redis = fakeredis.FakeAsyncRedis() if request.param == "fakeredis" else connect(REDIS)
    yield redis
    await redis.aclose()


@pytest.fixture
def key() -> str:
    return f"ast:{uuid4()}:{{scope}}:src:agent"


def field(value: object, name: str, at: int = NOW) -> HeldField:
    return HeldField(f"sealed:{value}".encode(), fragment_size(name, value), at)


async def fresh(store: RedisAgentStateStore, key: str) -> None:
    await store.restore(key, HeldState(0, 0), 60)


async def test_a_state_the_store_does_not_hold_is_restored_before_it_is_written(
    client: Redis, key: str
) -> None:
    store = RedisAgentStateStore(client)
    missing = await store.write(
        key, {"step": field(1, "step")}, replace=True, if_version=0, now_ms=NOW, ttl_s=60
    )
    assert (missing.result, missing.version) == (WriteResult.MISSING, 0)
    await fresh(store, key)
    await store.restore(key, HeldState(9, NOW, {}), 60)  # restored only once: the first one stays
    stored = await store.write(
        key, {"step": field(1, "step")}, replace=True, if_version=0, now_ms=NOW, ttl_s=60
    )
    assert (stored.result, stored.version) == (WriteResult.STORED, 1)
    assert stored.state is not None
    assert stored.state.fields["step"] == field(1, "step")
    assert await client.ttl(key) > 0


async def test_compare_and_swap_applies_only_at_the_version_it_read(client: Redis, key: str) -> None:
    store = RedisAgentStateStore(client)
    await fresh(store, key)
    write = {"step": field(1, "step"), "offer": field("a", "offer")}
    first = await store.write(key, write, replace=True, if_version=0, now_ms=NOW, ttl_s=60)
    assert first.version == 1
    stale = await store.write(
        key, {"step": field(2, "step")}, replace=True, if_version=0, now_ms=NOW, ttl_s=60
    )
    assert (stale.result, stale.version) == (WriteResult.CONFLICT, 1)
    whole = await store.write(
        key, {"step": field(2, "step")}, replace=True, if_version=1, now_ms=NOW, ttl_s=60
    )
    assert whole.state is not None
    assert set(whole.state.fields) == {"step"}  # a field left out of a compare-and-swap is gone


async def test_merge_by_key_replaces_named_fields_and_removes_explicitly(client: Redis, key: str) -> None:
    store = RedisAgentStateStore(client)
    await fresh(store, key)
    await store.write(
        key,
        {"a": field(1, "a"), "b": field(2, "b")},
        replace=False,
        if_version=None,
        now_ms=NOW,
        ttl_s=60,
    )
    # Two sub-agents writing different fields, in any order, keep each other's writes.
    await store.write(key, {"c": field(3, "c")}, replace=False, if_version=None, now_ms=NOW, ttl_s=60)
    merged = await store.write(
        key,
        {"a": None, "b": field(20, "b")},
        replace=False,
        if_version=None,
        now_ms=NOW + 5,
        ttl_s=60,
    )
    assert merged.state is not None
    assert merged.version == 3
    assert set(merged.state.fields) == {"b", "c"}
    assert merged.state.fields["b"].written_ms == NOW + 5
    again = await store.write(
        key, {"c": field(30, "c")}, replace=False, if_version=None, now_ms=NOW, ttl_s=60
    )
    assert again.state is not None
    assert "a" not in again.state.fields  # a removed field never comes back


async def test_a_state_is_kept_whole_whatever_its_size(client: Redis, key: str) -> None:
    """Memory never cuts: the state used to be dropped past a cap, the previous one kept."""
    store = RedisAgentStateStore(client)
    await fresh(store, key)
    first = await store.write(
        key, {"a": field("x" * 10, "a")}, replace=False, if_version=None, now_ms=NOW, ttl_s=60
    )
    assert first.result is WriteResult.STORED
    big = {"b": field("y" * 200_000, "b")}
    whole = await store.write(key, big, replace=False, if_version=None, now_ms=NOW, ttl_s=60)
    assert (whole.result, whole.version) == (WriteResult.STORED, 2)
    kept = await store.load(key)
    assert kept is not None
    assert set(kept.fields) == {"a", "b"}
    assert kept.fields["b"].size == fragment_size("b", "y" * 200_000)


async def test_a_conflict_is_checked_first(client: Redis, key: str) -> None:
    store = RedisAgentStateStore(client)
    await fresh(store, key)
    big = {"a": field("z" * 100, "a")}
    outcome = await store.write(key, big, replace=True, if_version=4, now_ms=NOW, ttl_s=60)
    assert outcome.result is WriteResult.CONFLICT
