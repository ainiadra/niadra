"""Shared objects on the in-memory cell (24.3.12; object-state 10): the working set, pushes decided against
its hot copy with the per-field version rule, the consolidator writing in one statement what the hot copy
served, observations from turns, watches, reads, a hot copy lost and loaded again, and snapshots."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import orjson
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.state import push_router
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.memory.typed import InMemoryTypedRepository
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.shared_objects import SharedObjectsService, SharedObserved
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest, ObjectRead, StateReadRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.state.shared import SharedKey, shared_object_id
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature, Scope
from niadra.ports.infra import CacheUnavailable
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, config_with
from tests.unit.state.test_http import FakeEdge
from tests.unit.turns.records import turn

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ITEM: dict[str, Any] = {
    "type": "item_variant",
    "ownership": "shared",
    "mirror_of": {"system": "shop"},
    "working_set": {"enter_on": ["presented", "engaged", "watched"], "leave_after": "30d"},
    "fields": {
        "price_sale": {
            "type": "money",
            "freshness": {"class": "price", "max_age": "60s", "claim_max_age": "15s"},
            "claim": {"allowed": True, "class": "money"},
            "track_changes": True,
        },
        "available": {"type": "bool", "claim": {"allowed": True, "class": "quantity"}},
        "color": {"type": "enum"},
    },
    "sources": {
        "platform": {"kind": "push", "precedence": 1},
        "search": {"kind": "tool_observation", "precedence": 2},
    },
}
ORDER: dict[str, Any] = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "shop"},
    "fields": {"status": {"type": "enum"}},
    "sources": {"shop": {"kind": "push", "precedence": 1}},
}
REF = "item_variant:loja:v-1"


@pytest.fixture
def world(cell: Cell) -> World:
    cell.clock.at = NOW
    cell.config.put(
        config_with().model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": [ITEM, ORDER]}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )
    return World(cell)


@pytest.fixture
def hot() -> InMemorySharedObjects:
    return InMemorySharedObjects()


@pytest.fixture
def shared(world: World, hot: InMemorySharedObjects) -> SharedObjectsService:
    observations = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    return SharedObjectsService(
        world.uow, hot, world.cell.keys, world.cell.clock, world.cell.config, observations, world.cell.objects
    )


@pytest.fixture
def state(world: World, shared: SharedObjectsService) -> StateService:
    return StateService(
        world.uow,  # type: ignore[arg-type]
        world.cell.cache,
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        shared=shared,
    )


def pusher(world: World) -> Any:
    return principal(BILLING_SOURCE, world.cell.config.space(SPACE_ID), frozenset({Scope.STATE_PUSH}))


def item(
    version: int, fields: dict[str, Any], *, ref: str = REF, source: str = "live", ago: float = 0
) -> dict[str, Any]:
    kind, namespace, external = ref.split(":", 2)
    observed = (NOW - timedelta(seconds=ago)).isoformat()
    return {
        "ref": {"type": kind, "namespace": namespace, "id": external},
        "version": version,
        "fields": fields,
        "provenance": {"source": source, "source_observed_at": observed},
    }


async def push(
    world: World, shared: SharedObjectsService, *items: dict[str, Any]
) -> tuple[int, int, int, int]:
    answer = await shared.push(pusher(world), ObjectPushRequest.model_validate({"objects": list(items)}))
    return answer.applied, answer.stale_version, answer.out_of_set, answer.recorded


async def read(world: World, state: StateService, ref: str = REF, purpose: str = "claim") -> ObjectRead:
    kind, namespace, external = ref.split(":", 2)
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    request = {"refs": [{"type": kind, "namespace": namespace, "id": external}], "purpose": purpose}
    [found] = (await state.read(caller, StateReadRequest.model_validate(request))).response.objects
    return found


def repo(world: World) -> InMemoryTypedRepository:
    return InMemoryTypedRepository(world.cell.uow.store)


async def test_a_push_moves_each_field_of_its_source_only_forward(
    world: World, shared: SharedObjectsService
) -> None:
    assert await push(world, shared, item(3, {"price_sale": 199.9})) == (0, 0, 1, 0)  # not in the working set
    assert await shared.enter_working_set(SPACE_ID, [REF], "presented") == 1
    assert await push(world, shared, item(3, {"price_sale": 199.9})) == (1, 0, 0, 0)
    assert await push(world, shared, item(3, {"price_sale": 199.9})) == (0, 1, 0, 0)  # a retry
    # An older version still brings a field no newer one wrote, and moves nothing else back.
    assert await push(world, shared, item(2, {"price_sale": 250.0, "color": "bege"})) == (1, 0, 0, 0)
    [(_, fields, _)] = await shared._hot.load(
        SPACE_ID, [await shared._digest(SPACE_ID, SharedKey("item_variant", "loja", "v-1"))]
    )  # type: ignore[attr-defined]
    assert {name: version for name, (version, _) in fields.items()} == {
        f"price_sale|src:{BILLING_SOURCE}": 3,
        f"color|src:{BILLING_SOURCE}": 2,
    }
    # A reason its type does not enter by keeps the object out.
    assert await shared.enter_working_set(SPACE_ID, ["item_variant:loja:v-2"], "committed") == 0


async def test_the_working_set_is_off_until_the_space_turns_state_on(
    world: World, shared: SharedObjectsService
) -> None:
    config = world.cell.config.space(SPACE_ID)
    world.cell.config.put(config.model_copy(update={"features": FeatureSettings(enabled=frozenset())}))
    assert await shared.enter_working_set(SPACE_ID, [REF], "presented") == 0
    assert await shared.consolidate(SPACE_ID) == 0


async def test_ten_thousand_pushes_in_a_second_are_one_statement(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    refs = [f"item_variant:loja:v-{n}" for n in range(500)]
    await shared.enter_working_set(SPACE_ID, refs, "presented")
    for version in range(1, 21):
        items = [item(version, {"price_sale": 100.0 + version}, ref=ref) for ref in refs]
        for start in range(0, len(items), 1000):
            await push(world, shared, *items[start : start + 1000])
    assert await shared.consolidate(SPACE_ID) == 500
    assert repo(world).consolidations == [500]
    stored = repo(world).shared[shared_object_id(SPACE_ID, SharedKey("item_variant", "loja", "v-7"))]
    assert stored.meta["price_sale"][f"src:{BILLING_SOURCE}"]["version"] == 20
    assert await shared.consolidate(SPACE_ID) == 0  # nothing moved since
    assert repo(world).consolidations == [500]


async def test_a_read_serves_the_hot_copy_and_the_database_once_it_is_lost(
    world: World, shared: SharedObjectsService, state: StateService, hot: InMemorySharedObjects
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 199.9, "available": True}, ago=5))
    price = (await read(world, state)).fields["price_sale"]
    assert (price.v, price.src, price.status, price.claim_safe) == (199.9, "platform", "fresh", True)
    await shared.consolidate(SPACE_ID)
    hot.objects.clear()  # the cache store lost it
    again = (await read(world, state)).fields["price_sale"]
    assert (again.v, again.src) == (199.9, "platform")
    assert len(hot.objects) == 1  # seeded back from the database
    # Too old to claim: served with its age and the prohibitions, never refused.
    world.cell.clock.advance(seconds=30)
    stale = await read(world, state)
    assert not stale.fields["price_sale"].claim_safe


async def test_a_store_that_came_back_empty_is_loaded_again(
    world: World, shared: SharedObjectsService, hot: InMemorySharedObjects
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await shared.consolidate(SPACE_ID)
    hot.objects.clear()
    hot.marked.clear()
    assert await push(world, shared, item(1, {"price_sale": 1.0})) == (0, 0, 1, 0)
    await shared.consolidate(SPACE_ID)  # finds the mark gone and loads the working set
    assert await push(world, shared, item(1, {"price_sale": 1.0})) == (1, 0, 0, 0)


async def test_a_customer_object_is_recorded_for_the_objects_worker(
    world: World, shared: SharedObjectsService
) -> None:
    order = item(4, {"status": "shipped"}, ref="order:shop:1001")
    assert await push(world, shared, order) == (0, 0, 0, 1)
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    opened = await shared._observations.open(task)  # type: ignore[attr-defined]
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
    [written] = await objects.apply_observations(SPACE_ID, opened.subject)
    assert written.state["status"] == "shipped"
    assert written.state_version == "4"


async def test_a_push_scoped_to_a_customer_never_feeds_a_shared_object(
    world: World, shared: SharedObjectsService
) -> None:
    scoped = item(1, {"price_sale": 1.0})
    scoped["provenance"]["scope"] = "customer"
    with pytest.raises(InvalidInput):
        await push(world, shared, scoped)


def presented_turn(observed_price: float) -> AppliedTurn:
    record = turn()
    record["calls"][0]["name"] = "search"
    record["calls"][0]["observations"] = [
        {
            "ref": REF,
            "fields": {"price_sale": observed_price},
            "provenance": {"source": "live", "source_observed_at": NOW.isoformat()},
        }
    ]
    record["interactions"] = [
        {
            "kind": "presented",
            "exposure_id": "0199a1b2-0000-7000-8000-000000000001",
            "list_id": "results",
            "items": [{"pos": 1, "ref": REF, "shown": {"price_sale": observed_price}}],
            "delivered_at": NOW.isoformat(),
        },
        {
            "kind": "watch",
            "ref": REF,
            "condition": "price_sale < 150",
            "consent_event_key": "consent-1",
        },
    ]
    return AppliedTurn(WHATSAPP_SOURCE, NOW, TurnRecord.model_validate(record))


async def test_a_turn_brings_what_it_presented_into_the_working_set_and_what_its_tools_saw(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [presented_turn(189.9)])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    opened = await consumer.open(task)
    assert [(ref, reason) for ref, reason, _ in opened.enter] == [(REF, "presented"), (REF, "watched")]
    for ref, reason, _ in opened.enter:
        await shared.enter_working_set(SPACE_ID, [ref], reason)
    outcomes = await shared.observe(SPACE_ID, [SharedObserved(key, report) for key, report in opened.shared])
    assert [o.value for o in outcomes] == ["applied"]
    price = (await read(world, state)).fields["price_sale"]
    assert (price.v, price.src) == (189.9, "search")


async def test_a_watch_tells_its_subject_once_the_condition_holds(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    conversation = TurnRecord.model_validate(turn()).conversation_id
    handle = UUID("0199a1b2-0000-7000-8000-00000000abcd")
    world.ingest.primary = {conversation: handle}
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [presented_turn(189.9)])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    opened = await consumer.open(task)
    await shared.enter_working_set(SPACE_ID, [REF], "watched")
    await shared.watch(SPACE_ID, opened.watches)
    [watch] = repo(world).watches.values()
    assert (watch.handle_id, watch.condition) == (handle, "price_sale < 150")
    # The customer's data package lists it (24.3.12).
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.typed.watches_for([handle]) == [watch]
    await push(world, shared, item(1, {"price_sale": 180.0}))
    await shared.consolidate(SPACE_ID)
    async with world.cell.uow(SPACE_ID) as uow:
        assert [d for d in await uow.notify.list_deliveries() if d.event_type == "object.watch_fired"] == []
    await push(world, shared, item(2, {"price_sale": 140.0}))
    await shared.consolidate(SPACE_ID)
    async with world.cell.uow(SPACE_ID) as uow:
        [fired] = [d for d in await uow.notify.list_deliveries() if d.event_type == "object.watch_fired"]
    assert fired.body["data"]["revalidated"] is True
    assert "price_sale" not in str(fired.body)
    assert 140.0 not in fired.body["data"].values()
    # Once an hour at most.
    await push(world, shared, item(3, {"price_sale": 130.0}))
    await shared.consolidate(SPACE_ID)
    async with world.cell.uow(SPACE_ID) as uow:
        assert (
            len([d for d in await uow.notify.list_deliveries() if d.event_type == "object.watch_fired"]) == 1
        )


async def test_a_snapshot_is_applied_as_pushes_that_never_sustain_a_claim(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    lines = [item(9, {"price_sale": 175.0}), item(9, {"price_sale": 1.0}, ref="item_variant:loja:out")]

    async def body() -> Any:
        yield b"\n".join(orjson.dumps(line) for line in lines) + b"\nnot json\n"

    answer = await shared.take_snapshot(pusher(world), "item_variant", body())
    assert answer.objects == 3
    [task] = world.pipeline.take(TaskName.APPLY_SNAPSHOT)
    await shared.apply_snapshot(Task(task.name, task.space_id, task.payload))
    price = (await read(world, state)).fields["price_sale"]
    assert (price.v, price.claim_safe) == (175.0, False)
    assert await world.cell.objects.list_keys(f"imports/{SPACE_ID}/snapshots/") == []


async def test_the_push_and_snapshot_routes(world: World, shared: SharedObjectsService) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    container = SimpleNamespace(
        edge=FakeEdge({"nia_sk_push": pusher(world)}), config=world.cell.config, shared=shared
    )
    app = create_app("ingest", container, [push_router])
    key = {"authorization": "Bearer nia_sk_push"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://cell.test"
    ) as http:
        pushed = await http.post(
            "/v1/objects/push", headers=key, json={"objects": [item(1, {"price_sale": 9.9})]}
        )
        assert pushed.status_code == 200
        assert pushed.json() == {"applied": 1, "stale_version": 0, "out_of_set": 0, "recorded": 0}
        # A type nobody declared yet is a customer's type until the inference says otherwise:
        # kept for the objects worker, never refused.
        undeclared = await http.post(
            "/v1/objects/push", headers=key, json={"objects": [item(1, {"x": 1}, ref="nope:a:b")]}
        )
        assert undeclared.status_code == 200
        assert undeclared.json()["recorded"] == 1
        snapshot = await http.post(
            "/v1/objects/snapshot?type=item_variant",
            headers=key | {"content-type": "application/x-ndjson"},
            content=orjson.dumps(item(2, {"price_sale": 8.8})) + b"\n",
        )
        assert snapshot.status_code == 202
        assert snapshot.json()["objects"] == 1


async def test_a_customers_object_brings_the_shared_objects_it_relates_to_into_the_working_set(
    world: World, shared: SharedObjectsService
) -> None:
    related = {**ITEM, "working_set": {"enter_on": ["related"], "leave_after": "30d"}}
    order = {**ORDER, "relations": {"items": {"type": "item_variant", "via": "item_ids[*]", "many": True}}}
    space = world.cell.config.space(SPACE_ID)
    registry = ObjectTypeRegistry.model_validate({"types": [related, order]})
    world.cell.config.put(space.model_copy(update={"object_types": registry}))
    written = BusinessObject(
        uuid7(),
        ObjectKey("order", "loja", "o-1"),
        {"status": "paid", "item_ids": ["v-1", "item_variant:outlet:v-9"]},
        NOW,
        BILLING_SOURCE,
    )
    assert await shared.enter_related(SPACE_ID, [written]) == 2
    async with world.cell.uow(SPACE_ID) as uow:
        rows = await uow.typed.shared_by_id(
            [
                shared_object_id(SPACE_ID, SharedKey("item_variant", "loja", "v-1")),
                shared_object_id(SPACE_ID, SharedKey("item_variant", "outlet", "v-9")),
            ]
        )
    assert len(rows) == 2
    # A type whose working set `related` does not enter keeps out.
    world.cell.config.put(space)
    assert await shared.enter_related(SPACE_ID, [written]) == 0


async def test_a_push_the_hot_copy_cannot_take_answers_503_with_a_retry(
    world: World, shared: SharedObjectsService, hot: InMemorySharedObjects, monkeypatch: pytest.MonkeyPatch
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")

    async def timed_out(*args: Any, **kwargs: Any) -> list[str]:
        raise CacheUnavailable("cache shared_push failed: TimeoutError")

    monkeypatch.setattr(hot, "push", timed_out)
    with pytest.raises(Unavailable) as refused:
        await push(world, shared, item(1, {"price_sale": 140.0}))
    assert refused.value.retry_after_s == 1
