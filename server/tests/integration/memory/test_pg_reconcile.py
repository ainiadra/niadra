from datetime import UTC, datetime, timedelta

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels, FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    FactCommand,
    FactOp,
    RunStatus,
    ValueDraft,
)
from niadra.domain.extract.rules import EXTRACTOR_VERSION, PROMPT_VERSION
from niadra.domain.memory.values import ValueOrigin, ValueType, current
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, Speaker
from niadra.testing.cell import SPACE_ID, PatchedUnitOfWorkFactory, sample_config
from tests.unit.extract.world import FakeAudit, FakeEvents, FakeIdentity, FakeObjects, FakeSessions, World

NOW = datetime(2026, 9, 22, 17, tzinfo=UTC)
KEY = f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.s1"


async def test_out_of_order_sessions_settle_the_same_way_in_postgres(pg_uow: PgUnitOfWorkFactory) -> None:
    world = World(now=NOW)
    uow = PatchedUnitOfWorkFactory(
        pg_uow,  # type: ignore[arg-type]
        sessions=FakeSessions(world),
        ingest=FakeEvents(world),
        identity=FakeIdentity(world),
        audit=FakeAudit(world),
        objects=FakeObjects(world),
    )
    clock = FixedClock(NOW)
    memory = MemoryService(
        uow,
        DeterministicModels(),
        DecideService(uow, DeterministicModels(), clock, decider=DeterministicDecider()),
        clock,
        StaticConfig(sample_config()),
    )
    profile = world.customer()
    older = world.session(profile, [(Speaker.CUSTOMER, "Ana")], start=NOW - timedelta(days=5))
    newer = world.session(profile, [(Speaker.CUSTOMER, "Bia")], start=NOW - timedelta(days=1))
    for session in (newer, older):
        said = world.session_events(session)[0]
        command = FactCommand(
            FactOp.ADD,
            "preferred_name",
            (said.event_id,),
            value=said.text or "",
            category="contact",
            speaker=Speaker.CUSTOMER,
            valid_at=said.occurred_at,
        )
        # The newer run names its matter (17, 6.2); the older one is from before matters existed.
        matter = ("invoice_contest", "contestação da fatura") if session is newer else (None, None)
        draft = EpisodeDraft(
            "resumo", "request", "invoice", "resolved", matter=matter[0], matter_label=matter[1]
        )
        result = ExtractionResult(draft, facts=(command,))
        async with pg_uow(SPACE_ID) as tx:
            await tx.extract.save_run(
                ExtractionRun(session.session_id, KEY, RunStatus.OK, NOW, result=result)
            )
        payload = {"session_id": str(session.session_id), "versions_key": KEY}
        task = Task(TaskName.RECONCILE, SPACE_ID, payload, profile_id=profile.profile_id)
        await memory.reconcile(task)
        await memory.reconcile(task)
    async with pg_uow(SPACE_ID) as tx:
        facts = {f.value: f for f in await tx.memory.facts_for(profile.handle_ids, tuple(FactStatus))}
        episodes = await tx.memory.episodes_for(profile.handle_ids)
    assert facts["Bia"].status is FactStatus.ACTIVE
    assert facts["Ana"].status is FactStatus.EXPIRED
    assert facts["Ana"].invalid_at == world.session_events(newer)[0].occurred_at
    assert [f.times_seen for f in facts.values()] == [1, 1]
    assert [e.normalized_category for e in episodes] == ["billing", "billing"]
    assert [(e.matter, e.matter_label) for e in episodes] == [
        ("invoice_contest", "contestação da fatura"),
        (None, None),
    ]
    assert await memory.drift(SPACE_ID, profile.profile_id) == 0


async def test_settled_values_round_trip_the_run_and_leave_the_latest_current(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    world = World(now=NOW)
    uow = PatchedUnitOfWorkFactory(
        pg_uow,  # type: ignore[arg-type]
        sessions=FakeSessions(world),
        ingest=FakeEvents(world),
        identity=FakeIdentity(world),
        audit=FakeAudit(world),
        objects=FakeObjects(world),
    )
    clock = FixedClock(NOW)
    memory = MemoryService(
        uow,
        DeterministicModels(),
        DecideService(uow, DeterministicModels(), clock, decider=DeterministicDecider()),
        clock,
        StaticConfig(sample_config()),
    )
    profile = world.customer()
    older = world.session(
        profile,
        [(Speaker.CUSTOMER, "O CEP de coleta é 88221-615.")],
        channel="email",
        start=NOW - timedelta(days=39),
    )
    newer = world.session(
        profile, [(Speaker.CUSTOMER, "Agora o CEP de coleta é 45778-204.")], start=NOW - timedelta(days=4)
    )
    for session in (newer, older):
        said = world.session_events(session)[0]
        cep = (said.text or "").split()[-1].rstrip(".")
        result = ExtractionResult(
            EpisodeDraft("resumo", "request", "invoice", "resolved"),
            values=(ValueDraft(ValueType.POSTAL_CODE, cep, (said.event_id,)),),
        )
        async with pg_uow(SPACE_ID) as tx:
            await tx.extract.save_run(
                ExtractionRun(session.session_id, KEY, RunStatus.OK, NOW, result=result)
            )
        payload = {"session_id": str(session.session_id), "versions_key": KEY}
        task = Task(TaskName.RECONCILE, SPACE_ID, payload, profile_id=profile.profile_id)
        await memory.reconcile(task)
        await memory.reconcile(task)
    async with pg_uow(SPACE_ID) as tx:
        found = await tx.memory.values_for(profile.handle_ids)
    assert [(v.raw, v.origin, v.channel) for v in found] == [
        ("45778-204", ValueOrigin.EXTRACTION, "whatsapp"),
        ("88221-615", ValueOrigin.EXTRACTION, "email"),
    ]
    now_holding = current(found, ValueType.POSTAL_CODE)
    assert now_holding is not None
    assert now_holding.raw == "45778-204"
