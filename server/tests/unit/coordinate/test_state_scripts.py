"""The coordination scripts of the state store (24.3.4), the specification the check is built on: budgets
reserved atomically, ownership where the most restrictive claim wins, and effects exactly once with
`unknown_outcome`. On fakeredis with Lua, and again on a real Redis-compatible store when NIADRA_TEST_REDIS
names one."""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import fakeredis
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from redis.asyncio import Redis

from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.coordinate import (
    RedisCoordination,
    claims_key,
    effect_key,
    suppressions_key,
)
from niadra.domain.coordinate.model import (
    Admission,
    ClaimKind,
    ClaimResult,
    ClaimVia,
    Decision,
    EffectState,
    Settler,
    StoreLife,
)
from niadra.ports.coordination import BudgetSpend, CommitmentEntry

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
T0 = 1_790_000_000_000
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
WEEK = 7 * 24 * HOUR
LEASE = MINUTE
# The ownership order a space declares, most restrictive first (24.3.4).
RANK = {"closed": 0, "human_active": 1, "transfer_pending": 2, "soft_pause": 3, "agent_active": 4}


@pytest.fixture(params=["fakeredis", "redis"])
async def store(request: pytest.FixtureRequest, space: UUID) -> AsyncIterator[Redis]:
    if request.param == "redis" and not REDIS:
        pytest.skip("needs NIADRA_TEST_REDIS")
    client: Redis = fakeredis.FakeAsyncRedis() if request.param == "fakeredis" else connect(REDIS)
    yield client
    keys = [key async for key in client.scan_iter(match=f"co:{space}:*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest.fixture
def space() -> UUID:
    return uuid4()


@pytest.fixture
def coordination(store: Redis) -> RedisCoordination:
    return RedisCoordination(store)


async def admit(
    coordination: RedisCoordination,
    tag: tuple[UUID, str],
    reservation: str,
    *,
    now: int = T0,
    limit: int | None = 2,
    agent: str = "agent:sales",
    intent: str = "promo",
    channel: str = "whatsapp",
    quiet_until: int | None = None,
    slice_limit: int | None = None,
    spacing: int = 0,
) -> Admission:
    seen = await coordination.check_subject(
        *tag,
        reservation,
        direction="outbound",
        purpose="marketing",
        channel=channel,
        agent=agent,
        intent=intent,
        units=1,
        budget="c:marketing",
        limit=limit,
        window_ms=WEEK,
        quiet_until_ms=quiet_until,
        slice_budget=f"c:marketing:{intent}" if slice_limit is not None else None,
        slice_limit=slice_limit,
        spacing_ms=spacing,
        now_ms=now,
    )
    return seen.admission


async def claim(
    coordination: RedisCoordination,
    key: str,
    slot: str,
    level: str,
    holder: str,
    *,
    via: ClaimVia,
    until: int,
    now: int,
    intents: tuple[str, ...] = (),
    since: int | None = None,
    kind: ClaimKind = ClaimKind.OWNER,
) -> ClaimResult:
    return await coordination.claim(
        key,
        slot,
        rank=RANK[level],
        level=level,
        holder=holder,
        kind=kind,
        via=via,
        until_ms=until,
        claim_id=f"claim-{slot}-{now}",
        intents=intents,
        since_ms=since,
        now_ms=now,
    )


async def test_a_budget_of_two_admits_exactly_two_of_a_hundred_concurrent_contacts(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    results = await asyncio.gather(*(admit(coordination, tag, f"r{i}") for i in range(100)))
    assert sum(r.decision is Decision.ALLOW for r in results) == 2
    denied = [r for r in results if r.decision is not Decision.ALLOW]
    assert len(denied) == 98
    assert {(r.decision, r.reason, r.remaining, r.retry_at_ms) for r in denied} == {
        (Decision.DENY, "budget_exhausted", 0, T0 + WEEK)
    }


async def test_the_same_reservation_is_counted_once(coordination: RedisCoordination, space: UUID) -> None:
    tag = (space, "profile-1")
    first = await admit(coordination, tag, "r1")
    again = await admit(coordination, tag, "r1", now=T0 + SECOND)
    assert (first.decision, first.remaining) == (Decision.ALLOW, 1)
    assert (again.decision, again.remaining) == (Decision.ALLOW, 1)
    assert (await admit(coordination, tag, "r2")).remaining == 0


async def test_the_window_gives_the_budget_back(coordination: RedisCoordination, space: UUID) -> None:
    tag = (space, "profile-1")
    await admit(coordination, tag, "r1")
    await admit(coordination, tag, "r2", now=T0 + HOUR)
    refused = await admit(coordination, tag, "r3", now=T0 + 2 * HOUR)
    assert (refused.decision, refused.retry_at_ms) == (Decision.DENY, T0 + WEEK)
    back = await admit(coordination, tag, "r4", now=T0 + WEEK)
    assert (back.decision, back.remaining) == (Decision.ALLOW, 0)


async def test_a_suppression_denies_before_the_budget_is_touched(
    coordination: RedisCoordination, store: Redis, space: UUID
) -> None:
    tag = (space, "profile-1")
    await store.hset(suppressions_key(*tag), "marketing|*|source-a", "0|s1")
    refused = await admit(coordination, tag, "r1")
    assert (refused.decision, refused.reason) == (Decision.DENY, "suppressed")
    await store.hdel(suppressions_key(*tag), "marketing|*|source-a")
    # Only on e-mail, and a suppression that lapsed an hour ago.
    await store.hset(
        suppressions_key(*tag),
        mapping={"marketing|email|source-a": "0|s2", "marketing|sms|source-b": f"{T0 - HOUR}|s3"},
    )
    assert (await admit(coordination, tag, "r2", channel="email")).reason == "suppressed"
    assert (await admit(coordination, tag, "r3", channel="sms")).decision is Decision.ALLOW
    assert (await admit(coordination, tag, "r4")).remaining == 0


async def test_another_holder_defers_the_contact_until_its_claim_ends(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    key = claims_key(*tag)
    await claim(
        coordination,
        key,
        "o:panel",
        "human_active",
        "human:attendant-7",
        via=ClaimVia.MAPPING,
        until=T0 + 30 * MINUTE,
        now=T0,
    )
    deferred = await admit(coordination, tag, "r1", now=T0 + MINUTE)
    assert (deferred.decision, deferred.reason, deferred.retry_at_ms) == (
        Decision.DEFER,
        "owner_active",
        T0 + 30 * MINUTE,
    )
    assert deferred.holder is not None
    assert deferred.holder.holder == "human:attendant-7"
    # The holder itself, and a holder that allows the intent, are let through.
    assert (await admit(coordination, tag, "r2", now=T0 + MINUTE, agent="human:attendant-7")).remaining == 1
    await claim(
        coordination,
        key,
        "o:panel",
        "human_active",
        "human:attendant-7",
        via=ClaimVia.MAPPING,
        until=T0 + 30 * MINUTE,
        now=T0 + 2 * MINUTE,
        intents=("promo",),
    )
    assert (await admit(coordination, tag, "r3", now=T0 + 3 * MINUTE)).remaining == 0


async def test_quiet_hours_defer_without_spending_and_an_exhausted_budget_denies_in_them(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    morning = T0 + 8 * HOUR
    quiet = await admit(coordination, tag, "r1", quiet_until=morning)
    assert (quiet.decision, quiet.reason, quiet.retry_at_ms) == (Decision.DEFER, "quiet_hours", morning)
    assert (await admit(coordination, tag, "r2", now=morning)).remaining == 1
    assert (await admit(coordination, tag, "r3", now=morning)).remaining == 0
    exhausted = await admit(coordination, tag, "r4", now=morning + HOUR, quiet_until=morning + 10 * HOUR)
    assert (exhausted.decision, exhausted.reason) == (Decision.DENY, "budget_exhausted")


async def test_a_purpose_without_a_budget_spends_nothing(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    results = [await admit(coordination, tag, f"r{i}", limit=None) for i in range(5)]
    assert {(r.decision, r.remaining) for r in results} == {(Decision.ALLOW, -1)}


async def test_a_funnel_slice_caps_its_stage_and_the_rest_of_the_budget_stays_for_the_others(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    first = await admit(coordination, tag, "r1", limit=3, intent="proposal", slice_limit=1)
    assert (first.decision, first.remaining) == (Decision.ALLOW, 0)
    refused = await admit(coordination, tag, "r2", limit=3, intent="proposal", slice_limit=1, now=T0 + HOUR)
    assert (refused.decision, refused.reason, refused.retry_at_ms) == (
        Decision.DENY,
        "budget_exhausted",
        T0 + WEEK,
    )
    # Another stage spends what the slice left of the budget.
    other = await admit(coordination, tag, "r3", limit=3, intent="reminder", now=T0 + HOUR)
    assert (other.decision, other.remaining) == (Decision.ALLOW, 1)


async def test_a_spread_slice_gives_its_units_out_evenly(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    spacing = 2 * 24 * HOUR  # four units spread over eight days

    async def paced(reservation: str, now: int) -> Admission:
        return await admit(
            coordination, tag, reservation, limit=4, intent="nurture", slice_limit=4, spacing=spacing, now=now
        )

    assert (await paced("r1", T0)).decision is Decision.ALLOW
    early = await paced("r2", T0 + HOUR)
    assert (early.decision, early.reason, early.retry_at_ms) == (Decision.DEFER, "budget_paced", T0 + spacing)
    assert (await paced("r3", T0 + spacing)).remaining == 2


async def test_a_reloaded_target_counts_the_spends_of_the_durable_record(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")
    life = StoreLife(whole=False, since_ms=T0)
    spends = [
        BudgetSpend("r1", "c:marketing", "c:marketing:proposal", 1, T0 - HOUR, T0 - HOUR + WEEK),
        BudgetSpend("r0", "c:marketing", None, 1, T0 - WEEK - HOUR, T0 - HOUR),  # its window already ended
    ]
    assert await coordination.load_target(*tag, life, claims={}, suppressions={}, spends=spends, now_ms=T0)
    sliced = await admit(coordination, tag, "r2", limit=3, intent="proposal", slice_limit=1)
    assert (sliced.decision, sliced.reason) == (Decision.DENY, "budget_exhausted")
    assert (await admit(coordination, tag, "r3", limit=3, intent="reminder")).remaining == 1
    # Once per life: a second load changes nothing the store holds since.
    assert not await coordination.load_target(
        *tag, life, claims={}, suppressions={}, spends=spends, now_ms=T0
    )


async def test_a_paid_call_reserves_its_highest_cost_and_keeps_it_when_it_fails(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "object-1")

    async def spend(reservation: str, units: int) -> Admission:
        return await coordination.spend(
            *tag, "paid_update", reservation, units=units, limit=10, window_ms=HOUR, now_ms=T0
        )

    assert (await spend("c1", 6)).remaining == 4
    assert await coordination.settle_units(*tag, "paid_update", "c1", 2)  # the real cost was lower
    assert (await spend("c2", 6)).remaining == 2  # the unknown cost reserves the highest measured
    assert (await spend("c3", 3)).decision is Decision.DENY  # c2 failed: what it reserved still counts
    assert await coordination.settle_units(*tag, "paid_update", "c2", None)  # never confirmed: undone
    assert (await spend("c3", 3)).remaining == 5
    assert not await coordination.settle_units(*tag, "paid_update", "unknown", 1)


async def test_the_most_restrictive_claim_wins_among_three_sources_with_different_validities(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "profile-1")
    agent = await claim(
        coordination,
        key,
        "d:agent:sales",
        "agent_active",
        "agent:sales",
        via=ClaimVia.DECLARATION,
        until=T0 + 4 * HOUR,
        now=T0,
    )
    assert agent.accepted
    assert agent.owner is not None
    assert agent.owner.holder == "agent:sales"
    panel = await claim(
        coordination,
        key,
        "o:panel",
        "human_active",
        "human:attendant-7",
        via=ClaimVia.MAPPING,
        until=T0 + 30 * MINUTE,
        now=T0 + MINUTE,
    )
    assert panel.owner is not None
    assert panel.owner.level == "human_active"
    # A softer claim never demotes a stricter one still valid.
    pause = await claim(
        coordination,
        key,
        "o:middleware",
        "soft_pause",
        "system:middleware",
        via=ClaimVia.WORKER,
        until=T0 + 2 * HOUR,
        now=T0 + 2 * MINUTE,
    )
    assert pause.owner is not None
    assert pause.owner.level == "human_active"

    async def owner_at(at: int) -> tuple[str, str, int] | None:
        owner = await coordination.owner(key, now_ms=at)
        return None if owner is None else (owner.level, owner.slot, owner.until_ms)

    assert await owner_at(T0 + 10 * MINUTE) == ("human_active", "o:panel", T0 + 30 * MINUTE)
    assert await owner_at(T0 + 31 * MINUTE) == ("soft_pause", "o:middleware", T0 + 2 * HOUR)
    assert await owner_at(T0 + 2 * HOUR + 1) == ("agent_active", "d:agent:sales", T0 + 4 * HOUR)
    assert await owner_at(T0 + 4 * HOUR) is None


async def test_a_declaration_is_refused_while_another_holder_is_as_restrictive(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "profile-1")
    first = await claim(
        coordination,
        key,
        "d:agent:sales",
        "agent_active",
        "agent:sales",
        via=ClaimVia.DECLARATION,
        until=T0 + HOUR,
        now=T0,
    )
    other = await claim(
        coordination,
        key,
        "d:agent:retention",
        "agent_active",
        "agent:retention",
        via=ClaimVia.DECLARATION,
        until=T0 + HOUR,
        now=T0 + MINUTE,
    )
    assert not other.accepted
    assert other.owner is not None
    assert (other.owner.holder, other.owner.until_ms, other.owner.epoch) == (
        "agent:sales",
        T0 + HOUR,
        first.epoch,
    )
    # The holder renews its own lease; a stricter declaration by another holder takes over.
    assert (
        await claim(
            coordination,
            key,
            "d:agent:sales",
            "agent_active",
            "agent:sales",
            via=ClaimVia.DECLARATION,
            until=T0 + 2 * HOUR,
            now=T0 + 2 * MINUTE,
        )
    ).accepted
    case = await claim(
        coordination,
        key,
        "d:agent:retention",
        "transfer_pending",
        "agent:retention",
        via=ClaimVia.DECLARATION,
        until=T0 + HOUR,
        now=T0 + 3 * MINUTE,
    )
    assert case.accepted
    assert case.owner is not None
    assert case.owner.holder == "agent:retention"
    # A declaration that already expired is refused as such.
    late = await claim(
        coordination,
        key,
        "d:agent:late",
        "closed",
        "agent:late",
        via=ClaimVia.DECLARATION,
        until=T0,
        now=T0 + 4 * MINUTE,
    )
    assert not late.accepted


async def test_an_observation_replaces_what_its_source_said_before(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "profile-1")
    await claim(
        coordination,
        key,
        "o:panel",
        "human_active",
        "human:attendant-7",
        via=ClaimVia.MAPPING,
        until=T0 + HOUR,
        now=T0,
    )
    closed = await claim(
        coordination,
        key,
        "o:panel",
        "soft_pause",
        "human:attendant-7",
        via=ClaimVia.MAPPING,
        until=T0 + HOUR,
        now=T0 + MINUTE,
    )
    assert closed.owner is not None
    assert closed.owner.level == "soft_pause"


async def test_only_the_epoch_a_claim_was_written_with_releases_it(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "profile-1")
    held = await claim(
        coordination,
        key,
        "d:agent:sales",
        "agent_active",
        "agent:sales",
        via=ClaimVia.DECLARATION,
        until=T0 + HOUR,
        now=T0,
    )
    stale = await coordination.release(key, "d:agent:sales", held.epoch - 1, now_ms=T0 + MINUTE)
    assert not stale.accepted
    assert stale.owner is not None
    released = await coordination.release(key, "d:agent:sales", held.epoch, now_ms=T0 + MINUTE)
    assert released.accepted
    assert released.owner is None
    assert released.epoch > held.epoch
    again = await coordination.release(key, "d:agent:sales", held.epoch, now_ms=T0 + 2 * MINUTE)
    assert not again.accepted


async def settle(
    coordination: RedisCoordination,
    key: str,
    state: EffectState,
    *,
    attempt: int | None,
    by: Settler,
    now_ms: int,
) -> tuple[bool, EffectState | None]:
    status, snapshot, _ = await coordination.settle_effect(key, state, attempt=attempt, by=by, now_ms=now_ms)
    return status == "ok", snapshot.state if snapshot is not None else None


async def reserve(coordination: RedisCoordination, key: str, now: int) -> tuple[str, int]:
    attempt = await coordination.reserve_effect(
        key, kind="delivery", source="whatsapp-vendor", lease_ms=LEASE, ttl_ms=WEEK, now_ms=now
    )
    return attempt.outcome, attempt.attempt


async def test_an_effect_is_reserved_once_and_done_is_final(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = effect_key(space, "farewell-hash")
    assert await reserve(coordination, key, T0) == ("reserved", 1)
    assert await reserve(coordination, key, T0 + SECOND) == ("in_flight", 1)
    settled = await settle(
        coordination, key, EffectState.DONE, attempt=1, by=Settler.RESERVER, now_ms=T0 + 2 * SECOND
    )
    assert settled == (True, EffectState.DONE)
    assert await reserve(coordination, key, T0 + 3 * SECOND) == ("done", 1)
    late = await settle(
        coordination, key, EffectState.FAILED, attempt=None, by=Settler.OBSERVATION, now_ms=T0 + 4 * SECOND
    )
    assert late == (False, EffectState.DONE)


async def test_an_ambiguous_effect_is_never_emitted_again(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = effect_key(space, "farewell-hash")
    assert await reserve(coordination, key, T0) == ("reserved", 1)
    # The reserver never settled: the lease lapsed, and the effect may have gone out.
    retries = await asyncio.gather(*(reserve(coordination, key, T0 + LEASE + i) for i in range(50)))
    assert set(retries) == {("unknown_outcome", 1)}
    assert await reserve(coordination, key, T0 + 3 * HOUR) == ("unknown_outcome", 1)
    # Only a decision that it failed, by an observation or a person, opens a new attempt.
    decided = await settle(
        coordination, key, EffectState.FAILED, attempt=None, by=Settler.PERSON, now_ms=T0 + 4 * HOUR
    )
    assert decided == (True, EffectState.FAILED)
    assert await reserve(coordination, key, T0 + 5 * HOUR) == ("reserved", 2)


async def test_a_reserver_that_declares_the_outcome_unknown_waits_for_the_system(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = effect_key(space, "notice-hash")
    await reserve(coordination, key, T0)
    ambiguous = await settle(
        coordination, key, EffectState.UNKNOWN_OUTCOME, attempt=1, by=Settler.RESERVER, now_ms=T0 + SECOND
    )
    assert ambiguous == (True, EffectState.UNKNOWN_OUTCOME)
    assert await reserve(coordination, key, T0 + 2 * SECOND) == ("unknown_outcome", 1)
    confirmed = await settle(
        coordination, key, EffectState.DONE, attempt=None, by=Settler.OBSERVATION, now_ms=T0 + HOUR
    )
    assert confirmed == (True, EffectState.DONE)
    assert await reserve(coordination, key, T0 + 2 * HOUR) == ("done", 1)


async def test_a_stale_reserver_is_fenced_and_a_late_confirmation_turns_failed_into_done(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = effect_key(space, "filing-hash")
    await reserve(coordination, key, T0)
    await settle(coordination, key, EffectState.FAILED, attempt=None, by=Settler.PERSON, now_ms=T0 + HOUR)
    assert await reserve(coordination, key, T0 + 2 * HOUR) == ("reserved", 2)
    stale = await settle(
        coordination, key, EffectState.DONE, attempt=1, by=Settler.RESERVER, now_ms=T0 + 2 * HOUR + SECOND
    )
    assert stale == (False, EffectState.RESERVED)
    await settle(
        coordination, key, EffectState.FAILED, attempt=2, by=Settler.RESERVER, now_ms=T0 + 2 * HOUR + SECOND
    )
    confirmed = await settle(
        coordination, key, EffectState.DONE, attempt=None, by=Settler.OBSERVATION, now_ms=T0 + 3 * HOUR
    )
    assert confirmed == (True, EffectState.DONE)
    missing = await settle(
        coordination,
        effect_key(space, "never-reserved"),
        EffectState.DONE,
        attempt=None,
        by=Settler.PERSON,
        now_ms=T0,
    )
    assert missing == (False, None)


reserve_op = st.tuples(st.just("reserve"), st.integers(0, 2 * LEASE))
settle_op = st.tuples(
    st.just("settle"),
    st.integers(0, 2 * LEASE),
    st.sampled_from(list(EffectState)),
    st.sampled_from(list(Settler)),
    st.sampled_from([0, -1]),
)


@settings(max_examples=200, deadline=None)
@given(ops=st.lists(st.one_of(reserve_op, settle_op), min_size=1, max_size=25))
def test_no_sequence_of_calls_emits_an_ambiguous_or_finished_effect_again(
    ops: list[tuple[object, ...]],
) -> None:
    """Whatever the order of reservations, settlements and time: each attempt is handed out once, nothing
    is handed out after `done`, a live reservation answers `in_flight`, a lapsed one `unknown_outcome`, and
    a new attempt only ever follows a settlement to `failed`."""

    async def scenario() -> None:
        coordination = RedisCoordination(fakeredis.FakeAsyncRedis())
        key = effect_key(UUID(int=1), "k")
        now, attempt, lease_until = T0, 0, 0
        state: str | None = None
        for op in ops:
            now += int(str(op[1]))
            if op[0] == "reserve":
                outcome, number = await reserve(coordination, key, now)
                if state == "reserved" and now >= lease_until:
                    state = "unknown_outcome"
                if outcome == "reserved":
                    assert state in (None, "failed")
                    assert number == attempt + 1
                    attempt, lease_until, state = number, now + LEASE, "reserved"
                else:
                    assert outcome == ("in_flight" if state == "reserved" else state)
                continue
            target, by, delta = EffectState(str(op[2])), Settler(str(op[3])), int(str(op[4]))
            given_attempt = attempt + delta if by is Settler.RESERVER else None
            ok, current = await settle(coordination, key, target, attempt=given_attempt, by=by, now_ms=now)
            if state == "reserved" and now >= lease_until:
                state = "unknown_outcome"
            if ok:
                assert state is not None
                assert state != "done" or target is EffectState.DONE
                state = target.value
            assert current == (EffectState(state) if state else None)

    asyncio.run(scenario())


async def test_a_task_lock_is_its_holders_alone_and_never_the_subjects_owner(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "object-1")

    async def lock(holder: str, now: int = T0) -> ClaimResult:
        return await claim(
            coordination, key, "t:summary", "agent_active", holder, via=ClaimVia.DECLARATION,
            until=now + HOUR, now=now, kind=ClaimKind.TASK_LOCK,
        )  # fmt: skip

    first = await lock("agent:drafts")
    assert first.accepted
    assert first.owner is None  # a lock holds its task, not the object
    refused = await lock("human:lawyer")
    assert refused.owner is not None
    assert (refused.accepted, refused.owner.holder) == (False, "agent:drafts")
    assert (await lock("agent:drafts", T0 + MINUTE)).accepted  # the holder renews
    [held] = await coordination.locks(key, now_ms=T0 + MINUTE)
    assert (held.slot, held.holder) == ("t:summary", "agent:drafts")
    assert (await lock("human:lawyer", T0 + 2 * HOUR)).accepted  # an expired lock frees the task


async def test_an_observation_ends_at_its_time_and_a_late_older_one_never_comes_back(
    coordination: RedisCoordination, space: UUID
) -> None:
    key = claims_key(space, "profile-1")

    async def observe(at: int, now: int) -> ClaimResult:
        return await claim(
            coordination, key, "o:panel", "human_active", "human:panel", via=ClaimVia.MAPPING,
            until=at + HOUR, now=now, since=at,
        )  # fmt: skip

    assert (await observe(T0, T0)).accepted
    ended = await coordination.end_observation(
        key, "o:panel", at_ms=T0 + 10 * MINUTE, keep_until_ms=T0 + HOUR, now_ms=T0 + 10 * MINUTE
    )
    assert ended.released is not None
    assert (ended.accepted, ended.released.holder, ended.owner) == (True, "human:panel", None)
    late = await observe(T0 + 5 * MINUTE, T0 + 11 * MINUTE)
    assert (late.accepted, late.owner) == (False, None)
    assert (await observe(T0 + 12 * MINUTE, T0 + 12 * MINUTE)).accepted  # a newer opening holds again
    stale_end = await coordination.end_observation(
        key, "o:panel", at_ms=T0 + 11 * MINUTE, keep_until_ms=T0 + HOUR, now_ms=T0 + 13 * MINUTE
    )
    assert stale_end.accepted is False
    assert (await coordination.owner(key, now_ms=T0 + 13 * MINUTE)) is not None


async def test_commitments_meet_by_compatibility_and_leave_when_decided(
    coordination: RedisCoordination, space: UUID
) -> None:
    tag = (space, "profile-1")

    async def commit(
        external: str, kind: str, compatibility: str, score: float | None = None
    ) -> tuple[bool, list[str]]:
        entry = CommitmentEntry(f"id-{external}", kind, compatibility, T0 + WEEK, score, "agent:sales")
        return await coordination.commit(*tag, external, entry, now_ms=T0)

    assert await commit("c1", "discount", "best_wins", 10) == (True, [])
    assert await commit("c0", "discount", "best_wins", 5) == (False, [])
    assert await commit("c2", "discount", "best_wins", 20) == (True, ["id-c1"])  # the better one wins
    assert await commit("c2", "discount", "best_wins", 1) == (True, [])  # the same one again: what it got
    assert await commit("t1", "trial", "first_holds") == (True, [])
    assert await commit("t2", "trial", "first_holds") == (False, [])
    assert await commit("s1", "settlement", "exclusive") == (False, [])  # an exclusive one holds alone
    held = await coordination.commitments(*tag, now_ms=T0)
    assert sorted(external for external, _ in held) == ["c2", "t1"]
    assert await coordination.uncommit(*tag, "t1", now_ms=T0) == "id-t1"
    assert await coordination.uncommit(*tag, "t1", now_ms=T0) is None
    assert await coordination.commitments(*tag, now_ms=T0 + WEEK) == []


async def test_the_budgets_a_read_advises_on_and_what_an_erasure_leaves(
    coordination: RedisCoordination, store: Redis, space: UUID
) -> None:
    tag = (space, "profile-1")
    await admit(coordination, tag, "r1")
    assert await coordination.budgets_used(
        *tag, [("c:marketing", WEEK), ("c:retention", WEEK)], now_ms=T0
    ) == [
        1,
        0,
    ]
    await store.hset(suppressions_key(*tag), "marketing|*|source-a", "0|s1")
    assert await coordination.suppressed(*tag, now_ms=T0) == ["marketing"]
    assert await coordination.purge(*tag) >= 2  # the budget's window and units
    assert await coordination.budgets_used(*tag, [("c:marketing", WEEK)], now_ms=T0) == [0]
    assert await coordination.suppressed(*tag, now_ms=T0) == ["marketing"]  # it outlives the erasure
