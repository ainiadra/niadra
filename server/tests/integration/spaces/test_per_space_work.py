"""Spec 12, tests 7 to 10: the per-space loops, receipts and watermarks, dedupe rotation and partition
upkeep, each against real space databases."""

import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import (
    DeterministicDecider,
    DeterministicModels,
    FixedClock,
    InMemoryObjectStore,
)
from niadra.adapters.outbound.postgres.probe import PgPipelineProbe
from niadra.app.audit import AuditService
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService
from niadra.app.pipeline import PipelineService
from niadra.app.sessions import SessionService
from niadra.app.space_loops import LoopSpec, RelayStep, SpaceLoopSupervisor
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.schedule import PARTITIONED_BY_WEEK
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import Episode, SessionRecord
from niadra.domain.spaces.directory import db_name_for
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.testing.cell import sample_config
from tests.integration.conftest import (
    CELL_DB,
    SPACE_A,
    SPACE_B,
    CellStack,
    CloningUnitOfWork,
    PgServer,
    owner_sql,
)

NOW = datetime.now(UTC)


class FakeTime:
    def __init__(self, passes: int) -> None:
        self.waits: dict[UUID, list[float]] = defaultdict(list)
        self.passes = passes
        self.current: UUID | None = None

    async def sleep(self, stop: asyncio.Event, seconds: float) -> None:
        assert self.current is not None
        self.waits[self.current].append(seconds)
        if len(self.waits[self.current]) >= self.passes:
            stop.set()
        await asyncio.sleep(0)


class Spaces:
    def serving_spaces(self) -> list[UUID]:
        return [SPACE_A, SPACE_B]


async def test_both_outboxes_are_relayed_and_the_idle_one_backs_off(pg_stack: CellStack) -> None:
    # The relay takes rows the writer left behind for two seconds; the database stamps them with its clock.
    pipeline = PipelineService(
        pg_stack.uow, pg_stack.bus, FixedClock(datetime.now(UTC) + timedelta(seconds=5))
    )
    for space in (SPACE_A, SPACE_B):
        async with pg_stack.uow(space) as uow:
            await uow.pipeline.enqueue([Task(TaskName.EXTRACT, space, {"from": str(space)})])

    time = FakeTime(passes=6)

    async def relay(space: UUID) -> int:
        time.current = space
        if space == SPACE_A:  # A keeps writing: something is waiting at every pass
            async with pg_stack.uow(space) as uow:
                await uow.pipeline.schedule(
                    Task(TaskName.EXTRACT, space), NOW - timedelta(seconds=1), "retry"
                )
        return await pipeline.relay_once(space)

    spec = LoopSpec("relay", 0.5, 15.0, relay)
    supervisor = SpaceLoopSupervisor(Spaces(), [spec], sleep=time.sleep)
    for space in (SPACE_A, SPACE_B):
        await supervisor.space_loop(space, spec, asyncio.Event())

    published = {t.payload.get("from") for t in pg_stack.bus.published if t.payload.get("from")}
    assert published == {str(SPACE_A), str(SPACE_B)}
    assert time.waits[SPACE_A] == [0.5] * 6
    assert time.waits[SPACE_B] == [0.5, 1.0, 2.0, 4.0, 8.0, 15.0]  # found its row once, then idle
    probe = PgPipelineProbe(pg_stack.registry)
    for space in (SPACE_A, SPACE_B):
        assert (await probe.due(space)).outbox_oldest is None


async def test_the_relay_probe_reports_each_space_s_lag_and_intake(pg_stack: CellStack) -> None:
    lags: dict[UUID, float] = {}
    intake: dict[UUID, int] = {}

    class Telemetry:
        def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None:
            return None

        def outbox_lag(self, space_id: UUID, seconds: float) -> None:
            lags[space_id] = seconds

        def intake_backlog(self, space_id: UUID, pending: int, oldest_s: float) -> None:
            intake[space_id] = pending

        def forget_space(self, space_id: UUID) -> None:
            return None

    pipeline = PipelineService(
        pg_stack.uow, pg_stack.bus, pg_stack.clock, probe=PgPipelineProbe(pg_stack.registry)
    )
    relay = RelayStep(pipeline, Telemetry())
    for space in (SPACE_A, SPACE_B):
        await relay(space)
    assert set(lags) == {SPACE_A, SPACE_B}
    assert intake == {SPACE_A: 0, SPACE_B: 0}


