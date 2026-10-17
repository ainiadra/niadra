"""A full or unreachable Valkey never fails `context()`.

These run the real Redis adapters on fakeredis, raising the client's own errors (`OutOfMemoryError`
when the store is full under `noeviction`, `ConnectionError` when it is down), which are not
Python's `ConnectionError`: the doubles of the other tests raise Python's and would hide the bug.
"""

from dataclasses import replace
from typing import Any

import fakeredis
import pytest
from redis.exceptions import OutOfMemoryError

from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.adapters.outbound.redis.errors import CACHE_FAILURES
from niadra.adapters.outbound.redis.hot import RedisHotLayer
from niadra.contracts.context import ContextRequest
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, Verification
from niadra.ports.infra import CacheUnavailable
from niadra.testing.cell import SPACE_ID, Cell, principal
from tests.unit.compile.factories import PHONE, PROFILE
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()
WRITES = {"SET", "EVALSHA", "EVAL", "INCR", "EXPIRE", "ZADD", "DEL"}


def ask() -> ContextRequest:
    return ContextRequest(subject=SUBJECT, verification=Verification.V1, conversation_id="wa-now")


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


def on_valkey(cell: Cell, server: fakeredis.FakeServer) -> tuple[Cell, fakeredis.FakeAsyncRedis]:
    redis = fakeredis.FakeAsyncRedis(server=server)
    return replace(cell, cache=RedisCache(redis), hot=RedisHotLayer(redis, cell.clock)), redis


def full(redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valkey at `maxmemory` with `noeviction`: reads answer, every write is refused."""
    original = redis.execute_command

    async def refuse_writes(*args: Any, **options: Any) -> Any:
        if str(args[0]).upper() in WRITES:
            raise OutOfMemoryError("command not allowed when used memory > 'maxmemory'.")
        return await original(*args, **options)

    monkeypatch.setattr(redis, "execute_command", refuse_writes)


def failures(store: str, operation: str) -> float:
    return CACHE_FAILURES.labels(store, operation)._value.get()  # type: ignore[attr-defined, no-any-return]


async def test_with_valkey_down_the_read_compiles_on_demand_and_counts_the_failures(
    cell: Cell, server: fakeredis.FakeServer
) -> None:
    valkey, _ = on_valkey(cell, server)
    h = Harness(valkey)
    before = failures("cache", "get")
    server.connected = False
    response = await h.serve.context(AGENT, ask())
    assert response.path is DeliveryPath.T2
    assert "[Cliente] Marina" in (response.text or "")
    assert response.live_complete is False
    assert failures("cache", "get") > before
    assert failures("hot", "read") >= 1


async def test_with_valkey_down_a_stored_pack_answers(cell: Cell, server: fakeredis.FakeServer) -> None:
    valkey, _ = on_valkey(cell, server)
    h = Harness(valkey)
    await h.compile()
    server.connected = False
    response = await h.serve.context(AGENT, ask())
    assert response.path in (DeliveryPath.T1, DeliveryPath.T2)
    assert response.text


async def test_with_valkey_full_reads_still_answer_and_compilation_finishes(
    cell: Cell, server: fakeredis.FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    valkey, redis = on_valkey(cell, server)
    h = Harness(valkey)
    full(redis, monkeypatch)
    before = failures("cache", "set")
    await h.compiler.compile(Task(TaskName.COMPILE, SPACE_ID, profile_id=PROFILE))
    first = await h.serve.context(AGENT, ask())
    again = await h.serve.context(AGENT, ask())
    assert first.text
    assert again.text
    assert first.etag == again.etag
    assert failures("cache", "set") > before  # the pin could not be written, and nothing broke


async def test_the_adapters_raise_the_ports_error(cell: Cell, server: fakeredis.FakeServer) -> None:
    valkey, _ = on_valkey(cell, server)
    server.connected = False
    with pytest.raises(CacheUnavailable):
        await valkey.cache.get("k")
    with pytest.raises(CacheUnavailable):
        await valkey.hot.read(SPACE_ID, [PHONE], 0)
