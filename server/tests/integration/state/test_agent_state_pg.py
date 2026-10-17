"""The agents' working state on PostgreSQL, the state store and the broker (24.3.9; 8.6): the batch writer
stores a batch in one statement per space, the customer's handle is found by its keyed digest, erasure and the
customer's package find the state, and a write goes from the state store through the confirmed queue."""

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.memory.erasure import InMemoryErasureMarks
from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, StaticConfig
from niadra.adapters.memory.state import InMemoryAgentStateLog, InMemoryAgentStateStore
from niadra.adapters.outbound.rabbitmq.agent_state import RabbitAgentStateLog
from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.agent_state import RedisAgentStateStore
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.app.agent_state import AgentStateService
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import AgentStateReadRequest, AgentStateWrite
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
PHONE = "+5511900002468"
ASSISTANT = {
    "type": "closing",
    "ownership": "agent",
    "agent_state": {"scope": "conversation"},
    "fields": {"step": {"type": "enum"}, "offer": {"type": "string"}},
}
REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
RABBIT = os.environ.get("NIADRA_TEST_RABBITMQ", "")


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()[:3]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


def config(space: UUID, retention: dict[str, str] | None = None) -> StaticConfig:
    base = sample_config(space)
    registry = ObjectTypeRegistry.model_validate({"types": [ASSISTANT | {"retention": retention or {}}]})
    return StaticConfig(base.model_copy(update={"object_types": registry}))


def service(
    pg_uow: CloningUnitOfWork,
    space: UUID,
    store: Any,
    log: Any,
    marks: Any = None,
    retention: dict[str, str] | None = None,
) -> AgentStateService:
    return AgentStateService(
        pg_uow,  # type: ignore[arg-type]
        store,
        InMemoryCache(),
        log,
        pg_uow._keys,
        FixedClock(NOW),
        config(space, retention),
        marks=marks or InMemoryErasureMarks(),
    )


def write(conversation: str, body: dict[str, Any], **extra: Any) -> AgentStateWrite:
    scope = {"kind": "conversation", "id": conversation}
    return AgentStateWrite.model_validate(
        {"scope": scope, "agent": "closing", "mode": "merge_by_key", "body": body} | extra
    )


async def phone_handle(pg_uow: CloningUnitOfWork, space: UUID) -> UUID:
    key = HandleKey(HandleType.PHONE_E164, "", PHONE)
    async with pg_uow(space) as uow:
        await uow.identity.add_handles([NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], NOW)
        found = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
    assert found is not None
    return found.handle_id


async def test_a_batch_of_500_states_of_two_spaces_is_one_statement_per_space(
    pg_uow: CloningUnitOfWork,
) -> None:
    first, second = uuid4(), uuid4()
    for space in (first, second):
        await pg_uow.ensure(space)
    log = InMemoryAgentStateLog()
    writers = {s: service(pg_uow, s, InMemoryAgentStateStore(), log) for s in (first, second)}
    for n in range(250):
        for space, writer in writers.items():
            caller = principal(WHATSAPP_SOURCE, config(space).space(space))
            await writer.write(caller, write(f"c-{n}", {"step": "quote"}))
    assert len(log.pending) == 500
    with counting() as statements:
        await writers[first].run_writer()
    # One statement per space, which indexes what the states name a person by (`domain.privacy.mentions`).
    inserts = [s for s in statements if not s.startswith("SELECT set_config")]
    assert inserts == ["WITH named_mentions AS"] * 2, statements
    assert log.acked == 500
    for space in (first, second):
        async with pg_uow(space) as uow:
            count = (await uow.session.execute(text("SELECT count(*) FROM agent_state"))).scalar_one()
        assert count == 250


async def test_the_customer_is_found_by_the_keyed_digest_of_the_handle(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    handle_id = await phone_handle(pg_uow, space)
    log = InMemoryAgentStateLog()
    writer = service(pg_uow, space, InMemoryAgentStateStore(), log)
    caller = principal(WHATSAPP_SOURCE, config(space).space(space))
    subject = {"type": "phone_e164", "value": PHONE}
    await writer.write(caller, write("wa-1", {"offer": "family plan"}, subject=subject))
    await writer.write(caller, write("wa-2", {"step": "quote"}))
    await writer.run_writer()
    async with pg_uow(space) as uow:
        [mine] = await uow.agent_state.for_handles([handle_id])
        [packaged] = await uow.agent_state.package_for([handle_id])
        raw = (await uow.session.execute(text("SELECT body_ct FROM agent_state"))).scalars().all()
    assert mine.origin_handle_id == handle_id
    assert packaged.fields == {"offer": "family plan"}
    assert all(b"family" not in body for body in raw)

    # A version already stored never moves the row again, whatever order the batches arrive in.
    async with pg_uow(space) as uow:
        assert await uow.agent_state.upsert([mine]) == 0

    # Erasing the customer takes their state; erasing a conversation takes its own.
    async with pg_uow(space) as uow:
        scope = ErasureScope(uuid7(), TargetKind.PROFILE, (handle_id,), uuid7(), None, NOW)
        erased = await uow.agent_state.erase_for(scope, limit=10)
        by_conversation = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), None, "wa-2", NOW)
        erased += await uow.agent_state.erase_for(by_conversation, limit=10)
        left = (await uow.session.execute(text("SELECT count(*) FROM agent_state"))).scalar_one()
    assert len(erased) == 2
    assert left == 0


