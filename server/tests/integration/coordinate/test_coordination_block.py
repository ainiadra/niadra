"""A warm context read with `include: ["coordination"]` runs no statement (24.2.10, 24.6): the block comes
from the state store, like the check."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

import fakeredis
import pytest
from pydantic import TypeAdapter
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import (
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.adapters.outbound.redis.coordinate import RedisCoordination
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.app.compile import CompileService
from niadra.app.coordinate import CoordinateService, CoordinationReadBlock
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.serve import ServeService
from niadra.app.serve_reads import SETTLING
from niadra.app.sessions import SessionService
from niadra.config.coordination import CoordinationSettings
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.coordinate import DeclareRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.coordinate.records import CoordinationRecord
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, Feature, HandleType
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

PHONE = "+5511900007733"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
DECLARATION: TypeAdapter[DeclareRequest] = TypeAdapter(DeclareRequest)


class ConfirmingReceipts:
    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


class Queue:
    def __init__(self) -> None:
        self.records: list[CoordinationRecord] = []

    async def publish(self, records: Sequence[CoordinationRecord], timeout_s: float) -> bool:
        self.records.extend(records)
        return True


@pytest.fixture
async def served(server: PgServer) -> AsyncIterator[tuple[ServeService, CoordinateService, Any, list[Any]]]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    coordination = CoordinationSettings.model_validate(
        {"commitments": [{"name": "trial", "compatibility": "first_holds"}]}
    )
    config = StaticConfig(
        sample_config(space_id).model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.COORDINATION})),
                "coordination": coordination,
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "coordination-block"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock, cache, hot = FixedClock(), InMemoryCache(), InMemoryHotLayer()
    identity = IdentityService(uow, clock, config)
    sessions = SessionService(uow, clock, config)
    ingest = IngestService(
        uow, identity, sessions, keys, hot, clock, config, InMemoryObjectStore(), DictSecrets()
    )
    compile_ = CompileService(uow, keys, cache, clock, config, hot=hot)
    serve = ServeService(
        uow, cache, hot, keys, ConfirmingReceipts(), compile_, DeterministicModels(), clock, config,
        "read-coordination", databases=registry,
    )  # fmt: skip
    state = fakeredis.FakeServer()
    fakeredis.FakeRedis(server=state).set("coord:marker", "whole")
    store = fakeredis.FakeAsyncRedis(server=state)
    coordinate = CoordinateService(
        uow, RedisCoordination(store), Queue(), keys, clock, config, marks=RedisErasureMarks(store)
    )
    serve.add_block(CoordinationReadBlock(coordinate))
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Quero o teste grátis"},
        "occurred_at": clock.now().isoformat(),
    }
    valid, errors = validate_items([item])
    await ingest.ingest_items(principal(WHATSAPP_SOURCE, config.space(space_id)), valid, errors)
    clock.advance(seconds=SETTLING.total_seconds() + 1)
    async with uow(space_id) as tx:
        found = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert found is not None
        profile = await tx.identity.profile_of(found.handle_id)
    assert profile is not None
    await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=profile.profile_id))
    caller = principal(WHATSAPP_SOURCE, config.space(space_id))
    for key, body in (
        ("s1", {"kind": "suppression.added", "detail": {"purpose": "marketing", "reason": "opt_out"}}),
        ("t1", {"kind": "commitment.made", "detail": {"commitment_id": "t1", "type": "trial", "terms": {}}}),
        ("l1", {"kind": "lease", "detail": {"lease_s": 600}}),
    ):
        request = DECLARATION.validate_python(
            {"subject": SUBJECT.model_dump(), "agent": "agent:sales", **body}
        )
        await coordinate.declare(caller, request, key)
    statements: list[Any] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append((asyncio.current_task(), statement))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield serve, coordinate, principal(VOICE_SOURCE, config.space(space_id)), statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)
        await registry.close()
        await cell_engine.dispose()


async def test_a_warm_read_with_the_coordination_block_runs_no_statement(
    served: tuple[ServeService, CoordinateService, Any, list[Any]],
) -> None:
    serve, _, caller, statements = served

    async def read(conversation: str) -> tuple[Any, int]:
        before = len(statements)
        request = ContextRequest(subject=SUBJECT, conversation_id=conversation, include=["coordination"])
        response = await serve.context(caller, request)
        mine = asyncio.current_task()
        ran = sum(1 for task, _ in statements[before:] if task is mine)
        await serve.drain()
        return response, ran

    first, cold = await read("call-1")
    assert cold > 0  # the first read resolves the customer
    block = first.coordination
    assert block is not None
    assert block.owner is not None
    assert (block.owner.holder, block.suppressions) == ("agent:sales", ["marketing"])
    assert [c.id for c in block.commitments_active] == ["t1"]
    warm, ran = await read("call-2")
    assert (warm.path, ran) == (DeliveryPath.T0, 0)
    assert warm.coordination == block
