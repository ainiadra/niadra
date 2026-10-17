"""Contacts, their tokens and the budgets they spend (24.3.4): the durable contact log, a token verified
offline the way a gateway does, budgets that survive a restart of the state store and a queue that does not
confirm, funnel slices, and the admission of paid operations."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import fakeredis
import httpx
import pytest

from niadra.adapters.inbound.http import coordinate as coordinate_routes
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.memory.fakes import StaticConfig
from niadra.app.coordinate import CoordinateService
from niadra.config.coordination import (
    BudgetSlice,
    ContactBudget,
    ContactKeySettings,
    CoordinationSettings,
    Gateway,
    PaidBudget,
    PurposeRule,
)
from niadra.config.models import FeatureSettings
from niadra.contracts.common import ObjectRef
from niadra.contracts.coordinate import CheckResult
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.coordinate.destination import b64url, canonical_destination
from niadra.domain.coordinate.model import FailDirection
from niadra.domain.coordinate.token import PublicKey, TokenRefused, verify
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE
from tests.unit.coordinate.support import OTHER_PHONE, PHONE, SAME_PHONE, World, make_world

GATEWAY_KEY = bytes(range(32))
GATEWAY_REF = f"spaces/{SPACE_ID}/gateway/wa"
QUOTE = ObjectRef(type="quote", namespace="operator", id="q-77")


def _marketing(limit: int = 1, slices: list[BudgetSlice] | None = None) -> PurposeRule:
    budget = ContactBudget(limit=limit, per_hours=168, slices=slices or [])
    return PurposeRule(name="marketing", fail=FailDirection.CLOSED, token=True, budget=budget)


def _settings(marketing: PurposeRule | None = None, **fields: object) -> CoordinationSettings:
    rule = marketing or _marketing()
    purposes = [p if p.name != "marketing" else rule for p in CoordinationSettings().purposes]
    gateway = Gateway(gateway_id="wa_gateway", secret_ref=GATEWAY_REF, channels=frozenset({"whatsapp"}))
    return CoordinationSettings.model_validate({"purposes": purposes, "gateways": [gateway], **fields})


async def _world(settings: CoordinationSettings | None = None) -> World:
    world = await make_world(settings or _settings())
    world.cell.secrets.values[GATEWAY_REF] = b64url(GATEWAY_KEY)
    return world


async def _marketing_check(world: World, agent: str = "agent:sales", **fields: object) -> CheckResult:
    body = {"subject": PHONE.model_dump(), "purpose": "marketing", "intent": "promo", "channel": "whatsapp"}
    return await world.check(agent=agent, **{**body, **fields})


# The contact token.


async def test_two_agents_and_a_budget_of_one_give_exactly_one_token_the_gateway_verifies_offline() -> None:
    world = await _world()
    first = await _marketing_check(world, agent="agent:sales")
    second = await _marketing_check(world, agent="agent:retention")
    assert (first.decision, second.decision, second.reasons) == ("allow", "deny", ["budget_exhausted"])
    assert second.contact_token is None
    token = first.contact_token
    assert token is not None
    destination = canonical_destination(SAME_PHONE.type.value, SAME_PHONE.value)
    jti = await _send(world, token, destination, set())
    assert jti == str(first.decision_id)
    with pytest.raises(TokenRefused) as replayed:
        await _send(world, token, destination, {jti})
    assert replayed.value.code == "replayed"
    with pytest.raises(TokenRefused) as elsewhere:
        await _send(world, token, "phone:+5511912345678", set())
    assert elsewhere.value.code == "wrong_recipient"


async def _send(world: World, token: str, destination: str, seen: set[str]) -> str:
    """The example gateway: the space's published keys, its own key, its clock and the `jti` it saw."""
    keys = await world.service.contact_keys(SPACE_ID)
    claims = verify(
        token,
        keys=[PublicKey(k.kid, k.x, str(k.space), k.status, k.not_after) for k in keys.keys],
        gateway_id="wa_gateway",
        space=str(SPACE_ID),
        gateway_key=GATEWAY_KEY,
        destination=destination,
        channel="whatsapp",
        now=int(world.cell.clock.now().timestamp()),
        seen=seen,
    )
    return claims.jti


async def test_a_purpose_without_a_token_or_a_channel_without_a_gateway_gets_none() -> None:
    world = await _world()
    service = await world.check(subject=PHONE.model_dump(), purpose="service", channel="whatsapp")
    email = await _marketing_check(world, channel="email")
    assert (service.contact_token, email.decision, email.contact_token) == (None, "allow", None)
    with pytest.raises(InvalidInput):
        await _marketing_check(world, gateway_id="sms_gateway")


