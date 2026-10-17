"""The worker runtime over the in-memory bus and task store (5.5)."""

import asyncio
import itertools
import time
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from prometheus_client import REGISTRY

from niadra.adapters.inbound.workers.runtime import WorkerRuntime, copy_dead_letters, merge_handlers
from niadra.adapters.memory.fakes import InMemoryCache, RecordedDelivery
from niadra.adapters.memory.pipeline import InMemoryDeadTaskRepository, InMemoryTaskStore
from niadra.app import lease_claims
from niadra.app.lease_claims import LeaseClaims
from niadra.app.pipeline import PipelineService, fence
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.lease import LeaseGrant, current_lease, profile_unit
from niadra.domain.pipeline.retry import ATTEMPT_KEY, attempt_of, with_attempt
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell


def dead_tasks(cell: Cell) -> InMemoryDeadTaskRepository:
    return InMemoryDeadTaskRepository(cell.uow.store)


def store(cell: Cell) -> InMemoryTaskStore:
    return InMemoryTaskStore(cell.uow.store)


def runtime(cell: Cell, handlers: dict[TaskName, object], **kwargs: object) -> WorkerRuntime:
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    return WorkerRuntime(
        cell.bus,
        pipeline,
        handlers,
        queues=[q.value for q in QueueClass],
        jitter=lambda: 0.0,
        **kwargs,  # type: ignore[arg-type]
    )


def compile_task() -> Task:
    return Task(TaskName.COMPILE, SPACE_ID, profile_id=uuid7())


async def test_a_task_runs_under_its_lease_and_releases_it(cell: Cell) -> None:
    seen: list[LeaseGrant | None] = []

    async def handler(task: Task) -> None:
        seen.append(current_lease.get())
        async with cell.uow(task.space_id) as uow:
            await fence(uow)

    task = compile_task()
    delivery = RecordedDelivery(task)
    await runtime(cell, {TaskName.COMPILE: handler}).handle(delivery)
    assert delivery.acked
    assert seen[0] is not None
    assert seen[0].unit_keys == (profile_unit(task.profile_id),)  # type: ignore[arg-type]
    assert current_lease.get() is None
    assert store(cell).leases == {}


async def test_a_busy_lease_reschedules_without_counting_a_failure(cell: Cell) -> None:
    task = compile_task()
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    await pipeline.acquire(SPACE_ID, [profile_unit(task.profile_id)], "someone-else", 60)  # type: ignore[arg-type]
    calls: list[Task] = []

    async def handler(t: Task) -> None:
        calls.append(t)

    delivery = RecordedDelivery(task)
    await runtime(cell, {TaskName.COMPILE: handler}).handle(delivery)
    assert delivery.acked
    assert calls == []
    [(scheduled, due, reason)] = store(cell).scheduled
    assert (scheduled, reason) == (task, "lease_busy")
    assert attempt_of(scheduled) == 1
    assert due == cell.clock.now() + timedelta(seconds=5)


async def test_a_failure_is_retried_with_backoff_then_buried(cell: Cell) -> None:
    async def handler(task: Task) -> None:
        raise RuntimeError("provider said: customer +55 11 99999-0000 unknown")

    worker = runtime(cell, {TaskName.EXTRACT: handler})
    task = Task(TaskName.EXTRACT, SPACE_ID, payload={"session_id": "s"})
    for attempt, delay in [(1, 5), (2, 10), (3, 20), (4, 40)]:
        await worker.handle(RecordedDelivery(task))
        [(task, due, reason)] = store(cell).scheduled
        store(cell).scheduled.clear()
        assert (attempt_of(task), reason) == (attempt + 1, "retry")
        assert due == cell.clock.now() + timedelta(seconds=delay)
    delivery = RecordedDelivery(task)
    await worker.handle(delivery)
    assert delivery.acked
    assert store(cell).scheduled == []
    [dead] = await dead_tasks(cell).dead(SPACE_ID, TaskName.EXTRACT, None, 10)
    assert dead.id == task.id
    [(_, attempts, error, _)] = cell.uow.store.module("pipeline")["dead"].values()
    assert (attempts, error) == (5, "RuntimeError")


async def test_handlers_never_see_the_attempt_counter(cell: Cell) -> None:
    payloads: list[dict[str, object]] = []

    async def handler(task: Task) -> None:
        payloads.append(task.payload)

    task = with_attempt(Task(TaskName.EXTRACT, SPACE_ID, payload={"session_id": "s"}), 2)
    await runtime(cell, {TaskName.EXTRACT: handler}).handle(RecordedDelivery(task))
    assert payloads == [{"session_id": "s"}]
    assert ATTEMPT_KEY not in payloads[0]


