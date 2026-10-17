"""The read path over the production adapters: PgBouncer in transaction mode with production's small
pools, receipts to RabbitMQ with publisher confirms, packs and hot turns in a Redis-compatible store,
the read role's background loops, and conversations opening (with their warm-up compilations) while
agents read at a steady rate.

Needs, besides NIADRA_TEST_DSN: NIADRA_TEST_PGBOUNCER (host:port of a transaction-mode PgBouncer in
front of that server, `default_pool_size = 4`, `max_db_connections = 6`), NIADRA_TEST_RABBITMQ (an AMQP
URL) and NIADRA_TEST_REDIS (a Redis URL). CI starts all three.
"""

import asyncio
import inspect
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import DeterministicModels, DictSecrets, InMemoryObjectStore, StaticConfig
from niadra.adapters.outbound.clock import SystemClock
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.outbox import OutboxPublisher, PublishingUnitOfWorkFactory
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.servers import Endpoint, Servers
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.adapters.outbound.rabbitmq.receipts import RabbitReceiptSink
from niadra.adapters.outbound.rabbitmq.tasks import RabbitTaskBus
from niadra.adapters.outbound.redis import connect as connect_redis
from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.adapters.outbound.redis.hot import RedisHotLayer
from niadra.app.compile import CompileService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.serve import LIVE_TURNS, ServeService
from niadra.app.sessions import SessionService, warm_task
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, HandleType, Verification
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CELL_DB, PgServer, clone_space
from tests.latency import SLACK, within

PGBOUNCER = os.environ.get("NIADRA_TEST_PGBOUNCER", "")
RABBITMQ = os.environ.get("NIADRA_TEST_RABBITMQ", "")
REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
PHONE = "+5511900001234"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
READ_BUDGET_S = 5.0
"""A read over the production adapters on a test machine; the incident this guards against was reads
that never answered. Held through `within`, ten times over on a shared machine."""

pytestmark = pytest.mark.skipif(
    not (PGBOUNCER and RABBITMQ and REDIS), reason="needs PgBouncer, RabbitMQ and Redis"
)


class SlowStore:
    """The cache or the hot layer across a slower network: every command takes `delay_s` more."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.delay_s = 0.0

    def __getattr__(self, attr: str) -> Any:
        found = getattr(self._inner, attr)
        if not inspect.iscoroutinefunction(found):
            return found

        async def call(*args: Any, **kwargs: Any) -> Any:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            return await found(*args, **kwargs)

        return call


@dataclass
class Stack:
    space_id: UUID
    caller: SourcePrincipal
    ingest: IngestService
    compile: CompileService
    serve: ServeService
    uow: PublishingUnitOfWorkFactory
    slow: tuple[SlowStore, SlowStore]
    config: StaticConfig


@pytest.fixture
async def stack(server: PgServer) -> AsyncIterator[Stack]:
    host, port = PGBOUNCER.rsplit(":", 1)
    servers = Servers(
        pooled=Endpoint(host, int(port)), direct=server.servers.direct, passwords={}, cell_database=CELL_DB
    )
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(sample_config(space_id))
    # The pools of a production process (entrypoints/cell.py): one connection and two more at most.
    cell_engine = make_engine(servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "stack"))
    rabbit = RabbitConnection(RABBITMQ)
    await rabbit.open()
    inner = PgUnitOfWorkFactory(keys, registry, cell_engine)
    publisher = OutboxPublisher(RabbitTaskBus(rabbit), inner)
    uow = PublishingUnitOfWorkFactory(inner, publisher)
    redis = connect_redis(REDIS)
    clock = SystemClock()
    cache, hot = SlowStore(RedisCache(redis)), SlowStore(RedisHotLayer(redis, clock))
    identity = IdentityService(uow, clock, config)
    sessions = SessionService(uow, clock, config)
    ingest = IngestService(
        uow,
        identity,
        sessions,
        keys,
        hot,
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),  # type: ignore[arg-type]
    )
    compile_ = CompileService(uow, keys, cache, clock, config, hot=hot)  # type: ignore[arg-type]
    receipts = RabbitReceiptSink(rabbit)
    serve = ServeService(
        uow, cache, hot, keys, receipts, compile_, DeterministicModels(), clock, config, "read-stack",  # type: ignore[arg-type]
        databases=registry,
    )  # fmt: skip
    try:
        caller = principal(WHATSAPP_SOURCE, config.space(space_id))
        yield Stack(space_id, caller, ingest, compile_, serve, uow, (cache, hot), config)
    finally:
        await publisher.drain()
        await rabbit.close()
        await redis.aclose()
        await registry.close()
        await cell_engine.dispose()


def turn(conversation: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Oi, é sobre a fatura"},
        "occurred_at": SystemClock().now().isoformat(),
    }


async def send(stack: Stack, conversation: str) -> None:
    valid, errors = validate_items([turn(conversation)])
    assert (await stack.ingest.ingest_items(stack.caller, valid, errors)).errors == []


async def every(stop: asyncio.Event, seconds: float, step: Callable[[], Awaitable[Any]]) -> None:
    """As `entrypoints.serve.every`: the read role's background duties share the event loop with reads."""
    while not stop.is_set():
        await step()
        await asyncio.sleep(seconds)


