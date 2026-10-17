"""Reasons to fetch again with a budget, the `now` view and the verdict for a claim, on the in-memory cell
(24.3.5, 24.3.13): a reason is evaluated at the read with no statement, only what the budget admits becomes a
request, the worker leases it and a fresh value settles it; the view shows what changed since the subject saw
it, in JSON and as lines for the turn block."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.refresh import InMemoryRefreshWants
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.memory.typed import InMemoryTypedRepository
from niadra.app.observations import ObservationsConsumer
from niadra.app.refresh import RefreshService
from niadra.app.serve import BlockGate
from niadra.app.serve_reads import AboutLink, Target
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.state import StateService
from niadra.config.models import CategoryRule, FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest, ObjectRead, StateReadRequest, StateVerifyRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.policy import PolicyContext, Reader
from niadra.domain.records import BusinessObject, ObjectKey, ProfileRecord
from niadra.domain.state.observed import Report, merge
from niadra.domain.vocabulary import Audience, Feature, Scope, SubjectKind, Verification
from niadra.ports.admission import PaidReservation
from niadra.ports.state import SeenInterest
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World, config_with

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
REF = "item_variant:loja:v-1"
ITEM: dict[str, Any] = {
    "type": "item_variant",
    "ownership": "shared",
    "mirror_of": {"system": "shop"},
    "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
    "fields": {
        "price_sale": {
            "type": "money",
            "freshness": {"class": "price", "max_age": "60s", "claim_max_age": "15s"},
            "claim": {"allowed": True, "class": "money"},
            "track_changes": True,
        },
        "available": {"type": "bool", "claim": {"allowed": True, "class": "quantity"}},
    },
    "sources": {"platform": {"kind": "push", "precedence": 1}},
    "purposes": {"claim": {"prohibitions": ["affirm_price"]}},
    "refetch": {
        "reasons": [
            {
                "name": "never_observed",
                "when": "observer(available) == none",
                "budget": {"units": 1, "per": "item/1h"},
            },
            {"name": "claim_pending", "when": "purpose == 'claim'", "budget": {"units": 1, "per": "turn"}},
            {"name": "floor_expired", "when": "age(price_sale) > 90s", "priority": "low"},
            {"name": "no_news", "when": "age(price_sale) > 1h"},
        ],
        "never": ["no_news"],
        "budget": {"unit": "platform_call"},
    },
}
ORDER: dict[str, Any] = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "shop"},
    "fields": {"status": {"type": "enum"}},
    "sources": {"shop": {"kind": "push", "precedence": 1}},
    "time": {"delivered_at": {"origin": "source"}},
    "states": ["delivered", "kept"],
    "lifecycle": {
        "transitions": [{"from": "delivered", "to": "kept", "by": ["clock"]}],
        "outcome": {"provisional": ["delivered"], "final": ["kept"]},
    },
    "timers": {"exchange_window": {"due": "delivered_at + 30d", "on_fire": ["transition(delivered->kept)"]}},
}


class Budgets:
    """The coordination's paid budgets, as a double: admits `left` units, and records settlements."""

    def __init__(self, left: int = 10) -> None:
        self.left = left
        self.settled: list[tuple[str, str]] = []

    async def reserve(self, space_id: UUID, operation: str, **asked: Any) -> PaidReservation:
        units = asked.get("units") or 1
        if units > self.left:
            return PaidReservation(False, operation, units, reason="budget_exhausted", remaining=self.left)
        self.left -= units
        return PaidReservation(
            True, operation, units, remaining=self.left, reservation_id=uuid7(), target="t"
        )

    async def settle(self, space_id: UUID, reservation: PaidReservation, *, outcome: str, **_: Any) -> None:
        self.settled.append((reservation.operation, outcome))


class Interests:
    def __init__(self) -> None:
        self.seen: list[SeenInterest] = []

    async def interests(self, space_id: UUID, handle_ids: Any) -> list[SeenInterest]:
        return self.seen


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
def budgets() -> Budgets:
    return Budgets()


@pytest.fixture
def wants() -> InMemoryRefreshWants:
    return InMemoryRefreshWants()


