"""What tools showed, taken from applied turns (24.3.5): only observations with provenance, new, of a declared
subject type, in one sealed task; the objects worker applies it by the rule of a system event."""

import base64
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from niadra.adapters.inbound.workers.objects import handlers
from niadra.adapters.memory.erasure import InMemoryErasureMarks
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer, observed
from niadra.app.state import StateService
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import StateReadRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, handle
from tests.unit.state.test_state_service import QUOTE, health_config
from tests.unit.turns.records import turn

RECEIVED = datetime.fromisoformat("2026-09-29T14:02:20+00:00")
QUOTE_REF = "cotacao_plano_saude:operadora:q-77"


def observation(fields: dict[str, Any], **provenance: Any) -> dict[str, Any]:
    source = {"source": "live", "source_observed_at": "2026-09-29T14:02:12Z"} | provenance
    return {"ref": QUOTE_REF, "fields": fields, "provenance": source}


def applied(*observations: dict[str, Any], **call: Any) -> AppliedTurn:
    record = turn()
    record["calls"][0]["observations"] = list(observations)
    record["calls"][0].update(call)
    return AppliedTurn(WHATSAPP_SOURCE, RECEIVED, TurnRecord.model_validate(record))


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(health_config())
    return World(cell)


def test_only_new_observations_with_provenance_of_a_declared_subject_type_are_state(world: World) -> None:
    space = world.cell.config.space(SPACE_ID)
    live = observation({"price_full": 511.06})
    snapshot = observation({"price_discounted": 459.95}, source="snapshot")
    cached = observation({"price_full": 1.0}, source="cache")
    context = observation({"price_full": 2.0}, scope="context")
    shown = {"ref": QUOTE_REF, "fields": {"price_full": 3.0}}  # no provenance: shown, never state
    undeclared = observation({"x": 1}) | {"ref": "invoice:erp:1"}
    [first, second] = observed(space, [applied(live, snapshot, cached, context, shown, undeclared)])
    assert (first.key, first.report.fields, first.report.provenance) == (
        QUOTE,
        {"price_full": 511.06},
        "live",
    )
    assert first.report.source == "tool:quote"
    assert first.report.valid_at == datetime.fromisoformat("2026-09-29T14:02:12+00:00")
    assert (second.report.fields, second.report.provenance) == ({"price_discounted": 459.95}, "snapshot")
    # A cached answer of the tool, a synthetic call and a failed one are no new observation.
    assert observed(space, [applied(live, cache_hit=True)]) == []
    assert observed(space, [applied(live, synthetic=True)]) == []
    assert observed(space, [applied(live, status="error")]) == []


def test_a_value_of_another_kind_than_declared_is_dropped(world: World) -> None:
    space = world.cell.config.space(SPACE_ID)
    [seen] = observed(space, [applied(observation({"price_full": "R$ 511", "price_discounted": 459.95}))])
    assert seen.report.fields == {"price_discounted": 459.95}
    assert observed(space, [applied(observation({"price_full": True}))]) == []


async def test_a_group_leaves_one_sealed_task_and_the_worker_applies_it(world: World) -> None:
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    turns = [applied(observation({"price_full": 511.06, "enrollment_fee": 0})) for _ in range(3)]
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, turns)
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    assert b"511.06" not in base64.b64decode(task.payload["sealed"])
    assert "511.06" not in str(task.payload)

    opened = await consumer.open(task)
    assert len(opened.subject) == 3
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
    written = await objects.apply_observations(SPACE_ID, opened.subject)
    [quote] = written
    assert quote.origin_handle_id is None  # nobody named the customer yet
    assert quote.state["price_full"] == 511.06

    state = StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)
    await state.publish(SPACE_ID, written)
    ref = {"type": QUOTE.type, "namespace": QUOTE.namespace, "id": QUOTE.external_id}
    read = await state.read(
        principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID)),
        StateReadRequest.model_validate({"refs": [ref], "purpose": "claim"}),
    )
    [served] = read.response.objects
    price = served.fields["price_full"]
    # The type declares no source for this tool: its observation stands after every declared source.
    assert (price.v, price.src, price.claim_safe) == (511.06, "quote", True)
    assert price.observed_at == datetime.fromisoformat("2026-09-29T14:02:12+00:00")
    assert (served.fields["enrollment_fee"].v, served.fields["enrollment_fee"].logic) == (0, "yes")


