"""The typed pass of the shared objects on the in-memory cell: a computed value of a shared
object is versioned only when it changes, a revised value fires again a timer that already fired, naming the
firing it supersedes, a clock transition goes through the hot copy, a shared field that changes expires the
customers' quotes and the derived shared objects that take it as an input, a timer that fires while a pass
plans its object stays fired, and an object that leaves the working set takes its armed timers."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.memory.typed import InMemoryTypedRepository
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest, ObjectRead, StateReadRequest
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.state.shared import SharedKey, shared_object_id
from niadra.domain.state.typed import META_KEY
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Feature, Scope
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, config_with

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PLAN = "plan:operator:p-1"
ITEM = "item_variant:loja:v-1"
BEST = "best_plan:operator:b-1"
QUOTE = ObjectKey("plan_quote", "operator", "q-1")

TYPES: list[dict[str, Any]] = [
    {
        # A plan the operator pushes, with a promotion whose end its rule computes and a review before it.
        "type": "plan",
        "ownership": "shared",
        "mirror_of": {"system": "operator"},
        "working_set": {"enter_on": ["presented", "related"], "leave_after": "30d"},
        "fields": {"price_table": {"type": "money"}, "name": {"type": "string"}},
        "values": {"promo_ends": {"rule": "promo@v1"}},
        "sources": {"operator": {"kind": "push", "precedence": 1}},
        "timers": {"promo_review": {"due": "promo_ends - 1h", "on_fire": ["notify"]}},
    },
    {
        # A customer's quote, a function of the plan's price table.
        "type": "plan_quote",
        "ownership": "subject",
        "nature": "derived",
        "mirror_of": {"system": "operator"},
        "inputs": ["plan.price_table"],
        "fields": {"price_full": {"type": "money"}},
        "sources": {"operator": {"kind": "push", "precedence": 1}},
    },
    {
        # An item that goes on sale by the clock.
        "type": "item_variant",
        "ownership": "shared",
        "mirror_of": {"system": "shop"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "fields": {"launch_at": {"type": "datetime"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
        "states": ["scheduled", "active"],
        "lifecycle": {"transitions": [{"from": "scheduled", "to": "active", "by": ["clock"]}]},
        "timers": {"launch": {"due": "launch_at", "on_fire": ["transition(scheduled->active)", "notify"]}},
    },
    {
        # The best offer among the plans: shared, and a function of a plan's price table.
        "type": "best_plan",
        "ownership": "shared",
        "nature": "derived",
        "mirror_of": {"system": "operator"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "inputs": ["plan.price_table"],
        "fields": {"price": {"type": "money"}},
        "sources": {"operator": {"kind": "push", "precedence": 1}},
    },
]


@pytest.fixture
def world(cell: Cell) -> World:
    cell.clock.at = NOW
    cell.config.put(
        config_with().model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": TYPES}),
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


def item(
    ref: str, version: int, fields: dict[str, Any], *, at: datetime = NOW, **extra: Any
) -> dict[str, Any]:
    kind, namespace, external = ref.split(":", 2)
    return {
        "ref": {"type": kind, "namespace": namespace, "id": external},
        "version": version,
        "fields": fields,
        "provenance": {"source": "live", "source_observed_at": at.isoformat()},
        **extra,
    }


async def push(world: World, shared: SharedObjectsService, *items: dict[str, Any]) -> None:
    caller = principal(BILLING_SOURCE, world.cell.config.space(SPACE_ID), frozenset({Scope.STATE_PUSH}))
    await shared.push(caller, ObjectPushRequest.model_validate({"objects": list(items)}))


async def written(world: World, shared: SharedObjectsService) -> list[BusinessObject]:
    """The customers' items of the pushes, applied by the objects worker, then their relations."""
    found: list[BusinessObject] = []
    for task in world.pipeline.take(TaskName.APPLY_OBSERVATIONS):
        opened = await shared._observations.open(task)  # type: ignore[attr-defined]
        objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
        found += await objects.apply_observations(SPACE_ID, opened.subject)
    await shared.enter_related(SPACE_ID, found)
    return found


async def settled(shared: SharedObjectsService) -> list[BusinessObject]:
    """A consolidation, then a typed pass."""
    await shared.consolidate(SPACE_ID)
    return await shared.plan_typed(SPACE_ID)