@pytest.fixture
def refresh(world: World, wants: InMemoryRefreshWants, budgets: Budgets) -> RefreshService:
    service = RefreshService(world.uow, wants, world.cell.clock, world.cell.config)
    service.admission = budgets
    return service


@pytest.fixture
def shared(world: World, refresh: RefreshService) -> SharedObjectsService:
    observations = ObservationsConsumer(world.uow, world.cell.keys, world.cell.config)
    return SharedObjectsService(
        world.uow,
        InMemorySharedObjects(),
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        observations,
        world.cell.objects,
        refresh=refresh,
    )


@pytest.fixture
def state(world: World, shared: SharedObjectsService, refresh: RefreshService) -> StateService:
    service = StateService(
        world.uow,  # type: ignore[arg-type]
        world.cell.cache,
        world.cell.keys,
        world.cell.clock,
        world.cell.config,
        shared=shared,
        refresh=refresh,
    )
    service.interests = Interests()
    return service


def caller(world: World, *scopes: Scope) -> Any:
    return principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID), frozenset(scopes) or None)


def item(version: int, fields: dict[str, Any], ago: float = 0) -> dict[str, Any]:
    return {
        "ref": {"type": "item_variant", "namespace": "loja", "id": "v-1"},
        "version": version,
        "fields": fields,
        "provenance": {"source": "live", "source_observed_at": (NOW - timedelta(seconds=ago)).isoformat()},
    }


async def push(world: World, shared: SharedObjectsService, *items: dict[str, Any]) -> None:
    pusher = principal(BILLING_SOURCE, world.cell.config.space(SPACE_ID), frozenset({Scope.STATE_PUSH}))
    await shared.push(pusher, ObjectPushRequest.model_validate({"objects": list(items)}))


async def read(world: World, state: StateService, purpose: str = "display") -> ObjectRead:
    request = {"refs": [{"type": "item_variant", "namespace": "loja", "id": "v-1"}], "purpose": purpose}
    [found] = (await state.read(caller(world), StateReadRequest.model_validate(request))).response.objects
    return found


def repo(world: World) -> InMemoryTypedRepository:
    return InMemoryTypedRepository(world.cell.uow.store)


async def test_a_reason_the_budget_admits_becomes_one_request_the_worker_leases_and_a_fresh_value_settles(
    world: World,
    shared: SharedObjectsService,
    state: StateService,
    refresh: RefreshService,
    budgets: Budgets,
    wants: InMemoryRefreshWants,
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 199.9}))
    found = await read(world, state)
    assert found.refetch is not None
    assert (found.refetch.reason, found.refetch.priority, found.refetch.admitted) == (
        "never_observed",
        "normal",
        True,
    )
    again = await read(world, state)
    assert again.refetch is not None
    assert again.refetch.admitted  # the first answer stands for the period
    assert budgets.left == 9  # one reservation, not two
    assert await refresh.flush(SPACE_ID) == 1
    assert await refresh.flush(SPACE_ID) == 0
    page = await refresh.lease(caller(world, Scope.STATE_PUSH), 10)
    [request] = page.items
    assert (request.reason, request.ref.id, request.budget_units) == ("never_observed", "v-1", 1)
    assert (await refresh.lease(caller(world, Scope.STATE_PUSH), 10)).items == []  # leased for 60 s
    # The worker answers with a fresh value: the request leaves and its reservation is settled.
    await push(world, shared, item(2, {"available": True}))
    await shared.consolidate(SPACE_ID)
    assert repo(world).refreshes == {}
    assert budgets.settled == [("platform_call", "done")]


async def test_what_the_budget_denies_and_a_reason_that_never_spends_are_not_admitted(
    world: World, shared: SharedObjectsService, state: StateService, budgets: Budgets, refresh: RefreshService
) -> None:
    budgets.left = 0
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 199.9, "available": True}, ago=100))
    found = await read(world, state)
    assert found.refetch is not None
    assert (found.refetch.reason, found.refetch.admitted) == ("floor_expired", False)
    budgets.left = 10
    world.cell.clock.advance(hours=2)
    # An hour on, `no_news` holds too: its priority is higher, and it never authorizes spending.
    later = await read(world, state)
    assert later.refetch is not None
    assert (later.refetch.reason, later.refetch.admitted) == ("no_news", False)
    assert await refresh.flush(SPACE_ID) == 0


