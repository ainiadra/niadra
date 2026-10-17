"""`TriggersService`: windows, dedupe, lateness, the tenant cap, retraction, the sweep and dry-runs (22.1)."""

from datetime import timedelta
from typing import Any

import pytest

from niadra.app.triggers import TriggersService
from niadra.config.models import TraitRuleConfig, TriggerRuleConfig, WebhookEndpoint
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.measure.model import DailyCounters, DailyKey
from niadra.domain.records import ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.conditions import TriggerRule
from niadra.domain.triggers.firing import Firing
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.objects.fakes import CRM, T0, World, config_with, handle, open_item

OVERDUE = TriggerRuleConfig(
    rule_id="promise_overdue", condition="promise.overdue", version="v1", endpoint_id="crm"
)
MARKETING = WebhookEndpoint(
    endpoint_id="marketing",
    url="https://mkt.example/hooks",
    source_id=WHATSAPP_SOURCE,
    event_types=["*"],
    secret_refs=["vault/mkt"],
)


def configure(cell: Cell, *rules: TriggerRuleConfig, **extra: Any) -> None:
    cell.config.put(config_with(trigger_rules=list(rules), webhook_endpoints=[CRM, MARKETING], **extra))


@pytest.fixture
def world(cell: Cell) -> World:
    configure(cell, OVERDUE)
    cell.clock.at = T0
    return World(cell)


@pytest.fixture
def marina(world: World) -> ProfileRecord:
    return world.identity.add_profile(handle())


def service(world: World) -> TriggersService:
    return TriggersService(world.uow, world.cell.clock, world.cell.config)


def owe(world: World, profile: ProfileRecord, due_hours_ago: float) -> None:
    origin = world.identity.handles[profile.handle_ids[0]]
    world.memory.items.append(open_item(origin, T0 - timedelta(hours=due_hours_ago)))


async def evaluate(world: World, profile: ProfileRecord) -> None:
    await service(world).evaluate(Task(TaskName.TRIGGER, SPACE_ID, {}, profile_id=profile.profile_id))


async def firings(world: World) -> list[Firing]:
    async with world.cell.uow(SPACE_ID) as uow:
        return await uow.triggers.list_firings(limit=500)


async def deliveries(world: World) -> list[tuple[str, str, dict[str, Any]]]:
    async with world.cell.uow(SPACE_ID) as uow:
        found = await uow.notify.list_deliveries(limit=500)
    return [(d.endpoint_id, d.event_type, d.body["data"]) for d in reversed(found)]


async def test_fires_once_per_window_to_the_rule_endpoint_only(world: World, marina: ProfileRecord) -> None:
    owe(world, marina, 1)
    await evaluate(world, marina)
    await evaluate(world, marina)
    [firing] = await firings(world)
    assert firing.rule_id == "promise_overdue"
    assert not firing.late
    assert firing.profile_id == marina.profile_id
    [(endpoint, kind, data)] = await deliveries(world)
    assert (endpoint, kind) == ("crm", "trigger.fired")
    assert data["firing_id"] == str(firing.firing_id)
    assert data["evidence"][0]["kind"] == "open_item"


async def test_the_same_evidence_never_fires_twice_new_evidence_does(
    world: World, marina: ProfileRecord
) -> None:
    owe(world, marina, 1)
    await evaluate(world, marina)
    world.cell.clock.advance(days=1)
    await evaluate(world, marina)
    assert len(await firings(world)) == 1
    owe(world, marina, 0.5)
    await evaluate(world, marina)
    assert len(await firings(world)) == 2


async def test_late_firings_are_marked_or_suppressed(world: World, marina: ProfileRecord, cell: Cell) -> None:
    owe(world, marina, 30)
    await evaluate(world, marina)
    [firing] = await firings(world)
    assert firing.late
    assert (await deliveries(world))[0][2]["late"] is True
    configure(cell, OVERDUE.model_copy(update={"rule_id": "strict", "late_policy": "suppress"}))
    await evaluate(world, marina)
    assert len(await firings(world)) == 1


