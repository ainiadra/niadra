"""Undoing a correction in PostgreSQL (WP-G): the row comes back, the correction is retracted, both
supersession edges hold side by side, and `fact.restored` leaves in the same transaction."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels, FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService
from niadra.config.models import WebhookEndpoint
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import governance_source
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    FactCommand,
    FactOp,
    RunStatus,
)
from niadra.domain.extract.rules import EXTRACTOR_VERSION, PROMPT_VERSION
from niadra.domain.memory.facts import ref
from niadra.domain.records import EventRecord, ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, FactStatus, RelationType, Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, PatchedUnitOfWorkFactory, sample_config
from tests.unit.extract.world import FakeAudit, FakeEvents, FakeIdentity, FakeObjects, FakeSessions, World

NOW = datetime(2026, 9, 22, 17, tzinfo=UTC)
# A space of its own: the fixed test space's database is shared, and other tests count its deliveries.
SPACE = uuid7()
KEY = f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.s1"


def feedback(world: World, profile: ProfileRecord, action: str, fields: dict[str, str], at: datetime) -> Task:
    event = EventRecord(
        uuid7(),
        EventKind.SYSTEM_EVENT,
        governance_source(SPACE),
        "feedback",
        Speaker.SYSTEM,
        at,
        at,
        profile.handle_ids,
        canonical_type=f"feedback.{action}",
        fields={"action": action, **fields},
    )
    world.events.append(event)
    return Task(TaskName.RECONCILE, SPACE, {"event_id": str(event.event_id)}, profile_id=profile.profile_id)


async def test_a_correction_undone_in_postgres(pg_uow: PgUnitOfWorkFactory) -> None:
    world = World(now=NOW)
    uow = PatchedUnitOfWorkFactory(
        pg_uow,  # type: ignore[arg-type]
        sessions=FakeSessions(world),
        ingest=FakeEvents(world),
        identity=FakeIdentity(world),
        audit=FakeAudit(world),
        objects=FakeObjects(world),
    )
    config = sample_config(SPACE)
    endpoint = WebhookEndpoint(
        endpoint_id="agent-hooks",
        url="https://agent.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["fact.restored"],
        secret_refs=["spaces/agent/webhook/agent-hooks"],
    )
    clock = FixedClock(NOW)
    memory = MemoryService(
        uow,
        DeterministicModels(),
        DecideService(uow, DeterministicModels(), clock, decider=DeterministicDecider()),
        clock,
        StaticConfig(config.model_copy(update={"webhook_endpoints": [endpoint]})),
    )
    profile = world.customer()
    session = world.session(profile, [(Speaker.CUSTOMER, "Ana")], start=NOW - timedelta(days=5))
    said = world.session_events(session)[0]
    command = FactCommand(
        FactOp.ADD,
        "preferred_name",
        (said.event_id,),
        value="Ana",
        category="contact",
        valid_at=said.occurred_at,
    )
    result = ExtractionResult(EpisodeDraft("resumo", "request", "invoice", "resolved"), facts=(command,))
    async with pg_uow(SPACE) as tx:
        await tx.extract.save_run(ExtractionRun(session.session_id, KEY, RunStatus.OK, NOW, result=result))
    payload = {"session_id": str(session.session_id), "versions_key": KEY}
    await memory.reconcile(Task(TaskName.RECONCILE, SPACE, payload, profile_id=profile.profile_id))
    async with pg_uow(SPACE) as tx:
        [ana] = await tx.memory.facts_for(profile.handle_ids)

    fix = {"fact_id": str(ana.fact_id), "value": "Bia"}
    await memory.reconcile(feedback(world, profile, "correct_fact", fix, NOW - timedelta(hours=2)))
    await memory.reconcile(feedback(world, profile, "restore_fact", {"fact_id": str(ana.fact_id)}, NOW))

    async with pg_uow(SPACE) as tx:
        facts = {f.value: f for f in await tx.memory.facts_for(profile.handle_ids, tuple(FactStatus))}
        edges = {(r.a, r.b, r.type) for r in await tx.memory.relations_for(profile.handle_ids)}
        sent = (
            (
                await tx.session.execute(
                    text("SELECT body FROM webhook_deliveries WHERE event_type = 'fact.restored'")
                )
            )
            .scalars()
            .all()
        )
    assert {v: f.status for v, f in facts.items()} == {"Ana": FactStatus.ACTIVE, "Bia": FactStatus.RETRACTED}
    assert facts["Ana"].fact_id == ana.fact_id
    assert facts["Ana"].last_confirmed_at == NOW
    anna, bia = ref(facts["Ana"]), ref(facts["Bia"])
    assert {(bia, anna, RelationType.SUPERSEDES), (anna, bia, RelationType.SUPERSEDES)} <= edges
    [body] = sent
    assert body["data"]["retracted"] == [str(facts["Bia"].fact_id)]
    assert "Ana" not in str(body)