async def test_a_claim_read_admits_claim_pending_for_the_sdk_and_queues_nothing(
    world: World, shared: SharedObjectsService, state: StateService, refresh: RefreshService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 199.9, "available": True}))
    found = await read(world, state, "claim")
    assert found.refetch is not None
    assert (found.refetch.reason, found.refetch.admitted) == ("claim_pending", True)
    assert await refresh.flush(SPACE_ID) == 0


async def test_a_verdict_for_a_claim_compares_the_value_and_says_what_may_not_be_claimed(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 199.9, "available": True}, ago=30))
    checks = [
        {
            "ref": {"type": "item_variant", "namespace": "loja", "id": "v-1"},
            "field": "price_sale",
            "value": 199.9,
        },
        {
            "ref": {"type": "item_variant", "namespace": "loja", "id": "v-1"},
            "field": "available",
            "value": False,
        },
    ]
    answer = await state.verify(caller(world), StateVerifyRequest.model_validate({"checks": checks}))
    assert answer.verdicts is not None
    price, available = answer.verdicts.verdicts
    assert (price.matches, price.claim_safe, price.prohibitions) == (True, False, ["affirm_price"])
    assert (available.matches, available.claim_safe) == (False, True)


async def test_the_now_view_shows_what_changed_since_the_subject_saw_it(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    handle = uuid7()
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_ana", 1, 1, (handle,))
    order = BusinessObject(uuid7(), ObjectKey("order", "shop", "1001"), {}, NOW, BILLING_SOURCE, handle)
    fields, _ = merge(
        {},
        Report(
            "src:shop",
            {"state": "delivered", "delivered_at": (NOW - timedelta(days=2)).isoformat()},
            NOW,
            NOW,
            NOW,
        ),
    )
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.objects.add_object(replace(order, fields=fields))
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 140.0, "available": True}))
    state.interests.seen = [  # type: ignore[attr-defined]
        SeenInterest(REF, "presented", NOW - timedelta(hours=1), {"price_sale": 189.9})
    ]
    view, _ = await state.subject_view(caller(world), Target(profile))
    [changed] = view.changes_since_seen
    assert (changed.ref.id, changed.field, changed.seen, changed.now) == ("v-1", "price_sale", 189.9, 140.0)
    [interest] = view.interests
    assert interest.object is not None
    assert interest.object.fields["price_sale"].v == 140.0
    assert view.text is not None
    assert "price_sale 140.00 (era 189.90 quando visto)" in view.text
    assert "order shop:1001: situação delivered; desfecho delivered" not in view.text  # same word, said once
    assert (
        "exchange_window vence 30/09/2026" not in view.text
    )  # not armed: this order never went through a write
    assert view.text.startswith("<estado>")
    # Nothing to say: no text.
    state.interests.seen = []  # type: ignore[attr-defined]
    quiet, _ = await state.subject_view(caller(world), Target(replace(profile, handle_ids=())))
    assert quiet.text is None


async def test_a_timer_that_asks_for_a_field_again_admits_a_request(
    world: World, refresh: RefreshService, wants: InMemoryRefreshWants
) -> None:
    from niadra.app.refresh import Ask
    from niadra.app.typed_state import Wanted
    from niadra.domain.state.shared import SharedKey

    declared = world.cell.config.space(SPACE_ID).object_types.get("item_variant")
    assert declared is not None
    ask = Ask(
        uuid7(),
        SharedKey("item_variant", "loja", "v-2"),
        declared,
        Wanted("promo_end", "high", None, False, 3600, False),
    )
    [answer] = await refresh.want(SPACE_ID, [ask])
    assert answer.admitted
    assert await refresh.flush(SPACE_ID) == 1


async def ana_with_an_order_and_an_interest(
    world: World, shared: SharedObjectsService, state: StateService
) -> ProfileRecord:
    handle = uuid7()
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_ana", 1, 1, (handle,))
    order = BusinessObject(uuid7(), ObjectKey("order", "shop", "1001"), {}, NOW, BILLING_SOURCE, handle)
    fields, _ = merge({}, Report("src:shop", {"state": "delivered", "status": "on_time"}, NOW, NOW, NOW))
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.objects.add_object(replace(order, fields=fields))
    await shared.enter_working_set(SPACE_ID, [REF], "presented")
    await push(world, shared, item(1, {"price_sale": 140.0, "available": True}))
    state.interests.seen = [  # type: ignore[attr-defined]
        SeenInterest(REF, "presented", NOW - timedelta(hours=1), {"price_sale": 189.9})
    ]
    return profile