async def test_the_probe_reads_what_is_due_in_one_statement_with_no_transaction(pg_stack: CellStack) -> None:
    space = SPACE_B
    probe = PgPipelineProbe(pg_stack.registry)
    later = datetime.now(UTC) + timedelta(minutes=5)
    async with pg_stack.uow(space) as uow:
        await uow.pipeline.enqueue([Task(TaskName.EXTRACT, space, {"probe": "1"})])
        await uow.pipeline.schedule(Task(TaskName.MEASURE, space), later, "retry")
    due = await probe.due(space)
    assert due.outbox_oldest is not None
    assert due.scheduled_due is not None
    assert abs((due.scheduled_due - later).total_seconds()) < 0.01
    assert due.intake_pending == 0
    assert not due.relay_due(datetime.now(UTC), timedelta(seconds=2))
    assert due.relay_due(datetime.now(UTC) + timedelta(seconds=3), timedelta(seconds=2))
    async with pg_stack.uow.system(space) as uow:
        await uow.session.execute(text("DELETE FROM outbox WHERE payload->>'probe' = '1'"))
        await uow.session.execute(text("DELETE FROM scheduled_tasks WHERE task = 'measure'"))


async def test_the_idle_sweep_closes_idle_sessions_in_every_space(pg_stack: CellStack) -> None:
    clock = FixedClock(NOW)
    sessions = SessionService(pg_stack.uow, clock, pg_stack.config)
    created = {}
    for space in (SPACE_A, SPACE_B):
        session = SessionRecord(
            session_id=uuid7(),
            kind="conversation",
            source_id=uuid7(),
            channel="whatsapp",
            opened_at=NOW - timedelta(hours=3),
            last_event_at=NOW - timedelta(hours=2),
            conversation_id=f"idle-{uuid4()}",
        )
        async with pg_stack.uow(space) as uow:
            created[space] = await uow.sessions.create_session(space, session)
    for space in (SPACE_A, SPACE_B):
        assert await sessions.sweep_idle(space) >= 1
        async with pg_stack.uow(space) as uow:
            [row] = (
                await uow.session.execute(
                    text("SELECT close_reason FROM sessions WHERE session_id = :s"),
                    {"s": created[space].session_id},
                )
            ).all()
            closes = await uow.session.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE task = 'close_session' AND payload->>'session_id' = :s"
                ),
                {"s": str(created[space].session_id)},
            )
        assert row.close_reason == "idle"
        assert closes.scalar_one() == 1


def draft(space: UUID, producer: str, seq: int, at: datetime) -> ReceiptDraft:
    return ReceiptDraft(
        receipt_id=uuid7(int(at.timestamp() * 1000)),
        space_id=space,
        producer_id=producer,
        producer_seq=seq,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.PACK,
        audience=Audience.CUSTOMER_AGENT,
        read_at=at,
    )


