"""Derived fields at the read (24.2.7): a look's `all`, `count`, `any`, `min` and `max` over
its pieces, computed from the pieces' hot copies with no statement and never stored; a piece that is not known
leaves the answer not known unless it is decided; a substitute and a missing piece are not known, an optional
one is left out; a field the reader may not read never reaches an aggregate; the look brings its pieces into
the working set; and the state block's lines say them only when the space turns it on."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.app.observations import ObservationsConsumer
from niadra.app.serve_reads import Target
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest, ObjectRead, StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.records import ProfileRecord
from niadra.domain.state.derived import Piece, aggregate
from niadra.domain.state.logic import Logic
from niadra.domain.vocabulary import Feature, Scope, SubjectKind
from niadra.ports.state import SeenInterest
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, config_with

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LOOK = "look:loja:l-1"
YES, NO, UNKNOWN = Logic.YES, Logic.NO, Logic.UNOBSERVED

VARIANT: dict[str, Any] = {
    "type": "item_variant",
    "ownership": "shared",
    "mirror_of": {"system": "shop"},
    "working_set": {"enter_on": ["presented", "related"], "leave_after": "30d"},
    "fields": {
        "available": {"type": "bool"},
        "price_sale": {"type": "money"},
        "cost": {"type": "money", "access": [{"readers": ["agent:billing_agent"], "effect": "allow"}]},
    },
    "sources": {"shop": {"kind": "push", "precedence": 1}},
}
LOOK_TYPE: dict[str, Any] = {
    "type": "look",
    "ownership": "shared",
    "mirror_of": {"system": "shop"},
    "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
    "fields": {"name": {"type": "string"}},
    "relations": {
        "pieces": {
            "type": "item_variant",
            "via": "pieces[*].variant_id",
            "many": True,
            "status_field": "status",
            "required_field": "required",
        }
    },
    "derived_fields": {
        "all_pieces_available": {"agg": "all", "over": "pieces", "where": "available == yes"},
        "pieces_count": {"agg": "count", "over": "pieces"},
        "any_piece_available": {"agg": "any", "over": "pieces", "where": "available == yes"},
        "lowest_price": {"agg": "min", "over": "pieces", "field": "price_sale"},
        "lowest_cost": {"agg": "min", "over": "pieces", "field": "cost"},
    },
    "sources": {"shop": {"kind": "push", "precedence": 1}},
}


@pytest.mark.parametrize(
    ("agg", "pieces", "expected"),
    [
        ("all", [YES, YES], (YES, True)),
        ("all", [YES, NO, UNKNOWN], (NO, False)),
        ("all", [YES, UNKNOWN], (UNKNOWN, None)),
        ("all", [], (UNKNOWN, None)),
        ("any", [NO, UNKNOWN, YES], (YES, True)),
        ("any", [NO, NO], (NO, False)),
        ("any", [NO, UNKNOWN], (UNKNOWN, None)),
        ("any", [], (NO, False)),
        ("count", [YES, NO, YES], (YES, 2)),
        ("count", [YES, UNKNOWN], (UNKNOWN, None)),
    ],
)
def test_all_any_and_count_decide_only_what_the_known_pieces_decide(
    agg: Any, pieces: list[Logic], expected: tuple[Logic, Any]
) -> None:
    found = aggregate(agg, [Piece(p) for p in pieces])
    assert (found.logic, found.value) == expected
    assert (found.over, found.unknown) == (len(pieces), pieces.count(UNKNOWN))


def test_min_and_max_compare_the_pieces_the_condition_holds_for() -> None:
    pieces = [Piece(YES, 10.0), Piece(NO, 1.0), Piece(YES, 30.0)]
    assert (aggregate("min", pieces).value, aggregate("max", pieces).value) == (10.0, 30.0)
    assert aggregate("min", [Piece(NO, 1.0)]).logic is NO  # none meets it: absent, not zero
    assert aggregate("max", [Piece(YES, 1.0), Piece(YES)]).logic is UNKNOWN
    assert aggregate("max", [Piece(YES, 1.0), Piece(UNKNOWN)]).logic is UNKNOWN


def test_a_declaration_names_the_compared_field_of_the_related_type_and_its_condition_reads_that_type() -> (
    None
):
    def registry(derived: dict[str, Any]) -> Any:
        return ObjectTypeRegistry.model_validate(
            {"types": [VARIANT, {**LOOK_TYPE, "derived_fields": derived}]}
        )

    with pytest.raises(ValueError, match="min and max name the field they compare"):
        registry({"low": {"agg": "min", "over": "pieces"}})
    with pytest.raises(ValueError, match="min and max name the field they compare"):
        registry({"n": {"agg": "count", "over": "pieces", "field": "price_sale"}})
    with pytest.raises(ValueError, match="'stock' is not a field of item_variant"):
        registry({"low": {"agg": "min", "over": "pieces", "field": "stock"}})
    with pytest.raises(ValueError, match=r"look\.derived_fields\.cheap\.where"):
        registry({"cheap": {"agg": "all", "over": "pieces", "where": "price_sale == 'low'"}})
    with pytest.raises(ValueError, match="the relation's type 'item_variant' is not declared"):
        ObjectTypeRegistry.model_validate({"types": [LOOK_TYPE]})


class Interests:
    def __init__(self) -> None:
        self.seen: list[SeenInterest] = []

    async def interests(self, space_id: UUID, handle_ids: Any) -> list[SeenInterest]:
        return self.seen


def space_with(*, in_text: bool = False) -> Any:
    registry = {"types": [VARIANT, LOOK_TYPE], "derived_fields_in_text": in_text}
    return config_with().model_copy(
        update={
            "object_types": ObjectTypeRegistry.model_validate(registry),
            "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
        }
    )


@pytest.fixture
def world(cell: Cell) -> World:
    cell.clock.at = NOW
    cell.config.put(space_with())
    return World(cell)


@pytest.fixture
def shared(world: World) -> SharedObjectsService:
    observations = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    return SharedObjectsService(
        world.uow,
        InMemorySharedObjects(),
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        observations,
        world.cell.objects,
    )


@pytest.fixture
def state(world: World, shared: SharedObjectsService) -> StateService:
    service = StateService(
        world.uow,  # type: ignore[arg-type]
        world.cell.cache,
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        shared=shared,
    )
    service.interests = Interests()
    return service


def pushed(ref: str, version: int, fields: dict[str, Any]) -> dict[str, Any]:
    kind, namespace, external = ref.split(":", 2)
    return {
        "ref": {"type": kind, "namespace": namespace, "id": external},
        "version": version,
        "fields": fields,
        "provenance": {"source": "live", "source_observed_at": NOW.isoformat()},
    }


async def push(world: World, shared: SharedObjectsService, *items: dict[str, Any]) -> None:
    pusher = principal(BILLING_SOURCE, world.cell.config.space(SPACE_ID), frozenset({Scope.STATE_PUSH}))
    await shared.push(pusher, ObjectPushRequest.model_validate({"objects": list(items)}))


async def read_look(world: World, state: StateService, source: UUID = WHATSAPP_SOURCE) -> ObjectRead:
    caller = principal(source, world.cell.config.space(SPACE_ID))
    request = {"refs": [{"type": "look", "namespace": "loja", "id": "l-1"}]}
    [found] = (await state.read(caller, StateReadRequest.model_validate(request))).response.objects
    return found


PIECES = [
    {"variant_id": "v-1", "status": "exact", "required": True},
    {"variant_id": "v-2", "status": "exact"},
    {"variant_id": "v-3", "status": "exact", "required": False},
]


async def a_look(world: World, shared: SharedObjectsService, pieces: list[dict[str, Any]]) -> None:
    variants = [f"item_variant:loja:v-{n}" for n in (1, 2, 3)]
    await shared.enter_working_set(SPACE_ID, [LOOK, *variants], "presented")
    await push(
        world,
        shared,
        pushed(LOOK, 1, {"name": "Linho de verão", "pieces": pieces}),
        pushed(variants[0], 1, {"available": True, "price_sale": 189.9, "cost": 80.0}),
        pushed(variants[1], 1, {"available": True, "price_sale": 99.9, "cost": 40.0}),
        pushed(variants[2], 1, {"available": False, "price_sale": 59.9, "cost": 20.0}),
    )


async def test_a_looks_derived_fields_follow_its_pieces_at_every_read(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await a_look(world, shared, PIECES)
    look = await read_look(world, state)
    # The optional third piece is left out.
    assert {name: (d.logic, d.v, d.over) for name, d in look.derived.items()} == {
        "all_pieces_available": ("yes", True, 2),
        "any_piece_available": ("yes", True, 2),
        "pieces_count": ("yes", 2, 2),
        "lowest_price": ("yes", 99.9, 2),
    }
    # Nothing stored: the next piece's change is the next read's answer.
    await push(world, shared, pushed("item_variant:loja:v-2", 2, {"available": False}))
    look = await read_look(world, state)
    assert (look.derived["all_pieces_available"].v, look.derived["any_piece_available"].v) == (False, True)
    # The field names the piece that is out; one that is `yes` names none.
    assert look.derived["all_pieces_available"].pieces_out == ["item_variant:loja:v-2"]
    assert look.derived["any_piece_available"].pieces_out == []


async def test_a_piece_not_known_or_a_substitute_leaves_the_answer_not_known_unless_it_is_decided(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    pieces = [
        {"variant_id": "v-1", "status": "exact"},
        {"variant_id": "v-9", "status": "exact"},  # never in the working set
        {"variant_id": "v-2", "status": "future"},  # a substitute: an observation, not state
    ]
    await a_look(world, shared, pieces)
    look = await read_look(world, state)
    everything = look.derived["all_pieces_available"]
    assert (everything.logic, everything.over, everything.unknown) == ("unobserved", 3, 2)
    assert everything.pieces_out == ["item_variant:loja:v-9", "item_variant:loja:v-2"]
    assert look.derived["pieces_count"].v == 3  # counts what the look names, held or not
    assert look.derived["any_piece_available"].v is True  # one known piece decides it
    await push(world, shared, pushed("item_variant:loja:v-1", 2, {"available": False}))
    assert (await read_look(world, state)).derived["all_pieces_available"].v is False  # and one decides all


async def test_past_the_pieces_one_read_takes_a_decided_answer_is_given_and_said_partial(
    world: World, shared: SharedObjectsService, state: StateService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Past 50 pieces every derived field was "not known", even one its first pieces already
    # decide. The first ones are read now, the rest count as not known, and the field says it is partial.
    import niadra.app.derived as derived_module
    import niadra.app.shared_objects as shared_module
    import niadra.app.state as state_module

    for module in (derived_module, shared_module, state_module):
        monkeypatch.setattr(module, "PIECES_MAX", 1)
    await a_look(world, shared, PIECES)
    look = await read_look(world, state)
    anything = look.derived["any_piece_available"]
    assert (anything.logic, anything.v, anything.partial) == ("yes", True, True)
    everything = look.derived["all_pieces_available"]
    assert (everything.logic, everything.partial) == ("unobserved", True)


async def test_a_field_the_reader_may_not_read_never_reaches_an_aggregate(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await a_look(world, shared, PIECES)
    look = await read_look(world, state)
    assert "lowest_cost" not in look.derived
    assert look.withheld["lowest_cost"] == "access"
    billing = await read_look(world, state, BILLING_SOURCE)
    assert billing.derived["lowest_cost"].v == 40.0


async def test_the_state_blocks_lines_say_derived_fields_only_when_the_space_turns_it_on(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await a_look(world, shared, PIECES)
    await push(world, shared, pushed("item_variant:loja:v-2", 2, {"available": False}))
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_ana", 1, 1, (uuid7(),))
    state.interests.seen = [  # type: ignore[attr-defined]
        SeenInterest(LOOK, "presented", NOW - timedelta(hours=1), {})
    ]
    caller = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))
    off, _ = await state.subject_view(caller, Target(profile))
    [interest] = off.interests
    assert interest.object is not None
    assert interest.object.derived["all_pieces_available"].v is False
    assert off.text is None  # nothing changed since seen, and the space did not turn the lines on

    world.cell.config.put(space_with(in_text=True))
    on, _ = await state.subject_view(caller, Target(profile))
    assert on.text is not None
    assert (
        "- look loja:l-1: all_pieces_available não (fora: item_variant loja:v-2); any_piece_available sim; "
        "lowest_price 99.90; " in on.text
    )
    assert "pieces_count 2" in on.text
    assert "lowest_cost" not in on.text  # reads a field some reader may not: never in a model's text


async def test_a_look_brings_the_pieces_its_derived_fields_read_into_the_working_set(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [LOOK], "presented")
    await push(world, shared, pushed(LOOK, 1, {"pieces": PIECES[:2]}))
    variant = pushed("item_variant:loja:v-1", 1, {"available": True})
    await push(world, shared, variant)  # not in the working set yet: kept nowhere
    assert (await read_look(world, state)).derived["all_pieces_available"].unknown == 2
    await shared.consolidate(SPACE_ID)
    await shared.plan_typed(SPACE_ID)  # the look's pieces join as related
    await push(world, shared, variant, pushed("item_variant:loja:v-2", 1, {"available": True}))
    assert (await read_look(world, state)).derived["all_pieces_available"].v is True
    # Presented again later, the look keeps its pieces referenced with it.
    world.cell.clock.at = NOW + timedelta(days=20)
    await shared.enter_working_set(SPACE_ID, [LOOK], "presented")
    world.cell.clock.at = NOW + timedelta(days=40)
    assert await shared.leave(SPACE_ID) == 0