async def test_lease_lost_inside_the_handler_reschedules_as_busy(cell: Cell) -> None:
    async def handler(task: Task) -> None:
        grant = current_lease.get()
        assert grant is not None
        # Another worker took over (e.g. after a pause longer than the TTL).
        cell.clock.advance(seconds=120)
        await PipelineService(cell.uow, cell.bus, cell.clock).acquire(
            task.space_id, grant.unit_keys, "thief", 60
        )
        async with cell.uow(task.space_id) as uow:
            await fence(uow)

    task = compile_task()
    delivery = RecordedDelivery(task)
    await runtime(cell, {TaskName.COMPILE: handler}).handle(delivery)
    assert delivery.acked
    [(_, _, reason)] = store(cell).scheduled
    assert reason == "lease_busy"
    assert next(iter(store(cell).leases.values()))[0] == "thief"  # the loser did not release the new owner


async def test_bookkeeping_failure_hands_the_message_back_to_the_broker(cell: Cell) -> None:
    class BrokenPipeline(PipelineService):
        async def delay(self, task: Task, delay_s: float, reason: str) -> None:
            raise ConnectionError("database down")

    async def handler(task: Task) -> None:
        raise RuntimeError("boom")

    worker = WorkerRuntime(
        cell.bus, BrokenPipeline(cell.uow, cell.bus, cell.clock), {TaskName.EXTRACT: handler}, queues=["x"]
    )
    delivery = RecordedDelivery(Task(TaskName.EXTRACT, SPACE_ID))
    await worker.handle(delivery)
    assert not delivery.acked
    assert delivery.retried == ["bookkeeping failed"]


async def test_the_lease_is_renewed_while_a_long_task_runs(cell: Cell) -> None:
    renewed = asyncio.Event()

    class Watching(PipelineService):
        async def renew(self, grant: LeaseGrant, ttl_s: int) -> bool:
            renewed.set()
            return await super().renew(grant, ttl_s)

    async def handler(task: Task) -> None:
        await asyncio.wait_for(renewed.wait(), 1)

    worker = WorkerRuntime(
        cell.bus,
        Watching(cell.uow, cell.bus, cell.clock),
        {TaskName.COMPILE: handler},
        queues=["x"],
        heartbeat_s=0.01,
    )
    delivery = RecordedDelivery(compile_task())
    await worker.handle(delivery)
    assert delivery.acked


async def test_run_consumes_the_pool_queues_and_drains_in_flight_work(cell: Cell) -> None:
    done: list[UUID] = []

    async def handler(task: Task) -> None:
        await asyncio.sleep(0.01)
        done.append(task.id)

    tasks = [Task(TaskName.EXTRACT, SPACE_ID) for _ in range(5)]
    await cell.bus.publish(tasks)
    await runtime(cell, {TaskName.EXTRACT: handler}, prefetch=2).run()
    assert sorted(done) == sorted(t.id for t in tasks)


async def test_stop_ends_consumption_and_waits_for_in_flight_tasks() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    finished: list[bool] = []

    class EndlessBus:
        async def publish(self, tasks: object) -> None: ...

        async def consume(self, queues: object, prefetch: int):  # type: ignore[no-untyped-def]
            yield RecordedDelivery(Task(TaskName.EXTRACT, SPACE_ID))
            await asyncio.Event().wait()

    async def handler(task: Task) -> None:
        started.set()
        await release.wait()
        finished.append(True)

    cell = Cell()
    stop = asyncio.Event()
    worker = WorkerRuntime(
        EndlessBus(),
        PipelineService(cell.uow, cell.bus, cell.clock),  # type: ignore[arg-type]
        {TaskName.EXTRACT: handler},
        queues=["x"],
    )
    running = asyncio.create_task(worker.run(stop))
    await started.wait()
    stop.set()
    await asyncio.sleep(0.01)
    assert not running.done()  # still finishing the in-flight task
    release.set()
    await asyncio.wait_for(running, 1)
    assert finished == [True]


async def test_a_task_without_handler_in_this_pool_is_buried(cell: Cell) -> None:
    delivery = RecordedDelivery(Task(TaskName.EMBED, SPACE_ID, profile_id=uuid7()))
    await runtime(cell, {}).handle(delivery)
    assert delivery.acked
    assert len(await dead_tasks(cell).dead(None, None, None, 10)) == 1


async def test_dead_letters_from_the_broker_are_copied_to_dead_tasks(cell: Cell) -> None:
    task = Task(TaskName.EXTRACT, SPACE_ID)
    await cell.bus.publish([task])
    await copy_dead_letters(
        cell.bus, PipelineService(cell.uow, cell.bus, cell.clock), task.queue, asyncio.Event()
    )
    assert [t.id for t in await dead_tasks(cell).dead(SPACE_ID, None, None, 10)] == [task.id]


