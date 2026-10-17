"""The coordination service against the state store's scripts on fakeredis, an in-memory queue and the
in-memory durable record (24.3.4): effects exactly once with `unknown_outcome`, claims where the most
restrictive wins, the suppression list, the channel, the declared default when a dependency is down (L23),
the reload after the store came back empty, and the effects turn records report."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import fakeredis
import pytest

from niadra.config.coordination import (
    ChannelRule,
    CoordinationSettings,
    QuietHours,
)
from niadra.contracts.common import ObjectRef
from niadra.contracts.coordinate import (
    ClaimRequest,
    EffectReserve,
    EffectSettle,
)
from niadra.contracts.events import BatchRequest
from niadra.contracts.turns import TurnAgent, TurnEffect, TurnRecord
from niadra.domain.common.errors import (
    Conflict,
    EffectInFlight,
    InvalidHandle,
    InvalidInput,
    LeaseHeld,
    UnsupportedHandleType,
)
from niadra.domain.coordinate.destination import canonical_destination, suppression_key, unb64url
from niadra.domain.coordinate.model import ClaimKind
from niadra.domain.vocabulary import Feature
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, principal, sample_config
from tests.unit.coordinate.support import (
    FAREWELL,
    OTHER_PHONE,
    PHONE,
    SAME_PHONE,
    World,
    make_world,
)
from tests.unit.ingest.fakes import build, message


@pytest.fixture
async def world() -> World:
    return await make_world()


# Effects exactly once.


async def test_a_farewell_is_sent_once_and_the_second_attempt_hears_it_is_done(world: World) -> None:
    first = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    assert (first.decision, first.effect) == ("allow", first.effect)
    assert first.effect is not None
    assert (first.effect.state, first.effect.attempt) == ("none", 1)
    concurrent = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL, agent="agent:other")
    assert (concurrent.decision, concurrent.reasons) == ("deny", ["effect_in_flight"])
    await world.declare(kind="effect", detail={"effect_key": FAREWELL, "state": "done", "attempt": 1})
    again = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    assert again.decision == "deny"
    assert again.reasons == ["effect_done"]
    assert again.effect is not None
    assert (again.effect.state, again.effect.attempt) == ("done", 1)
    await world.drain()
    async with world.cell.uow(SPACE_ID) as uow:
        stored = await uow.coordinate.effect(_hash(await world.service._effect_hash(SPACE_ID, FAREWELL)))
    assert stored is not None
    assert (stored.state.value, stored.attempt) == ("done", 1)


async def test_a_purpose_the_coordination_document_does_not_declare_is_checked_as_service(
    world: World,
) -> None:
    # Zero touch: an agent that names its own purpose (`legal`) is not refused; the contact is
    # checked as serving the customer, which fails open and needs no token.
    service = await world.check(subject=PHONE.model_dump(), purpose="service")
    named = await world.check(subject=PHONE.model_dump(), purpose="legal")
    assert (named.decision, named.reasons) == (service.decision, service.reasons) == ("allow", [])
    assert named.contact_token is None


async def test_an_unknown_suppression_reason_is_refused_naming_the_ones_declared(world: World) -> None:
    # The integrator's two names were refused with no hint of the accepted ones: the message says them.
    with pytest.raises(InvalidInput) as refused:
        await world.declare(
            kind="suppression.added",
            subject=PHONE.model_dump(),
            detail={"purpose": "marketing", "reason": "client_request"},
        )
    assert str(refused.value) == (
        "unknown suppression reason client_request: the coordination document declares "
        "opt_out, in_service, awaiting_third_party, grace_period"
    )


async def test_an_ambiguous_effect_is_never_sent_again_until_it_is_settled_as_failed(world: World) -> None:
    reserved = await world.check(effect_key=FAREWELL)
    assert reserved.effect is not None
    assert reserved.effect.state == "none"
    await world.declare(
        kind="effect", detail={"effect_key": FAREWELL, "state": "unknown_outcome", "attempt": 1}
    )
    for _ in range(3):
        again = await world.check(effect_key=FAREWELL)
        assert (again.decision, again.reasons) == ("deny", ["effect_unknown_outcome"])
    # A lease that lapses without a settlement is ambiguous too, and the durable record hears of it.
    other = "notice:case-9"
    await world.check(effect_key=other)
    world.advance(seconds=61)
    lapsed = await world.check(effect_key=other)
    assert lapsed.reasons == ["effect_unknown_outcome"]
    assert [r.snapshot.state.value for r in world.log.records if hasattr(r, "snapshot")][
        -1
    ] == "unknown_outcome"
    # Only a settlement to `failed` by the caller of that attempt opens the next one.
    effect_id = await world.service._effect_hash(SPACE_ID, FAREWELL)
    settled = await world.service.settle_effect(
        principal(), effect_id, EffectSettle(state="failed", attempt=1), "settle-1"
    )
    assert settled.state == "failed"
    retry = await world.check(effect_key=FAREWELL)
    assert retry.effect is not None
    assert (retry.decision, retry.effect.state, retry.effect.attempt) == ("allow", "none", 2)


async def test_the_reservation_route_answers_done_and_in_flight_and_repeats_its_first_answer(
    world: World,
) -> None:
    caller = principal()
    body = EffectReserve(effect_key="filing:notice-3", kind="filing")
    first = await world.service.reserve_effect(caller, body, "k1")
    assert (first.state, first.attempt) == ("reserved", 1)
    assert await world.service.reserve_effect(caller, body, "k1") == first
    with pytest.raises(EffectInFlight):
        await world.service.reserve_effect(caller, body, "k2")
    await world.service.settle_effect(caller, first.effect_id, EffectSettle(state="done", attempt=1), "s1")
    done = await world.service.reserve_effect(caller, body, "k3")
    assert (done.state, done.attempt) == ("done", 1)
    with pytest.raises(Conflict):
        await world.service.settle_effect(
            caller, first.effect_id, EffectSettle(state="failed", attempt=1), "s2"
        )


# Ownership by declaration.


async def test_the_most_restrictive_of_three_sources_with_different_validities_wins(world: World) -> None:
    async def claim(source: UUID, level: str, holder: str, lease_s: int) -> Any:
        request = ClaimRequest(
            subject=PHONE, kind=ClaimKind.OWNER, holder=holder, level=level, lease_s=lease_s
        )
        return await world.service.claim(principal(source), request, f"{holder}-{level}")

    sales = await claim(WHATSAPP_SOURCE, "agent_active", "agent:sales", 4 * 3600)
    await claim(VOICE_SOURCE, "soft_pause", "system:middleware", 2 * 3600)
    await claim(sample_config().sources[2].source_id, "human_active", "human:attendant-7", 1800)
    with pytest.raises(LeaseHeld, match="human:attendant-7"):
        await claim(WHATSAPP_SOURCE, "soft_pause", "agent:retention", 3600)

    async def owner_after(minutes: int) -> tuple[str, str] | None:
        world.cell.clock.at = datetime(2026, 9, 29, 15, 0, tzinfo=UTC) + timedelta(minutes=minutes)
        result = await world.check(subject=PHONE.model_dump(), agent="agent:closing", intent="followup")
        assert result.decision == ("defer" if result.owner else "allow")
        return (result.owner.level, result.owner.holder) if result.owner else None

    assert await owner_after(10) == ("human_active", "human:attendant-7")
    assert await owner_after(31) == ("soft_pause", "system:middleware")
    assert await owner_after(121) == ("agent_active", "agent:sales")
    assert await owner_after(241) is None
    world.cell.clock.at = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    released = await world.service.release(principal(), sales.claim_id, sales.epoch, "r1")
    assert released.epoch > sales.epoch
    with pytest.raises(Conflict):
        await world.service.release(principal(), sales.claim_id, sales.epoch, "r2")


async def test_a_customer_message_is_handed_to_the_holder_and_never_denied(world: World) -> None:
    await world.service.claim(
        principal(),
        ClaimRequest(
            subject=PHONE, kind=ClaimKind.CASE, holder="human:attendant-7", level="human_active", lease_s=900
        ),
        "case-1",
    )
    await world.declare(
        kind="suppression.added",
        subject=PHONE.model_dump(),
        detail={"purpose": "service", "reason": "opt_out"},
    )
    inbound = await world.check(subject=PHONE.model_dump(), direction="inbound", channel="whatsapp")
    assert (inbound.decision, inbound.holder, inbound.reasons) == (
        "handoff_to",
        "human:attendant-7",
        ["owner_active"],
    )
    outbound = await world.check(subject=PHONE.model_dump())
    assert (outbound.decision, outbound.reasons) == ("deny", ["suppressed"])


async def test_a_claim_on_an_object_defers_another_agents_act_on_it(world: World) -> None:
    hearing = ObjectRef(type="hearing", namespace="court", id="123")
    await world.service.claim(
        principal(),
        ClaimRequest(object=hearing, kind=ClaimKind.OWNER, holder="agent:summaries", lease_s=1500),
        "c1",
    )
    other = await world.check(object=hearing.model_dump(), agent="agent:other", intent="summary")
    assert (other.decision, other.reasons) == ("defer", ["owner_active"])
    own = await world.check(object=hearing.model_dump(), agent="agent:summaries", intent="summary")
    assert own.decision == "allow"


# Budgets, channel, suppressions.


async def test_a_budget_of_two_allows_exactly_two_of_a_hundred_concurrent_checks(world: World) -> None:
    results = await asyncio.gather(
        *(world.check(subject=PHONE.model_dump(), purpose="marketing", intent="promo") for _ in range(100))
    )
    allowed = [r for r in results if r.decision == "allow"]
    assert len(allowed) == 2
    denied = {
        (r.decision, tuple(r.reasons), r.contact_budget["marketing"].remaining)
        for r in results
        if r not in allowed
    }
    assert denied == {("deny", ("budget_exhausted",), 0)}
    # The same person through another handle of the same number shares the budget.
    through_whatsapp = await world.check(subject=SAME_PHONE.model_dump(), purpose="marketing", intent="promo")
    assert through_whatsapp.decision == "deny"


async def test_the_service_window_runs_from_the_customers_last_message_in_the_hot_layer() -> None:
    quiet = ChannelRule(
        name="whatsapp",
        free_form_hours=24,
        template_outside_window=True,
        quiet_hours=QuietHours(start="21:00", end="08:00"),
    )
    world = await make_world(CoordinationSettings(channels=[quiet]))
    cold = await world.check(subject=PHONE.model_dump(), channel="whatsapp")
    assert cold.decision == "allow"
    assert "template_required" in cold.reasons
    assert cold.channel is not None
    assert (cold.channel.template_required, cold.channel.free_form_until) == (True, None)
    # The customer wrote two hours ago, and the agent answered since: no check saw either of them.
    ingest, _, _ = build(world.cell)
    wrote = world.cell.clock.now() - timedelta(hours=2)
    answered = wrote + timedelta(minutes=5)
    batch = [
        message("m1", occurred_at=wrote.isoformat(), handles=[PHONE.model_dump()]),
        message("m2", occurred_at=answered.isoformat(), speaker={"role": "ai_agent"}),
    ]
    await ingest.ingest_batch(principal(), BatchRequest.model_validate({"items": batch}))
    warm = await world.check(subject=PHONE.model_dump(), channel="whatsapp")
    assert warm.channel is not None
    assert warm.channel.free_form_until == wrote + timedelta(hours=24)
    assert warm.reasons == []
    # The same number reached through its WhatsApp id is the same customer.
    same = await world.check(subject=SAME_PHONE.model_dump(), channel="whatsapp")
    assert same.channel is not None
    assert same.channel.free_form_until == wrote + timedelta(hours=24)
    world.cell.hot.fail = True  # the hot layer does not answer: the template is required again
    blind = await world.check(subject=PHONE.model_dump(), channel="whatsapp")
    assert (blind.decision, blind.reasons) == ("allow", ["template_required"])
    world.cell.hot.fail = False
    world.cell.clock.at = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)  # 22:00 in Sao Paulo
    night = await world.check(subject=PHONE.model_dump(), channel="whatsapp")
    assert (night.decision, night.reasons) == ("defer", ["quiet_hours"])
    assert night.channel is not None
    assert night.channel.quiet_until == datetime(2026, 9, 30, 11, 0, tzinfo=UTC)


async def test_a_suppression_is_listed_keyed_per_reader_and_only_its_source_lifts_it(world: World) -> None:
    await world.declare(
        kind="suppression.added",
        subject=PHONE.model_dump(),
        detail={"purpose": "marketing", "reason": "opt_out"},
    )
    await world.declare(
        kind="suppression.added",
        subject=OTHER_PHONE.model_dump(),
        detail={"purpose": "collection", "channel": "email", "reason": "grace_period"},
    )
    denied = await world.check(subject=SAME_PHONE.model_dump(), purpose="marketing", intent="promo")
    assert (denied.decision, denied.reasons, denied.suppressions) == ("deny", ["suppressed"], ["marketing"])
    assert (await world.check(subject=PHONE.model_dump(), purpose="transactional")).decision == "allow"
    await world.drain()
    reader = principal(VOICE_SOURCE)
    salt = await world.service.salt(reader)
    page = await world.service.suppressions(reader, None, 200)
    assert [(i.purpose, i.channel, i.removed) for i in page.items] == [
        ("marketing", None, False),
        ("collection", "email", False),
    ]
    raw_salt = unb64url(salt.salt)
    assert page.items[0].key == suppression_key(raw_salt, canonical_destination("phone_e164", PHONE.value))
    assert page.items[1].until == world.cell.clock.now() + timedelta(days=7)
    other_reader = await world.service.suppressions(principal(), None, 200)
    assert other_reader.items[0].key != page.items[0].key
    lifted_by_other = await world.declare(
        source=VOICE_SOURCE,
        kind="suppression.lifted",
        subject=PHONE.model_dump(),
        detail={"purpose": "marketing"},
    )
    assert not lifted_by_other.accepted
    lifted = await world.declare(
        kind="suppression.lifted", subject=PHONE.model_dump(), detail={"purpose": "marketing"}
    )
    assert lifted.accepted
    assert (
        await world.check(subject=PHONE.model_dump(), purpose="marketing", intent="promo")
    ).decision == "allow"
    await world.drain()
    changes = await world.service.suppressions(reader, page.next_cursor, 200)
    assert [(i.id, i.removed) for i in changes.items] == [(page.items[0].id, True)]
    assert (await world.service.suppressions(reader, changes.next_cursor, 200)).items == []
    with pytest.raises(UnsupportedHandleType):
        await world.declare(
            kind="suppression.added",
            subject={"type": "system_id", "value": "C-1", "scope": "crm"},
            detail={"purpose": "marketing", "reason": "opt_out"},
        )
    with pytest.raises(InvalidHandle):
        await world.declare(
            kind="suppression.added",
            subject={"type": "email", "value": "ana@"},
            detail={"purpose": "marketing", "reason": "opt_out"},
        )


# When a dependency is down (L23).


async def test_with_the_state_store_down_each_purpose_answers_its_declared_default(world: World) -> None:
    world.server.connected = False
    marketing = await world.check(subject=PHONE.model_dump(), purpose="marketing", intent="promo")
    service = await world.check(subject=PHONE.model_dump(), purpose="service", intent="reply")
    farewell = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    inbound = await world.check(subject=PHONE.model_dump(), direction="inbound", effect_key=FAREWELL)
    assert (marketing.decision, marketing.reasons) == ("defer", ["unavailable"])
    assert (service.decision, service.reasons) == ("allow", ["unchecked"])
    assert (farewell.decision, farewell.reasons) == ("defer", ["unavailable"])
    assert (inbound.decision, inbound.reasons) == ("allow", ["unchecked"])


async def test_a_reservation_the_queue_does_not_confirm_is_undone(world: World) -> None:
    world.log.down = True
    farewell = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    assert (farewell.decision, farewell.reasons) == ("defer", ["unavailable"])
    world.log.down = False
    retry = await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    assert retry.effect is not None
    assert (retry.decision, retry.effect.state, retry.effect.attempt) == ("allow", "none", 1)


# The state store came back empty.


async def test_a_restarted_state_store_is_reloaded_and_stays_closed_for_sixty_seconds(world: World) -> None:
    await world.check(subject=PHONE.model_dump(), effect_key=FAREWELL)
    await world.declare(kind="effect", detail={"effect_key": FAREWELL, "state": "done", "attempt": 1})
    await world.declare(
        kind="suppression.added",
        subject=PHONE.model_dump(),
        detail={"purpose": "service", "reason": "in_service"},
    )
    await world.drain()
    await fakeredis.FakeAsyncRedis(server=world.server).flushall()

    closed = await world.check(subject=OTHER_PHONE.model_dump(), purpose="marketing", intent="promo")
    assert (closed.decision, closed.reasons) == ("defer", ["rebuilding"])
    done = await world.check(effect_key=FAREWELL)
    assert (done.decision, done.reasons) == ("deny", ["effect_done"])
    unknown = await world.check(effect_key="farewell:conv-2")
    assert (unknown.decision, unknown.reasons) == ("defer", ["rebuilding"])
    suppressed = await world.check(subject=SAME_PHONE.model_dump(), purpose="service", intent="reply")
    assert (suppressed.decision, suppressed.reasons) == ("deny", ["suppressed"])

    world.advance(seconds=60)
    assert (
        await world.check(subject=OTHER_PHONE.model_dump(), purpose="marketing", intent="p")
    ).decision == "allow"
    assert (await world.check(effect_key="farewell:conv-2")).decision == "allow"
    assert await world.service.watch_state_store()
    assert not await world.service.watch_state_store()
    store = fakeredis.FakeAsyncRedis(server=world.server)
    assert await store.get("coord:marker") == b"whole"


# What turn records report.


def _turn(turn_id: str, *effects: tuple[str, str]) -> AppliedTurn:
    record = TurnRecord(
        turn_id=turn_id,
        conversation_id="conv-1",
        agent=TurnAgent(name="closing"),
        started_at=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
        content_mode="hash_only",
        effects=[TurnEffect(key=key, state=state) for key, state in effects],  # type: ignore[arg-type]
    )
    return AppliedTurn(WHATSAPP_SOURCE, datetime(2026, 9, 29, 15, 0, 1, tzinfo=UTC), record)


async def test_an_effect_a_turn_reports_is_never_sent_again_and_a_group_applied_twice_writes_the_same(
    world: World,
) -> None:
    await world.check(effect_key="farewell:conv-3")
    turns = [
        _turn("t1", (FAREWELL, "done")),
        _turn("t2", ("farewell:conv-3", "unknown_outcome"), ("x", "reserved")),
    ]
    for _ in range(2):
        async with world.cell.uow(SPACE_ID) as uow:
            await world.service.consume(uow, SPACE_ID, turns)
    assert (await world.check(effect_key=FAREWELL)).reasons == ["effect_done"]
    assert (await world.check(effect_key="farewell:conv-3")).reasons == ["effect_unknown_outcome"]
    async with world.cell.uow(SPACE_ID) as uow:
        writes = uow.coordinate.writes
        assert len(writes) == 2
        assert writes[0].effects == writes[1].effects
        assert len(writes[0].effects) == 2


async def test_a_space_without_coordination_takes_nothing_from_the_turns() -> None:
    world = await make_world(None, Feature.TURNS)
    async with world.cell.uow(SPACE_ID) as uow:
        await world.service.consume(uow, SPACE_ID, [_turn("t1", (FAREWELL, "done"))])
        assert uow.coordinate.writes == []


def _hash(effect_id: str) -> bytes:
    return unb64url(effect_id)