async def read(world: World, state: StateService, ref: str) -> ObjectRead:
    kind, namespace, external = ref.split(":", 2)
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    request = {"refs": [{"type": kind, "namespace": namespace, "id": external}]}
    [found] = (await state.read(caller, StateReadRequest.model_validate(request))).response.objects
    return found


async def deliveries(world: World, event_type: str) -> list[dict[str, Any]]:
    async with world.cell.uow(SPACE_ID) as uow:
        return [d.body["data"] for d in await uow.notify.list_deliveries() if d.event_type == event_type]


def repo(world: World) -> InMemoryTypedRepository:
    return InMemoryTypedRepository(world.cell.uow.store)


def plan_id() -> Any:
    return shared_object_id(SPACE_ID, SharedKey("plan", "operator", "p-1"))


async def test_a_shared_value_is_versioned_when_it_changes_and_a_revision_fires_its_timer_again(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [PLAN], "presented")
    ends = NOW + timedelta(minutes=90)
    await push(world, shared, item(PLAN, 1, {"promo_ends": ends.isoformat()}))
    assert await settled(shared) == []
    [first] = [v for (o, _, _), v in repo(world).values.items() if o == plan_id()]
    assert (first.version, first.supersedes) == (1, None)
    # Due within the hour: the firing task is scheduled at its due time, besides the sweep.
    [(task, due, _)] = world.pipeline.scheduled
    assert (task.name, due) == (TaskName.FIRE_TIMERS, ends - timedelta(hours=1))
    # The same value pushed again is no revision.
    await push(world, shared, item(PLAN, 2, {"promo_ends": ends.isoformat()}))
    await settled(shared)
    assert len(repo(world).values) == 1

    world.cell.clock.at = NOW + timedelta(minutes=31)
    assert await shared.fire_timers(SPACE_ID) == 1
    [notice] = await deliveries(world, "object.timer_due")
    assert (notice["object"], notice["timer"]) == (
        {"type": "plan", "namespace": "operator", "id": "p-1"},
        "promo_review",
    )
    assert notice["supersedes_firing_id"] is None

    # The rule's owner moves the promotion's end: a new version, and the review fires again naming the first.
    later = NOW + timedelta(minutes=110)
    await push(world, shared, item(PLAN, 3, {"promo_ends": later.isoformat()}))
    await settled(shared)
    [revised] = await deliveries(world, "object.value_revised")
    assert (revised["name"], revised["version"], revised["supersedes_version"]) == ("promo_ends", 2, 1)
    plan = await read(world, state, PLAN)
    assert (plan.values["promo_ends"].version, plan.values["promo_ends"].supersedes_version) == (2, 1)
    [timer] = plan.timers
    assert (timer.state, timer.supersedes_firing_id) == ("armed", notice["firing_id"])
    world.cell.clock.at = NOW + timedelta(minutes=51)
    assert await shared.fire_timers(SPACE_ID) == 1
    again = [d for d in await deliveries(world, "object.timer_due") if d["firing_id"] != notice["firing_id"]]
    assert [d["supersedes_firing_id"] for d in again] == [notice["firing_id"]]
    assert await shared.fire_timers(SPACE_ID) == 0  # once per firing


async def test_a_shared_field_that_changes_expires_the_quotes_derived_from_it(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [PLAN], "presented")
    await push(world, shared, item(PLAN, 1, {"price_table": 100.0}))
    await settled(shared)
    await push(
        world,
        shared,
        item(
            f"plan_quote:{QUOTE.namespace}:{QUOTE.external_id}",
            1,
            {"price_full": 812.4},
            inputs={"plan": PLAN},
        ),
    )
    [quote] = await written(world, shared)
    assert quote.derived_status == "current"
    # Bound to the plan by the plan's own id: no customer's object is made up for it.
    assert (plan_id(), "plan.price_table", quote.object_id) in repo(world).dependencies
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.objects.get_objects([ObjectKey("plan", "operator", "p-1")]) == []

    # The same price pushed again expires nothing; a new price expires the quote, naming the input.
    await push(world, shared, item(PLAN, 2, {"price_table": 100.0, "name": "Plano Ouro"}))
    assert await settled(shared) == []
    await push(world, shared, item(PLAN, 3, {"price_table": 120.0}))
    [expired] = await settled(shared)
    assert (expired.key, expired.derived_status, expired.typed.expired_by) == (
        QUOTE,
        "expired_by_input",
        ("plan.price_table",),
    )
    [event] = await deliveries(world, "object.expired_by_input")
    assert event["inputs"] == ["plan.price_table"]
    assert event["input"] == {"type": "plan", "namespace": "operator", "id": "p-1"}


