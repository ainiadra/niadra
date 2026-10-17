"""What a state read does on the database (24.3.1; 8.6): warm, no statement; a miss, one statement for all the
objects it did not have. The objects come from system events applied on PostgreSQL by the objects worker, and
their copies from what it publishes."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, StaticConfig
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, EventKind, Feature, Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

ORDER_SYSTEM = uuid7()
ORDER = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "order_management"},
    "fields": {
        "status": {"type": "enum"},
        "total": {
            "type": "money",
            "claim": {"allowed": True, "class": "money"},
            "freshness": {"class": "semi"},
        },
    },
    "sources": {"order_management": {"kind": "push", "precedence": 1}},
    "purposes": {"claim": {"prohibitions": ["affirm_total"]}},
}


@dataclass
class Stack:
    state: StateService
    objects: ObjectsService
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    space_id: UUID
    caller: SourcePrincipal
    statements: list[str]

    async def apply(self, key: ObjectKey, minutes: float, fields: dict[str, Any]) -> None:
        """A system event about `key` from the order system, `minutes` into the last ten."""
        at = self.clock.now()
        record = EventRecord(
            event_id=uuid7(),
            kind=EventKind.SYSTEM_EVENT,
            source_id=ORDER_SYSTEM,
            channel="erp",
            speaker=Speaker.SYSTEM,
            occurred_at=at - timedelta(minutes=10 - minutes),
            received_at=at,
            handle_ids=(),
            canonical_type="order.updated",
            fields=fields,
            object_refs=(key,),
        )
        async with self.uow(self.space_id) as uow:
            await uow.ingest.insert_events([NewEvent(self.space_id, record, f"key-{record.event_id}")])
        task = Task(TaskName.APPLY_OBJECT_EVENT, self.space_id, {"event_id": str(record.event_id)})
        await self.state.publish(self.space_id, await self.objects.apply_object_event(task))

    async def read(self, *keys: ObjectKey, purpose: str = "claim") -> tuple[Any, int]:
        refs = [{"type": k.type, "namespace": k.namespace, "id": k.external_id} for k in keys]
        before = len(self.statements)
        answer = await self.state.read(
            self.caller, StateReadRequest.model_validate({"refs": refs, "purpose": purpose})
        )
        return answer, len(self.statements) - before


@contextmanager
def counting(statements: list[str]) -> Iterator[None]:
    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@pytest.fixture
async def stack(server: PgServer) -> AsyncIterator[Stack]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    base = sample_config(space_id)
    system = SourceConfig(source_id=ORDER_SYSTEM, name="order_management", audience=Audience.INTERNAL_AGENT)
    config = StaticConfig(
        base.model_copy(
            update={
                "sources": [*base.sources, system],
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": [ORDER]}),
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "state-budget"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock = FixedClock()
    state = StateService(uow, InMemoryCache(), keys, clock, config)
    objects = ObjectsService(uow, clock, config)
    statements: list[str] = []
    caller = principal(WHATSAPP_SOURCE, config.space(space_id))
    with counting(statements):
        try:
            yield Stack(state, objects, uow, clock, space_id, caller, statements)
        finally:
            await registry.close()
            await cell_engine.dispose()


async def test_a_warm_state_read_runs_no_statement(stack: Stack) -> None:
    first, second = ObjectKey("order", "shop", "1001"), ObjectKey("order", "shop", "1002")
    await stack.apply(first, 1, {"status": "shipped", "total": 189.9})
    await stack.apply(second, 2, {"status": "paid", "total": 42.0})
    await stack.state.read(
        stack.caller, StateReadRequest.model_validate({"refs": [_ref(first)]})
    )  # keys warm
    answer, ran = await stack.read(first, second)
    assert ran == 0
    shipped, paid = answer.response.objects
    assert (shipped.fields["status"].v, shipped.fields["status"].src) == ("shipped", "order_management")
    assert paid.fields["total"].v == 42.0
    assert shipped.fields["total"].claim_safe


async def test_a_miss_reads_every_missing_object_in_one_statement(stack: Stack) -> None:
    known = [ObjectKey("order", "shop", str(n)) for n in range(5)]
    for n, key in enumerate(known):
        await stack.apply(key, n, {"status": "open"})
    stack.state._cache.values.clear()  # type: ignore[attr-defined]  # evicted
    unknown = ObjectKey("order", "shop", "never")
    answer, ran = await stack.read(*known, unknown)
    # Besides pinning the transaction to the space, one statement.
    reads = [
        s for s in stack.statements[-ran:] if "set_config" not in s and not s.startswith(("BEGIN", "COMMIT"))
    ]
    assert len(reads) == 1, reads
    assert [o.fields["status"].logic for o in answer.response.objects] == ["yes"] * 5 + ["unobserved"]
    _, again = await stack.read(*known, unknown)
    assert again == 0


async def test_an_event_reaches_the_next_read_without_a_statement(stack: Stack) -> None:
    key = ObjectKey("order", "shop", "2001")
    await stack.apply(key, 1, {"status": "paid", "total": 99.0})
    await stack.read(key)
    await stack.apply(key, 5, {"status": "shipped"})
    answer, ran = await stack.read(key)
    assert ran == 0
    [order] = answer.response.objects
    assert (order.fields["status"].v, order.fields["total"].v) == ("shipped", 99.0)


def _ref(key: ObjectKey) -> dict[str, str]:
    return {"type": key.type, "namespace": key.namespace, "id": key.external_id}
