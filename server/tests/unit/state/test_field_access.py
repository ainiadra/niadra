"""Field access, source licences and named readings (24.2.4, 24.2.11, 24.2.13): the same decision on the
state read, the subject's view, the pack and the SDK profile, so a field an agent may not read reaches its
model through none of them."""

from typing import Any

import pytest

from niadra.app.compile import for_reader
from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.config.object_types import AccessRule, FieldSpec, Licence, ObjectTypeRegistry, Reading
from niadra.contracts.state import StateReadRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.compile.model import MemorySnapshot, Variant
from niadra.domain.policy.fields import FieldReader, field_effect, licence_allows, reader_of
from niadra.domain.policy.rules import Effect
from niadra.domain.records import ProfileRecord
from niadra.domain.vocabulary import SubjectKind
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, at
from tests.unit.state.test_state_service import BUDGET_DOCUMENT, QUOTE, QUOTE_API, apply, health_config

DECLARATION = FieldSpec.model_validate(
    {
        "type": "text",
        "sensitivity": "health",
        "pii": True,
        "access": [
            {"readers": ["agent:closing"], "purposes": ["closing"], "effect": "allow"},
            {"readers": ["agent:whatsapp_bot"], "effect": "mask"},
        ],
    }
)


def guarded_config(commercial: tuple[str, ...] = ("support",)) -> Any:
    """The health quote with a declaration only closers read, which the WhatsApp agent sees masked and the
    voice agent never; and a budget document whose licence forbids commercial use."""
    config = health_config()
    [quote] = config.object_types.types
    sources = dict(quote.sources)
    sources["budget_document"] = sources["budget_document"].model_copy(
        update={"licence": Licence(commercial_use="forbidden")}
    )
    readings = dict(quote.readings) | {
        "declared": {"expr": "health_declaration != ''", "purposes": ["support"]},
        "sales_only": {"expr": "price_full > 0", "purposes": ["sales"]},
    }
    guarded = quote.model_copy(
        update={
            "fields": {**quote.fields, "health_declaration": DECLARATION},
            "sources": sources,
            "readings": {name: _reading(r) for name, r in readings.items()},
        }
    )
    registry = ObjectTypeRegistry(types=[guarded], commercial_purposes=list(commercial))
    return config.model_copy(update={"object_types": registry})


def _reading(reading: Any) -> Any:
    return reading if isinstance(reading, Reading) else Reading.model_validate(reading)


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(guarded_config())
    return World(cell)


@pytest.fixture
def state(world: World) -> StateService:
    return StateService(world.uow, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)


async def filled(world: World, state: StateService) -> None:
    world.cell.clock.at = at(10)
    await apply(world, state, QUOTE_API, 9, {"price_full": 511.06, "health_declaration": "asthma"})
    await apply(world, state, BUDGET_DOCUMENT, 9.5, {"price_discounted": 480.0})


def ask(**extra: Any) -> StateReadRequest:
    ref = {"type": QUOTE.type, "namespace": QUOTE.namespace, "id": QUOTE.external_id}
    return StateReadRequest.model_validate({"refs": [ref], **extra})


def test_the_first_rule_that_names_the_reader_decides_and_a_field_no_rule_releases_is_denied() -> None:
    rules = DECLARATION.access
    closer = FieldReader(frozenset({"agent:closing"}), frozenset({"closing"}))
    assert field_effect(rules, closer) is Effect.ALLOW
    assert field_effect(rules, FieldReader(frozenset({"agent:closing"}), frozenset({"sales"}))) is Effect.DENY
    assert field_effect(rules, reader_of("WhatsApp Bot", ["support"])) is Effect.MASK
    assert field_effect(rules, reader_of("voice-agent", ["support"])) is Effect.DENY
    assert field_effect([], reader_of("anyone", [])) is Effect.ALLOW
    anyone = [AccessRule(readers=["public"], effect="allow")]
    assert field_effect(anyone, reader_of("voice-agent", [])) is Effect.ALLOW


def test_a_licence_bars_the_purposes_it_excludes_and_commercial_ones_when_it_forbids_them() -> None:
    reader = reader_of("voice-agent", ["support"])
    assert licence_allows(None, reader, ["support"])
    assert not licence_allows(Licence(exclude_from_purposes=["support"]), reader, [])
    assert not licence_allows(Licence(commercial_use="forbidden"), reader, ["support"])
    assert licence_allows(Licence(commercial_use="forbidden"), reader, ["sales"])