async def test_receipts_land_in_their_own_databases_and_the_watermark_in_the_cell(
    server: PgServer, pg_stack: CellStack
) -> None:
    day = date(2026, 9, 20)
    at = datetime(2026, 9, 20, 12, tzinfo=UTC)
    producer = f"read:{uuid4()}"
    clock = FixedClock(at + timedelta(days=2))
    objects = InMemoryObjectStore()
    audit = AuditService(pg_stack.uow, None, objects, clock)  # type: ignore[arg-type]
    batch = [
        draft(SPACE_A, producer, 1, at),
        draft(SPACE_B, producer, 2, at),
        draft(SPACE_A, producer, 3, at),
    ]

    assert await audit.write(batch, [ProducerMark(producer, 3, at + timedelta(days=1, hours=1))]) == 3

    for space, count in ((SPACE_A, 2), (SPACE_B, 1)):
        ids = await owner_sql(
            server, db_name_for(space), "SELECT receipt_id FROM receipts WHERE producer_id = :p", p=producer
        )
        assert len(ids) == count
    marks = await owner_sql(
        server, CELL_DB, "SELECT contiguous_seq FROM receipt_watermarks WHERE producer_id = :p", p=producer
    )
    assert marks == [(3,)]
    has_marks = "SELECT to_regclass('public.receipt_watermarks') IS NOT NULL"
    assert await owner_sql(server, db_name_for(SPACE_A), has_marks) == [(False,)]

    # The anchor of A reads the cell's watermark, then writes the root in A's database only.
    async with pg_stack.uow.cell() as uow:
        await uow.session.execute(
            text("DELETE FROM receipt_watermarks WHERE producer_id <> :p"), {"p": producer}
        )
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, SPACE_A, {"day": day.isoformat()}))
    roots = "SELECT count(*) FROM audit_roots WHERE day = :d"
    assert await owner_sql(server, db_name_for(SPACE_A), roots, d=day) == [(1,)]
    assert await owner_sql(server, db_name_for(SPACE_B), roots, d=day) == [(0,)]
    assert f"audit/{SPACE_A}/{day.isoformat()}.json" in objects.objects


async def test_rotating_dedupe_in_one_space_leaves_the_other_alone(pg_uow: CloningUnitOfWork) -> None:
    one, two = uuid4(), uuid4()
    for space in (one, two):
        async with pg_uow(space) as uow:
            await uow.ingest.claim_keys(space, [("k", uuid7())], NOW)
    async with pg_uow.system(one) as uow:
        # As `niadra_system`: the rotation truncates the retired table, which the application role may not.
        assert await uow.ingest.rotate_dedupe(NOW + timedelta(days=30), timedelta(0)) == 1
        assert (await uow.session.execute(text("SELECT current_user"))).scalar_one() == "niadra_system"
    async with pg_uow(two) as uow:
        assert (await uow.ingest.claim_keys(two, [], NOW))[0] == 0
        with pytest.raises(Exception, match="permission denied"):
            await uow.session.execute(text("TRUNCATE event_keys_2"))
    async with pg_uow(one) as uow:
        assert (await uow.ingest.claim_keys(one, [], NOW))[0] == 1


async def test_partition_upkeep_runs_as_the_system_role_through_the_definer_functions(
    server: PgServer, pg_uow: CloningUnitOfWork
) -> None:
    space = uuid4()
    later = datetime(2031, 1, 6, tzinfo=UTC)
    service = PipelineService(pg_uow, None, FixedClock(later))  # type: ignore[arg-type]
    await service.maintain_partitions(Task(TaskName.PARTITION_MAINTENANCE, space))
    week = int(later.timestamp() * 1000) // (7 * 24 * 3600 * 1000)
    owners = await owner_sql(
        server,
        db_name_for(space),
        "SELECT relname, pg_get_userbyid(relowner) FROM pg_class WHERE relname LIKE :n ORDER BY 1",
        n=f"%_w{week}",
    )
    assert owners == [(f"{p}_w{week}", "niadra_owner") for p in sorted(PARTITIONED_BY_WEEK)]
    # The application role may not create partitions: only the maintenance role runs the functions.
    async with pg_uow(space) as uow:
        with pytest.raises(Exception, match="permission denied for function ensure_week_partition"):
            await uow.session.execute(text("SELECT ensure_week_partition('events', 0)"))
    assert sample_config(space).space_id == space