async def test_the_tenant_cap_defers_evaluation(world: World, marina: ProfileRecord) -> None:
    async with world.cell.uow(SPACE_ID) as uow:
        for _ in range(100):
            await uow.triggers.add_firing(Firing(uuid7(), "other", "v1", uuid7(), "w", b"k", (), 1, T0))
    owe(world, marina, 1)
    await evaluate(world, marina)
    assert len(await firings(world)) == 100
    [(task, due, reason)] = world.pipeline.scheduled
    assert task.name is TaskName.TRIGGER
    assert due == T0 + timedelta(minutes=1)
    assert reason == "rate_limited"


async def test_an_unmerge_that_separates_the_evidence_retracts(world: World, marina: ProfileRecord) -> None:
    second = handle()
    whole = world.identity.add_profile(
        world.identity.handles[marina.handle_ids[0]], second, profile_id=marina.profile_id
    )
    world.memory.items.append(open_item(second, T0 - timedelta(hours=1)))
    await evaluate(world, whole)
    world.identity.add_profile(
        world.identity.handles[marina.handle_ids[0]], profile_id=marina.profile_id, membership_version=2
    )
    await evaluate(world, whole)
    [firing] = await firings(world)
    assert firing.retracted_at == T0
    kinds = [(e, k) for e, k, _ in await deliveries(world)]
    assert kinds == [("crm", "trigger.fired"), ("crm", "trigger.retracted")]


async def test_a_target_that_cannot_read_the_payload_never_fires(
    world: World, marina: ProfileRecord, cell: Cell
) -> None:
    trait_rule = TraitRuleConfig(
        rule_id="overdue_invoices", catalog="overdue_invoices", version="v1", purposes=frozenset({"billing"})
    )
    present = TriggerRuleConfig(
        rule_id="owes",
        condition="trait.present",
        version="v1",
        params={"name": "overdue_invoices"},
        endpoint_id="marketing",
    )
    configure(cell, present, trait_rules=[trait_rule])
    result = await service(world).dry_run(
        SPACE_ID, TriggerRule("owes", "trait.present", "v1", {"name": "overdue_invoices"}, "marketing")
    )
    assert result.target_problem == "purpose_not_allowed"


async def test_the_catalog_keeps_overdue_invoices_from_marketing_when_the_rule_names_no_purpose(
    world: World, marina: ProfileRecord, cell: Cell
) -> None:
    trait_rule = TraitRuleConfig(rule_id="overdue_invoices", catalog="overdue_invoices", version="v1")
    present = TriggerRuleConfig(
        rule_id="owes",
        condition="trait.present",
        version="v1",
        params={"name": "overdue_invoices"},
        endpoint_id="marketing",
    )
    configure(cell, present, trait_rules=[trait_rule])
    result = await service(world).dry_run(
        SPACE_ID, TriggerRule("owes", "trait.present", "v1", {"name": "overdue_invoices"}, "marketing")
    )
    assert result.target_problem == "purpose_not_allowed"


async def test_the_sweep_requests_due_profiles_and_fires_silent_sources(
    world: World, marina: ProfileRecord, cell: Cell
) -> None:
    silent = TriggerRuleConfig(
        rule_id="silent", condition="source.silent", version="v1", params={"minutes": 30}, endpoint_id="crm"
    )
    configure(cell, OVERDUE, silent)
    owe(world, marina, 1)
    world.ingest.last_seen = {BILLING_SOURCE: T0 - timedelta(hours=2), WHATSAPP_SOURCE: T0}
    await service(world).sweep(T0)
    assert [t.profile_id for t in world.pipeline.take(TaskName.TRIGGER)] == [marina.profile_id]
    [firing] = await firings(world)
    assert firing.profile_id == BILLING_SOURCE
    assert firing.evidence[0].kind == "source"
    await service(world).sweep(T0 + timedelta(minutes=5))
    assert len(await firings(world)) == 1


