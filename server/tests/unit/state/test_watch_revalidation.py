"""A watch whose condition holds on a value that did not come live from its source (24.3.12; object-state 8):
the resolver worker is asked for the value again on the `interest_alert` budget, and the watch fires only on
its answer; with no worker, a release or a give-up, the type's `unconfirmed_watch` decides."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.refresh import InMemoryRefreshWants
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.app.observations import ObservationsConsumer
from niadra.app.refresh import RefreshService
from niadra.app.shared_objects import SharedObjectsService, uuid5_of
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.state.shared import SharedKey, shared_object_id
from niadra.domain.vocabulary import Feature
from niadra.ports.repos.typed import WatchRow
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.objects.fakes import World, config_with
from tests.unit.state.test_shared_objects import ITEM, NOW, REF, item, pusher

HANDLE = UUID("0199a1b2-0000-7000-8000-00000000abcd")
KEY = SharedKey("item_variant", "loja", "v-1")
REFETCH = {
    "reasons": [
        {
            "name": "interest_alert",
            "when": "watch_count > 0",
            "priority": "high",
            "budget": {"units": 1, "per": "item/5min"},
        }
    ],
    "budget": {"unit": "platform_call"},
}


def configured(cell: Cell, **refetch: Any) -> None:
    declared = {**ITEM, "refetch": {**REFETCH, **refetch}}
    cell.config.put(
        config_with().model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": [declared]}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )


@pytest.fixture
def world(cell: Cell) -> World:
    cell.clock.at = NOW
    configured(cell)
    return World(cell)


@pytest.fixture
def wants() -> InMemoryRefreshWants:
    return InMemoryRefreshWants()


@pytest.fixture
def refresh(world: World, wants: InMemoryRefreshWants) -> RefreshService:
    return RefreshService(world.uow, wants, world.cell.clock, world.cell.config)


@pytest.fixture
async def shared(world: World, refresh: RefreshService) -> SharedObjectsService:
    observations = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    service = SharedObjectsService(
        world.uow,
        InMemorySharedObjects(),
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        observations,
        world.cell.objects,
        refresh=refresh,
    )
    refresh.watches = service
    await service.enter_working_set(SPACE_ID, [REF], "watched")
    object_id = shared_object_id(SPACE_ID, KEY)
    watch = WatchRow(
        uuid5_of(object_id, HANDLE),
        object_id,
        HANDLE,
        "price_sale < 150",
        "consent-1",
        NOW + timedelta(days=30),
    )
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.typed.watch([watch], NOW)
    return service


async def push(world: World, shared: SharedObjectsService, *items: dict[str, Any]) -> None:
    await shared.push(pusher(world), ObjectPushRequest.model_validate({"objects": list(items)}))


async def fired(world: World) -> list[bool]:
    async with world.cell.uow(SPACE_ID) as uow:
        found = [d for d in await uow.notify.list_deliveries() if d.event_type == "object.watch_fired"]
    return [d.body["data"]["revalidated"] for d in found]


async def requested(world: World, refresh: RefreshService) -> list[tuple[str, str]]:
    await refresh.flush(SPACE_ID)
    page = await refresh.lease(pusher(world), 10)
    return [(r.request_id, r.reason) for r in page.items]


async def test_a_watch_on_a_snapshot_value_waits_for_the_worker_and_fires_on_its_live_answer(
    world: World, shared: SharedObjectsService, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    wants.workers.add(SPACE_ID)
    await push(world, shared, item(5, {"price_sale": 140.0}, source="snapshot"))
    await shared.consolidate(SPACE_ID)
    assert await fired(world) == []
    [(request_id, reason)] = await requested(world, refresh)
    assert reason == "watch_revalidation"
    # The worker's live value carries an older version than the snapshot's: stale for the hot copy, and still
    # what the watch is decided on.
    await push(world, shared, item(4, {"price_sale": 139.0}) | {"request_id": request_id})
    await shared.consolidate(SPACE_ID)
    assert await fired(world) == [True]
    assert await requested(world, refresh) == []


async def test_an_answer_on_which_the_condition_no_longer_holds_fires_nothing_and_settles(
    world: World, shared: SharedObjectsService, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    wants.workers.add(SPACE_ID)
    await push(world, shared, item(5, {"price_sale": 140.0}, source="snapshot"))
    await shared.consolidate(SPACE_ID)
    [(request_id, _)] = await requested(world, refresh)
    await push(world, shared, item(6, {"price_sale": 160.0}) | {"request_id": request_id})
    await shared.consolidate(SPACE_ID)
    assert await fired(world) == []
    assert await requested(world, refresh) == []


@pytest.mark.parametrize(("policy", "expected"), [("fire", [False]), ("drop", [])])
async def test_with_no_worker_the_type_decides_at_once(
    world: World, shared: SharedObjectsService, policy: str, expected: list[bool]
) -> None:
    configured(world.cell, unconfirmed_watch=policy)
    await push(world, shared, item(5, {"price_sale": 140.0}, source="cache"))
    await shared.consolidate(SPACE_ID)
    assert await fired(world) == expected


async def test_a_released_request_falls_back_to_the_type(
    world: World, shared: SharedObjectsService, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    wants.workers.add(SPACE_ID)
    await push(world, shared, item(5, {"price_sale": 140.0}, source="snapshot"))
    await shared.consolidate(SPACE_ID)
    [(request_id, _)] = await requested(world, refresh)
    released = await refresh.release(pusher(world), request_id, "not_found")
    assert released.reason == "watch_revalidation"
    assert await fired(world) == [False]
    with pytest.raises(NotFound):
        await refresh.release(pusher(world), request_id, "failed")
    with pytest.raises(NotFound):
        await refresh.release(pusher(world), "not-an-id", "failed")


async def test_a_request_the_worker_never_answers_is_given_up_and_falls_back(
    world: World, shared: SharedObjectsService, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    wants.workers.add(SPACE_ID)
    await push(world, shared, item(5, {"price_sale": 140.0}, source="snapshot"))
    await shared.consolidate(SPACE_ID)
    await refresh.flush(SPACE_ID)
    for _ in range(3):
        assert len((await refresh.lease(pusher(world), 10)).items) == 1
        world.cell.clock.advance(seconds=61)
    assert await refresh.give_up(SPACE_ID) == 1
    assert await fired(world) == [False]


async def test_a_live_value_fires_at_once_and_asks_no_worker(
    world: World, shared: SharedObjectsService, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    wants.workers.add(SPACE_ID)
    await push(world, shared, item(5, {"price_sale": 140.0}))
    await shared.consolidate(SPACE_ID)
    assert await fired(world) == [True]
    assert await requested(world, refresh) == []