async def test_turns_without_state_leave_no_task(world: World) -> None:
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [applied()])
    assert world.pipeline.take(TaskName.APPLY_OBSERVATIONS) == []


async def test_an_observation_before_its_type_reaches_the_worker_is_applied_once_it_does(
    world: World,
) -> None:
    """A company turns a type on and sends turns at once: the process that applies them may not know the type
    for a few seconds. The observation waits for it instead of staying only in the turn's record."""
    declared = world.cell.config.space(SPACE_ID)
    without = declared.model_copy(update={"object_types": ObjectTypeRegistry()})
    world.cell.config.put(without)
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [applied(observation({"price_full": 511.06}))])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
    state = StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)
    c = SimpleNamespace(
        objects=objects,
        observations=consumer,
        shared=SimpleNamespace(
            enter_working_set=_nothing, watch=_nothing, observe=_nothing, enter_related=_nothing
        ),
        state=state,
        refresh=SimpleNamespace(on_written=_nothing),
        clock=world.cell.clock,
    )
    handle = handlers(c)[TaskName.APPLY_OBSERVATIONS]
    world.cell.clock.at = RECEIVED + timedelta(seconds=5)
    await handle(task)
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.objects.get_object(QUOTE) is None  # not applied, and not lost:
    [(later, due, _)] = world.pipeline.scheduled
    assert later.name is TaskName.APPLY_OBSERVATIONS
    assert due == RECEIVED + timedelta(seconds=20)
    # The worker's configuration gets the type; the task comes back.
    world.cell.config.put(declared)
    world.cell.clock.at = due
    await handle(later)
    async with world.cell.uow(SPACE_ID) as uow:
        quote = await uow.objects.get_object(QUOTE)
    assert quote is not None
    assert quote.state["price_full"] == 511.06


async def test_an_observation_of_a_type_never_declared_is_given_up_after_the_wait(
    world: World,
) -> None:
    world.cell.config.put(
        world.cell.config.space(SPACE_ID).model_copy(update={"object_types": ObjectTypeRegistry()})
    )
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [applied(observation({"price_full": 511.06}))])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    opened = await consumer.open(task)
    _, _, still = consumer.resolve(SPACE_ID, opened.unresolved)
    assert len(still) == 1
    dropped = await consumer.defer(SPACE_ID, still, RECEIVED + timedelta(minutes=2))
    assert dropped == still
    assert world.pipeline.scheduled == []


async def _nothing(*args: Any, **kwargs: Any) -> None:
    return None