async def test_the_public_keys_rotate_with_a_retiring_key_and_a_space_without_coordination_has_none() -> None:
    world = await _world()
    before = (await _marketing_check(world)).contact_token
    [active] = (await world.service.contact_keys(SPACE_ID)).keys
    assert (active.status, active.space) == ("active", SPACE_ID)
    until = world.cell.clock.now() + timedelta(minutes=10)
    rotated = _settings(contact_keys=ContactKeySettings(generation=2, previous_until=until))
    config = world.cell.config.space(SPACE_ID)
    world.cell.config.put(config.model_copy(update={"coordination": rotated}))
    new, old = (await world.service.contact_keys(SPACE_ID)).keys
    assert (new.status, old.status, old.kid, old.not_after) == ("active", "retiring", active.kid, until)
    assert new.kid != active.kid
    after = (
        await _marketing_check(world, agent="agent:other", subject=OTHER_PHONE.model_dump())
    ).contact_token
    assert before is not None
    assert after is not None
    destination = canonical_destination(PHONE.type.value, PHONE.value)
    await _send(world, before, destination, set())  # the retiring key still verifies what it signed
    await _send(world, after, canonical_destination(OTHER_PHONE.type.value, OTHER_PHONE.value), set())
    world.advance(minutes=11)
    assert [k.kid for k in (await world.service.contact_keys(SPACE_ID)).keys] == [new.kid]
    off = config.model_copy(update={"features": FeatureSettings(enabled=frozenset())})
    world.cell.config.put(off)
    with pytest.raises(NotFound):
        await world.service.contact_keys(SPACE_ID)


@dataclass
class _Container:
    config: StaticConfig
    coordinate: CoordinateService


async def test_the_keys_are_public_per_space_and_cached_by_the_gateway() -> None:
    world = await _world()
    app = create_app("niadra-test", _Container(world.cell.config, world.service), (coordinate_routes.router,))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as client:
        found = await client.get("/.well-known/niadra-contact-keys.json", params={"space": str(SPACE_ID)})
        unknown = await client.get("/.well-known/niadra-contact-keys.json", params={"space": str(uuid4())})
        unnamed = await client.get("/.well-known/niadra-contact-keys.json")
    assert found.status_code == 200
    assert found.headers["cache-control"] == "public, max-age=300"
    [key] = found.json()["keys"]
    assert (key["kty"], key["crv"], key["space"], key["status"]) == (
        "OKP",
        "Ed25519",
        str(SPACE_ID),
        "active",
    )
    assert (unknown.status_code, unnamed.status_code) == (404, 422)


# The contact log.


async def test_the_contact_log_keeps_the_reservation_and_counts_each_use_of_a_token() -> None:
    world = await _world()
    allowed = await _marketing_check(world)
    detail = {"purpose": "marketing", "channel": "whatsapp", "gateway_id": "wa_gateway"}
    jti = str(allowed.decision_id)
    for key in ("gw-1", "gw-2"):
        await world.declare(
            key=key, kind="contact.made", subject=PHONE.model_dump(), detail={**detail, "jti": jti}
        )
    await world.drain()
    contacts = world.cell.uow.store.module("coordinate")["contacts"]
    [contact] = contacts.values()
    assert (str(contact.contact_id), contact.state, len(contact.use_ids)) == (jti, "made", 2)
    assert contact.reserved_at_ms is not None
    assert contact.gateway == "wa_gateway"


async def test_a_contact_that_went_out_without_a_decision_still_counts_in_its_budget() -> None:
    world = await _world()
    detail = {"purpose": "marketing", "channel": "whatsapp", "unchecked": True}
    await world.declare(key="late", kind="contact.made", subject=PHONE.model_dump(), detail=detail)
    refused = await _marketing_check(world)
    assert (refused.decision, refused.reasons) == ("deny", ["budget_exhausted"])
    await world.drain()
    [contact] = world.cell.uow.store.module("coordinate")["contacts"].values()
    assert (contact.state, contact.unchecked, contact.decision_id) == ("made", True, None)


# Budgets that survive.


