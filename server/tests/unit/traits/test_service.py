"""Derivation, expiry, retraction, opt-out, merges and the dry-run of `TraitsService` (20)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from niadra.app.traits import TraitsService
from niadra.config.models import TraitRuleConfig, WebhookEndpoint
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.records import ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.rules import TraitRule
from niadra.domain.vocabulary import EventKind
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.objects.fakes import CRM, T0, FakePipeline, World, config_with, episode, event, handle

RECURRING = TraitRuleConfig(rule_id="recurring_complaint", catalog="recurring_complaint", version="v3")
OVERDUE = TraitRuleConfig(
    rule_id="overdue_invoices", catalog="overdue_invoices", version="v1", purposes=frozenset({"billing"})
)
MARKETING = WebhookEndpoint(
    endpoint_id="marketing",
    url="https://mkt.example/hooks",
    source_id=WHATSAPP_SOURCE,
    event_types=["trait.added", "trait.expired"],
    secret_refs=["vault/mkt"],
)


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(config_with(trait_rules=[RECURRING, OVERDUE], webhook_endpoints=[CRM, MARKETING]))
    cell.clock.at = T0
    return World(cell)


@pytest.fixture
def marina(world: World) -> ProfileRecord:
    return world.identity.add_profile(handle())


def service(world: World) -> TraitsService:
    return TraitsService(world.uow, world.cell.clock, world.cell.config)


def complaints(world: World, profile: ProfileRecord, *days_ago: float) -> None:
    origin = world.identity.handles[profile.handle_ids[0]]
    world.memory.episodes.extend(episode(origin, T0 - timedelta(days=d)) for d in days_ago)


async def derive(world: World, profile: ProfileRecord) -> None:
    await service(world).derive(Task(TaskName.DERIVE_TRAITS, SPACE_ID, {}, profile_id=profile.profile_id))


async def traits_of(world: World, profile: ProfileRecord) -> list:  # type: ignore[type-arg]
    async with world.cell.uow(SPACE_ID) as uow:
        return await uow.traits.traits_of(profile.profile_id)


async def deliveries(world: World) -> list[tuple[str, str]]:
    async with world.cell.uow(SPACE_ID) as uow:
        return sorted((d.endpoint_id, d.event_type) for d in await uow.notify.list_deliveries(limit=200))


async def test_a_trait_is_born_with_evidence_and_announced(world: World, marina: ProfileRecord) -> None:
    complaints(world, marina, 49, 24, 0.1)
    await derive(world, marina)
    [trait] = await traits_of(world, marina)
    assert trait.name == "recurring_complaint"
    assert trait.value == "technical_visit"
    assert len(trait.evidence) == 3
    assert trait.rule_version == "v3"
    assert trait.membership_version == 1
    assert await deliveries(world) == [("crm", "trait.added"), ("marketing", "trait.added")]
    # The new pattern gets its vector for history search right away (16.2).
    assert {t.name for t in world.pipeline.outbox} == {
        TaskName.COMPILE,
        TaskName.TRIGGER,
        TaskName.DELIVER_WEBHOOK,
        TaskName.EMBED,
    }


async def test_a_purpose_bound_trait_only_reaches_sources_with_that_purpose(
    world: World, marina: ProfileRecord
) -> None:
    origin = world.identity.handles[marina.handle_ids[0]]
    world.ingest.add(
        *(
            event(EventKind.SYSTEM_EVENT, T0 - timedelta(days=d), [origin], canonical_type="invoice.overdue")
            for d in (10, 40)
        )
    )
    await derive(world, marina)
    assert await deliveries(world) == [("crm", "trait.added")]


async def test_overdue_invoices_reach_only_billing_even_when_the_rule_names_no_purpose(
    world: World, marina: ProfileRecord
) -> None:
    # The catalog releases overdue invoices to billing by default.
    unbound = OVERDUE.model_copy(update={"purposes": frozenset()})
    world.cell.config.put(config_with(trait_rules=[RECURRING, unbound], webhook_endpoints=[CRM, MARKETING]))
    origin = world.identity.handles[marina.handle_ids[0]]
    world.ingest.add(
        *(
            event(EventKind.SYSTEM_EVENT, T0 - timedelta(days=d), [origin], canonical_type="invoice.overdue")
            for d in (10, 40)
        )
    )
    await derive(world, marina)
    assert await deliveries(world) == [("crm", "trait.added")]


async def test_declining_sentiment_reaches_only_customer_agents(world: World, marina: ProfileRecord) -> None:
    # How the customer feels is for whoever talks to them; the billing CRM never hears it.
    sentiment = TraitRuleConfig(rule_id="declining_sentiment", catalog="declining_sentiment", version="v1")
    world.cell.config.put(config_with(trait_rules=[sentiment], webhook_endpoints=[CRM, MARKETING]))
    origin = world.identity.handles[marina.handle_ids[0]]
    world.memory.episodes.extend(
        replace(episode(origin, T0 - timedelta(days=d)), sentiment=-0.6) for d in (3, 2, 1)
    )
    await derive(world, marina)
    [trait] = await traits_of(world, marina)
    assert trait.name == "declining_sentiment"
    assert await deliveries(world) == [("marketing", "trait.added")]


async def test_rederiving_without_evidence_removes_the_trait(world: World, marina: ProfileRecord) -> None:
    complaints(world, marina, 3, 2, 1)
    await derive(world, marina)
    world.memory.episodes.pop()  # `forget` erased one episode
    await derive(world, marina)
    assert await traits_of(world, marina) == []
    assert ("crm", "trait.expired") in await deliveries(world)


async def test_derivation_is_coalesced(world: World, marina: ProfileRecord) -> None:
    task = Task(TaskName.DERIVE_TRAITS, SPACE_ID, {}, profile_id=marina.profile_id)
    await world.pipeline.request_work(task)
    seen_before = world.pipeline.work.copy()
    await world.pipeline.request_work(task)  # arrives while the first request is pending
    assert len(world.pipeline.take(TaskName.DERIVE_TRAITS)) == 1
    assert seen_before
    await service(world).derive(task)
    assert world.pipeline.take(TaskName.DERIVE_TRAITS) == []  # both requests satisfied by one run
    await world.pipeline.request_work(task)
    world.pipeline.work[(SPACE_ID, marina.profile_id, TaskName.DERIVE_TRAITS)] = (3, 0)
    await service(world).derive(task)
    assert world.pipeline.take(TaskName.DERIVE_TRAITS)  # a request came in during the run: runs again


async def test_a_retraction_sticks_until_new_evidence(world: World, marina: ProfileRecord) -> None:
    complaints(world, marina, 3, 2, 1)
    await derive(world, marina)
    [trait] = await traits_of(world, marina)
    view = await service(world).retract(SPACE_ID, trait.trait_id)
    assert view.retracted_at == T0
    await derive(world, marina)
    [kept] = await traits_of(world, marina)
    assert kept.retracted_at == T0
    complaints(world, marina, 0.5)
    await derive(world, marina)
    [back] = await traits_of(world, marina)
    assert back.retracted_at is None
    assert len(back.evidence) == 4
    with pytest.raises(NotFound):
        await service(world).retract(SPACE_ID, uuid7())


async def test_opt_out_removes_traits_and_stops_new_ones(world: World, marina: ProfileRecord) -> None:
    complaints(world, marina, 3, 2, 1)
    await derive(world, marina)
    await service(world).opt_out(SPACE_ID, marina.profile_id)
    await derive(world, marina)
    assert await traits_of(world, marina) == []
    with pytest.raises(NotFound):
        await service(world).opt_out(SPACE_ID, uuid7())


async def test_merge_drops_the_absorbed_profiles_traits(world: World, marina: ProfileRecord) -> None:
    other = world.identity.add_profile(handle())
    complaints(world, other, 3, 2, 1)
    await derive(world, other)
    # identity merges `other` into marina: marina now holds both handles and `other` is gone
    merged = world.identity.add_profile(
        *(world.identity.handles[h] for h in (*marina.handle_ids, *other.handle_ids)),
        profile_id=marina.profile_id,
        membership_version=2,
    )
    del world.identity.profiles[other.profile_id]
    await derive(world, merged)
    assert await traits_of(world, other) == []
    [trait] = await traits_of(world, merged)
    assert trait.membership_version == 2
    assert len(trait.evidence) == 3


async def test_unmerge_keeps_only_what_each_half_supports(world: World, marina: ProfileRecord) -> None:
    second = handle()
    whole = world.identity.add_profile(
        world.identity.handles[marina.handle_ids[0]], second, profile_id=marina.profile_id
    )
    complaints(world, whole, 3, 2)
    world.memory.episodes.append(episode(second, T0 - timedelta(days=1)))
    await derive(world, whole)
    assert await traits_of(world, whole)
    world.identity.add_profile(world.identity.handles[marina.handle_ids[0]], profile_id=marina.profile_id)
    await derive(world, whole)
    assert await traits_of(world, whole) == []


async def test_expired_traits_are_rederived_by_the_sweep(world: World, marina: ProfileRecord) -> None:
    complaints(world, marina, 3, 2, 1)
    await derive(world, marina)
    world.pipeline = FakePipeline()
    [trait] = await traits_of(world, marina)
    assert await service(world).expire_due(trait.expires_at - timedelta(seconds=1)) == 0
    assert await service(world).expire_due(trait.expires_at) == 1
    assert [t.profile_id for t in world.pipeline.take(TaskName.DERIVE_TRAITS)] == [marina.profile_id]


async def test_dry_run_counts_matching_profiles_over_90_days(world: World, marina: ProfileRecord) -> None:
    quiet = world.identity.add_profile(handle())
    complaints(world, marina, 3, 2, 1)
    world.sessions.active = [marina.profile_id, quiet.profile_id, uuid7()]
    result = await service(world).dry_run(
        SPACE_ID, TraitRule("rc", "recurring_complaint", "draft", {"min_count": 3})
    )
    assert (result.profiles_evaluated, result.profiles_matching) == (2, 1)
    assert result.sample[0].pseudonym == marina.pseudonym
    assert len(result.sample[0].evidence) == 3
    with pytest.raises(InvalidInput):
        await service(world).dry_run(
            SPACE_ID, TraitRule("rc", "recurring_complaint", "draft", {"category": "health"})
        )


async def test_dry_run_leaves_out_who_objected_to_profiling(world: World, marina: ProfileRecord) -> None:
    # The derivation never gives them a pattern, so the dry run neither counts nor shows them.
    other = world.identity.add_profile(handle())
    complaints(world, marina, 3, 2, 1)
    complaints(world, other, 3, 2, 1)
    world.sessions.active = [marina.profile_id, other.profile_id]
    rule = TraitRule("rc", "recurring_complaint", "draft", {"min_count": 3})
    assert (await service(world).dry_run(SPACE_ID, rule)).profiles_matching == 2
    await service(world).opt_out(SPACE_ID, marina.profile_id)
    result = await service(world).dry_run(SPACE_ID, rule)
    assert (result.profiles_evaluated, result.profiles_matching) == (1, 1)
    assert [s.profile_id for s in result.sample] == [str(other.profile_id)]


async def test_derive_is_a_per_profile_task(world: World) -> None:
    with pytest.raises(InvalidInput):
        await service(world).derive(Task(TaskName.DERIVE_TRAITS, SPACE_ID, {}))