async def test_a_quote_brings_its_shared_input_into_the_working_set(
    world: World, shared: SharedObjectsService
) -> None:
    await push(
        world,
        shared,
        item(
            f"plan_quote:{QUOTE.namespace}:{QUOTE.external_id}", 1, {"price_full": 1.0}, inputs={"plan": PLAN}
        ),
    )
    await written(world, shared)
    async with world.cell.uow(SPACE_ID) as uow:
        assert [r.key.ref for r in await uow.typed.shared_by_id([plan_id()])] == [PLAN]
    # A price the quote never saw expires it at once: nothing tells what it was computed from.
    await shared.consolidate(SPACE_ID)  # loads the working set into the hot copy
    await push(world, shared, item(PLAN, 1, {"price_table": 90.0}))
    [expired] = await settled(shared)
    assert expired.key == QUOTE


async def test_the_clock_moves_a_shared_object_through_its_hot_copy(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [ITEM], "presented")
    launch = NOW + timedelta(minutes=5)
    await push(world, shared, item(ITEM, 1, {"state": "scheduled", "launch_at": launch.isoformat()}))
    await settled(shared)
    assert (await read(world, state, ITEM)).state == "scheduled"
    world.cell.clock.at = launch
    assert await shared.fire_timers(SPACE_ID) == 1
    [notice] = await deliveries(world, "object.timer_due")
    assert notice["timer"] == "launch"
    await settled(shared)
    moved = await read(world, state, ITEM)
    assert moved.state == "active"
    assert [t.state for t in moved.timers] == ["fired"]


async def test_an_object_that_leaves_the_working_set_takes_its_armed_timers(
    world: World, shared: SharedObjectsService
) -> None:
    await shared.enter_working_set(SPACE_ID, [ITEM], "presented")
    await push(world, shared, item(ITEM, 1, {"launch_at": (NOW + timedelta(days=90)).isoformat()}))
    await settled(shared)
    assert len(repo(world).timers) == 1
    world.cell.clock.at = NOW + timedelta(days=31)
    assert await shared.leave(SPACE_ID) == 1
    assert repo(world).timers == {}


async def test_an_untyped_shared_type_is_never_planned(
    world: World, shared: SharedObjectsService, hot: InMemorySharedObjects
) -> None:
    plain = {**TYPES[0], "values": {}, "timers": {}}
    world.cell.config.put(
        world.cell.config.space(SPACE_ID).model_copy(
            update={"object_types": ObjectTypeRegistry.model_validate({"types": [plain]})}
        )
    )
    await shared.enter_working_set(SPACE_ID, [PLAN], "presented")
    await push(world, shared, item(PLAN, 1, {"price_table": 100.0}))
    await shared.consolidate(SPACE_ID)
    assert hot.typed.get(SPACE_ID, set()) == set()
    assert await shared.plan_typed(SPACE_ID) == []
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.typed.shared_by_id([plan_id()])
    assert META_KEY not in row.meta


def best_id() -> Any:
    return shared_object_id(SPACE_ID, SharedKey("best_plan", "operator", "b-1"))


