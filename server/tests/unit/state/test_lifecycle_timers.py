"""Computed values, derived objects, lifecycle and timers on the in-memory cell (24.3.6 to 24.3.8): a value is
a versioned fact, a revised value fires again a timer that already fired, naming the firing it supersedes, a
changed input expires what was derived from it, a clock transition closes an outcome, and an outcome's
deadline passes without one."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.typed import InMemoryTypedRepository
from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectRead, StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, EventKind, Feature, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, config_with, event, handle

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
COURT = uuid7()
SHOP = uuid7()
CRM = uuid7()
QUOTES = uuid7()
NOTICE = ObjectKey("intimacao", "produto", "pub-42")
ORDER = ObjectKey("order", "shop", "1001")
LEAD = ObjectKey("lead", "crm", "L-9")
QUOTE = ObjectKey("quote", "operadora", "q-77")

TYPES: list[dict[str, Any]] = [
    {
        "type": "intimacao",
        "ownership": "subject",
        "mirror_of": {"system": "court"},
        "time": {
            "published_on": {"origin": "derived", "rule": "publicacao_primeiro_dia_util@v3"},
            "known_at": {"origin": "platform", "immutable": True},
            "timers_on": "published_on",
        },
        "fields": {"addressee_is_client": {"type": "bool"}},
        "values": {
            "due_date": {
                "rule": "prazo_forense@v9",
                "inputs": ["published_on", "calendar@2026"],
                "declared_gaps": ["portaria_de_tribunal"],
                "gap_effect": "errs_early",
                "absent_as": "sem_prazo",
            }
        },
        "sources": {"court": {"kind": "push", "precedence": 1}},
        "states": ["open", "fulfilled"],
        "lifecycle": {
            "transitions": [{"from": "open", "to": "fulfilled", "by": ["source"]}],
            "latches": ["fulfilled"],
            "outcome": {"final": ["fulfilled"], "expired_without_outcome": {"after": "due_date"}},
        },
        "timers": {"notice_48h": {"due": "due_date - 48h", "once_per": "publication", "on_fire": ["notify"]}},
    },
    {
        "type": "order",
        "ownership": "subject",
        "mirror_of": {"system": "shop"},
        "time": {"delivered_at": {"origin": "source"}, "timers_on": "delivered_at"},
        "sources": {"shop": {"kind": "push", "precedence": 1}},
        "states": ["placed", "paid", "delivered", "kept", "returned", "cancelled"],
        "lifecycle": {
            "transitions": [
                {"from": "placed", "to": "paid", "by": ["source"]},
                {"from": "paid", "to": "delivered", "by": ["source"]},
                {"from": "delivered", "to": "kept", "by": ["clock"]},
                {"from": "*", "to": "cancelled", "by": ["source"]},
            ],
            "latches": ["paid", "delivered"],
            "outcome": {
                "provisional": ["delivered"],
                "final": ["kept", "returned", "cancelled"],
                "expired_without_outcome": {"after": "60d"},
                "vocabularies": [{"source": "shop", "map": {"DLV": "delivered", "PAG": "paid"}}],
            },
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
        "inputs": ["lead.city", "lead.ages", "turn.product_type"],
        "fields": {"price": {"type": "money", "claim": {"allowed": True, "class": "money"}}},
        "sources": {"quotes": {"kind": "push", "precedence": 1}},
    },
]


@pytest.fixture
def world(cell: Cell) -> World:
    base = config_with()
    systems = [
        SourceConfig(source_id=COURT, name="court", audience=Audience.INTERNAL_AGENT),
        SourceConfig(source_id=SHOP, name="shop", audience=Audience.INTERNAL_AGENT),
        SourceConfig(source_id=CRM, name="crm", audience=Audience.INTERNAL_AGENT),
        SourceConfig(source_id=QUOTES, name="quotes", audience=Audience.INTERNAL_AGENT),
    ]
    cell.clock.at = NOW
    cell.config.put(
        base.model_copy(
            update={
                "sources": [*base.sources, *systems],
                "object_types": ObjectTypeRegistry.model_validate({"types": TYPES}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )
    return World(cell)


class Outcomes:
    def __init__(self) -> None:
        self.seen: list[BusinessObject] = []

    async def consume(self, uow: Any, space_id: UUID, objects: Any) -> None:
        self.seen.extend(objects)


@pytest.fixture
def outcomes() -> Outcomes:
    return Outcomes()


@pytest.fixture
def objects(world: World, outcomes: Outcomes) -> ObjectsService:
    return ObjectsService(world.uow, world.cell.clock, world.cell.config, outcomes=outcomes)


@pytest.fixture
def state(world: World) -> StateService:
    return StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)  # type: ignore[arg-type]


async def report(
    world: World,
    objects: ObjectsService,
    state: StateService,
    key: ObjectKey,
    source: UUID,
    fields: dict[str, Any],
    *,
    ago: timedelta = timedelta(0),
) -> list[BusinessObject]:
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    e = event(
        EventKind.SYSTEM_EVENT,
        world.cell.clock.now() - ago,
        [world.identity.handles[owner.handle_ids[0]]],
        objects=[key],
        canonical_type=f"{key.type}.updated",
        fields=fields,
        source_id=source,
    )
    e = replace(e, received_at=world.cell.clock.now())
    world.ingest.add(e)
    written = await objects.apply_object_event(
        Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
    )
    await state.publish(SPACE_ID, written)
    return written


async def fire(world: World, objects: ObjectsService, state: StateService) -> list[BusinessObject]:
    written = await objects.fire_timers(SPACE_ID)
    await state.publish(SPACE_ID, written)
    return written


async def read(world: World, state: StateService, key: ObjectKey, purpose: str = "display") -> ObjectRead:
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    ref = {"type": key.type, "namespace": key.namespace, "id": key.external_id}
    answer = await state.read(caller, StateReadRequest.model_validate({"refs": [ref], "purpose": purpose}))
    [found] = answer.response.objects
    return found


def typed(world: World) -> InMemoryTypedRepository:
    return InMemoryTypedRepository(world.cell.uow.store)


async def deliveries(world: World, event_type: str) -> list[dict[str, Any]]:
    async with world.cell.uow(SPACE_ID) as uow:
        return [d.body["data"] for d in await uow.notify.list_deliveries() if d.event_type == event_type]


# A computed value is a versioned fact (L26).


async def test_a_revised_value_is_a_new_version_naming_the_one_it_supersedes(
    world: World, objects: ObjectsService, state: StateService
) -> None:
    await report(
        world, objects, state, NOTICE, COURT, {"published_on": "2026-10-01", "due_date": "2026-10-20"}
    )
    first = (await read(world, state, NOTICE)).values["due_date"]
    assert (first.version, first.supersedes_version, first.rule) == (1, None, "prazo_forense@v9")
    world.cell.clock.advance(minutes=5)
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-10-22"})
    second = (await read(world, state, NOTICE)).values["due_date"]
    assert (second.v, second.version, second.supersedes_version) == ("2026-10-22", 2, 1)
    versions = sorted((name, version) for _, name, version in typed(world).values)
    assert versions == [("due_date", 1), ("due_date", 2), ("published_on", 1)]
    assert [
        (d["name"], d["version"], d["supersedes_version"])
        for d in await deliveries(world, "object.value_revised")
    ] == [("due_date", 2, 1)]
    # The same value again is no new fact.
    world.cell.clock.advance(minutes=5)
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-10-22"})
    assert (await read(world, state, NOTICE)).values["due_date"].version == 2


async def test_a_recomputed_value_fires_again_a_timer_that_already_fired_with_a_reference(
    world: World, objects: ObjectsService, state: StateService
) -> None:
    # Due in a day: the 48-hour notice is already due, and fires at the next sweep.
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-10-02"})
    [timer] = (await read(world, state, NOTICE)).timers
    assert (timer.name, timer.state, timer.due_at) == (
        "notice_48h",
        "armed",
        datetime(2026, 9, 30, 3, 0, tzinfo=UTC),
    )
    assert world.pipeline.scheduled[-1][0].name is TaskName.FIRE_TIMERS
    await fire(world, objects, state)
    [fired] = (await read(world, state, NOTICE)).timers
    assert (fired.state, fired.fired_at, fired.supersedes_firing_id) == ("fired", NOW, None)
    [first] = await deliveries(world, "object.timer_due")
    assert first["late_s"] > 0
    assert first["supersedes_firing_id"] is None
    # Nothing fires twice for the same version.
    assert await fire(world, objects, state) == []
    # The company corrects its calendar: the notice goes out again, naming the one it replaces.
    world.cell.clock.advance(minutes=10)
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-10-03"})
    [rearmed] = (await read(world, state, NOTICE)).timers
    assert (rearmed.state, rearmed.supersedes_firing_id) == ("armed", first["firing_id"])
    await fire(world, objects, state)
    [second] = [
        d for d in await deliveries(world, "object.timer_due") if d["firing_id"] != first["firing_id"]
    ]
    assert second["supersedes_firing_id"] == first["firing_id"]


async def test_a_clock_transition_closes_the_outcome_and_the_latches_keep_the_first_time(
    world: World, objects: ObjectsService, state: StateService, outcomes: Outcomes
) -> None:
    delivered = NOW - timedelta(days=31)
    await report(world, objects, state, ORDER, SHOP, {"state": "PAG"}, ago=timedelta(days=32))
    await report(
        world,
        objects,
        state,
        ORDER,
        SHOP,
        {"state": "DLV", "delivered_at": delivered.isoformat()},
        ago=timedelta(days=31),
    )
    order = await read(world, state, ORDER)
    assert (order.state, order.outcome and order.outcome.final) == ("delivered", False)
    assert set(order.latches) == {"paid", "delivered"}
    assert order.axes["delivered_at"] == "2026-08-31T12:00:00.000Z"
    written = await fire(world, objects, state)
    assert [o.key for o in written] == [ORDER]
    kept = await read(world, state, ORDER)
    assert (kept.state, kept.outcome and kept.outcome.state, kept.outcome and kept.outcome.final) == (
        "kept",
        "kept",
        True,
    )
    assert kept.latches["delivered"] == delivered
    assert outcomes.seen
    assert outcomes.seen[-1].key == ORDER
    assert outcomes.seen[-1].state["state"] == "kept"  # what measurement reads: the canonical state
    exchange = next(t for t in kept.timers if t.name == "exchange_window")
    assert exchange.state == "fired"


async def test_an_object_past_its_deadline_expires_without_an_outcome(
    world: World, objects: ObjectsService, state: StateService, outcomes: Outcomes
) -> None:
    await report(world, objects, state, ORDER, SHOP, {"state": "placed"})
    assert (await read(world, state, ORDER)).outcome is None
    world.cell.clock.advance(days=61)
    # Computed at the read before the sweep runs; written by the sweep for measurement.
    expired = await read(world, state, ORDER)
    assert expired.outcome is not None
    assert (expired.outcome.state, expired.outcome.final) == ("expired_without_outcome", True)
    await fire(world, objects, state)
    assert (await read(world, state, ORDER)).state == "expired_without_outcome"
    assert outcomes.seen[-1].key == ORDER


async def test_a_final_state_cancels_what_is_still_armed(
    world: World, objects: ObjectsService, state: StateService
) -> None:
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-11-20", "state": "open"})
    world.cell.clock.advance(minutes=1)
    await report(world, objects, state, NOTICE, COURT, {"state": "fulfilled"})
    notice = await read(world, state, NOTICE)
    assert [t.state for t in notice.timers] == ["cancelled"]
    assert notice.outcome is not None
    assert notice.outcome.state == "fulfilled"
    assert await fire(world, objects, state) == []


# A derived object expires when an input changes (24.3.7).


async def test_a_changed_input_expires_the_quote_with_the_input_name(
    world: World, objects: ObjectsService, state: StateService
) -> None:
    await report(world, objects, state, LEAD, CRM, {"city": "Campinas", "ages": [34, 36]})
    refs = {"lead": "lead:crm:L-9", "turn": "ignored"}
    await report(world, objects, state, QUOTE, QUOTES, {"price": 812.4, "input_refs": refs})
    assert (await read(world, state, QUOTE)).derived_status == "current"
    world.cell.clock.advance(minutes=3)
    written = await report(world, objects, state, LEAD, CRM, {"city": "Sorocaba"})
    assert {o.key for o in written} == {LEAD, QUOTE}
    quote = await read(world, state, QUOTE, "claim")
    assert (quote.derived_status, quote.expired_by) == ("expired_by_input", ["lead.city"])
    assert not quote.fields["price"].claim_safe
    [expired] = await deliveries(world, "object.expired_by_input")
    assert expired["inputs"] == ["lead.city"]
    # A field no quote depends on moves nothing else.
    world.cell.clock.advance(minutes=1)
    assert {o.key for o in await report(world, objects, state, LEAD, CRM, {"name": "x"})} == {LEAD}
    # Priced again: current once more.
    world.cell.clock.advance(minutes=1)
    await report(world, objects, state, QUOTE, QUOTES, {"price": 799.0, "input_refs": refs})
    again = await read(world, state, QUOTE)
    assert (again.derived_status, again.expired_by) == ("current", [])


async def test_typed_writes_wait_for_the_state_feature(
    world: World, objects: ObjectsService, state: StateService
) -> None:
    config = world.cell.config.space(SPACE_ID)
    world.cell.config.put(config.model_copy(update={"features": FeatureSettings(enabled=frozenset())}))
    await report(world, objects, state, NOTICE, COURT, {"due_date": "2026-10-02"})
    assert typed(world).values == {}
    assert typed(world).timers == {}
    assert await objects.fire_timers(SPACE_ID) == []
