"""Computed values, derived objects and timers on PostgreSQL (24.3.6 to 24.3.8; 8.6): a revised value fires
again a timer that already fired, naming that firing; a changed input expires all its dependents in one
statement; each timer transition is one statement, and the sweep fires a page in one."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
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
from niadra.contracts.state import ObjectRead, StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import BusinessObject, EventRecord, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, EventKind, Feature, Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
COURT, SHOP, CRM, QUOTES = uuid7(), uuid7(), uuid7(), uuid7()
TYPES: list[dict[str, Any]] = [
    {
        "type": "intimacao",
        "ownership": "subject",
        "mirror_of": {"system": "court"},
        "values": {"due_date": {"rule": "prazo_forense@v9", "absent_as": "sem_prazo"}},
        "sources": {"court": {"kind": "push", "precedence": 1}},
        "timers": {"notice_48h": {"due": "due_date - 48h", "on_fire": ["notify"]}},
    },
    {
        "type": "order",
        "ownership": "subject",
        "mirror_of": {"system": "shop"},
        "time": {"delivered_at": {"origin": "source"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
        "states": ["delivered", "kept", "returned"],
        "lifecycle": {
            "transitions": [
                {"from": "delivered", "to": "kept", "by": ["clock"]},
                {"from": "delivered", "to": "returned", "by": ["source"]},
            ],
            "outcome": {"provisional": ["delivered"], "final": ["kept", "returned"]},
        },
        "timers": {
            "exchange_window": {"due": "delivered_at + 30d", "on_fire": ["transition(delivered->kept)"]}
        },
    },
    {
        "type": "quote",
        "ownership": "subject",
        "nature": "derived",
        "mirror_of": {"system": "quotes"},
        "inputs": ["lead.city", "lead.ages"],
        "fields": {"price": {"type": "money"}},
        "sources": {"quotes": {"kind": "push", "precedence": 1}},
    },
]


@dataclass
class Stack:
    state: StateService
    objects: ObjectsService
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    space_id: UUID
    caller: SourcePrincipal
    statements: list[str]

    async def report(self, key: ObjectKey, source: UUID, fields: dict[str, Any]) -> list[BusinessObject]:
        at = self.clock.now()
        record = EventRecord(
            event_id=uuid7(),
            kind=EventKind.SYSTEM_EVENT,
            source_id=source,
            channel="erp",
            speaker=Speaker.SYSTEM,
            occurred_at=at,
            received_at=at,
            handle_ids=(),
            canonical_type=f"{key.type}.updated",
            fields=fields,
            object_refs=(key,),
        )
        async with self.uow(self.space_id) as uow:
            await uow.ingest.insert_events([NewEvent(self.space_id, record, f"key-{record.event_id}")])
        task = Task(TaskName.APPLY_OBJECT_EVENT, self.space_id, {"event_id": str(record.event_id)})
        written = await self.objects.apply_object_event(task)
        await self.state.publish(self.space_id, written)
        return written

    async def fire(self) -> list[BusinessObject]:
        written = await self.objects.fire_timers(self.space_id)
        await self.state.publish(self.space_id, written)
        return written

    async def read(self, key: ObjectKey) -> ObjectRead:
        ref = {"type": key.type, "namespace": key.namespace, "id": key.external_id}
        answer = await self.state.read(self.caller, StateReadRequest.model_validate({"refs": [ref]}))
        [found] = answer.response.objects
        return found

    def touching(self, since: int, table: str) -> list[str]:
        return [s for s in self.statements[since:] if table in s]


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
    systems = [
        SourceConfig(source_id=source, name=name, audience=Audience.INTERNAL_AGENT)
        for source, name in ((COURT, "court"), (SHOP, "shop"), (CRM, "crm"), (QUOTES, "quotes"))
    ]
    config = StaticConfig(
        base.model_copy(
            update={
                "sources": [*base.sources, *systems],
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": TYPES}),
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "typed-state"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock = FixedClock(NOW)
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


async def test_a_recomputed_value_fires_again_a_timer_that_already_fired_with_a_reference(
    stack: Stack,
) -> None:
    notice = ObjectKey("intimacao", "produto", "pub-1")
    before = len(stack.statements)
    await stack.report(notice, COURT, {"due_date": "2026-10-02"})
    # Arming is one statement on the timers; due within the hour, a task waits at its due time.
    assert len(stack.touching(before, "object_timers")) == 1
    assert len(stack.touching(before, "INSERT INTO object_values")) == 1
    await stack.fire()
    [fired] = (await stack.read(notice)).timers
    assert fired.state == "fired"
    stack.clock.advance(minutes=10)
    await stack.report(notice, COURT, {"due_date": "2026-10-03"})
    [again] = (await stack.read(notice)).timers
    assert again.state == "armed"
    await stack.fire()
    async with stack.uow(stack.space_id) as uow:
        rows = (
            await uow.session.execute(
                text("SELECT timer_id, state, supersedes_firing_id FROM object_timers ORDER BY created_at")
            )
        ).all()
        versions = (
            await uow.session.execute(
                text("SELECT version, supersedes_version FROM object_values ORDER BY version")
            )
        ).all()
    first, second = rows
    assert (first.state, second.state) == ("fired", "fired")
    assert second.supersedes_firing_id == first.timer_id
    assert [tuple(v) for v in versions] == [(1, None), (2, 1)]
    [reread] = (await stack.read(notice)).timers
    assert reread.supersedes_firing_id == str(first.timer_id)


async def test_an_input_change_expires_every_dependent_in_one_statement(stack: Stack) -> None:
    lead = ObjectKey("lead", "crm", "L-1")
    await stack.report(lead, CRM, {"city": "Campinas", "ages": [30]})
    quotes = [ObjectKey("quote", "operadora", f"q-{n}") for n in range(3)]
    for key in quotes:
        await stack.report(key, QUOTES, {"price": 700.0, "input_refs": {"lead": "lead:crm:L-1"}})
    stack.clock.advance(minutes=1)
    before = len(stack.statements)
    written = await stack.report(lead, CRM, {"city": "Sorocaba"})
    assert len(stack.touching(before, "object_dependencies")) == 1
    assert {o.key for o in written} == {lead, *quotes}
    for key in quotes:
        read = await stack.read(key)
        assert (read.derived_status, read.expired_by) == ("expired_by_input", ["lead.city"])
    # A field no quote reads runs no statement on the dependencies.
    stack.clock.advance(minutes=1)
    before = len(stack.statements)
    await stack.report(lead, CRM, {"name": "Ana"})
    assert stack.touching(before, "object_dependencies") == []


async def test_the_sweep_fires_a_page_in_one_statement_and_each_transition_in_one(stack: Stack) -> None:
    delivered = (NOW - timedelta(days=31)).isoformat()
    orders = [ObjectKey("order", "shop", str(n)) for n in range(4)]
    for key in orders:
        await stack.report(key, SHOP, {"state": "delivered", "delivered_at": delivered})
    notices = [ObjectKey("intimacao", "produto", f"n-{n}") for n in range(3)]
    for key in notices:
        await stack.report(key, COURT, {"due_date": "2026-10-02"})
    before = len(stack.statements)
    written = await stack.fire()
    assert {o.key for o in written} == {*orders, *notices}
    assert len(stack.touching(before, "UPDATE object_timers")) == 1
    # Each order's transition writes the order once; the notices' marks are one statement together.
    updates = stack.touching(before, "UPDATE business_objects")
    assert len(updates) == len(orders) + 1
    for key in orders:
        assert (await stack.read(key)).state == "kept"
    assert await stack.fire() == []
