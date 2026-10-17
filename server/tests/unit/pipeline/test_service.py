"""Relay, scheduler, dead-task replay and partition upkeep (5.5, 4.5)."""

import contextlib
from datetime import timedelta

from niadra.adapters.inbound.workers.pipeline import handlers
from niadra.adapters.memory.pipeline import (
    InMemoryDeadTaskRepository,
    InMemoryPipelineProbe,
    InMemoryTaskStore,
)
from niadra.app.pipeline import PipelineService
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.retry import attempt_of, with_attempt
from niadra.domain.pipeline.schedule import MAINTENANCE_DAILY, PARTITIONED_BY_WEEK, ScheduleKey
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell, sample_config


def service(cell: Cell) -> PipelineService:
    return PipelineService(cell.uow, cell.bus, cell.clock)


def store(cell: Cell) -> InMemoryTaskStore:
    return InMemoryTaskStore(cell.uow.store)


async def test_relay_publishes_stragglers_and_due_scheduled_tasks(cell: Cell) -> None:
    straggler = Task(TaskName.GATE, SPACE_ID, profile_id=uuid7())
    due = Task(TaskName.EXTRACT, SPACE_ID)
    later = Task(TaskName.MEASURE, SPACE_ID)
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.enqueue([straggler])
        await uow.pipeline.schedule(due, cell.clock.now() - timedelta(seconds=1), "retry")
        await uow.pipeline.schedule(later, cell.clock.now() + timedelta(minutes=1), "delay")
    assert await service(cell).relay_once(SPACE_ID) == 2
    assert cell.bus.take() == [straggler, due]
    assert store(cell).outbox == []
    assert [t for t, _, _ in store(cell).scheduled] == [later]


async def test_relay_keeps_the_rows_when_the_broker_is_down(cell: Cell) -> None:
    class DownBus:
        async def publish(self, tasks: object) -> None:
            raise ConnectionError("broker down")

    task = Task(TaskName.GATE, SPACE_ID, profile_id=uuid7())
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.enqueue([task])
    relay = PipelineService(cell.uow, DownBus(), cell.clock)  # type: ignore[arg-type]
    with contextlib.suppress(ConnectionError):
        await relay.relay_once(SPACE_ID)
    assert store(cell).outbox == [task]


async def test_scheduler_fires_due_schedules_and_moves_them_forward(cell: Cell) -> None:
    now = cell.clock.now()
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.upsert_schedule(
            SPACE_ID, TaskName.USAGE_ROLLUP, now - timedelta(minutes=5), 3600, "UTC"
        )
        await uow.pipeline.upsert_schedule(
            SPACE_ID, TaskName.AUDIT_ANCHOR, now + timedelta(hours=2), 86400, "UTC"
        )
    assert await service(cell).schedule_once(SPACE_ID) == 1
    [task] = store(cell).take()
    assert (task.name, task.space_id) == (TaskName.USAGE_ROLLUP, SPACE_ID)
    assert task.payload == {"scheduled_for": (now - timedelta(minutes=5)).isoformat()}
    [next_run] = [
        r["next_run"] for k, r in store(cell)._s["schedules"].items() if k.task == TaskName.USAGE_ROLLUP
    ]
    assert next_run == now + timedelta(minutes=55)
    assert await service(cell).schedule_once(SPACE_ID) == 0


async def test_replay_sends_dead_tasks_back_with_fresh_attempts(cell: Cell) -> None:
    other_space = uuid7()
    first = with_attempt(Task(TaskName.EXTRACT, SPACE_ID), 5)
    second = Task(TaskName.MEASURE, other_space)
    await service(cell).bury(first, "RuntimeError")
    await service(cell).bury(second, "RuntimeError")
    assert await service(cell).requeue_dead(space_id=SPACE_ID) == 1
    # Dead tasks live in the cell database: they go straight back to the broker, not to an outbox.
    [replayed] = cell.bus.take()
    assert replayed.id == first.id
    assert attempt_of(replayed) == 1
    dead = InMemoryDeadTaskRepository(cell.uow.store)
    assert [t.id for t in await dead.dead(None, None, None, 10)] == [second.id]