async def test_an_observation_on_its_way_never_brings_an_erased_customers_object_back(world: World) -> None:
    owner = handle(HandleType.SYSTEM_ID)
    world.identity.add_profile(owner)
    consumer = ObservationsConsumer(
        world.uow, world.cell.keys, world.cell.config, erasures=(InMemoryErasureMarks(), world.cell.clock)
    )
    async with world.uow(SPACE_ID) as uow:
        await uow.objects.add_object(
            BusinessObject(uuid7(), QUOTE, {}, RECEIVED, WHATSAPP_SOURCE, owner.handle_id)
        )
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(uow, SPACE_ID, [applied(observation({"price_full": 511.06}))])
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    # The erasure runs while the task, received before it, still waits.
    world.cell.clock.at = RECEIVED + timedelta(seconds=5)
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (owner.handle_id,), None, None, RECEIVED)
    await consumer.fence(SPACE_ID, scope)
    async with world.uow(SPACE_ID) as uow:
        await uow.objects.erase_for(scope, limit=10)
    opened = await consumer.open(task)
    assert await consumer.unfenced(SPACE_ID, opened.subject) == []
    other = replace(opened.subject[0], key=ObjectKey(QUOTE.type, QUOTE.namespace, "q-78"))
    assert await consumer.unfenced(SPACE_ID, [other]) == [other]
    # What the cell receives after the erasure is new activity: it lands.
    seen = opened.subject[0]
    later = replace(
        seen, report=replace(seen.report, received_at=world.cell.clock.now() + timedelta(seconds=1))
    )
    assert await consumer.unfenced(SPACE_ID, [later]) == [later]
    # One that slipped in between (created with no owner while the cascade ran) leaves with the purge.
    async with world.uow(SPACE_ID) as uow:
        await uow.objects.add_object(BusinessObject(uuid7(), QUOTE, {}, RECEIVED, WHATSAPP_SOURCE))
    await consumer.purge(SPACE_ID, scope)
    async with world.uow(SPACE_ID) as uow:
        assert await uow.objects.get_object(QUOTE) is None


async def test_a_shared_object_presented_before_its_type_reaches_the_worker_enters_before_its_observation(
    world: World,
) -> None:
    """The turn that presents a shared object and shows its price, taken before the type reached the
    process: the entry waits with the observation, so the object is in the working set when the observation
    is written, and the price is not lost as out of the set."""
    item_type = {
        "type": "item_variant",
        "ownership": "shared",
        "mirror_of": {"system": "shop"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "fields": {"price_sale": {"type": "money"}},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
    }
    declared = world.cell.config.space(SPACE_ID).model_copy(
        update={"object_types": ObjectTypeRegistry.model_validate({"types": [item_type]})}
    )
    world.cell.config.put(declared.model_copy(update={"object_types": ObjectTypeRegistry()}))
    ref = "item_variant:loja:v-1"
    record = turn()
    record["calls"][0]["observations"] = [
        {"ref": ref, "fields": {"price_sale": 189.9},
         "provenance": {"source": "live", "source_observed_at": "2026-09-29T14:02:12Z"}}
    ]  # fmt: skip
    record["interactions"] = [
        {"kind": "presented", "exposure_id": str(uuid7()), "list_id": "results", "visible_k": 1,
         "items": [{"pos": 1, "ref": ref}], "delivered_at": "2026-09-29T14:02:13Z"}
    ]  # fmt: skip
    consumer = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    async with world.uow(SPACE_ID) as uow:
        await consumer.consume(
            uow, SPACE_ID, [AppliedTurn(WHATSAPP_SOURCE, RECEIVED, TurnRecord.model_validate(record))]
        )
    [task] = world.pipeline.take(TaskName.APPLY_OBSERVATIONS)
    calls: list[tuple[str, Any]] = []

    async def entering(space_id: Any, refs: Any, reason: str) -> None:
        calls.append(("enter", (list(refs), reason)))

    async def observing(space_id: Any, observed: Any) -> None:
        calls.append(("observe", [o.key.external_id for o in observed]))

    c = SimpleNamespace(
        objects=ObjectsService(world.uow, world.cell.clock, world.cell.config),
        observations=consumer,
        shared=SimpleNamespace(
            enter_working_set=entering, watch=_nothing, observe=observing, enter_related=_nothing
        ),
        state=StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config),
        refresh=SimpleNamespace(on_written=_nothing),
        clock=world.cell.clock,
    )
    handle = handlers(c)[TaskName.APPLY_OBSERVATIONS]
    world.cell.clock.at = RECEIVED + timedelta(seconds=5)
    await handle(task)
    assert [name for name, _ in calls if name == "enter"] == []
    [(later, _, _)] = world.pipeline.scheduled
    world.cell.config.put(declared)
    calls.clear()
    await handle(later)
    assert calls == [("enter", ([ref], "presented")), ("observe", ["v-1"])]