async def test_reads_keep_answering_while_conversations_open(stack: Stack) -> None:
    await send(stack, "wa-first")
    first = await stack.serve.context(
        stack.caller, ContextRequest(subject=SUBJECT, conversation_id="wa-first")
    )
    assert first.path is DeliveryPath.T2
    async with stack.uow(stack.space_id) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    await stack.compile.compile(Task(TaskName.COMPILE, stack.space_id, profile_id=profile.profile_id))

    stop = asyncio.Event()
    opened = 0

    async def conversations() -> None:
        """A conversation opens every 200 ms and its warm-up runs, three at a time, as a busy worker would."""
        nonlocal opened
        while not stop.is_set():
            opened += 1
            name = f"wa-open-{opened}"
            await send(stack, name)
            async with stack.uow(stack.space_id) as uow:
                session = await uow.sessions.open_session(conversation_id=name)
            assert session is not None
            warm = warm_task(stack.space_id, session)
            await asyncio.gather(*(stack.compile.compile(warm) for _ in range(3)))
            await asyncio.sleep(0.2)

    async def read(n: int) -> tuple[float, DeliveryPath]:
        started = time.perf_counter()
        request = ContextRequest(subject=SUBJECT, conversation_id=f"c{n % 20}", verification=Verification.V1)
        response = await asyncio.wait_for(stack.serve.context(stack.caller, request), READ_BUDGET_S * SLACK)
        return time.perf_counter() - started, response.path

    serve = stack.serve
    background = [
        asyncio.create_task(every(stop, 5.0, serve.refresh_cache_epoch)),
        asyncio.create_task(every(stop, 2.0, serve.refresh_privacy)),
        asyncio.create_task(every(stop, 1.0, serve.flush_receipts)),
        asyncio.create_task(every(stop, 5.0, serve.heartbeat)),
        asyncio.create_task(conversations()),
    ]
    try:
        reads = []
        started = time.perf_counter()
        for n in range(25 * 8):  # 25 reads per second for 8 seconds, over 20 conversations
            delay = started + n / 25 - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            reads.append(asyncio.create_task(read(n)))
        results = await asyncio.gather(*reads)
    finally:
        stop.set()
        await asyncio.gather(*background)
    assert opened > 5
    assert all(path in (DeliveryPath.T0, DeliveryPath.T1) for _, path in results)
    within("the slowest read while conversations open", max(seconds for seconds, _ in results), READ_BUDGET_S)


async def test_25_reads_a_second_hold_the_pace_across_a_slow_cache(stack: Stack) -> None:
    # The throughput incident of 24/09: a read held its space connection while it awaited the cache and
    # the hot layer; with those a little slow, 25 reads a second needed more connections than the pool
    # has, and reads queued for seconds. Only database statements run inside the transaction now.
    await send(stack, "wa-first")
    async with stack.uow(stack.space_id) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    await stack.compile.compile(Task(TaskName.COMPILE, stack.space_id, profile_id=profile.profile_id))
    for n in range(20):
        await stack.serve.context(stack.caller, ask_in(f"t{n}"))  # pins every conversation first
    for store in stack.slow:
        store.delay_s = 0.04  # every cache and hot-layer command 40 ms slower

    async def read(n: int) -> float:
        started = time.perf_counter()
        response = await asyncio.wait_for(stack.serve.context(stack.caller, ask_in(f"t{n % 20}")), 30)
        assert response.path is DeliveryPath.T0
        return time.perf_counter() - started

    reads = []
    started = time.perf_counter()
    for n in range(25 * 10):  # 25 reads a second for 10 seconds
        delay = started + n / 25 - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        reads.append(asyncio.create_task(read(n)))
    took = sorted(await asyncio.gather(*reads))
    p95 = took[int(len(took) * 0.95)]
    # A read makes a handful of cache and hot-layer calls, 20 ms each: under a second with room to spare.
    within("25 reads a second across a slow cache, p95", p95, 1.0)


