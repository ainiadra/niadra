"""MeasureService over the in-memory cell, with the other modules' ports faked (19.3, 19.7)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.app.measure import MAX_WAITS, MeasureService
from niadra.config.models import SourceConfig
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.extract.result import (
    AgentClaim,
    AgentQuestion,
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    QuestionType,
    RunStatus,
)
from niadra.domain.measure.model import MEASURE_RULES, TransferState
from niadra.domain.records import EventRecord, Fact, ProfileRecord, SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    Audience,
    Environment,
    EventKind,
    FactStatus,
    Scope,
    Sensitivity,
    Speaker,
    SubjectKind,
)
from niadra.ports.infra import NoTelemetry
from niadra.testing.cell import (
    SPACE_ID,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    PatchedUnitOfWorkFactory,
    principal,
    sample_config,
)
from tests.unit.measure.fakes import (
    FakeAudit,
    FakeCompile,
    FakeExtract,
    FakeIdentity,
    FakeIngest,
    FakeMemory,
    FakeObjects,
    FakeSessions,
)

T0 = datetime(2026, 9, 22, 14, 7, tzinfo=UTC)
HUMAN_DESK = UUID("0192f5a0-0000-7000-8000-00000000c009")
PROFILE = UUID("0192f5a0-0000-7000-8000-0000000000e1")
HANDLE = UUID("0192f5a0-0000-7000-8000-0000000000f1")
FACT_ID = UUID("0192f5a0-0000-7000-8000-0000000000a1")


class World:
    def __init__(self, cell: Cell) -> None:
        config = sample_config()
        human = SourceConfig(source_id=HUMAN_DESK, name="desk", channel="voice", audience=Audience.HUMAN)
        cell.config.put(config.model_copy(update={"sources": [*config.sources, human]}))
        self.cell = cell
        self.sessions = FakeSessions()
        self.audit = FakeAudit(mark=T0 + timedelta(days=10))
        self.extract = FakeExtract()
        self.compile = FakeCompile()
        self.ingest = FakeIngest()
        self.identity = FakeIdentity(
            {PROFILE: ProfileRecord(PROFILE, SubjectKind.PERSON, "p", 1, 1, (HANDLE,))}
        )
        self.memory = FakeMemory([_fact(FACT_ID, "preferred_name", "Marina")])
        self.objects = FakeObjects()
        self.uow = PatchedUnitOfWorkFactory(
            cell.uow,
            sessions=self.sessions,
            audit=self.audit,
            # The receipt watermark is the cell's, read from the cell database.
            watermarks=self.audit,
            extract=self.extract,
            compile=self.compile,
            ingest=self.ingest,
            identity=self.identity,
            memory=self.memory,
            objects=self.objects,
        )
        self.ready: list[float] = []
        self.service = MeasureService(self.uow, cell.clock, cell.config, telemetry=_Ready(self.ready))
        self.compile.manifests["m1"] = {
            "view": "voice",
            "items": [
                {"id": str(FACT_ID), "kind": "fact", "predicate": "preferred_name", "category": "contact"},
                {"id": "oi-visit", "kind": "open_item", "predicate": "technical_visit"},
            ],
            "excluded": [{"id": "f-doc", "kind": "fact", "predicate": "document", "reason": "verification"}],
        }

    def session(
        self,
        conversation: str,
        start: datetime,
        *,
        minutes: int = 10,
        initiated_by: str = "customer",
        channel: str = "voice",
        source: UUID = VOICE_SOURCE,
    ) -> SessionRecord:
        record = SessionRecord(
            session_id=uuid7(),
            kind="conversation",
            source_id=source,
            channel=channel,
            opened_at=start,
            last_event_at=start + timedelta(minutes=minutes),
            conversation_id=conversation,
            profile_id=PROFILE,
            closed_at=start + timedelta(minutes=minutes),
            initiated_by=initiated_by,
        )
        self.sessions.sessions[record.session_id] = record
        return record

    def read(
        self,
        conversation: str,
        at: datetime,
        *,
        manifest: str = "m1",
        source: UUID = VOICE_SOURCE,
        kind: str = "read",
        group: str | None = None,
        profile: UUID | None = None,
    ) -> None:
        self.audit.receipts.append(
            {
                "conversation_id": conversation,
                "kind": kind,
                "source_id": source,
                "manifest_hash": manifest,
                "read_at": at,
                "experiment_group": group,
                "profile_id": profile,
            }
        )

    def turn(
        self,
        conversation: str,
        at: datetime,
        *,
        speaker: Speaker = Speaker.AI_AGENT,
        source: UUID = VOICE_SOURCE,
        stt: float | None = 0.9,
        injected_at: datetime | None = None,
    ) -> UUID:
        event = EventRecord(
            event_id=uuid7(),
            kind=EventKind.MESSAGE,
            source_id=source,
            channel="voice",
            speaker=speaker,
            occurred_at=at,
            received_at=at,
            handle_ids=(HANDLE,),
            text="...",
            conversation_id=conversation,
            stt_confidence=stt,
            # What ingestion keeps from the SDK's `context_stamp` on the agent's turn (5.18).
            content_meta={"context_stamp": {"injected_at": injected_at.isoformat()}} if injected_at else None,
        )
        self.ingest.events.append(event)
        return event.event_id

    def handoff(self, conversation: str, at: datetime, target: UUID | None = HUMAN_DESK) -> None:
        self.ingest.events.append(
            EventRecord(
                event_id=uuid7(),
                kind=EventKind.SYSTEM_EVENT,
                source_id=VOICE_SOURCE,
                channel="voice",
                speaker=Speaker.SYSTEM,
                occurred_at=at,
                received_at=at,
                handle_ids=(HANDLE,),
                conversation_id=conversation,
                canonical_type="handoff",
                fields={"target_source": str(target) if target else None},
            )
        )

    def extracted(
        self,
        session: SessionRecord,
        *,
        questions=(),
        claims=(),
        category="technical_visit",
        versions="x1",
        status=RunStatus.OK,
    ) -> None:
        result = ExtractionResult(
            episode=EpisodeDraft("resumo", "support", category, "resolved"),
            agent_questions=tuple(
                AgentQuestion(QuestionType(q["type"]), UUID(q["event_id"]), q.get("predicate"))
                for q in questions
            ),
            agent_claims=tuple(
                AgentClaim(UUID(c["event_id"]), c["value"], c.get("predicate")) for c in claims
            ),
        )
        self.extract.runs[session.session_id] = ExtractionRun(
            session.session_id, versions, status, T0, result=result
        )

    async def measure(self, session: SessionRecord, **payload) -> None:
        await self.service.measure(
            Task(TaskName.MEASURE, SPACE_ID, {"session_id": str(session.session_id), **payload})
        )

    def measure_repo(self) -> InMemoryMeasureRepository:
        return InMemoryMeasureRepository(self.cell.uow.store)

    async def daily_total(self, **filters) -> dict[str, int]:
        rows = await self.measure_repo().daily(date(2026, 1, 1), date(2026, 12, 31))
        total: dict[str, int] = {}
        for row in rows:
            if all(getattr(row.key, k) == v for k, v in filters.items()):
                for name, value in row.counters.as_dict().items():
                    total[name] = total.get(name, 0) + value
        return total

    def outbox(self, name: TaskName) -> list[Task]:
        return [t for t in self.cell.uow.store.module("pipeline").get("outbox", []) if t.name == name]

    def scheduled(self, name: TaskName) -> list[tuple[Task, datetime, str]]:
        return [s for s in self.cell.uow.store.module("pipeline").get("scheduled", []) if s[0].name == name]


def _fact(fact_id: UUID, predicate: str, value: str) -> Fact:
    return Fact(
        fact_id=fact_id,
        origin_handle_id=HANDLE,
        predicate=predicate,
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.9,
        valid_at=T0,
        created_at=T0,
        status=FactStatus.ACTIVE,
        last_confirmed_at=T0,
    )


@pytest.fixture
def world(cell: Cell) -> World:
    return World(cell)


def standard_call(world: World) -> SessionRecord:
    """The call of 8.6: the agent asks the name again, gets it wrong once and cites the visit."""
    session = world.session("call-1", T0)
    world.read("call-1", T0 + timedelta(seconds=30))
    world.read("call-1", T0 + timedelta(minutes=2))
    asked = world.turn("call-1", T0 + timedelta(minutes=1))
    said = world.turn("call-1", T0 + timedelta(minutes=3))
    world.extracted(
        session,
        questions=[{"type": "asks_data", "predicate": "preferred_name", "event_id": str(asked)}],
        claims=[
            {"predicate": "preferred_name", "value": "Mariana", "event_id": str(said)},
            {"predicate": "technical_visit", "value": "remarcada", "event_id": str(said)},
        ],
    )
    return session


async def test_measure_writes_one_row_per_manifest_and_source(world: World):
    session = standard_call(world)
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert (row.questions, row.repeated, row.used, row.contradicted) == (1, 1, 1, 1)
    assert row.measure_version == f"{MEASURE_RULES}/x1"
    assert row.not_measured_reason is None
    assert not row.late
    total = await world.daily_total()
    assert total["deliveries"] == 1
    assert total["sessions"] == 1
    assert total["deliveries_used"] == 1
    assert total["repeated"] == 1
    assert total["contradicted"] == 1


async def test_a_session_without_an_identified_customer_is_measured_by_the_profile_read(world: World):
    # The session's own events named nobody, but the agent read one customer's context.
    session = standard_call(world)
    world.sessions.sessions[session.session_id] = replace(session, profile_id=None)
    for receipt in world.audit.receipts:
        receipt["profile_id"] = PROFILE
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.profile_id == PROFILE
    assert (row.questions, row.repeated, row.used, row.contradicted) == (1, 1, 1, 1)


async def test_a_session_whose_reads_name_nobody_has_no_rows(world: World):
    session = standard_call(world)
    world.sessions.sessions[session.session_id] = replace(session, profile_id=None)
    await world.measure(session)
    assert await world.measure_repo().rows_for_session(session.session_id) == []
    assert (await world.daily_total())["sessions"] == 1


async def test_measure_is_idempotent(world: World):
    session = standard_call(world)
    await world.measure(session)
    await world.measure(session)
    total = await world.daily_total()
    assert (total["deliveries"], total["sessions"], total["repeated"]) == (1, 1, 1)


async def test_measure_asks_for_review_and_schedules_the_transfer_phase(world: World):
    session = standard_call(world)
    world.handoff("call-1", T0 + timedelta(minutes=9))
    await world.measure(session)

    [review] = world.outbox(TaskName.SAMPLE_REVIEW)
    assert review.payload == {"session_id": str(session.session_id), "measure_signal": True}
    [(_, due, _)] = world.scheduled(TaskName.MEASURE_TRANSFER)
    assert due == T0 + timedelta(minutes=19)


async def test_measure_waits_for_the_receipt_watermark(world: World):
    session = standard_call(world)
    world.audit.mark = T0 + timedelta(minutes=5)
    await world.measure(session)

    assert await world.measure_repo().rows_for_session(session.session_id) == []
    [(task, due, reason)] = world.scheduled(TaskName.MEASURE)
    assert (task.payload["waits"], reason) == (1, "receipt_watermark")
    assert due == world.cell.clock.now() + timedelta(seconds=30)


async def test_after_waiting_too_long_the_rows_say_receipt_gap(world: World):
    session = standard_call(world)
    world.audit.mark = None
    await world.measure(session, waits=MAX_WAITS)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.not_measured_reason == "receipt_gap"


async def test_a_session_without_extraction_is_not_measured_never_zero(world: World):
    session = standard_call(world)
    del world.extract.runs[session.session_id]
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.not_measured_reason == "not_extracted"
    assert row.measure_version == f"{MEASURE_RULES}/none"
    total = await world.daily_total()
    assert (total["deliveries"], total["not_measured"], total["deliveries_used"], total["questions"]) == (
        1,
        1,
        0,
        0,
    )


async def test_a_new_extractor_version_replaces_the_old_measurement(world: World):
    session = standard_call(world)
    run = world.extract.runs.pop(session.session_id)
    await world.measure(session)
    world.extract.runs[session.session_id] = run
    await world.measure(session)

    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.measure_version == f"{MEASURE_RULES}/x1"
    total = await world.daily_total()
    assert (total["deliveries"], total["not_measured"], total["sessions"], total["repeated"]) == (1, 0, 1, 1)
    # The delay after the session's end counts once; measuring again is not the first answer.
    assert len(world.ready) == 1
    assert world.ready[0] >= 0


async def test_voice_without_agent_text_is_a_partial_transcript(world: World):
    session = world.session("call-2", T0)
    world.read("call-2", T0 + timedelta(seconds=10))
    world.extracted(session)
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.not_measured_reason == "partial_transcript"


async def test_turns_below_the_stt_floor_yield_no_question(world: World):
    session = world.session("call-3", T0)
    world.read("call-3", T0 + timedelta(seconds=10))
    mumbled = world.turn("call-3", T0 + timedelta(minutes=1), stt=0.3)
    world.extracted(
        session, questions=[{"type": "asks_data", "predicate": "preferred_name", "event_id": str(mumbled)}]
    )
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert (row.questions, row.repeated) == (0, 0)


async def test_a_session_without_context_counts_as_coverage(world: World):
    session = world.session("chat-1", T0, channel="whatsapp", source=WHATSAPP_SOURCE)
    world.extracted(session)
    await world.measure(session)

    assert await world.measure_repo().rows_for_session(session.session_id) == []
    measured = await world.measure_repo().get_session(session.session_id)
    assert measured is not None
    assert measured.no_context
    total = await world.daily_total()
    assert (total["sessions"], total["no_context"], total["deliveries"]) == (1, 1, 0)


class TestAgentTasks:
    """An internal agent's task with no message is no conversation (B2): one that read nothing is no
    session without context either, and one that read is measured as any other."""

    def task(self, world: World, task_id: str) -> SessionRecord:
        record = SessionRecord(
            session_id=uuid7(),
            kind="task",
            source_id=VOICE_SOURCE,
            channel="case_system",
            opened_at=T0,
            last_event_at=T0 + timedelta(minutes=2),
            task_id=task_id,
            closed_at=T0 + timedelta(minutes=2),
            initiated_by="company",
        )
        world.sessions.sessions[record.session_id] = record
        world.ingest.events.append(
            EventRecord(
                event_id=uuid7(),
                kind=EventKind.ACTION,
                source_id=VOICE_SOURCE,
                channel="case_system",
                speaker=Speaker.AI_AGENT,
                occurred_at=T0 + timedelta(minutes=1),
                received_at=T0 + timedelta(minutes=1),
                handle_ids=(HANDLE,),
                task_id=task_id,
                canonical_type="analysis.generated",
            )
        )
        world.extracted(record)
        return record

    async def test_a_task_that_only_acted_and_read_nothing_is_not_a_session(self, world: World):
        session = self.task(world, "analysis-1")
        await world.measure(session)

        assert await world.measure_repo().get_session(session.session_id) is None
        assert (await world.daily_total()).get("sessions", 0) == 0
        assert (await world.daily_total()).get("no_context", 0) == 0
        assert world.outbox(TaskName.SAMPLE_REVIEW) == []
        assert world.ready == []

    async def test_a_task_that_read_is_measured(self, world: World):
        session = self.task(world, "drafting-1")
        world.read("drafting-1", T0 + timedelta(seconds=30), profile=PROFILE)
        await world.measure(session)

        measured = await world.measure_repo().get_session(session.session_id)
        assert measured is not None
        assert not measured.no_context
        total = await world.daily_total()
        assert (total["sessions"], total["no_context"], total["deliveries"]) == (1, 0, 1)


async def test_context_read_after_the_first_agent_turn_is_late(world: World):
    session = world.session("call-4", T0)
    world.turn("call-4", T0 + timedelta(seconds=5))
    world.read("call-4", T0 + timedelta(seconds=40))
    world.extracted(session)
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert row.late
    assert (await world.daily_total())["late_deliveries"] == 1


async def test_the_sdk_stamp_decides_late_context_over_the_receipt_time(world: World):
    # The agent read the context at 00:05 but put it in the prompt only at 00:50, after answering at 00:20.
    late = world.session("call-4b", T0)
    world.read("call-4b", T0 + timedelta(seconds=5))
    world.turn("call-4b", T0 + timedelta(seconds=20))
    world.turn("call-4b", T0 + timedelta(seconds=60), injected_at=T0 + timedelta(seconds=50))
    world.extracted(late)
    await world.measure(late)
    [row] = await world.measure_repo().rows_for_session(late.session_id)
    assert row.late
    # Stamped on the first turn itself: on time, although the receipt reached the writer later.
    on_time = world.session("call-4c", T0)
    world.read("call-4c", T0 + timedelta(seconds=40))
    world.turn("call-4c", T0 + timedelta(seconds=20), injected_at=T0 + timedelta(seconds=10))
    world.extracted(on_time)
    await world.measure(on_time)
    [row] = await world.measure_repo().rows_for_session(on_time.session_id)
    assert not row.late


async def test_a_holdout_receipt_puts_the_row_in_the_control_group(world: World):
    session = world.session("call-5", T0)
    world.read("call-5", T0 + timedelta(seconds=5), kind="holdout")
    asked = world.turn("call-5", T0 + timedelta(minutes=1))
    world.extracted(
        session, questions=[{"type": "asks_data", "predicate": "preferred_name", "event_id": str(asked)}]
    )
    await world.measure(session)
    [row] = await world.measure_repo().rows_for_session(session.session_id)
    assert (row.experiment_group, row.repeated) == ("control", 1)


class TestRecontact:
    async def measured(self, world: World, conversation: str, start: datetime, **kw) -> SessionRecord:
        category = kw.pop("category", "technical_visit")
        session = world.session(conversation, start, **kw)
        world.extracted(session, category=category)
        await world.measure(session)
        return session

    async def test_customer_returns_within_72h_in_the_same_category(self, world: World):
        first = await self.measured(world, "c1", T0)
        await self.measured(world, "c2", T0 + timedelta(hours=30))
        marked = await world.measure_repo().get_session(first.session_id)
        assert marked is not None
        assert marked.recontact_scope == "category"
        assert (await world.daily_total(day=T0.date()))["recontacts"] == 1

    async def test_another_category_is_a_general_recontact_outside_the_goal(self, world: World):
        first = await self.measured(world, "c1", T0)
        await self.measured(world, "c2", T0 + timedelta(hours=2), category="invoice")
        marked = await world.measure_repo().get_session(first.session_id)
        assert marked is not None
        assert marked.recontact_scope == "general"
        assert (await world.daily_total())["recontacts"] == 0

    async def test_a_contact_started_by_the_company_or_after_72h_does_not_count(self, world: World):
        first = await self.measured(world, "c1", T0)
        await self.measured(world, "c2", T0 + timedelta(hours=5), initiated_by="company")
        await self.measured(world, "c3", T0 + timedelta(hours=80))
        marked = await world.measure_repo().get_session(first.session_id)
        assert marked is not None
        assert marked.recontact_scope is None

    async def test_the_recontact_is_counted_once(self, world: World):
        await self.measured(world, "c1", T0)
        second = await self.measured(world, "c2", T0 + timedelta(hours=3))
        run = world.extract.runs[second.session_id]
        world.extract.runs[second.session_id] = replace(run, versions_key="x2")
        await world.measure(second)
        assert (await world.daily_total())["recontacts"] == 1


class TestTransfer:
    async def transfer(self, world: World, *, read_at: datetime | None) -> SessionRecord:
        session = standard_call(world)
        world.handoff("call-1", T0 + timedelta(minutes=9))
        if read_at:
            world.read("call-1", read_at, source=HUMAN_DESK, manifest="m-desk")
        await world.measure(session)
        world.cell.clock.at = T0 + timedelta(minutes=30)
        await world.service.measure_transfer(
            Task(TaskName.MEASURE_TRANSFER, SPACE_ID, {"session_id": str(session.session_id)})
        )
        return session

    async def test_the_desk_read_the_context(self, world: World):
        session = await self.transfer(world, read_at=T0 + timedelta(minutes=10))
        measured = await world.measure_repo().get_session(session.session_id)
        assert measured is not None
        assert measured.transfer_state is TransferState.READ
        total = await world.daily_total()
        assert (total["transfers"], total["transfers_unread"]) == (1, 0)

    async def test_transfer_without_read_marks_the_row_and_counts_once(self, world: World):
        session = await self.transfer(world, read_at=None)
        await world.service.measure_transfer(
            Task(TaskName.MEASURE_TRANSFER, SPACE_ID, {"session_id": str(session.session_id)})
        )
        [row] = await world.measure_repo().rows_for_session(session.session_id)
        assert row.transfer_unread is True
        total = await world.daily_total()
        assert (total["transfers"], total["transfers_unread"]) == (1, 1)

    async def test_waits_for_receipts_past_the_window(self, world: World):
        session = standard_call(world)
        world.handoff("call-1", T0 + timedelta(minutes=9))
        await world.measure(session)
        world.audit.mark = T0 + timedelta(minutes=12)
        await world.service.measure_transfer(
            Task(TaskName.MEASURE_TRANSFER, SPACE_ID, {"session_id": str(session.session_id)})
        )
        measured = await world.measure_repo().get_session(session.session_id)
        assert measured is not None
        assert measured.transfer_state is None
        assert [s[2] for s in world.scheduled(TaskName.MEASURE_TRANSFER)][-1] == "receipt_watermark"


class TestReports:
    async def seed(self, world: World) -> None:
        await world.measure(standard_call(world))
        chat = world.session("chat-9", T0, channel="whatsapp", source=WHATSAPP_SOURCE)
        world.read("chat-9", T0 + timedelta(seconds=3), source=WHATSAPP_SOURCE)
        world.extracted(chat)
        await world.measure(chat)

    async def test_aggregates_by_source_with_wilson_intervals(self, world: World):
        await self.seed(world)
        report = await world.service.context_use(_person(), since=T0.date(), until=T0.date())
        by_source = {b.source_id: b for b in report.buckets}
        voice = by_source[VOICE_SOURCE]
        assert voice.usage_rate is not None
        assert voice.usage_rate.value == 1.0
        assert 0 < voice.usage_rate.low < 1.0 == voice.usage_rate.high
        assert voice.repetition_rate is not None
        assert voice.repetition_rate.n == 1
        assert by_source[WHATSAPP_SOURCE].repetition_rate is None

    async def test_a_vendor_key_only_sees_its_own_source(self, world: World):
        await self.seed(world)
        report = await world.service.context_use(principal(WHATSAPP_SOURCE), since=T0.date(), until=T0.date())
        assert {b.source_id for b in report.buckets} == {WHATSAPP_SOURCE}
        with pytest.raises(NotFound):
            await world.service.context_use(
                principal(WHATSAPP_SOURCE), since=T0.date(), until=T0.date(), source_id=VOICE_SOURCE
            )
        with pytest.raises(NotFound):
            await world.service.conversation_detail(principal(WHATSAPP_SOURCE), "call-1")

    async def test_an_analyst_key_sums_every_source_and_opens_only_its_own(self, world: World):
        await self.seed(world)
        scopes = frozenset({Scope.ANALYTICS, Scope.ADMIN})
        analyst = replace(principal(WHATSAPP_SOURCE, scopes=scopes), audience=Audience.ANALYST)
        report = await world.service.context_use(analyst, since=T0.date(), until=T0.date())
        assert {b.source_id for b in report.buckets} == {VOICE_SOURCE, WHATSAPP_SOURCE}
        # One conversation is no aggregate: the key opens and lists its own source's only.
        with pytest.raises(NotFound):
            await world.service.conversation_detail(analyst, "call-1")
        page = await world.service.measured_conversations(
            analyst, source_id=None, since=T0.date(), until=T0.date(), cursor=None, limit=50
        )
        assert {c.source_id for c in page.items} == {WHATSAPP_SOURCE}
        # `analytics` on an agent's key is not an analyst's: its own source, as any agent's key.
        agent = principal(WHATSAPP_SOURCE, scopes=scopes)
        mine = await world.service.context_use(agent, since=T0.date(), until=T0.date())
        assert {b.source_id for b in mine.buckets} == {WHATSAPP_SOURCE}

    async def test_group_by_and_filters(self, world: World):
        await self.seed(world)
        report = await world.service.context_use(
            _person(), since=T0.date(), until=T0.date(), group_by=["channel", "view"], channel="voice"
        )
        assert {(b.channel, b.view) for b in report.buckets} == {("voice", ""), ("voice", "voice")}
        with pytest.raises(InvalidInput):
            await world.service.context_use(
                _person(), since=T0.date(), until=T0.date(), group_by=["profile_id"]
            )

    async def test_conversation_detail_lists_items_and_evidence(self, world: World):
        await self.seed(world)
        detail = await world.service.conversation_detail(_person(), "call-1")
        [entry] = detail.entries
        by_id = {i["id"]: i for i in entry.items}
        assert by_id[str(FACT_ID)]["repeated"]
        assert by_id[str(FACT_ID)]["contradicted"]
        assert by_id["oi-visit"]["used"]


def _person(**kw) -> PersonPrincipal:
    space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
    return replace(PersonPrincipal(space, uuid7(), frozenset({"analyst"})), **kw)


class _Ready(NoTelemetry):
    def __init__(self, seen: list[float]) -> None:
        self._seen = seen

    def measurement_ready(self, seconds: float) -> None:
        self._seen.append(seconds)