async def test_a_budget_spent_before_the_state_store_restarted_is_still_spent_after() -> None:
    world = await _world()
    assert (await _marketing_check(world)).decision == "allow"
    await world.drain()
    # Before the loops process reloads it, the subject is loaded the first time a check touches it, once the
    # sixty seconds a closed purpose waits for are over.
    await _flush(world)
    waiting = await _marketing_check(world, agent="agent:retention")
    assert (waiting.decision, waiting.reasons) == ("defer", ["rebuilding"])
    world.advance(seconds=61)
    touched = await _marketing_check(world, agent="agent:retention")
    assert (touched.decision, touched.reasons) == ("deny", ["budget_exhausted"])
    await _restart(world)
    after = await _marketing_check(world, agent="agent:retention")
    assert (after.decision, after.reasons) == ("deny", ["budget_exhausted"])


async def test_a_queue_that_does_not_confirm_defers_a_closed_purpose_and_gives_the_unit_back() -> None:
    world = await _world()
    world.log.down = True
    deferred = await _marketing_check(world)
    assert (deferred.decision, deferred.reasons, deferred.contact_token) == ("defer", ["unavailable"], None)
    # A purpose that fails open and spends nothing goes on without the record.
    service = await world.check(subject=PHONE.model_dump(), purpose="service", channel="whatsapp")
    assert service.decision == "allow"
    world.log.down = False
    assert (await _marketing_check(world)).decision == "allow"


async def test_a_funnel_slice_caps_its_stage_within_the_purpose_budget() -> None:
    slices = [BudgetSlice(intent="proposal", limit=1)]
    world = await _world(_settings(_marketing(limit=2, slices=slices)))
    first = await _marketing_check(world, intent="proposal")
    again = await _marketing_check(world, intent="proposal", agent="agent:retention")
    other = await _marketing_check(world, intent="reminder", agent="agent:retention")
    assert (first.decision, again.decision, again.reasons, other.decision) == (
        "allow",
        "deny",
        ["budget_exhausted"],
        "allow",
    )


# Paid admission.


def _paid(**fields: object) -> CoordinationSettings:
    budget = PaidBudget.model_validate({"operation": "price_lookup", "units": 10, "per_hours": 24, **fields})
    return _settings(paid_budgets=[budget])


async def test_an_unknown_cost_reserves_the_highest_measured_and_a_failed_call_still_counts() -> None:
    world = await _world(_paid(max_units_per_call=8))
    first = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE)
    assert (first.admitted, first.units) == (True, 8)  # nothing measured yet: the ceiling
    await world.service.settle(SPACE_ID, first, outcome="done", units=3)
    second = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE)
    assert (second.units, second.remaining) == (3, 4)
    await world.service.settle(SPACE_ID, second, outcome="failed", units=1)
    third = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=5)
    assert (third.admitted, third.reason, third.retry_at) == (
        False,
        "budget_exhausted",
        world.cell.clock.now() + timedelta(hours=24),
    )
    skipped = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=4)
    await world.service.settle(SPACE_ID, skipped, outcome="skipped")
    assert (await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=4)).admitted
    ceiling = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=9)
    assert (ceiling.admitted, ceiling.reason) == (False, "over_call_ceiling")


async def test_paid_admission_fails_closed_and_admits_whatever_has_no_budget() -> None:
    world = await _world(_paid())
    free = await world.service.reserve(SPACE_ID, "catalog_search", units=50)
    assert (free.admitted, free.reservation_id) == (True, None)
    world.log.down = True
    down = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=1)
    assert (down.admitted, down.reason) == (False, "unavailable")
    world.log.down = False
    assert (await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=10)).admitted
    with pytest.raises(InvalidInput):
        await world.service.reserve(SPACE_ID, "price_lookup", units=1)
    off = await make_world(_paid(), Feature.TURNS)
    assert (await off.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=10**6)).admitted


async def test_paid_units_reserved_before_the_state_store_restarted_still_count_after() -> None:
    world = await _world(_paid())
    held = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=6)
    await world.service.settle(SPACE_ID, held, outcome="done", units=7)
    await world.drain()
    await _restart(world)
    refused = await world.service.reserve(SPACE_ID, "price_lookup", object=QUOTE, units=4)
    assert (refused.admitted, refused.reason, refused.remaining) == (False, "budget_exhausted", 3)
    other_source = await world.check(source=VOICE_SOURCE, subject=PHONE.model_dump(), purpose="service")
    assert other_source.decision == "allow"


async def _restart(world: World) -> None:
    """The state store comes back empty, and the loops process reloads it from the durable record."""
    await _flush(world)
    assert await world.service.watch_state_store()


async def _flush(world: World) -> None:
    await fakeredis.FakeAsyncRedis(server=world.server).flushall()