async def test_a_read_masks_denies_and_withholds_by_licence_for_each_reader(
    world: World, state: StateService
) -> None:
    await filled(world, state)
    space = world.cell.config.space(SPACE_ID)
    [masked] = (await state.read(principal(WHATSAPP_SOURCE, space), ask())).response.objects
    declaration = masked.fields["health_declaration"]
    assert declaration.masked
    assert declaration.v is None
    assert not declaration.claim_safe
    assert masked.withheld == {"price_discounted": "licence"}
    assert masked.fields["price_full"].v == 511.06

    [denied] = (await state.read(principal(VOICE_SOURCE, space), ask())).response.objects
    assert "health_declaration" not in denied.fields
    assert denied.withheld == {"health_declaration": "access", "price_discounted": "licence"}
    assert "asthma" not in denied.model_dump_json()
    assert "asthma" not in masked.model_dump_json()

    # With no commercial purpose, the budget document's value reaches the reader again.
    world.cell.config.put(guarded_config(commercial=()))
    [licensed] = (await state.read(principal(VOICE_SOURCE, space), ask())).response.objects
    assert licensed.fields["price_discounted"].v == 480.0


async def test_a_reading_serves_only_its_purposes_and_never_sees_what_access_hides(
    world: World, state: StateService
) -> None:
    await filled(world, state)
    space = world.cell.config.space(SPACE_ID)
    request = ask(readings=["declared", "sales_only", "commercial"])
    [quote] = (await state.read(principal(VOICE_SOURCE, space), request)).response.objects
    # `sales_only` and `commercial` serve sales: a support agent asked for them and gets neither.
    assert set(quote.readings) == {"declared"}
    assert quote.readings["declared"].logic == "unobserved"
    assert "price_discounted" not in quote.fields or quote.withheld.get("price_discounted") == "licence"


async def test_a_reading_prefers_its_source_and_carries_the_inputs_it_asks_for(
    world: World, cell: Cell
) -> None:
    world.cell.config.put(health_config())
    state = StateService(world.uow, cell.cache, cell.keys, cell.clock, cell.config)
    cell.clock.at = at(10)
    await apply(
        world, state, QUOTE_API, 9, {"price_discounted": 500.0, "input_refs": {"lead": "lead:crm:L-9"}}
    )
    await apply(world, state, BUDGET_DOCUMENT, 9.5, {"price_discounted": 480.0})
    config = cell.config.space(SPACE_ID)
    [quote] = config.object_types.types
    readings = dict(quote.readings) | {
        "api": _reading({"expr": "price_discounted", "prefer_source": "quote_api"}),
        "listed": _reading({"expr": "price_discounted"}),
        "inputs": _reading({"fields": ["price_full"], "include_inputs": True}),
    }
    registry = ObjectTypeRegistry(types=[quote.model_copy(update={"readings": readings})])
    cell.config.put(config.model_copy(update={"object_types": registry}))
    request = ask(readings=["api", "listed", "inputs"])
    [read] = (await state.read(principal(WHATSAPP_SOURCE, config), request)).response.objects
    # Both sources precede at 1, so the newer observation serves; the reading reads the source it prefers.
    assert read.readings["listed"].v == 480.0
    assert read.readings["api"].v == 500.0
    assert read.readings["inputs"].inputs == {"lead": "lead:crm:L-9"}


async def test_the_profile_tells_each_key_the_fields_it_may_not_read_and_the_mirror(
    world: World, state: StateService
) -> None:
    space = world.cell.config.space(SPACE_ID)
    [whatsapp] = state.profile(principal(WHATSAPP_SOURCE, space)).types
    [voice] = state.profile(principal(VOICE_SOURCE, space)).types
    assert whatsapp["field_access"] == {"health_declaration": "mask"}
    assert voice["field_access"] == {"health_declaration": "deny"}
    assert "access" not in voice["fields"]["health_declaration"]
    assert voice["mirror_of"] == {"system": "api_da_operadora", "derived_by": "declaration", "drift": "alert"}


async def test_the_pack_and_the_object_route_leave_out_what_the_reader_may_not_read(
    world: World, state: StateService
) -> None:
    await filled(world, state)
    space = world.cell.config.space(SPACE_ID)
    async with world.cell.uow(SPACE_ID) as uow:
        stored = await uow.objects.get_object(QUOTE)
    assert stored is not None
    assert stored.state["health_declaration"] == "asthma"
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "p_test", 1, 1)
    snapshot = MemorySnapshot(profile=profile, objects=(stored,))
    for source in (WHATSAPP_SOURCE, VOICE_SOURCE):
        variant = Variant(view="chat", source_id=source, purposes=frozenset({"support"}))
        [plain] = for_reader(space, snapshot, variant, at(10)).objects
        assert "health_declaration" not in plain.state
        assert "price_discounted" not in plain.state
        assert plain.state["price_full"] == 511.06
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
    served = await objects.read_object(principal(VOICE_SOURCE, space), QUOTE)
    assert "health_declaration" not in served.state.fields