async def test_an_item_that_just_fell_due_never_waits_behind_old_ones(
    world: World, marina: ProfileRecord, cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing closes an item at its deadline, so old ones come back at every sweep; with more of
    # them than a sweep takes, the newest deadline still goes first.
    monkeypatch.setattr("niadra.app.triggers.SWEEP_ITEMS", 1)
    configure(cell, OVERDUE)
    joao = world.identity.add_profile(handle())
    owe(world, marina, 24 * 30)
    owe(world, joao, 0.1)
    await service(world).sweep(T0)
    assert [t.profile_id for t in world.pipeline.take(TaskName.TRIGGER)] == [joao.profile_id]


async def test_a_second_silence_of_the_same_source_warns_again(world: World, cell: Cell) -> None:
    silent = TriggerRuleConfig(
        rule_id="silent", condition="source.silent", version="v1", params={"minutes": 30}, endpoint_id="crm"
    )
    configure(cell, silent)
    world.ingest.last_seen = {BILLING_SOURCE: T0 - timedelta(hours=2)}
    await service(world).sweep(T0)
    # The source comes back the next day, then falls silent again.
    world.ingest.last_seen = {BILLING_SOURCE: T0 + timedelta(days=1)}
    await service(world).sweep(T0 + timedelta(days=1, minutes=10))
    await service(world).sweep(T0 + timedelta(days=1, hours=1))
    assert [f.profile_id for f in await firings(world)] == [BILLING_SOURCE, BILLING_SOURCE]


async def test_a_source_whose_sdk_sent_more_than_arrived_warns_once_a_day(world: World, cell: Cell) -> None:
    # The counts the SDK reports against what arrived; a small gap or a close count stays quiet.
    gap = TriggerRuleConfig(
        rule_id="gap", condition="source.gap", version="v1", params={"min_missing": 10}, endpoint_id="crm"
    )
    configure(cell, gap)
    today = T0.date()
    world.ingest.days = {
        BILLING_SOURCE: [SourceDay(today, sent=500, received=460, rejected=2, unmapped=0, last_event_at=T0)],
        WHATSAPP_SOURCE: [SourceDay(today, sent=500, received=495, rejected=0, unmapped=0, last_event_at=T0)],
    }
    await service(world).sweep(T0)
    await service(world).sweep(T0 + timedelta(minutes=5))
    [firing] = await firings(world)
    assert firing.profile_id == BILLING_SOURCE
    assert firing.evidence[0].kind == "source"


REPETITION = TriggerRuleConfig(
    rule_id="agent_ignores_context",
    condition="context_use.repetition_rate",
    version="v1",
    params={"above": 0.2, "window_days": 3, "min_questions": 10},
    endpoint_id="crm",
)


async def measured(
    world: World, day_offset: int, source: Any, questions: int, repeated: int, **key: Any
) -> None:
    """What the measurement adds to the daily aggregates when a session of `source` is measured."""
    async with world.cell.uow(SPACE_ID) as uow:
        day = (T0 + timedelta(days=day_offset)).date()
        dims = {"channel": "whatsapp", "view": "chat", "experiment_group": "", **key}
        await uow.measure.add_daily(
            DailyKey(day, source, dims["channel"], dims["view"], dims["experiment_group"]),
            DailyCounters(questions=questions, repeated=repeated),
        )


async def test_a_source_that_repeats_what_it_received_warns_once_per_run(world: World, cell: Cell) -> None:
    # The condition the Console offered never fired.
    configure(cell, REPETITION)
    await measured(world, 0, WHATSAPP_SOURCE, 20, 8)  # 40% asked again
    await measured(world, 0, VOICE_SOURCE, 20, 1)
    await measured(world, 0, VOICE_SOURCE, 20, 19, experiment_group="control")  # never had the context
    await service(world).sweep(T0)
    [firing] = await firings(world)
    assert firing.profile_id == WHATSAPP_SOURCE
    [(endpoint, event_type, data)] = await deliveries(world)
    assert (endpoint, event_type, data["condition"]) == (
        "crm",
        "trigger.fired",
        "context_use.repetition_rate",
    )
    assert data["detail"] == {
        "questions": 20,
        "repeated": 8,
        "rate": 0.4,
        "above": 0.2,
        "window_days": 3,
        "above_since": T0.date().isoformat(),
    }
    # Still above an hour and a day later: the same run, no second warning.
    await service(world).sweep(T0 + timedelta(hours=1))
    await measured(world, 1, WHATSAPP_SOURCE, 10, 3)
    await service(world).sweep(T0 + timedelta(days=1))
    assert len(await firings(world)) == 1
    # It gets better, then worse again: a new run warns again.
    await measured(world, 4, WHATSAPP_SOURCE, 40, 1)
    await service(world).sweep(T0 + timedelta(days=4))
    await measured(world, 6, WHATSAPP_SOURCE, 30, 20)
    await service(world).sweep(T0 + timedelta(days=6))
    assert [f.profile_id for f in await firings(world)] == [WHATSAPP_SOURCE, WHATSAPP_SOURCE]


async def test_the_repetition_rule_can_watch_one_source_and_be_tried_first(world: World, cell: Cell) -> None:
    only_voice = REPETITION.model_copy(
        update={"params": {**REPETITION.params, "source_id": str(VOICE_SOURCE)}}
    )
    configure(cell, only_voice)
    await measured(world, 0, WHATSAPP_SOURCE, 20, 8)
    await measured(world, 0, VOICE_SOURCE, 20, 9)
    rule = TriggerRule("try", "context_use.repetition_rate", "draft", only_voice.params, "crm")
    result = await service(world).dry_run(SPACE_ID, rule)
    assert (result.subjects_evaluated, result.would_fire, result.target_problem) == (3, 1, None)
    assert [s.pseudonym for s in result.sample] == ["voice-agent"]
    assert await firings(world) == []  # a dry run never fires
    await service(world).sweep(T0)
    assert [f.profile_id for f in await firings(world)] == [VOICE_SOURCE]


async def test_dry_run_over_the_last_30_days(world: World, marina: ProfileRecord) -> None:
    quiet = world.identity.add_profile(handle())
    owe(world, marina, 1)
    world.sessions.active = [marina.profile_id, quiet.profile_id]
    result = await service(world).dry_run(SPACE_ID, TriggerRule("p", "promise.overdue", "draft", {}, "crm"))
    assert (result.subjects_evaluated, result.would_fire, result.target_problem) == (2, 1, None)
    assert result.sample[0].pseudonym == marina.pseudonym
    assert await firings(world) == []  # a dry-run never fires
    with pytest.raises(InvalidInput):
        await service(world).dry_run(SPACE_ID, TriggerRule("p", "nope", "draft", {}, "crm"))


async def test_a_siem_stream_is_never_a_warning_target(
    world: World, cell: Cell, marina: ProfileRecord
) -> None:
    siem = MARKETING.model_copy(update={"endpoint_id": "siem", "format": "ocsf", "source_id": BILLING_SOURCE})
    aimed = OVERDUE.model_copy(update={"rule_id": "to_siem", "endpoint_id": "siem"})
    cell.config.put(config_with(trigger_rules=[aimed], webhook_endpoints=[CRM, MARKETING, siem]))
    owe(world, marina, 1)
    world.sessions.active = [marina.profile_id]
    rule = TriggerRule("to_siem", "promise.overdue", "draft", {}, "siem")
    assert (await service(world).dry_run(SPACE_ID, rule)).target_problem == "endpoint_is_siem_stream"
    await evaluate(world, marina)
    assert await firings(world) == []


async def test_firings_are_listed_newest_first_with_a_cursor(world: World) -> None:
    async with world.cell.uow(SPACE_ID) as uow:
        for i in range(3):
            ref = EvidenceRef("open_item", uuid7(), uuid7())
            await uow.triggers.add_firing(
                Firing(uuid7(), "r", "v1", uuid7(), f"w{i}", b"k", (ref,), 1, T0 + timedelta(minutes=i))
            )
    first = await service(world).list_firings(SPACE_ID, limit=2)
    second = await service(world).list_firings(SPACE_ID, limit=2, cursor=first.next_cursor)
    assert [f.window_key for f in first.items] == ["w2", "w1"]
    assert first.next_cursor
    assert [f.window_key for f in second.items] == ["w0"]
    assert second.next_cursor is None
