"""Reverse relations (24.2.7): a lawsuit names none of its notices, and its derived fields
still read them, as the customers' objects whose `case_number` holds the lawsuit's. Each write keeps the
value the relation matches on; a read finds the notices by it, drops one that no longer holds it, reads at
most 50 and says which notice kept a field from `yes`."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry, Relation
from niadra.contracts.state import ObjectRead, StateReadRequest
from niadra.domain.records import HandleRecord, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, Feature, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, at, config_with, event, handle

NOTICE: dict[str, Any] = {
    "type": "notice",
    "ownership": "subject",
    "mirror_of": {"system": "court_feed"},
    "fields": {"case_number": {"type": "string"}, "answered": {"type": "bool"}},
    "states": ["open", "fulfilled"],
    "sources": {"billing": {"kind": "push", "precedence": 1}},
}
LAWSUIT: dict[str, Any] = {
    "type": "lawsuit",
    "ownership": "subject",
    "mirror_of": {"system": "case_db"},
    "fields": {"case_number": {"type": "string"}, "court": {"type": "string"}},
    "relations": {"notices": {"type": "notice", "via": "case_number", "match": "case_number", "many": True}},
    "derived_fields": {
        "open_deadlines": {"agg": "count", "over": "notices", "where": "state == 'open'"},
        "all_answered": {"agg": "all", "over": "notices", "where": "answered == yes"},
    },
    "sources": {"billing": {"kind": "push", "precedence": 1}},
}
CASE, OTHER_CASE = "0001234-56.2026.8.26.0100", "0009999-11.2026.8.26.0100"
SUIT = ObjectKey("lawsuit", "firm", "s-1")


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(
        config_with().model_copy(
            update={
                "object_types": ObjectTypeRegistry.model_validate({"types": [NOTICE, LAWSUIT]}),
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
            }
        )
    )
    return World(cell)


class Firm:
    def __init__(self, world: World) -> None:
        self.world = world
        self.objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
        self.state = StateService(
            world.uow,  # type: ignore[arg-type]
            world.cell.cache,
            world.cell.keys,
            world.cell.clock,
            world.cell.config,
        )
        self.client: HandleRecord = handle(HandleType.SYSTEM_ID)
        world.identity.add_profile(self.client)
        self.minute = 0

    async def write(self, key: ObjectKey, fields: dict[str, Any]) -> None:
        self.minute += 1
        e = event(
            EventKind.SYSTEM_EVENT, at(self.minute), [self.client], objects=[key],
            canonical_type=f"{key.type}.updated", fields=fields,
        )  # fmt: skip
        self.world.ingest.add(e)
        task = Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
        await self.state.publish(SPACE_ID, await self.objects.apply_object_event(task))

    async def suit(self) -> ObjectRead:
        caller = principal(WHATSAPP_SOURCE, self.world.cell.config.space(SPACE_ID))
        request = StateReadRequest.model_validate(
            {"refs": [{"type": "lawsuit", "namespace": "firm", "id": "s-1"}]}
        )
        [found] = (await self.state.read(caller, request)).response.objects
        return found


def notice(n: int) -> ObjectKey:
    return ObjectKey("notice", "firm", f"n-{n}")


async def test_a_lawsuits_notices_are_found_by_its_case_number(world: World) -> None:
    firm = Firm(world)
    await firm.write(SUIT, {"case_number": CASE, "court": "1a Vara"})
    await firm.write(notice(1), {"case_number": CASE, "state": "open", "answered": False})
    await firm.write(notice(2), {"case_number": CASE, "state": "open", "answered": True})
    await firm.write(notice(3), {"case_number": OTHER_CASE, "state": "open", "answered": False})
    suit = await firm.suit()
    opened, answered = suit.derived["open_deadlines"], suit.derived["all_answered"]
    assert (opened.logic, opened.v, opened.over) == ("yes", 2, 2)
    # The notice not yet answered is named: the agent says which one, rather than hedging.
    assert (answered.v, answered.pieces_out) == (False, ["notice:firm:n-1"])
    # A notice that moves to another case leaves this one; one that arrives joins it.
    await firm.write(notice(1), {"case_number": OTHER_CASE})
    await firm.write(notice(4), {"case_number": CASE, "state": "fulfilled", "answered": True})
    suit = await firm.suit()
    assert (suit.derived["open_deadlines"].v, suit.derived["open_deadlines"].over) == (1, 2)
    assert (suit.derived["all_answered"].v, suit.derived["all_answered"].pieces_out) == (True, [])


async def test_a_lawsuit_without_a_case_number_does_not_know_its_notices_and_one_without_notices_has_none(
    world: World,
) -> None:
    firm = Firm(world)
    await firm.write(SUIT, {"court": "1a Vara"})
    assert "open_deadlines" not in (await firm.suit()).derived
    await firm.write(SUIT, {"case_number": CASE})
    opened = (await firm.suit()).derived["open_deadlines"]
    assert (opened.logic, opened.v, opened.over) == ("yes", 0, 0)


async def test_a_reverse_relation_reads_fifty_notices_and_says_it_is_partial(world: World) -> None:
    # Past fifty, the first fifty are read and the rest count as not known.
    firm = Firm(world)
    await firm.write(SUIT, {"case_number": CASE})
    for n in range(51):
        await firm.write(notice(n), {"case_number": CASE, "state": "open"})
    opened = (await firm.suit()).derived["open_deadlines"]
    assert (opened.logic, opened.over, opened.unknown, opened.partial) == ("unobserved", 51, 1, True)


async def test_the_object_read_serves_the_derived_fields_a_state_read_serves(world: World) -> None:
    # `GET /v1/objects/...` (the SDK's object_state) left `derived` empty for a type that declares some.
    firm = Firm(world)
    await firm.write(SUIT, {"case_number": CASE, "court": "1a Vara"})
    await firm.write(notice(1), {"case_number": CASE, "state": "open", "answered": False})
    await firm.write(notice(2), {"case_number": CASE, "state": "fulfilled", "answered": True})
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config, derivations=firm.state)
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    read = (await objects.read_object(caller, SUIT)).state
    assert (read.derived["open_deadlines"].v, read.derived["open_deadlines"].over) == (1, 2)
    assert read.derived == (await firm.suit()).derived


def test_a_reverse_relation_matches_a_field_of_a_customers_type_and_has_no_entries() -> None:
    with pytest.raises(ValidationError, match="no status_field"):
        Relation.model_validate(
            {"type": "notice", "via": "case_number", "match": "case_number", "status_field": "s"}
        )
    unknown_field = {
        **LAWSUIT,
        "relations": {"notices": {**LAWSUIT["relations"]["notices"], "match": "number"}},
    }
    with pytest.raises(ValidationError, match="'number' is not a field of notice"):
        ObjectTypeRegistry.model_validate({"types": [NOTICE, unknown_field]})
    shared = {**NOTICE, "ownership": "shared", "working_set": {"enter_on": ["related"], "leave_after": "30d"}}
    with pytest.raises(ValidationError, match="reads customers' objects"):
        ObjectTypeRegistry.model_validate({"types": [shared, LAWSUIT]})
    registry = ObjectTypeRegistry.model_validate({"types": [NOTICE, LAWSUIT]})
    assert registry.matched_fields("notice") == frozenset({"case_number"})
    assert registry.matched_fields("lawsuit") == frozenset()
