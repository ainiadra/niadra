"""PgTaskStore against PostgreSQL: coalescing, leases and SKIP LOCKED under concurrency (5.5). Each test
runs in a space database of its own; dead tasks live in the cell database."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, InMemoryBus
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.pipeline import PipelineService
from niadra.domain.common.errors import LeaseLost
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.retry import attempt_of, with_attempt
from niadra.domain.pipeline.schedule import PARTITIONED_BY_WEEK, WEEK_MS, ScheduleKey
from niadra.domain.tasks import Task, TaskName

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


@pytest.fixture
async def uow(pg_uow: PgUnitOfWorkFactory) -> PgUnitOfWorkFactory:
    async with pg_uow.cell() as tx:
        await tx.session.execute(text("TRUNCATE dead_tasks"))
    return pg_uow


async def blocked(job: "asyncio.Task[object]", wait_s: float = 0.3) -> bool:
    await asyncio.sleep(wait_s)
    return not job.done()


async def test_outbox_rows_are_taken_once_across_concurrent_relays(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    tasks = [Task(TaskName.EXTRACT, space, payload={"n": i}) for i in range(4)]
    async with uow(space) as tx:
        await tx.pipeline.enqueue(tasks)
        await tx.pipeline.enqueue(tasks[:1])  # same id twice: one row
        assert [t.id for t in tx.pipeline.enqueued] == [t.id for t in tasks]
    later = datetime.now(UTC) + timedelta(seconds=5)
    async with uow.system(space) as first:
        taken_a = await first.pipeline.take_stale_outbox(later, 2)
        async with uow.system(space) as second:
            taken_b = await second.pipeline.take_stale_outbox(later, 10)
        assert {t.id for t in taken_a}.isdisjoint({t.id for t in taken_b})
        assert len(taken_a) + len(taken_b) == 4
        await first.pipeline.delete_outbox([t.id for t in taken_a])
    async with uow.system(space) as tx:
        left = await tx.pipeline.take_stale_outbox(later, 10)
        assert sorted(t.id for t in left) == sorted(t.id for t in taken_b)
        assert left[0].payload in ({"n": 2}, {"n": 3}, {"n": 0}, {"n": 1})
        assert await tx.pipeline.take_stale_outbox(datetime.now(UTC) - timedelta(minutes=1), 10) == []


async def test_a_request_during_the_task_is_never_lost(uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid7(), uuid7()
    compile_task = Task(TaskName.COMPILE, space, profile_id=profile)
    async with uow(space) as tx:
        await tx.pipeline.request_work(compile_task)
        assert len(tx.pipeline.enqueued) == 1
    async with uow(space) as tx:
        seen = await tx.pipeline.begin_work(space, profile, TaskName.COMPILE)
    assert seen == 1

    async def finish() -> bool:
        async with uow(space) as tx:
            return await tx.pipeline.finish_work(space, profile, TaskName.COMPILE, seen)

    async with uow(space) as writer:
        await writer.pipeline.request_work(Task(TaskName.COMPILE, space, profile_id=profile))
        assert writer.pipeline.enqueued == []  # a request was pending
        finishing = asyncio.create_task(finish())
        assert await blocked(finishing)  # waits for the writer's row lock
    assert await finishing is True  # saw the request that committed meanwhile

    async with uow(space) as tx:
        seen = await tx.pipeline.begin_work(space, profile, TaskName.COMPILE)
        assert await tx.pipeline.finish_work(space, profile, TaskName.COMPILE, seen) is False
        await tx.pipeline.request_work(Task(TaskName.COMPILE, space, profile_id=profile))
        assert len(tx.pipeline.enqueued) == 1  # nothing pending: a new request enqueues


async def test_only_one_worker_wins_a_lease_race(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    service = PipelineService(uow, InMemoryBus(), FixedClock(NOW))
    grants = await asyncio.gather(
        *(service.acquire(space, ["profile:p", "profile:q"], f"w{i}", 60) for i in range(4))
    )
    winners = [g for g in grants if g is not None]
    assert len(winners) == 1
    assert winners[0].epochs == {"profile:p": 1, "profile:q": 1}


async def test_an_expired_lease_is_taken_with_a_new_epoch_and_fences_the_old_owner(
    uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid7()
    async with uow(space) as tx:
        old = await tx.pipeline.acquire(space, ["profile:p"], "w1", 60, NOW)
        assert await tx.pipeline.acquire(space, ["profile:p"], "w2", 60, NOW + timedelta(seconds=30)) is None
    async with uow(space) as tx:
        new = await tx.pipeline.acquire(space, ["profile:p"], "w2", 60, NOW + timedelta(seconds=61))
    assert old is not None
    assert new is not None
    assert new.epochs["profile:p"] == 2
    async with uow(space) as tx:
        with pytest.raises(LeaseLost):
            await tx.pipeline.check_fence(space, "profile:p", "w1", 1)
    async with uow(space) as tx:
        assert await tx.pipeline.renew(old, 60, NOW + timedelta(seconds=62)) is False
        assert await tx.pipeline.renew(new, 60, NOW + timedelta(seconds=62)) is True
        await tx.pipeline.release(old)  # a no-op: w1 no longer owns it
        await tx.pipeline.check_fence(space, "profile:p", "w2", 2)


async def test_a_fenced_write_holds_off_a_takeover_until_it_commits(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with uow(space) as tx:
        await tx.pipeline.acquire(space, ["profile:p"], "w1", 60, NOW)

    async def take_over() -> object:
        async with uow(space) as tx:
            return await tx.pipeline.acquire(space, ["profile:p"], "w2", 60, NOW + timedelta(minutes=5))

    async with uow(space) as slow_writer:
        await slow_writer.pipeline.check_fence(space, "profile:p", "w1", 1)
        takeover = asyncio.create_task(take_over())
        assert await blocked(takeover)
    grant = await takeover
    assert grant is not None


async def test_multi_unit_acquire_releases_what_it_took_when_busy(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with uow(space) as tx:
        await tx.pipeline.acquire(space, ["profile:b"], "w1", 60, NOW)
    async with uow(space) as tx:
        assert await tx.pipeline.acquire(space, ["profile:b", "profile:a"], "w2", 60, NOW) is None
    async with uow(space) as tx:
        rows = (await tx.session.execute(text("SELECT unit_key FROM queue_leases WHERE owner = 'w2'"))).all()
    assert rows == []


async def test_due_scheduled_tasks_move_once_in_due_order(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    first = Task(TaskName.GATE, space, profile_id=uuid7())
    second = with_attempt(Task(TaskName.EXTRACT, space, payload={"session_id": "s"}), 2)
    future = Task(TaskName.MEASURE, space)
    async with uow(space) as tx:
        await tx.pipeline.schedule(second, NOW - timedelta(seconds=1), "retry")
        await tx.pipeline.schedule(first, NOW - timedelta(seconds=9), "lease_busy")
        await tx.pipeline.schedule(first, NOW - timedelta(seconds=9), "lease_busy")
        await tx.pipeline.schedule(future, NOW + timedelta(hours=1), "delay")
    async with uow.system(space) as a:
        moved_a = await a.pipeline.move_due_scheduled(NOW, 1)
        async with uow.system(space) as b:
            moved_b = await b.pipeline.move_due_scheduled(NOW, 10)
    assert moved_a == [first]
    assert moved_b == [second]
    assert attempt_of(moved_b[0]) == 2
    async with uow.system(space) as tx:
        assert await tx.pipeline.move_due_scheduled(NOW, 10) == []


async def test_dead_tasks_are_listed_and_replayed_to_the_broker(uow: PgUnitOfWorkFactory) -> None:
    space, other = uuid7(), uuid7()
    clock = FixedClock(NOW)
    bus = InMemoryBus()
    service = PipelineService(uow, bus, clock)
    dead = with_attempt(Task(TaskName.ERASE, space, profile_id=uuid7(), payload={"request_id": "r"}), 10)
    await service.bury(dead, "unavailable: models down")
    await service.bury(Task(TaskName.EXTRACT, other), "RuntimeError")
    async with uow.cell() as tx:
        [listed] = await tx.dead.dead(space, TaskName.ERASE, NOW - timedelta(days=1), 10)
        row = (
            await tx.session.execute(
                text("SELECT attempts, last_error FROM dead_tasks WHERE id = :id"), {"id": dead.id}
            )
        ).one()
    assert listed == dead
    assert tuple(row) == (10, "unavailable: models down")
    assert await service.requeue_dead(space_id=space) == 1
    # Dead tasks sit in the cell database, outside any space's outbox: they go straight to the broker.
    [replayed] = bus.take()
    async with uow.cell() as tx:
        assert [t.id for t in await tx.dead.dead(None, None, None, 10)] == [
            t.id for t in await tx.dead.dead(other, None, None, 10)
        ]
    assert (replayed.id, replayed.profile_id, attempt_of(replayed)) == (dead.id, dead.profile_id, 1)


async def test_one_scheduler_instance_at_a_time(uow: PgUnitOfWorkFactory) -> None:
    space, other = uuid7(), uuid7()
    async with uow.system(space) as a:
        assert await a.pipeline.try_scheduler_lock() is True
        async with uow.system(space) as b:
            assert await b.pipeline.try_scheduler_lock() is False
        # Advisory locks are per database: another space's scheduler never waits for this one.
        async with uow.system(other) as elsewhere:
            assert await elsewhere.pipeline.try_scheduler_lock() is True
    async with uow.system(space) as c:
        assert await c.pipeline.try_scheduler_lock() is True  # released at commit


async def test_schedules_fire_and_advance(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    clock = FixedClock(NOW)
    service = PipelineService(uow, InMemoryBus(), clock)
    async with uow(space) as tx:
        await tx.pipeline.upsert_schedule(
            space, TaskName.USAGE_ROLLUP, NOW - timedelta(minutes=1), 3600, "UTC"
        )
    async with uow(space) as tx:  # keeps next_run
        await tx.pipeline.upsert_schedule(space, TaskName.USAGE_ROLLUP, NOW - timedelta(days=9), 1800, "UTC")
    assert await service.schedule_once(space) == 1
    async with uow.system(space) as tx:
        [task] = await tx.pipeline.take_stale_outbox(datetime.now(UTC) + timedelta(seconds=5), 10)
        next_run = (await tx.session.execute(text("SELECT next_run, every_seconds FROM schedules"))).one()
    assert (task.name, task.space_id) == (TaskName.USAGE_ROLLUP, space)
    assert tuple(next_run) == (NOW + timedelta(minutes=29), 1800)


async def test_weekly_partitions_are_created_ahead_and_idempotently(uow: PgUnitOfWorkFactory) -> None:
    # Weeks ahead of any row, as the real upkeep runs: a partition over rows already in the default
    # partition is refused, as it should be.
    later = NOW + timedelta(days=5 * 365)
    space = uuid7()
    service = PipelineService(uow, InMemoryBus(), FixedClock(later))
    await service.maintain_partitions(Task(TaskName.PARTITION_MAINTENANCE, space))
    await service.maintain_partitions(Task(TaskName.PARTITION_MAINTENANCE, space))
    week = int(later.timestamp() * 1000) // WEEK_MS
    async with uow.system(space) as tx:
        names = set(
            (
                await tx.session.execute(text("SELECT relname FROM pg_class WHERE relname ~ '_w[0-9]+$'"))
            ).scalars()
        )
    assert {f"{parent}_w{week + i}" for parent in PARTITIONED_BY_WEEK for i in range(5)} <= names


async def test_keyed_schedules_carry_their_payload_and_go_one_by_one(uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with uow.system(space) as tx:
        for schedule_id in ("lake", "siem"):
            await tx.pipeline.upsert_schedule(
                space,
                TaskName.EXPORT_RUN,
                NOW - timedelta(minutes=1),
                6 * 3600,
                key=schedule_id,
                payload={"schedule_id": schedule_id},
            )
        # A later apply changes the period and payload but keeps the slot already planned.
        await tx.pipeline.upsert_schedule(
            space,
            TaskName.EXPORT_RUN,
            NOW + timedelta(days=3),
            3600,
            key="lake",
            payload={"schedule_id": "lake"},
        )
    async with uow.system(space) as tx:
        due = {s.key: s for s in await tx.pipeline.due_schedules(NOW, 10) if s.space_id == space}
        assert {k: (s.every_seconds, dict(s.payload)) for k, s in due.items()} == {
            "lake": (3600, {"schedule_id": "lake"}),
            "siem": (6 * 3600, {"schedule_id": "siem"}),
        }
        await tx.pipeline.set_next_run(space, TaskName.EXPORT_RUN, NOW + timedelta(hours=1), "lake")
        await tx.pipeline.delete_schedule(space, TaskName.EXPORT_RUN, "siem")
    async with uow.system(space) as tx:
        mine = [s for s in await tx.pipeline.list_schedules() if s.space_id == space]
        assert mine == [ScheduleKey(space, TaskName.EXPORT_RUN, "lake")]
        assert [s.key for s in await tx.pipeline.due_schedules(NOW, 10) if s.space_id == space] == []


async def test_work_done_reads_the_counter_a_finished_task_left(uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid7(), uuid7()
    async with uow(space) as tx:
        assert await tx.pipeline.work_done(space, profile, TaskName.COMPILE) == 0
        await tx.pipeline.request_work(Task(TaskName.COMPILE, space, profile_id=profile))
        await tx.pipeline.request_work(Task(TaskName.COMPILE, space, profile_id=profile))
        assert await tx.pipeline.work_done(space, profile, TaskName.COMPILE) == 0
        seen = await tx.pipeline.begin_work(space, profile, TaskName.COMPILE)
        await tx.pipeline.finish_work(space, profile, TaskName.COMPILE, seen)
    async with uow(space) as tx:
        assert await tx.pipeline.work_done(space, profile, TaskName.COMPILE) == 2