def ask_in(conversation: str) -> ContextRequest:
    return ContextRequest(subject=SUBJECT, conversation_id=conversation, verification=Verification.V1)


async def test_25_reads_a_second_hold_the_pace_for_a_busy_number(stack: Stack) -> None:
    # The second throughput incident of 24/09: the sandbox number had hundreds of turns in the hot layer
    # (the end-to-end runs), and every read decrypted and screened all of them. The read process's event
    # loop starved, awaits resumed late, and connection checkouts timed out (503) while CPU looked idle.
    for batch in range(20):  # 1,000 turns in 20 batches
        items = [
            {
                **turn(f"e2e-{batch}-{i // 10}"),
                "idempotency_key": f"busy-{batch}-{i}",
                "content": {"type": "text", "text": f"turno {batch} {i}: " + "o cliente escreveu " * 8},
            }
            for i in range(50)
        ]
        valid, errors = validate_items(items)
        assert (await stack.ingest.ingest_items(stack.caller, valid, errors)).errors == []
    async with stack.uow(stack.space_id) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    await stack.compile.compile(Task(TaskName.COMPILE, stack.space_id, profile_id=profile.profile_id))
    for n in range(20):
        await stack.serve.context(stack.caller, ask_in(f"b{n}"))

    async def read(n: int) -> float:
        started = time.perf_counter()
        response = await asyncio.wait_for(stack.serve.context(stack.caller, ask_in(f"b{n % 20}")), 30)
        assert len(response.live) <= LIVE_TURNS
        return time.perf_counter() - started

    reads = []
    started = time.perf_counter()
    for n in range(25 * 8):
        delay = started + n / 25 - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        reads.append(asyncio.create_task(read(n)))
    took = sorted(await asyncio.gather(*reads))
    p95 = took[int(len(took) * 0.95)]
    within("25 reads a second for a busy number, p95", p95, 0.5)


async def test_10_reads_a_second_with_a_question_each(stack: Stack) -> None:
    # A read with `query` serves the pinned skeleton and computes the turn's slots,
    # with the settled values read in a short transaction of its own when the turn asks for a value. The
    # budget in the cluster is 45 ms at p95 without the semantic channel; this machine only guards the pace.
    await send(stack, "wa-first")
    async with stack.uow(stack.space_id) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    await stack.compile.compile(Task(TaskName.COMPILE, stack.space_id, profile_id=profile.profile_id))
    questions = [
        "Qual o protocolo que me passaram por e-mail?",
        "Quantas vezes eu já reclamei da internet caindo?",
        "O que eu relatei depois da vistoria?",
        "Até que dia eu disse que precisava da solução?",
    ]
    for n in range(20):
        await stack.serve.context(stack.caller, ask_in(f"v{n}"))

    async def read(n: int) -> tuple[float, float]:
        started = time.perf_counter()
        request = ContextRequest(
            subject=SUBJECT,
            conversation_id=f"v{n % 20}",
            verification=Verification.V1,
            query=questions[n % 4],
        )
        response = await asyncio.wait_for(stack.serve.context(stack.caller, request), READ_BUDGET_S * SLACK)
        assert response.path is DeliveryPath.T0
        return time.perf_counter() - started, response.timing.get("slots", 0.0)

    reads = []
    started = time.perf_counter()
    for n in range(10 * 10):  # 10 reads a second for 10 seconds
        delay = started + n / 10 - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        reads.append(asyncio.create_task(read(n)))
    results = await asyncio.gather(*reads)
    took = sorted(seconds for seconds, _ in results)
    slots = sorted(ms for _, ms in results)
    p95 = took[int(len(took) * 0.95)]
    print(f"questions at 10/s: p50 {took[len(took) // 2] * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms, "
          f"slots p95 {slots[int(len(slots) * 0.95)]:.2f} ms")  # fmt: skip
    within("10 reads a second with a question each, p95", p95, 0.5)
