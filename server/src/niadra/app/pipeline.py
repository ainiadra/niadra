"""The pipeline's use cases: relay, scheduler, retries, dead tasks and partition upkeep (5.5, 4.5).

Every space database has its own outbox, scheduled tasks and schedules, so the relay and the
scheduler run per space (`SpaceLoopSupervisor` decides how often); dead tasks live in the cell
database, where a task of a space whose database is unavailable can still be buried.
"""

import dataclasses
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import NiadraError
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.due import STRAGGLER_AGE, PipelineDue
from niadra.domain.pipeline.lease import LeaseGrant, current_lease
from niadra.domain.pipeline.retry import attempt_of, without_attempt
from niadra.domain.pipeline.schedule import (
    PARTITIONED_BY_WEEK,
    ScheduleSpec,
    following_run,
    space_schedules,
    stale_schedules,
    week_starts,
)
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.tasks import Task, TaskName
from niadra.ports.infra import Clock, TaskBus
from niadra.ports.repos.pipeline import LeaseStore, PipelineProbe
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)


async def fence(uow: UnitOfWork) -> None:
    """Call first in every transaction that writes under a lease: aborts if the lease moved on (5.5)."""
    grant = current_lease.get()
    if grant is None:
        return
    for key in grant.unit_keys:
        await uow.pipeline.check_fence(grant.space_id, key, grant.owner, grant.epochs[key])


@dataclasses.dataclass(frozen=True, slots=True)
class RelayPass:
    """One pass of a space's relay and scheduler, and what the loop needs to decide when to come back."""

    found: int
    due: PipelineDue | None = None
    """What the probe read before the pass; None without a probe."""
    next_in_s: float | None = None
    """Seconds until something known becomes due; None when nothing waits or without a probe."""


