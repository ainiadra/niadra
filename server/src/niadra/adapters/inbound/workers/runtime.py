"""Runs the task handlers of one worker pool on the broker's queues (5.5; 06, 4.4).

Per delivery: take the lease the task's spec asks for (busy is not a failure: the task comes
back in a few seconds through `scheduled_tasks`), renew it on a heartbeat while the handler
runs, then ack. A failing task is retried with exponential backoff through `scheduled_tasks`
until `max_attempts`, then copied to `dead_tasks`. The broker's own delivery limit and
dead-letter exchange only catch what crashes the process.
"""

import asyncio
import contextlib
import logging
import os
import random
import signal
import socket
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from niadra.adapters.observability import metrics
from niadra.app import lease_claims
from niadra.app.lease_claims import LeaseClaims
from niadra.app.pipeline import PipelineService
from niadra.domain.common.errors import InvalidInput, LeaseLost
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.lease import (
    BUSY_DELAY_S,
    HEARTBEAT_S,
    LEASE_TTL_S,
    LeaseGrant,
    current_lease,
    lease_units,
)
from niadra.domain.pipeline.retry import (
    attempt_of,
    error_summary,
    retry_delay_s,
    with_attempt,
    without_attempt,
)
from niadra.domain.spaces.directory import DbStatus
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.ports.infra import Delivery, SpaceDatabases, TaskBus, TaskHandler

log = logging.getLogger(__name__)

# One pool per queue class, each with its own scaling rule (06, 4.4).
POOLS: dict[str, tuple[QueueClass, ...]] = {
    "realtime": (QueueClass.REALTIME,),
    "identity": (QueueClass.IDENTITY,),
    "extraction": (QueueClass.EXTRACTION,),
    "measure": (QueueClass.MEASURE,),
    "delivery": (QueueClass.DELIVERY,),
    "background": (QueueClass.BACKGROUND,),
    "volume": (QueueClass.VOLUME,),
}

HandlerMap = Mapping[TaskName, TaskHandler]


def merge_handlers(*maps: HandlerMap) -> dict[TaskName, TaskHandler]:
    """Joins every module's `handlers(c)`; two modules claiming one task is a wiring bug."""
    merged: dict[TaskName, TaskHandler] = {}
    for handlers in maps:
        for name, handler in handlers.items():
            if name in merged:
                raise ValueError(f"two handlers for task {name}")
            merged[name] = handler
    return merged


def registry(container: Any, modules: Iterable[Callable[[Any], HandlerMap]]) -> dict[TaskName, TaskHandler]:
    return merge_handlers(*(handlers(container) for handlers in modules))


