"""`StateService` on the in-memory cell: the copies the read path serves, the database only on a miss, the
version rule that keeps a copy from going back, and the SDK profile."""

import json
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from niadra.adapters.memory.uow import InMemoryUnitOfWork
from niadra.app.objects import ObjectsService
from niadra.app.state import StateService
from niadra.config.claims import ClaimContract
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import StateReadRequest, StateRef
from niadra.domain.common.ids import uuid7
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, EventKind, Feature, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, at, config_with, event, handle

DECLARATIONS = Path(__file__).parent / "declarations"
CLAIM_CONTRACTS = Path(__file__).resolve().parents[2] / "spec" / "examples" / "claim-contract"
QUOTE_API = uuid7()
BUDGET_DOCUMENT = uuid7()
QUOTE = ObjectKey("cotacao_plano_saude", "operadora", "q-77")


def health_config() -> Any:
    declared = yaml.safe_load((DECLARATIONS / "cotacao_plano_saude.yaml").read_text())
    base = config_with()
    systems = [
        SourceConfig(source_id=QUOTE_API, name="quote-api", audience=Audience.INTERNAL_AGENT),
        SourceConfig(source_id=BUDGET_DOCUMENT, name="Budget document", audience=Audience.INTERNAL_AGENT),
    ]
    return base.model_copy(
        update={
            "sources": [*base.sources, *systems],
            "object_types": ObjectTypeRegistry.model_validate({"types": [declared]}),
            "features": FeatureSettings(enabled=frozenset({Feature.STATE, Feature.CLAIMS})),
        }
    )


class Counted:
    """The unit of work factory, counting the space transactions a read opens."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.opened = 0

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        self.opened += 1
        return self.inner(space_id)  # type: ignore[no-any-return]

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        self.opened += 1
        return self.inner.system(space_id)  # type: ignore[no-any-return]

    def cell(self) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        return self.inner.cell()  # type: ignore[no-any-return]


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(health_config())
    return World(cell)


@pytest.fixture
def counted(world: World) -> Counted:
    return Counted(world.uow)


@pytest.fixture
def state(world: World, counted: Counted) -> StateService:
    return StateService(counted, world.cell.cache, world.cell.keys, world.cell.clock, world.cell.config)  # type: ignore[arg-type]


async def apply(
    world: World, state: StateService, source: UUID, minutes: float, fields: dict[str, Any]
) -> None:
    """A system event about the quote, applied and published as the objects worker does."""
    owner = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    e = event(
        EventKind.SYSTEM_EVENT,
        at(minutes),
        [world.identity.handles[owner.handle_ids[0]]],
        objects=[QUOTE],
        canonical_type="quote.priced",
        fields=fields,
        source_id=source,
    )
    world.ingest.add(e)
    objects = ObjectsService(world.uow, world.cell.clock, world.cell.config)
    written = await objects.apply_object_event(
        Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
    )
    await state.publish(SPACE_ID, written)


def ask(purpose: str = "claim", key: ObjectKey = QUOTE) -> StateReadRequest:
    ref = StateRef(type=key.type, namespace=key.namespace, id=key.external_id)
    return StateReadRequest.model_validate({"refs": [ref.model_dump()], "purpose": purpose})


def caller(world: World) -> Any:
    return principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID))


async def test_a_published_object_is_read_without_the_database(
    world: World, state: StateService, counted: Counted
) -> None:
    world.cell.clock.at = at(10)
    await apply(world, state, QUOTE_API, 9, {"price_full": 511.06, "enrollment_fee": 0})
    counted.opened = 0
    answer = await state.read(caller(world), ask())
    assert counted.opened == 0
    [quote] = answer.response.objects
    assert quote.fields["price_full"].v == 511.06
    assert quote.fields["price_full"].src == "quote_api"
    assert quote.fields["enrollment_fee"].logic == "known_defect"
    assert quote.blocked == {"decide:close": ["enrollment_fee"]}
    assert len(answer.object_ids) == 1

    # The budget document's fee switches the source, and the next read sees it with no statement either.
    await apply(world, state, BUDGET_DOCUMENT, 9.5, {"enrollment_fee": 120.0})
    counted.opened = 0
    [quote] = (await state.read(caller(world), ask())).response.objects
    assert counted.opened == 0
    assert (quote.fields["enrollment_fee"].v, quote.fields["enrollment_fee"].src) == (
        120.0,
        "budget_document",
    )
    assert quote.blocked == {}


async def test_a_miss_reads_the_database_once_and_the_next_read_is_served_from_the_copy(
    world: World, state: StateService, counted: Counted
) -> None:
    await apply(world, state, QUOTE_API, 1, {"price_full": 511.06})
    world.cell.cache.values.clear()  # evicted
    counted.opened = 0
    first = await state.read(caller(world), ask())
    assert counted.opened == 1
    again = await state.read(caller(world), ask())
    assert counted.opened == 1
    assert first.response.objects == again.response.objects


async def test_an_object_nobody_reported_reads_as_never_observed_until_its_first_event(
    world: World, state: StateService, counted: Counted
) -> None:
    [unknown] = (await state.read(caller(world), ask())).response.objects
    assert unknown.fields["price_full"].logic == "unobserved"
    assert unknown.as_of is None
    await state.read(caller(world), ask())
    assert counted.opened == 1  # the absence is kept for a while too
    await apply(world, state, QUOTE_API, 1, {"price_full": 511.06})
    [known] = (await state.read(caller(world), ask())).response.objects
    assert known.fields["price_full"].v == 511.06


async def test_a_copy_never_goes_back_to_an_older_version(world: World, state: StateService) -> None:
    await apply(world, state, QUOTE_API, 1, {"price_full": 500.0})
    async with world.uow(SPACE_ID) as uow:
        older = await uow.objects.get_object(QUOTE)
    assert older is not None
    await apply(world, state, QUOTE_API, 2, {"price_full": 511.06})
    await state.publish(SPACE_ID, [older])  # a slow worker's copy arrives last
    [quote] = (await state.read(caller(world), ask())).response.objects
    assert quote.fields["price_full"].v == 511.06


async def test_a_copy_of_an_owner_the_privacy_feed_marked_is_read_again(
    world: World, state: StateService, counted: Counted
) -> None:
    await apply(world, state, QUOTE_API, 1, {"price_full": 511.06})
    async with world.uow(SPACE_ID) as uow:
        stored = await uow.objects.get_object(QUOTE)
    assert stored is not None
    assert stored.origin_handle_id is not None
    guard = PrivacyGuard()
    now = world.cell.clock.now()
    guard.apply([], now)
    counted.opened = 0
    await state.read(caller(world), ask(), guard)
    assert counted.opened == 0
    feed = {
        "id": uuid7(),
        "created_at": now + timedelta(seconds=1),
        "privacy_version": 2,
        "handle_ids": [stored.origin_handle_id],
        "kind": "erasure",
    }
    guard.apply([feed], now)
    await state.read(caller(world), ask(), guard)
    assert counted.opened == 1


async def test_a_type_nobody_declared_yet_is_read_never_refused(world: World, state: StateService) -> None:
    """Zero touch: before the inference's first run a type is nobody's declaration, and a read of
    it answers as one of a customer's type with nothing declared (L19: a read never refuses)."""
    answer = await state.read(caller(world), ask(key=ObjectKey("invoice", "erp", "1")))
    [invoice] = answer.response.objects
    assert invoice.ref.type == "invoice"