async def test_a_derived_shared_object_is_expired_by_its_shared_input_until_computed_after_the_change(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [BEST], "presented")
    await push(world, shared, item(BEST, 1, {"price": 90.0}, inputs={"plan": PLAN}))
    await settled(shared)
    best = await read(world, state, BEST)
    assert (best.derived_status, best.expired_by) == ("current", [])
    # Bound to the plan by the plan's id, which joins the working set as related to it.
    assert (plan_id(), "plan.price_table", best_id()) in repo(world).dependencies
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.typed.shared_by_id([best_id()])
        assert [r.key.ref for r in await uow.typed.shared_by_id([plan_id()])] == [PLAN]
    assert row.derived_status == "current"

    # A price it never saw expires it, naming the input and the plan.
    world.cell.clock.at = NOW + timedelta(minutes=2)
    await push(world, shared, item(PLAN, 1, {"price_table": 100.0}, at=NOW + timedelta(minutes=2)))
    await settled(shared)
    best = await read(world, state, BEST)
    assert (best.derived_status, best.expired_by) == ("expired_by_input", ["plan.price_table"])
    [event] = await deliveries(world, "object.expired_by_input")
    assert event["object"] == {"type": "best_plan", "namespace": "operator", "id": "b-1"}
    assert (event["inputs"], event["input"]) == (
        ["plan.price_table"],
        {"type": "plan", "namespace": "operator", "id": "p-1"},
    )
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.typed.shared_by_id([best_id()])
    assert row.derived_status == "expired_by_input"

    # A computation observed before the change leaves it expired; one after it makes it current again.
    await push(world, shared, item(BEST, 2, {"price": 91.0}, at=NOW + timedelta(minutes=1)))
    await settled(shared)
    assert (await read(world, state, BEST)).derived_status == "expired_by_input"
    await push(world, shared, item(BEST, 3, {"price": 95.0}, at=NOW + timedelta(minutes=3)))
    await settled(shared)
    assert (await read(world, state, BEST)).derived_status == "current"
    # Bound again only when its inputs change: the same plan keeps the same binding.
    assert [d for d in repo(world).dependencies if d[2] == best_id()] == [
        (plan_id(), "plan.price_table", best_id())
    ]


async def test_a_timer_that_fires_while_a_pass_plans_its_object_stays_fired(
    world: World,
    shared: SharedObjectsService,
    state: StateService,
    hot: InMemorySharedObjects,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await shared.enter_working_set(SPACE_ID, [PLAN], "presented")
    ends = NOW + timedelta(minutes=30)
    await push(world, shared, item(PLAN, 1, {"promo_ends": ends.isoformat(), "price_table": 100.0}))
    await settled(shared)
    # A new price: the next pass writes the plan's stamps; the sweep fires its due review between the pass's
    # read and its write.
    await push(world, shared, item(PLAN, 2, {"price_table": 101.0}))
    await shared.consolidate(SPACE_ID)
    read_rows = InMemoryTypedRepository.shared_by_id
    fired: list[int] = []

    async def racing(self: InMemoryTypedRepository, object_ids: Any) -> Any:
        rows = await read_rows(self, object_ids)
        if not fired:
            fired.append(-1)
            fired[0] = await shared.fire_timers(SPACE_ID)
        return rows

    monkeypatch.setattr(InMemoryTypedRepository, "shared_by_id", racing)
    await shared.plan_typed(SPACE_ID)
    monkeypatch.undo()
    assert fired == [1]
    [timer] = [t for t in repo(world).timers.values() if t.object_id == plan_id()]
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.typed.shared_by_id([plan_id()])
    # The row, the stored stamps and the hot copy agree; the pass's own stamps are kept.
    assert timer.write.state == "fired"
    assert row.meta[META_KEY]["t"]["promo_review"]["st"] == "fired"
    assert set(row.meta[META_KEY]["i"]) == {"price_table"}
    [review] = (await read(world, state, PLAN)).timers
    assert review.state == "fired"
    assert await shared.fire_timers(SPACE_ID) == 0


async def test_older_stamps_published_later_never_replace_newer_ones(hot: InMemorySharedObjects) -> None:
    await hot.enter(SPACE_ID, [("d", plan_id(), {}, 60, b'{"n": 1}', 1)])
    await hot.set_typed(SPACE_ID, [("d", b'{"n": 3}', 3)])
    await hot.set_typed(SPACE_ID, [("d", b'{"n": 2}', 2)])
    [(_, _, typed)] = [found for found in await hot.load(SPACE_ID, ["d"]) if found]
    assert typed == b'{"n": 3}'


async def test_a_derived_shared_object_bound_in_the_pass_its_input_changes_expires_at_once(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [BEST, PLAN], "presented")
    await push(world, shared, item(PLAN, 1, {"price_table": 100.0}))
    await settled(shared)
    # The best plan and a new price of its plan land in the same pass: which came first is not known.
    await push(world, shared, item(BEST, 1, {"price": 90.0}, inputs={"plan": PLAN}))
    await push(world, shared, item(PLAN, 2, {"price_table": 120.0}))
    await settled(shared)
    best = await read(world, state, BEST)
    assert (best.derived_status, best.expired_by) == ("expired_by_input", ["plan.price_table"])