async def test_a_state_written_before_its_customer_existed_finds_them_at_the_sweep(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    log = InMemoryAgentStateLog()
    writer = service(pg_uow, space, InMemoryAgentStateStore(), log)
    caller = principal(WHATSAPP_SOURCE, config(space).space(space))
    subject = {"type": "phone_e164", "value": PHONE}
    await writer.write(caller, write("wa-1", {"offer": "family plan"}, subject=subject))
    await writer.run_writer()
    handle_id = await phone_handle(pg_uow, space)
    async with pg_uow(space) as uow:
        assert await uow.agent_state.for_handles([handle_id]) == []
    assert await writer.sweep(space) == 1
    async with pg_uow(space) as uow:
        [found] = await uow.agent_state.for_handles([handle_id])
        assert await uow.agent_state.resolve_owners(limit=10) == 0
    assert found.origin_handle_id == handle_id


async def test_a_state_leaves_only_by_the_retention_its_type_names(pg_uow: CloningUnitOfWork) -> None:
    """Memory never cuts: a state whose type names no retention is kept, with no `valid_until`,
    until it names one, and a named one is honored. Every state went 30 days after its last write."""
    kept, named = uuid4(), uuid4()
    for space, retention in ((kept, None), (named, {"state": "30d"})):
        await pg_uow.ensure(space)
        writer = service(
            pg_uow, space, InMemoryAgentStateStore(), InMemoryAgentStateLog(), retention=retention
        )
        caller = principal(WHATSAPP_SOURCE, config(space, retention).space(space))
        await writer.write(caller, write("wa-1", {"step": "quote"}))
        await writer.run_writer()
    async with pg_uow(kept) as uow:
        assert (await uow.session.execute(text("SELECT valid_until FROM agent_state"))).scalar_one() is None
        assert await uow.agent_state.expire(NOW + timedelta(days=3650), limit=10) == 0
    async with pg_uow(named) as uow:
        assert await uow.agent_state.expire(NOW + timedelta(days=29), limit=10) == 0
        assert await uow.agent_state.expire(NOW + timedelta(days=31), limit=10) == 1


@pytest.mark.skipif(not (REDIS and RABBIT), reason="needs NIADRA_TEST_REDIS and NIADRA_TEST_RABBITMQ")
async def test_a_write_goes_from_the_state_store_through_the_confirmed_queue(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    redis = connect(REDIS)
    rabbit = RabbitConnection(RABBIT)
    await rabbit.open()
    try:
        # Whatever an earlier run left in the queue goes first.
        channel = await rabbit.channel(prefetch=1)
        queue = await channel.get_queue("niadra.agent_state", ensure=False)
        await queue.purge()
        await channel.close()
        log = RabbitAgentStateLog(rabbit, linger_s=0.2)
        writer = service(pg_uow, space, RedisAgentStateStore(redis), log, RedisErasureMarks(redis))
        caller = principal(WHATSAPP_SOURCE, config(space).space(space))
        created = await writer.write(caller, write("wa-9", {"step": "quote"}))
        assert (created.stored, created.version) == (True, 1)
        read = await writer.read(
            caller,
            AgentStateReadRequest.model_validate(
                {"scope": {"kind": "conversation", "id": "wa-9"}, "agent": "closing"}
            ),
        )
        assert (read.body, read.version) == ({"step": "quote"}, 1)
        batches = log.batches(10)
        batch = await asyncio.wait_for(anext(batches), 10)
        await writer._write_batch(batch.states)
        await batch.ack()
        await batches.aclose()
        async with pg_uow(space) as uow:
            version = (await uow.session.execute(text("SELECT version FROM agent_state"))).scalar_one()
        assert version == 1
    finally:
        await rabbit.close()
        await redis.aclose()


async def test_an_erasure_while_writes_wait_in_the_queue_leaves_no_row_after_the_flush(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    handle_id = await phone_handle(pg_uow, space)
    log = InMemoryAgentStateLog()
    store = InMemoryAgentStateStore()
    writer = service(pg_uow, space, store, log)
    caller = principal(WHATSAPP_SOURCE, config(space).space(space))
    subject = {"type": "phone_e164", "value": PHONE}
    await writer.write(caller, write("wa-1", {"offer": "family plan"}, subject=subject))
    await writer.write(caller, write("wa-2", {"step": "quote"}))
    await writer.run_writer()
    # A new version of the customer's state waits in the queue when the erasure starts.
    await writer.write(caller, write("wa-1", {"step": "close"}, subject=subject))
    assert len(log.pending) == 1
    scope = ErasureScope(uuid4(), TargetKind.HANDLE, (handle_id,), None, None, NOW)
    await writer.fence(space, scope)
    async with pg_uow(space) as uow:
        await uow.agent_state.erase_for(scope, limit=100)
    await writer.run_writer()
    await writer.purge(space, scope)
    async with pg_uow(space) as uow:
        rows = (await uow.session.execute(text("SELECT subject_key FROM agent_state"))).scalars().all()
    assert rows == [None]  # only the other conversation's state, which names no customer
    assert len(store.states) == 1  # the customer's state left the store with the fence