class PipelineService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        bus: TaskBus,
        clock: Clock,
        *,
        probe: PipelineProbe | None = None,
        leases: LeaseStore | None = None,
    ) -> None:
        """`leases` takes and gives back a task's lease in one statement each; without it, each is a
        transaction of the unit of work."""
        self._uow = uow
        self._bus = bus
        self._clock = clock
        self._probe = probe
        self._leases = leases

    async def relay_step(self, space_id: UUID) -> RelayPass:
        """The per-space loop's pass (06, 6.3). With a probe, one statement tells whether the relay or the
        scheduler has anything due; a quiet space gets nothing else, and the loop sleeps until the next
        known time. Without one, both run every pass."""
        if self._probe is None:
            return RelayPass(await self.relay_once(space_id) + await self.schedule_once(space_id))
        due = await self._probe.due(space_id)
        now = self._clock.now()
        found = 0
        if due.relay_due(now, STRAGGLER_AGE):
            found += await self.relay_once(space_id)
        if due.schedules_due(now):
            found += await self.schedule_once(space_id)
        next_at = due.next_at(STRAGGLER_AGE)
        next_in = None if found or next_at is None else max(0.0, (next_at - now).total_seconds())
        return RelayPass(found, due, next_in)

    async def relay_once(self, space_id: UUID, limit: int = 500) -> int:
        """Publishes a space's outbox stragglers and due scheduled tasks; rows go away only if the publish
        succeeded."""
        now = self._clock.now()
        # The application role, like the space's requests and tasks: a space database gets two server
        # connections, and a second role's pool would take one of them from the first at every pass.
        async with self._uow(space_id) as uow:
            stale = await uow.pipeline.take_stale_outbox(now - STRAGGLER_AGE, limit)
            due = await uow.pipeline.move_due_scheduled(now, limit)
            if stale or due:
                await self._bus.publish([*stale, *due])
            await uow.pipeline.delete_outbox([t.id for t in stale])
        return len(stale) + len(due)

    async def schedule_once(self, space_id: UUID, limit: int = 100) -> int:
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            if not await uow.pipeline.try_scheduler_lock():
                return 0
            due = await uow.pipeline.due_schedules(now, limit)
            await uow.pipeline.enqueue(
                [
                    Task(s.task, s.space_id, payload={**s.payload, "scheduled_for": s.next_run.isoformat()})
                    for s in due
                ]
            )
            for s in due:
                await uow.pipeline.set_next_run(
                    s.space_id, s.task, following_run(s.next_run, s.every_seconds, now), s.key
                )
        return len(due)

    async def acquire(
        self, space_id: UUID, unit_keys: Sequence[str], owner: str, ttl_s: int
    ) -> LeaseGrant | None:
        if self._leases is not None:
            return await self._leases.acquire(space_id, list(unit_keys), owner, ttl_s, self._clock.now())
        async with self._uow(space_id) as uow:
            return await uow.pipeline.acquire(space_id, unit_keys, owner, ttl_s, self._clock.now())

    async def renew(self, grant: LeaseGrant, ttl_s: int) -> bool:
        async with self._uow(grant.space_id) as uow:
            return await uow.pipeline.renew(grant, ttl_s, self._clock.now())

    async def release(self, grant: LeaseGrant) -> None:
        if self._leases is not None:
            await self._leases.release(grant)
            return
        async with self._uow(grant.space_id) as uow:
            await uow.pipeline.release(grant)

    async def delay(self, task: Task, delay_s: float, reason: DelayReason) -> None:
        """Busy lease or retry with backoff: the broker has no per-message delay, `scheduled_tasks` does."""
        async with self._uow(task.space_id) as uow:
            await uow.pipeline.schedule(task, self._clock.now() + timedelta(seconds=delay_s), reason)

    async def bury(self, task: Task, error: str | None) -> None:
        async with self._uow.cell() as uow:
            await uow.dead.bury(task, attempt_of(task), error, self._clock.now())
        if task.name is TaskName.ERASE and "request_id" in task.payload:
            await self._erasure_failed(task)

    async def _erasure_failed(self, task: Task) -> None:
        """An erasure out of attempts says so: `GET /v1/forget/{id}` answers `failed` instead of
        `running` for good. A replay from the dead tasks resumes it, and it ends `completed`."""
        try:
            request_id = UUID(str(task.payload["request_id"]))
        except ValueError:
            return
        # Best effort: the task is buried already, and a space that is away or gone keeps it there.
        try:
            async with self._uow(task.space_id) as uow:
                request = await uow.privacy.get_erasure(task.space_id, request_id)
                if request is not None and request.status is not ErasureStatus.COMPLETED:
                    await uow.privacy.save_erasure(
                        task.space_id, dataclasses.replace(request, status=ErasureStatus.FAILED)
                    )
        except NiadraError:
            log.warning("the failed erasure could not be marked; its space database is away")

    async def dead_tasks(self, space_id: UUID, limit: int = 200) -> list[Task]:
        """The space's buried tasks, newest first, for the people who may replay them."""
        async with self._uow.cell() as uow:
            return await uow.dead.dead(space_id, None, None, limit)

    async def requeue_dead(
        self,
        space_id: UUID | None = None,
        name: TaskName | None = None,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> int:
        """`ops queue-replay`: dead tasks go back to the broker with fresh attempts (5.5).

        Straight to the broker, since they sit in the cell database and the outbox is per space: a
        failure between the publish and the delete sends a task twice, and tasks are idempotent.
        """
        async with self._uow.cell() as uow:
            dead = await uow.dead.dead(space_id, name, since, limit)
        if not dead:
            return 0
        await self._bus.publish([without_attempt(t) for t in dead])
        async with self._uow.cell() as uow:
            for space in {t.space_id for t in dead}:
                await uow.dead.delete_dead(space, [t.id for t in dead if t.space_id == space])
        return len(dead)

    async def maintain_partitions(self, task: Task) -> None:
        """The space's weekly partitions, as the system role: creating one needs the parent's owner,
        which the SECURITY DEFINER function stands in for."""
        now_ms = int(self._clock.now().timestamp() * 1000)
        async with self._uow.system(task.space_id) as uow:
            await uow.pipeline.ensure_week_partitions(PARTITIONED_BY_WEEK, week_starts(now_ms))

    async def install_space_schedules(self, config: SpaceConfig, after: datetime) -> None:
        """Makes the space's schedules exactly what its configuration asks for: the hourly and daily
        jobs, partition upkeep, dedupe rotation and one export per schedule. Idempotent."""
        async with self._uow(config.space_id) as uow:
            await replace_space_schedules(uow, specs_for(config, after))


async def save_schedules(uow: UnitOfWork, specs: Sequence[ScheduleSpec]) -> None:
    for spec in specs:
        await uow.pipeline.upsert_schedule(
            spec.space_id, spec.task, spec.first_run, spec.every_seconds, key=spec.key, payload=spec.payload
        )


async def replace_space_schedules(uow: UnitOfWork, specs: Sequence[ScheduleSpec]) -> None:
    """Makes the space's managed schedules exactly `specs`, in the caller's transaction on its database."""
    await save_schedules(uow, specs)
    for row in stale_schedules(uow.space_id, specs, await uow.pipeline.list_schedules()):
        await uow.pipeline.delete_schedule(row.space_id, row.task, row.key)


def specs_for(config: SpaceConfig, after: datetime) -> list[ScheduleSpec]:
    exports = [(e.schedule_id, e.interval_hours) for e in config.export_schedules]
    return space_schedules(config.space_id, exports, after)