def gate(world: World, verification: Verification) -> BlockGate:
    policy = world.cell.config.space(SPACE_ID).policy
    return BlockGate(policy, Reader(Audience.CUSTOMER_AGENT), PolicyContext(verification))


async def test_beside_a_pack_the_state_block_passes_what_the_pack_passes(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    profile = await ana_with_an_order_and_an_interest(world, shared, state)
    open_, _ = await state.subject_view(caller(world), Target(profile), gate=gate(world, Verification.V1))
    assert [o.ref.id for o in open_.objects] == ["1001"]
    assert [c.field for c in open_.changes_since_seen] == ["price_sale"]
    # At V0 the pack shows nothing of the customer's: neither does the block.
    blind, _ = await state.subject_view(caller(world), Target(profile), gate=gate(world, Verification.V0))
    assert (blind.objects, blind.interests, blind.changes_since_seen, blind.text) == ([], [], [], None)


async def test_the_state_block_holds_back_only_the_items_the_policy_keeps(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    # The shared item's type is released to a purpose this agent lacks: its interest stays out at any
    # level, while the customer's order, ordinary, goes as the pack's line of it would.
    config = world.cell.config.space(SPACE_ID)
    kept = CategoryRule(name="item_variant", min_verification=Verification.V1, purposes=frozenset({"sales"}))
    policy = config.policy.model_copy(update={"categories": [*config.policy.categories, kept]})
    world.cell.config.put(config.model_copy(update={"policy": policy}))
    profile = await ana_with_an_order_and_an_interest(world, shared, state)
    view, _ = await state.subject_view(caller(world), Target(profile), gate=gate(world, Verification.V3))
    assert [o.ref.id for o in view.objects] == ["1001"]
    assert (view.interests, view.changes_since_seen) == ([], [])
    assert view.text is not None
    assert "item_variant" not in view.text


async def test_the_state_block_text_never_carries_a_private_field(
    world: World, shared: SharedObjectsService, state: StateService
) -> None:
    private = {
        **ITEM,
        "fields": {**ITEM["fields"], "price_sale": {**ITEM["fields"]["price_sale"], "pii": True}},
    }
    world.cell.config.put(
        world.cell.config.space(SPACE_ID).model_copy(
            update={"object_types": ObjectTypeRegistry.model_validate({"types": [private, ORDER]})}
        )
    )
    profile = await ana_with_an_order_and_an_interest(world, shared, state)
    view, _ = await state.subject_view(caller(world), Target(profile))
    assert view.text is not None
    assert "189" not in view.text
    assert "140" not in view.text


async def test_a_person_acting_for_a_company_reads_the_companys_objects_beside_their_own(
    world: World, state: StateService
) -> None:
    # `about` (21.4): the buyer's read carries the company's order, through the link, besides the buyer's own.
    company, buyer = uuid7(), uuid7()
    account = ProfileRecord(uuid7(), SubjectKind.ACCOUNT, "pse_acme", 1, 1, (company,))
    person = ProfileRecord(uuid7(), SubjectKind.PERSON, "pse_ana", 1, 1, (buyer,))
    fields, _ = merge({}, Report("src:shop", {"state": "placed"}, NOW, NOW, NOW))
    async with world.cell.uow(SPACE_ID) as uow:
        for owner, external_id in ((company, "2001"), (buyer, "2002")):
            order = BusinessObject(
                uuid7(), ObjectKey("order", "shop", external_id), {}, NOW, BILLING_SOURCE, owner
            )
            await uow.objects.add_object(replace(order, fields=fields))
    alone, _ = await state.subject_view(caller(world), Target(person))
    assert [o.ref.id for o in alone.objects] == ["2002"]
    about = AboutLink(company, account, "buyer", False)
    view, _ = await state.subject_view(caller(world), Target(person, about=about))
    assert sorted(o.ref.id for o in view.objects) == ["2001", "2002"]
