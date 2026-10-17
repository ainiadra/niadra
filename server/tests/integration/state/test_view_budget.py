"""The `now` view on PostgreSQL (24.3.13; 8.6): warm, the subject's objects, their interests in shared
objects, what changed since they saw them and a composite's derived fields over its pieces are read with no
statement, and so are the reasons to fetch again with their admission."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, InMemoryObjectStore, StaticConfig
from niadra.adapters.memory.refresh import InMemoryRefreshWants
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.refresh import RefreshService
from niadra.app.serve_reads import AboutLink, Target
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest, StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord, ObjectKey, ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    Audience,
    EventKind,
    Feature,
    HandleStatus,
    HandleType,
    Scope,
    Speaker,
    SubjectKind,
)
from niadra.ports.state import SeenInterest
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SHOP = uuid7()
TYPES: list[dict[str, Any]] = [
    {
        "type": "order",
        "ownership": "subject",
        "mirror_of": {"system": "shop"},
        "fields": {"status": {"type": "enum"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
    },
    {
        "type": "item_variant",
        "ownership": "shared",
        "mirror_of": {"system": "shop"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "fields": {
            "price_sale": {
                "type": "money",
                "claim": {"allowed": True, "class": "money"},
                "track_changes": True,
            }
        },
        "sources": {"platform": {"kind": "push", "precedence": 1}},
        "refetch": {
            "reasons": [{"name": "claim_pending", "when": "purpose == 'claim'", "budget": {"units": 1}}],
            "budget": {"unit": "platform_call"},
        },
    },
    {
        "type": "look",
        "ownership": "shared",
        "mirror_of": {"system": "shop"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "fields": {"name": {"type": "string"}},
        "relations": {"pieces": {"type": "item_variant", "via": "pieces[*]", "many": True}},
        "derived_fields": {"lowest_price": {"agg": "min", "over": "pieces", "field": "price_sale"}},
        "sources": {"platform": {"kind": "push", "precedence": 1}},
    },
    {
        "type": "notice",
        "ownership": "subject",
        "mirror_of": {"system": "court_feed"},
        "fields": {"case_number": {"type": "string"}, "answered": {"type": "bool"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
    },
    {
        "type": "lawsuit",
        "ownership": "subject",
        "mirror_of": {"system": "case_db"},
        "fields": {"case_number": {"type": "string"}},
        "relations": {
            "notices": {"type": "notice", "via": "case_number", "match": "case_number", "many": True}
        },
        "derived_fields": {"all_answered": {"agg": "all", "over": "notices", "where": "answered == yes"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
    },
]


class Interests:
    def __init__(self, seen: list[SeenInterest]) -> None:
        self.seen = seen

    async def interests(self, space_id: UUID, handle_ids: Any) -> list[SeenInterest]:
        return self.seen


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@pytest.fixture
async def world(server: PgServer) -> AsyncIterator[dict[str, Any]]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    base = sample_config(space_id)
    system = SourceConfig(source_id=SHOP, name="shop", audience=Audience.INTERNAL_AGENT)
    config = StaticConfig(
        base.model_copy(
            update={
                "sources": [*base.sources, system],
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": TYPES}),
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "view-budget"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock = FixedClock(NOW)
    refresh = RefreshService(uow, InMemoryRefreshWants(), clock, config)
    observations = ObservationsConsumer(uow, keys, config)
    shared = SharedObjectsService(
        uow,
        InMemorySharedObjects(),
        keys,
        clock,
        config,
        observations,
        InMemoryObjectStore(),
        refresh=refresh,
    )
    cache = InMemoryCache()
    state = StateService(uow, cache, keys, clock, config, shared=shared, refresh=refresh)
    objects = ObjectsService(uow, clock, config, refresh=refresh)
    try:
        yield {
            "space": space_id,
            "config": config,
            "uow": uow,
            "state": state,
            "shared": shared,
            "objects": objects,
            "cache": cache,
        }
    finally:
        await registry.close()
        await cell_engine.dispose()


async def test_a_warm_now_view_with_interests_and_changes_runs_no_statement(world: dict[str, Any]) -> None:
    space: UUID = world["space"]
    uow, state, shared = world["uow"], world["state"], world["shared"]
    async with uow(space) as tx:
        key = HandleKey(HandleType.SYSTEM_ID, "shop", "c-1")
        await tx.identity.add_handles([NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], NOW)
        handle = await tx.identity.find_handle(HandleType.SYSTEM_ID, "shop", "c-1")
    assert handle is not None
    record = EventRecord(
        event_id=uuid7(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=SHOP,
        channel="erp",
        speaker=Speaker.SYSTEM,
        occurred_at=NOW,
        received_at=NOW,
        handle_ids=(handle.handle_id,),
        canonical_type="order.updated",
        fields={"status": "shipped"},
        object_refs=(ObjectKey("order", "shop", "1001"),),
    )
    async with uow(space) as tx:
        await tx.ingest.insert_events([NewEvent(space, record, f"key-{record.event_id}")])
    task = Task(TaskName.APPLY_OBJECT_EVENT, space, {"event_id": str(record.event_id)})
    await state.publish(space, await world["objects"].apply_object_event(task))
    ref, look = "item_variant:loja:v-1", "look:loja:l-1"
    await shared.enter_working_set(space, [ref, look], "presented")
    pusher = principal(BILLING_SOURCE, world["config"].space(space), frozenset({Scope.STATE_PUSH}))
    item = {
        "ref": {"type": "item_variant", "namespace": "loja", "id": "v-1"},
        "version": 1,
        "fields": {"price_sale": 140.0},
        "provenance": {"source": "live", "source_observed_at": NOW.isoformat()},
    }
    composite = {
        **item,
        "ref": {"type": "look", "namespace": "loja", "id": "l-1"},
        "fields": {"pieces": ["v-1"]},
    }
    await shared.push(pusher, ObjectPushRequest.model_validate({"objects": [item, composite]}))
    await shared.consolidate(space)
    state.interests = Interests(
        [
            SeenInterest(ref, "presented", NOW - timedelta(hours=1), {"price_sale": 189.9}),
            SeenInterest(look, "presented", NOW - timedelta(hours=2), {}),
        ]
    )
    caller = principal(WHATSAPP_SOURCE, world["config"].space(space))
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_1", 1, 1, (handle.handle_id,))
    target = Target(profile)
    await world["cache"].set(f"gen:0:{space}:{{{profile.profile_id}}}", b"1")
    await state.subject_view(caller, target)  # the list of the subject's objects is kept, the copies too
    claim = {"refs": [{"type": "item_variant", "namespace": "loja", "id": "v-1"}], "purpose": "claim"}
    await state.read(caller, StateReadRequest.model_validate(claim))
    with counting() as statements:
        view, _ = await state.subject_view(caller, target)
        answer = await state.read(caller, StateReadRequest.model_validate(claim))
    assert statements == []
    assert [o.ref.id for o in view.objects] == ["1001"]
    [changed] = view.changes_since_seen
    assert (changed.field, changed.seen, changed.now) == ("price_sale", 189.9, 140.0)
    [looked] = [i.object for i in view.interests if i.ref.type == "look"]
    assert looked is not None
    assert looked.derived["lowest_price"].v == 140.0
    [found] = answer.response.objects
    assert found.refetch is not None
    assert (found.refetch.reason, found.refetch.admitted) == ("claim_pending", True)


async def test_a_reverse_relation_is_one_statement_cold_and_none_warm(world: dict[str, Any]) -> None:
    # A lawsuit's notices found by its case number, through the keyed digest each write keeps.
    space: UUID = world["space"]
    uow, state = world["uow"], world["state"]

    async def write(key: ObjectKey, fields: dict[str, Any], at: datetime = NOW) -> None:
        record = EventRecord(
            event_id=uuid7(), kind=EventKind.SYSTEM_EVENT, source_id=SHOP, channel="erp",
            speaker=Speaker.SYSTEM, occurred_at=at, received_at=at, handle_ids=(),
            canonical_type=f"{key.type}.updated", fields=fields, object_refs=(key,),
        )  # fmt: skip
        async with uow(space) as tx:
            await tx.ingest.insert_events([NewEvent(space, record, f"key-{record.event_id}")])
        task = Task(TaskName.APPLY_OBJECT_EVENT, space, {"event_id": str(record.event_id)})
        await state.publish(space, await world["objects"].apply_object_event(task))

    case = "0001234-56.2026.8.26.0100"
    await write(ObjectKey("lawsuit", "firm", "s-1"), {"case_number": case})
    for n, answered in ((1, True), (2, False)):
        await write(ObjectKey("notice", "firm", f"n-{n}"), {"case_number": case, "answered": answered})
    await write(
        ObjectKey("notice", "firm", "n-3"), {"case_number": "0009999-11.2026.8.26.0100", "answered": False}
    )
    caller = principal(WHATSAPP_SOURCE, world["config"].space(space))
    request = StateReadRequest.model_validate(
        {"refs": [{"type": "lawsuit", "namespace": "firm", "id": "s-1"}]}
    )
    with counting() as cold:
        await state.read(caller, request)
    assert len([s for s in cold if "match_keys" in s]) == 1, cold
    with counting() as warm:
        answer = await state.read(caller, request)
    assert warm == []
    [suit] = answer.response.objects
    answered = suit.derived["all_answered"]
    assert (answered.v, answered.over, answered.pieces_out) == (False, 2, ["notice:firm:n-2"])
    # A notice written since moves the index's version: the next read finds it, in one statement again.
    await write(ObjectKey("notice", "firm", "n-2"), {"answered": True}, NOW + timedelta(minutes=1))
    with counting() as moved:
        answer = await state.read(caller, request)
    assert len([s for s in moved if "match_keys" in s]) == 1, moved
    assert answer.response.objects[0].derived["all_answered"].v is True


async def test_a_warm_view_of_a_person_acting_for_a_company_runs_no_statement(world: dict[str, Any]) -> None:
    # `about` (21.4) adds the company's objects: kept under the company's own generation, read warm with none.
    space: UUID = world["space"]
    uow, state = world["uow"], world["state"]
    keys = {name: HandleKey(HandleType.SYSTEM_ID, "shop", name) for name in ("acme", "ana")}
    async with uow(space) as tx:
        await tx.identity.add_handles(
            [
                NewHandle(keys["acme"], SubjectKind.ACCOUNT, HandleStatus.ACTIVE),
                NewHandle(keys["ana"], SubjectKind.PERSON, HandleStatus.ACTIVE),
            ],
            NOW,
        )
        company = await tx.identity.find_handle(HandleType.SYSTEM_ID, "shop", "acme")
        buyer = await tx.identity.find_handle(HandleType.SYSTEM_ID, "shop", "ana")
    assert company is not None
    assert buyer is not None
    record = EventRecord(
        event_id=uuid7(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=SHOP,
        channel="erp",
        speaker=Speaker.SYSTEM,
        occurred_at=NOW,
        received_at=NOW,
        handle_ids=(company.handle_id,),
        canonical_type="order.updated",
        fields={"status": "placed"},
        object_refs=(ObjectKey("order", "shop", "3001"),),
    )
    async with uow(space) as tx:
        await tx.ingest.insert_events([NewEvent(space, record, f"key-{record.event_id}")])
    task = Task(TaskName.APPLY_OBJECT_EVENT, space, {"event_id": str(record.event_id)})
    await state.publish(space, await world["objects"].apply_object_event(task))
    account = ProfileRecord(uuid7(), SubjectKind.ACCOUNT, "pse_acme", 1, 1, (company.handle_id,))
    person = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_ana", 1, 1, (buyer.handle_id,))
    for profile in (account, person):
        await world["cache"].set(f"gen:0:{space}:{{{profile.profile_id}}}", b"1")
    target = Target(person, about=AboutLink(company.handle_id, account, "buyer", False))
    caller = principal(WHATSAPP_SOURCE, world["config"].space(space))
    await state.subject_view(caller, target)  # both lists and the copy are kept
    with counting() as statements:
        view, _ = await state.subject_view(caller, target)
    assert statements == []
    assert [o.ref.id for o in view.objects] == ["3001"]