async def test_a_keyed_schedule_fires_with_its_payload(cell: Cell) -> None:
    async with cell.uow(SPACE_ID) as uow:
        for schedule_id in ("lake", "siem"):
            await uow.pipeline.upsert_schedule(
                SPACE_ID,
                TaskName.EXPORT_RUN,
                cell.clock.now(),
                3600,
                key=schedule_id,
                payload={"schedule_id": schedule_id},
            )
    assert await service(cell).schedule_once(SPACE_ID) == 2
    assert sorted(t.payload["schedule_id"] for t in store(cell).take()) == ["lake", "siem"]
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.delete_schedule(SPACE_ID, TaskName.EXPORT_RUN, "lake")
        assert await uow.pipeline.list_schedules() == [ScheduleKey(SPACE_ID, TaskName.EXPORT_RUN, "siem")]


async def test_space_schedules_are_installed_once_with_the_upkeep_of_the_space_database(cell: Cell) -> None:
    pipeline = service(cell)
    await pipeline.install_space_schedules(sample_config(), cell.clock.now())
    await pipeline.install_space_schedules(sample_config(), cell.clock.now() + timedelta(days=3))
    async with cell.uow(SPACE_ID) as uow:
        rows = await uow.pipeline.list_schedules()
    assert {r.task for r in rows} >= set(MAINTENANCE_DAILY)
    assert len(rows) == len({(r.task, r.key) for r in rows})
    assert {r.space_id for r in rows} == {SPACE_ID}


async def test_partition_upkeep_covers_this_week_and_the_next_four(cell: Cell) -> None:
    pipeline = service(cell)
    await pipeline.install_space_schedules(sample_config(), cell.clock.now())
    cell.clock.advance(days=1, hours=2)
    assert await pipeline.schedule_once(SPACE_ID) >= 2
    [task] = [t for t in store(cell).take() if t.name is TaskName.PARTITION_MAINTENANCE]
    assert task.space_id == SPACE_ID
    container = type("C", (), {"pipeline": pipeline})()
    await handlers(container)[TaskName.PARTITION_MAINTENANCE](task)
    parents = {p for p, _ in store(cell).partitions}
    assert parents == set(PARTITIONED_BY_WEEK)
    assert len(store(cell).partitions) == len(PARTITIONED_BY_WEEK) * 5


async def test_the_probe_reads_the_outbox_lag_and_a_quiet_space_runs_nothing_else(cell: Cell) -> None:
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock, probe=InMemoryPipelineProbe(cell.uow.store))
    quiet = await pipeline.relay_step(SPACE_ID)
    assert (quiet.found, quiet.next_in_s) == (0, None)
    assert quiet.due is not None
    assert quiet.due.outbox_lag_s(cell.clock.now()) == 0.0
    written = cell.clock.now() - timedelta(seconds=12)
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.enqueue([Task(TaskName.GATE, SPACE_ID, id=uuid7(int(written.timestamp() * 1000)))])
    stale = await pipeline.relay_step(SPACE_ID)
    assert stale.due is not None
    assert round(stale.due.outbox_lag_s(cell.clock.now())) == 12
    assert stale.found == 1
    assert store(cell).outbox == []


async def test_the_relay_step_sleeps_until_the_next_delayed_task(cell: Cell) -> None:
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock, probe=InMemoryPipelineProbe(cell.uow.store))
    async with cell.uow(SPACE_ID) as uow:
        await uow.pipeline.schedule(
            Task(TaskName.EXTRACT, SPACE_ID), cell.clock.now() + timedelta(seconds=40), "retry"
        )
        assert uow.pipeline.scheduled_at == cell.clock.now() + timedelta(seconds=40)
    waiting = await pipeline.relay_step(SPACE_ID)
    assert (waiting.found, waiting.next_in_s) == (0, 40.0)
    assert store(cell).scheduled != []
    assert cell.bus.published == []
    cell.clock.advance(seconds=41)
    assert (await pipeline.relay_step(SPACE_ID)).found == 1
    assert [t.name for t in cell.bus.take()] == [TaskName.EXTRACT]