def test_two_modules_claiming_one_task_is_a_wiring_error() -> None:
    async def h(task: Task) -> None: ...

    assert merge_handlers({TaskName.GATE: h}, {TaskName.EXTRACT: h}).keys() == {
        TaskName.GATE,
        TaskName.EXTRACT,
    }
    with pytest.raises(ValueError, match="two handlers"):
        merge_handlers({TaskName.GATE: h}, {TaskName.GATE: h})


async def test_coalesced_task_reruns_when_requests_arrive_while_it_works(cell: Cell) -> None:
    """The handler pattern for `compile`: begin_work, compute, finish_work, re-enqueue if moved on."""
    profile = uuid7()

    async def request() -> None:
        async with cell.uow(SPACE_ID) as uow:
            await uow.pipeline.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))

    runs: list[int] = []

    async def handler(task: Task) -> None:
        async with cell.uow(task.space_id) as uow:
            seen = await uow.pipeline.begin_work(task.space_id, profile, task.name)
        runs.append(seen)
        if len(runs) == 1:
            await request()  # a new event lands mid-compilation
        async with cell.uow(task.space_id) as uow:
            await fence(uow)
            if await uow.pipeline.finish_work(task.space_id, profile, task.name, seen):
                await uow.pipeline.enqueue([Task(task.name, task.space_id, profile_id=profile)])

    await request()
    await request()
    worker = runtime(cell, {TaskName.COMPILE: handler})
    for _ in range(3):
        for task in store(cell).take():
            await worker.handle(RecordedDelivery(task))
    assert runs == [2, 3]


async def test_a_malformed_task_is_buried_at_once(cell: Cell) -> None:
    async def handler(task: Task) -> None: ...

    delivery = RecordedDelivery(Task(TaskName.COMPILE, SPACE_ID))  # a profile task without a profile
    await runtime(cell, {TaskName.COMPILE: handler}).handle(delivery)
    assert delivery.acked
    assert store(cell).scheduled == []
    assert len(await dead_tasks(cell).dead(SPACE_ID, None, None, 10)) == 1


async def test_one_space_never_holds_every_slot_of_a_pool(cell: Cell) -> None:
    release = asyncio.Event()
    started: list[Task] = []

    async def handler(t: Task) -> None:
        started.append(t)
        await release.wait()

    worker = runtime(cell, {TaskName.COMPILE: handler}, per_space=2)
    running = [asyncio.create_task(worker.handle(RecordedDelivery(compile_task()))) for _ in range(2)]
    await asyncio.sleep(0)
    third = compile_task()
    over = RecordedDelivery(third)
    await worker.handle(over)
    assert over.acked
    assert len(started) == 2
    [(scheduled, _, reason)] = store(cell).scheduled
    assert (scheduled, reason) == (third, "space_busy")
    release.set()
    await asyncio.gather(*running)
    after = RecordedDelivery(compile_task())
    await worker.handle(after)
    assert len(started) == 3


async def test_every_outcome_is_counted_for_the_dashboards(cell: Cell) -> None:
    def seen(result: str) -> float:
        labels = {"task": TaskName.COMPILE.value, "result": result}
        return REGISTRY.get_sample_value("niadra_tasks_total", labels) or 0.0

    async def ok(task: Task) -> None:
        return None

    before = seen("ok")
    await runtime(cell, {TaskName.COMPILE: ok}).handle(RecordedDelivery(compile_task()))
    assert seen("ok") == before + 1


def object_event(profile_id: UUID) -> Task:
    return Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(uuid7())}, profile_id=profile_id)