class WorkerRuntime:
    def __init__(
        self,
        bus: TaskBus,
        pipeline: PipelineService,
        handlers: HandlerMap,
        *,
        queues: Sequence[str],
        prefetch: int = 16,
        per_space: int | None = None,
        worker_id: str | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        lease_ttl_s: int = LEASE_TTL_S,
        busy_delay_s: float = BUSY_DELAY_S,
        jitter: Callable[[], float] = random.random,
        databases: SpaceDatabases | None = None,
        claims: LeaseClaims | None = None,
    ) -> None:
        """`claims` lets an urgent task take a busy profile lease next (`app.lease_claims`)."""
        self._bus = bus
        self._claims = claims
        self._databases = databases
        self._pipeline = pipeline
        self._handlers = dict(handlers)
        self._queues = list(queues)
        self._prefetch = prefetch
        self._worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._heartbeat_s = heartbeat_s
        self._lease_ttl_s = lease_ttl_s
        self._busy_delay_s = busy_delay_s
        self._jitter = jitter
        self._slots = asyncio.Semaphore(prefetch)
        self._in_flight: set[asyncio.Task[None]] = set()
        # RabbitMQ does not share turns between spaces, so no space may hold every slot (06, 8).
        self._per_space = per_space or prefetch
        self._running: dict[UUID, int] = {}

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Consumes until `stop` is set (or the bus ends), then finishes what is in flight."""
        try:
            await _until(stop or asyncio.Event(), self._consume())
        finally:
            await self.drain()

    async def drain(self) -> None:
        while self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)

    async def handle(self, delivery: Delivery) -> None:
        task = delivery.task
        if not await self._space_exists(delivery):
            return
        handler = self._handlers.get(task.name)
        if handler is None:
            _count(task, "dead")
            await self._settle(delivery, self._pipeline.bury(task, "no handler in this pool"))
            return
        try:
            units = lease_units(task)
        except InvalidInput as exc:
            # A malformed task never gets better by retrying.
            _count(task, "dead")
            await self._settle(delivery, self._pipeline.bury(task, error_summary(exc)))
            return
        if self._running.get(task.space_id, 0) >= self._per_space:
            _count(task, "space_busy")
            await self._settle(
                delivery, self._pipeline.delay(task, self._busy_delay_s, DelayReason.SPACE_BUSY)
            )
            return
        self._running[task.space_id] = self._running.get(task.space_id, 0) + 1
        try:
            await self._run_leased(delivery, task, handler, units)
        finally:
            self._running[task.space_id] -= 1
            if not self._running[task.space_id]:
                del self._running[task.space_id]

    async def _run_leased(
        self, delivery: Delivery, task: Task, handler: TaskHandler, units: Sequence[str]
    ) -> None:
        owner = f"{self._worker_id}:{uuid7()}"
        try:
            grant = await self._acquire(task, units, owner) if units else None
        except Exception:
            log.exception("lease acquisition failed", extra={"task": task.name.value})
            await delivery.retry_later("lease store unavailable")
            return
        if units and grant is None:
            _count(task, "lease_busy")
            await self._settle(
                delivery, self._pipeline.delay(task, self._busy_delay_s, DelayReason.LEASE_BUSY)
            )
            return
        started = time.perf_counter()
        try:
            await self._run_handler(handler, without_attempt(task), grant)
        except LeaseLost:
            _count(task, "lease_busy")
            await self._settle(
                delivery, self._pipeline.delay(task, self._busy_delay_s, DelayReason.LEASE_BUSY)
            )
        except Exception as exc:
            await self._settle(delivery, self._after_failure(task, exc))
        else:
            _count(task, "ok")
            await delivery.ack()
        finally:
            metrics.TASK_LATENCY.labels(task.name.value).observe(time.perf_counter() - started)
            if grant is not None:
                with contextlib.suppress(Exception):
                    await self._pipeline.release(grant)

    async def _acquire(self, task: Task, units: Sequence[str], owner: str) -> LeaseGrant | None:
        """The lease, or None when it is busy. A task defers to an urgent task's claim on its units; an urgent
        task that finds the lease busy claims it and asks again until it frees, within `WAIT_S`."""
        space_id = task.space_id
        claims = self._claims
        if not task.spec.urgent:
            if claims is not None and await claims.claimed_by_another(space_id, units, owner):
                return None
            return await self._pipeline.acquire(space_id, units, owner, self._lease_ttl_s)
        grant = await self._pipeline.acquire(space_id, units, owner, self._lease_ttl_s)
        if grant is not None or claims is None:
            return grant
        deadline = time.monotonic() + lease_claims.WAIT_S
        poll = lease_claims.POLL_S
        try:
            while grant is None and time.monotonic() < deadline:
                await claims.claim(space_id, units, owner)
                await asyncio.sleep(poll)
                poll = min(poll * 2, lease_claims.POLL_MAX_S)
                grant = await self._pipeline.acquire(space_id, units, owner, self._lease_ttl_s)
        finally:
            await claims.drop(space_id, units, owner)
        _count(task, "claimed" if grant is not None else "claim_expired")
        return grant

    async def _space_exists(self, delivery: Delivery) -> bool:
        """A task of a deleted space, or of a space the directory does not know (a task published before
        the cell database was recreated), has nothing left to act on: it is acked and dropped."""
        if self._databases is None:
            return True
        try:
            status = await self._databases.status(delivery.task.space_id)
        except Exception:
            log.exception("space directory unavailable", extra={"task": delivery.task.name.value})
            await delivery.retry_later("space directory unavailable")
            return False
        if status is None or status in (DbStatus.DELETING, DbStatus.DELETED):
            _count(delivery.task, "dropped")
            log.info("task of a deleted or unknown space dropped", extra={"task": delivery.task.name.value})
            await delivery.ack()
            return False
        return True

    async def _consume(self) -> None:
        async for delivery in self._bus.consume(self._queues, self._prefetch):
            await self._slots.acquire()
            job = asyncio.create_task(self._guarded(delivery))
            self._in_flight.add(job)
            job.add_done_callback(self._done)

    async def _guarded(self, delivery: Delivery) -> None:
        try:
            await self.handle(delivery)
        except Exception:
            log.exception("delivery handling failed", extra={"task": delivery.task.name.value})

    def _done(self, job: asyncio.Task[None]) -> None:
        self._in_flight.discard(job)
        self._slots.release()

    async def _run_handler(self, handler: TaskHandler, task: Task, grant: LeaseGrant | None) -> None:
        token = current_lease.set(grant)
        heartbeat = asyncio.create_task(self._heartbeat(grant)) if grant else None
        try:
            await handler(task)
        finally:
            current_lease.reset(token)
            if heartbeat:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(self, grant: LeaseGrant) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            try:
                if not await self._pipeline.renew(grant, self._lease_ttl_s):
                    # The fence check will abort the handler's next write; nothing else to do here.
                    log.warning("lease lost during the task")
                    return
            except Exception:
                log.exception("lease renewal failed")

    def _after_failure(self, task: Task, exc: Exception) -> Awaitable[None]:
        attempt = attempt_of(task)
        summary = error_summary(exc)
        log.warning("task failed", extra={"task": task.name.value, "attempt": attempt, "error": summary})
        if attempt < task.spec.max_attempts:
            _count(task, "retry")
            delay = retry_delay_s(attempt, self._jitter())
            return self._pipeline.delay(with_attempt(task, attempt + 1), delay, DelayReason.RETRY)
        _count(task, "dead")
        return self._pipeline.bury(task, summary)

    @staticmethod
    async def _settle(delivery: Delivery, bookkeeping: Awaitable[None]) -> None:
        # Ack only once the retry, delay or burial is committed; if that write fails the broker
        # redelivers, and its delivery limit dead-letters a task that keeps failing here.
        try:
            await bookkeeping
        except Exception:
            log.exception("task bookkeeping failed")
            await delivery.retry_later("bookkeeping failed")
            return
        await delivery.ack()


def _count(task: Task, result: str) -> None:
    metrics.TASKS.labels(task.name.value, result).inc()
    if result == "dead":
        metrics.DEAD_TASKS.labels(task.name.value).inc()


async def copy_dead_letters(bus: TaskBus, pipeline: PipelineService, queue: str, stop: asyncio.Event) -> None:
    """Copies what the broker dead-lettered (poison messages, crash loops) to `dead_tasks` (5.5)."""

    async def consume() -> None:
        async for delivery in bus.consume([queue], 8):
            _count(delivery.task, "dead")
            await WorkerRuntime._settle(delivery, pipeline.bury(delivery.task, "dead-lettered by the broker"))

    await _until(stop, consume())


async def _until(stop: asyncio.Event, work: Awaitable[None]) -> None:
    job = asyncio.ensure_future(work)
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({job, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopper.cancel()
        # Cancelling the consumer closes its channel, which returns unacked prefetched messages.
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job


def stop_on_signals(
    stop: asyncio.Event, signals: Sequence[signal.Signals] = (signal.SIGTERM, signal.SIGINT)
) -> None:
    """SIGTERM stops consuming; in-flight tasks finish and release their leases before exit."""
    loop = asyncio.get_running_loop()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