async def test_partition_upkeep_moves_the_rows_the_default_partition_caught_into_their_week(
    server: PgServer, pg_uow: CloningUnitOfWork
) -> None:
    # A week's partition cannot be created while the default holds rows of that week; the upkeep now
    # detaches the default, creates the partition, moves those rows and attaches the default back.
    space = uuid4()
    later = datetime(2031, 1, 6, tzinfo=UTC)
    week_ms = 7 * 24 * 3600 * 1000
    week = int(later.timestamp() * 1000) // week_ms
    await pg_uow.ensure(space)
    name = db_name_for(space)
    for at in (week * week_ms + 1, week * week_ms + 2, (week + 20) * week_ms):
        await owner_sql(
            server,
            name,
            "INSERT INTO events (space_id, event_id, idempotency_key, kind, source_id, channel, speaker, "
            "occurred_at, received_at, dek_version) VALUES (:s, uuid7_floor(:at), :k, 'message', :src, "
            "'voice', 'customer', now(), now(), 1)",
            s=space,
            at=at,
            k=f"k{at}",
            src=uuid4(),
        )
    service = PipelineService(pg_uow, None, FixedClock(later))  # type: ignore[arg-type]

    await service.maintain_partitions(Task(TaskName.PARTITION_MAINTENANCE, space))

    placed = await owner_sql(
        server, name, "SELECT tableoid::regclass::text, count(*) FROM events GROUP BY 1 ORDER BY 1"
    )
    # The row of a week that has no partition yet stays in the default.
    assert placed == [("events_default", 1), (f"events_w{week}", 2)]
    shape = await owner_sql(
        server,
        name,
        "SELECT (SELECT partdefid::regclass::text FROM pg_partitioned_table "
        "WHERE partrelid = 'events'::regclass), "
        "has_table_privilege('niadra_app', :part, 'DELETE'), "
        "has_table_privilege('niadra_app', :part, 'INSERT'), "
        "(SELECT count(*) FROM pg_trigger WHERE tgname = 'events_change_seq'), "
        "(SELECT count(*) FROM pg_indexes WHERE tablename = :part)",
        part=f"events_w{week}",
    )
    # One trigger on the parent and one on each partition; the new one has every index of the parent.
    partitions = await owner_sql(
        server, name, "SELECT 1 FROM pg_inherits WHERE inhparent = 'events'::regclass"
    )
    indexes = await owner_sql(server, name, "SELECT 1 FROM pg_indexes WHERE tablename = 'events'")
    assert shape == [("events_default", False, True, 1 + len(partitions), len(indexes))]
    # Run again, it has nothing left to do.
    await service.maintain_partitions(Task(TaskName.PARTITION_MAINTENANCE, space))


async def test_the_vector_backfill_fills_each_space_in_its_own_database(pg_stack: CellStack) -> None:
    # The loops run the backfill per space database; a space's vectors never land in another's.
    clock = FixedClock(NOW)
    models = DeterministicModels()
    memory = MemoryService(
        pg_stack.uow,
        models,
        DecideService(pg_stack.uow, models, clock, decider=DeterministicDecider()),
        clock,
        pg_stack.config,
    )
    episodes = {}
    for space in (SPACE_A, SPACE_B):
        origin = uuid7()
        episode = Episode(
            uuid7(), 1, origin, uuid7(), "whatsapp", uuid4(), f"conversa em {space}", "support", "invoice",
            "billing", "resolved", NOW, NOW,
        )  # fmt: skip
        async with pg_stack.uow(space) as uow:
            await uow.memory.add_episode_version(episode)
        episodes[space] = episode
    while await memory.backfill_vectors(SPACE_A):
        pass
    for space in (SPACE_A, SPACE_B):
        episode = episodes[space]
        rows = await owner_sql(
            pg_stack.test,
            db_name_for(space),
            "SELECT embedding_model FROM episodes WHERE episode_id = :e",
            e=episode.episode_id,
        )
        assert rows == [("hash-64",)] if space == SPACE_A else [(None,)]
    while await memory.backfill_vectors(SPACE_B):
        pass
    async with pg_stack.uow(SPACE_B) as uow:
        vectors = await uow.memory.vectors_for([episodes[SPACE_B].origin_handle_id])
    assert [v.item_id for v in vectors] == [episodes[SPACE_B].episode_id]
