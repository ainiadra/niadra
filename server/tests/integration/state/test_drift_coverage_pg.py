"""Drift by observation and the coverage of the declared types on PostgreSQL (24.2.1): a system event with a
field the type does not declare opens one `drift` data issue from the transaction that merges it, and the
coverage is measured from the stamps alone."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, StaticConfig
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.data_issues import DataIssuesService
from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.app.type_drift import TypeDriftService
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, Environment, EventKind, Feature, Speaker
from niadra.testing.cell import TENANT_ID, sample_config
from tests.integration.conftest import PgServer, clone_space
from tests.unit.audit.fakes import RecordingAdminAudit

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ERP = uuid7()
ORDER = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "erp", "drift": "alert"},
    "fields": {"total": {"type": "money"}, "carrier": {"type": "string"}},
    "states": ["placed", "shipped"],
    "sources": {"erp": {"kind": "push", "precedence": 1}},
}


@pytest.fixture
async def stack(server: PgServer) -> AsyncIterator[dict[str, Any]]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    base = sample_config(space_id)
    erp = SourceConfig(source_id=ERP, name="erp", audience=Audience.INTERNAL_AGENT)
    config = StaticConfig(
        base.model_copy(
            update={
                "sources": [*base.sources, erp],
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": [ORDER]}),
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "drift"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock = FixedClock(NOW)
    cache = InMemoryCache()
    issues = DataIssuesService(uow, clock, config, RecordingAdminAudit(), cache)  # type: ignore[arg-type]
    drift = TypeDriftService(uow, clock, config, issues, cache)
    try:
        yield {
            "space": space_id,
            "uow": uow,
            "objects": ObjectsService(uow, clock, config, drift=drift),
            "state": StateService(uow, InMemoryCache(), keys, clock, config),
            "clock": clock,
        }
    finally:
        await registry.close()
        await cell_engine.dispose()


async def report(stack: dict[str, Any], key: ObjectKey, fields: dict[str, Any]) -> None:
    stack["clock"].at += timedelta(minutes=1)
    at = stack["clock"].now()
    record = EventRecord(
        event_id=uuid7(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=ERP,
        channel="erp",
        speaker=Speaker.SYSTEM,
        occurred_at=at,
        received_at=at,
        handle_ids=(),
        canonical_type="order.updated",
        fields=fields,
        object_refs=(key,),
    )
    async with stack["uow"](stack["space"]) as uow:
        await uow.ingest.insert_events([NewEvent(stack["space"], record, f"key-{record.event_id}")])
    task = Task(TaskName.APPLY_OBJECT_EVENT, stack["space"], {"event_id": str(record.event_id)})
    await stack["objects"].apply_object_event(task)


async def test_an_undeclared_field_and_state_open_drift_and_coverage_reads_the_stamps(
    stack: dict[str, Any],
) -> None:
    await report(
        stack, ObjectKey("order", "erp", "o-1"), {"total": 10.0, "gift_wrap": True, "state": "placed"}
    )
    await report(stack, ObjectKey("order", "erp", "o-2"), {"total": 12.0, "state": "returned"})
    async with stack["uow"](stack["space"]) as uow:
        rows = (
            await uow.session.execute(
                text("SELECT kind, object_type, field, source, occurrences FROM data_issues ORDER BY field")
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("drift", "order", "gift_wrap", "erp", 1),
        ("drift", "order", "state", "erp", 1),
    ]
    person = PersonPrincipal(
        SpaceRef(stack["space"], TENANT_ID, "us-east-2", Environment.SANDBOX), uuid7(), frozenset()
    )
    [order] = (await stack["state"].coverage(person)).types
    assert order.objects == 2
    shares = {f.field: (f.coverage, f.age_p50_s) for f in order.fields}
    # Observed one minute and no time before the read: the median age is 30 s.
    assert shares == {"total": (1.0, 30), "carrier": (0.0, None)}