async def test_an_urgent_task_takes_a_busy_lease_as_soon_as_it_frees(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Release 2026.09.28-1750-da68f12: a burst of the customer's `reconcile` tasks kept the profile lease
    # busy, and the invoice's `apply_object_event`, delayed like any of them, lost it for two minutes.
    monkeypatch.setattr(lease_claims, "POLL_S", 0.01)
    claims = LeaseClaims(InMemoryCache())
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    profile_id = uuid7()
    unit = [profile_unit(profile_id)]
    held = await pipeline.acquire(SPACE_ID, unit, "reconcile-1", 60)
    assert held is not None
    ran: list[str] = []

    async def urgent(task: Task) -> None:
        ran.append("object")

    async def background(task: Task) -> None:
        ran.append("reconcile")

    worker = runtime(
        cell, {TaskName.APPLY_OBJECT_EVENT: urgent, TaskName.RECONCILE: background}, claims=claims
    )
    waiting = asyncio.create_task(worker.handle(RecordedDelivery(object_event(profile_id))))
    await asyncio.sleep(0.05)
    assert ran == []  # it waits, holding its delivery, instead of going back to the schedule

    # The lease frees; a burst task asks first, and defers to the claim.
    await pipeline.release(held)
    burst = RecordedDelivery(Task(TaskName.RECONCILE, SPACE_ID, {"session_id": "s"}, profile_id=profile_id))
    await worker.handle(burst)
    await asyncio.wait_for(waiting, 1)

    assert ran == ["object"]
    assert burst.acked
    [(scheduled, _, reason)] = store(cell).scheduled
    assert (scheduled.name, reason) == (TaskName.RECONCILE, "lease_busy")
    assert await claims.claimed_by_another(SPACE_ID, unit, "anyone") is False  # the claim went with it


async def test_an_urgent_task_waits_no_longer_than_its_bound(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_claims, "POLL_S", 0.01)
    monkeypatch.setattr(lease_claims, "WAIT_S", 0.05)
    claims = LeaseClaims(InMemoryCache())
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    profile_id = uuid7()
    await pipeline.acquire(SPACE_ID, [profile_unit(profile_id)], "stuck", 60)

    async def urgent(task: Task) -> None:
        raise AssertionError("never runs without its lease")

    delivery = RecordedDelivery(object_event(profile_id))
    await runtime(cell, {TaskName.APPLY_OBJECT_EVENT: urgent}, claims=claims).handle(delivery)

    assert delivery.acked
    [(_, _, reason)] = store(cell).scheduled
    assert reason == "lease_busy"
    assert await claims.claimed_by_another(SPACE_ID, [profile_unit(profile_id)], "anyone") is False


async def test_a_compilation_goes_before_the_background_work_of_the_same_change(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What an agent reads is the compiled pack. On the region cell of 30/09 the `compile`
    # a change requested lost the profile lease to its own `embed`, `trigger` and `derive_traits` for 5 s at a
    # time, and an action reached the context in 11 s at the median.
    monkeypatch.setattr(lease_claims, "POLL_S", 0.01)
    claims = LeaseClaims(InMemoryCache())
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    profile_id = uuid7()
    held = await pipeline.acquire(SPACE_ID, [profile_unit(profile_id)], "embed-1", 60)
    assert held is not None
    ran: list[str] = []

    async def handler(task: Task) -> None:
        ran.append(task.name.value)

    worker = runtime(cell, {TaskName.COMPILE: handler, TaskName.TRIGGER: handler}, claims=claims)
    compile_ = asyncio.create_task(
        worker.handle(RecordedDelivery(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile_id)))
    )
    await asyncio.sleep(0.05)
    await pipeline.release(held)
    trigger = RecordedDelivery(Task(TaskName.TRIGGER, SPACE_ID, profile_id=profile_id))
    await worker.handle(trigger)
    await asyncio.wait_for(compile_, 1)

    assert ran == ["compile"]
    [(scheduled, _, reason)] = store(cell).scheduled
    assert (scheduled.name, reason) == (TaskName.TRIGGER, "lease_busy")


async def test_an_urgent_task_asks_less_often_the_longer_the_lease_stays_busy(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The workers share one connection per space database: a holder that runs for seconds must not cost it
    # a statement every `POLL_S`.
    monkeypatch.setattr(lease_claims, "POLL_S", 0.01)
    monkeypatch.setattr(lease_claims, "POLL_MAX_S", 0.08)
    monkeypatch.setattr(lease_claims, "WAIT_S", 0.5)
    claims = LeaseClaims(InMemoryCache())
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    profile_id = uuid7()
    await pipeline.acquire(SPACE_ID, [profile_unit(profile_id)], "stuck", 60)
    asks: list[float] = []
    acquire = PipelineService.acquire

    async def counted(self: PipelineService, *args: Any) -> LeaseGrant | None:
        asks.append(time.monotonic())
        return await acquire(self, *args)

    monkeypatch.setattr(PipelineService, "acquire", counted)

    async def urgent(task: Task) -> None:
        raise AssertionError("never runs without its lease")

    worker = runtime(cell, {TaskName.APPLY_OBJECT_EVENT: urgent}, claims=claims)
    await worker.handle(RecordedDelivery(object_event(profile_id)))
    # 0.01, 0.02, 0.04, then 0.08 at most: about 9 asks in half a second, not 50.
    assert 6 <= len(asks) <= 12
    gaps = [b - a for a, b in itertools.pairwise(asks)]
    assert max(gaps) < 0.08 * 2


async def test_a_claims_store_that_fails_leaves_the_lease_to_whoever_asks(cell: Cell) -> None:
    class Down(InMemoryCache):
        async def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
            raise ConnectionError("state store down")

    ran: list[Task] = []

    async def handler(task: Task) -> None:
        ran.append(task)

    task = compile_task()
    await runtime(cell, {TaskName.COMPILE: handler}, claims=LeaseClaims(Down())).handle(
        RecordedDelivery(task)
    )
    assert ran == [task]