async def test_only_declared_types_get_copies(world: World, state: StateService) -> None:
    invoice = BusinessObject(uuid7(), ObjectKey("invoice", "erp", "1"), {"status": "open"}, at(0), QUOTE_API)
    await state.publish(SPACE_ID, [invoice])
    assert world.cell.cache.values == {}


def test_the_profile_carries_the_features_the_types_and_the_claim_contract(
    world: World, state: StateService
) -> None:
    assert state.profile(caller(world)).claim_contract is None  # the space declares none
    contract = json.loads((CLAIM_CONTRACTS / "retail.json").read_text())
    space = world.cell.config.space(SPACE_ID)
    world.cell.config.put(space.model_copy(update={"claim_contract": ClaimContract.model_validate(contract)}))
    profile = state.profile(caller(world))
    assert profile.features == [Feature.CLAIMS, Feature.STATE]
    [quote] = profile.types
    assert quote["type"] == "cotacao_plano_saude"
    assert quote["purposes"]["decide"]["on_stale"] == "structured_refusal"
    assert quote["fields"]["enrollment_fee"]["unobserved_blocks"] == ["decide:close"]
    assert quote["sources"]["quote_api"]["known_defects"] == [
        {"field": "enrollment_fee", "use_source": "budget_document"}
    ]
    assert profile.claim_contract is not None
    assert [c.id for c in profile.claim_contract.categories] == [c["id"] for c in contract["categories"]]
    body = profile.model_dump_json()
    # The negative corpus stays with the company's CI: only its version travels.
    assert contract["negative_corpus"]["phrases"]
    assert not [p for p in contract["negative_corpus"]["phrases"] if json.dumps(p)[1:-1] in body]
    assert profile.valid_for_s == 300
