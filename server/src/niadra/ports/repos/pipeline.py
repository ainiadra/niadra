"""Outbox, scheduled tasks, per-profile coalescing counters, leases and schedules (space database), and
dead tasks (cell database) (5.5)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.due import PipelineDue
from niadra.domain.pipeline.lease import LeaseGrant
from niadra.domain.pipeline.schedule import DueSchedule, ScheduleKey
from niadra.domain.tasks import Task, TaskName


class TaskStore(Protocol):
    """Owned by the `pipeline` module. Every module enqueues work only through this port."""

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        """Outbox rows in the caller's transaction (L2)."""
        ...

    async def schedule(self, task: Task, due_at: datetime, reason: DelayReason) -> None:
        """A row in `scheduled_tasks`; the relay publishes the task once `due_at` passes."""
        ...

    async def request_work(self, task: Task) -> None:
        """Coalesced enqueue for state-recomputing tasks (`compile`, `derive_traits`, `trigger`):
        bumps `requested` and adds the outbox row only if no request was pending (5.5)."""
        ...

    async def begin_work(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        """Returns the `requested` counter the task is about to satisfy."""
        ...

    async def finish_work(self, space_id: UUID, profile_id: UUID, name: TaskName, seen: int) -> bool:
        """Records `done = seen`; True when newer requests arrived and the task must run again."""
        ...

    async def work_done(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        """The `done` counter: every request up to it was satisfied (0 when none was ever made)."""
        ...

    @property
    def enqueued(self) -> list[Task]:
        """Tasks added to the outbox by this transaction, for the publish-after-commit path."""
        ...

    @property
    def scheduled_at(self) -> datetime | None:
        """The earliest `due_at` this transaction scheduled: after commit it rings the doorbell, so the
        relay of a quiet space wakes for it instead of polling."""
        ...

    async def acquire(
        self, space_id: UUID, unit_keys: Sequence[str], owner: str, ttl_s: int, now: datetime
    ) -> LeaseGrant | None:
        """Takes every unit in sorted order, or none: None means busy, which is not a failure."""
        ...

    async def renew(self, grant: LeaseGrant, ttl_s: int, now: datetime) -> bool:
        """False when any unit changed owner or epoch: the grant is lost."""
        ...

    async def release(self, grant: LeaseGrant) -> None: ...

    async def check_fence(self, space_id: UUID, unit_key: str, owner: str, epoch: int) -> None:
        """Locks the lease row until commit; raises LeaseLost if owner or epoch moved on (5.5)."""
        ...

    async def take_stale_outbox(self, older_than: datetime, limit: int) -> list[Task]:
        """Rows the writer did not publish, locked with SKIP LOCKED so relays split the backlog."""
        ...

    async def delete_outbox(self, ids: Sequence[UUID]) -> None: ...

    async def move_due_scheduled(self, now: datetime, limit: int) -> list[Task]:
        """Deletes and returns due rows, locked with SKIP LOCKED; the caller publishes before commit."""
        ...

    async def try_scheduler_lock(self) -> bool:
        """Transaction-scoped advisory lock of the database: one scheduler of the space at a time (4.5)."""
        ...

    async def due_schedules(self, now: datetime, limit: int) -> list[DueSchedule]: ...

    async def set_next_run(
        self, space_id: UUID, name: TaskName, next_run: datetime, key: str = ""
    ) -> None: ...

    async def upsert_schedule(
        self,
        space_id: UUID,
        name: TaskName,
        first_run: datetime,
        every_seconds: int,
        timezone: str = "UTC",
        *,
        key: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Inserts with `first_run`; an existing row keeps `next_run` and takes the new period and payload."""
        ...

    async def delete_schedule(self, space_id: UUID, name: TaskName, key: str = "") -> None: ...

    async def list_schedules(self) -> list[ScheduleKey]:
        """Every schedule of the space database."""
        ...

    async def ensure_week_partitions(self, parents: Sequence[str], week_starts_ms: Sequence[int]) -> None:
        """Space maintenance, as the system role: weekly range partitions of the UUIDv7-keyed tables
        (06, 6.2)."""
        ...


class LeaseStore(Protocol):
    """A task's lease taken and given back outside any transaction of the task, one round trip each (5.5)."""

    async def acquire(
        self, space_id: UUID, unit_keys: list[str], owner: str, ttl_s: int, now: datetime
    ) -> LeaseGrant | None:
        """Every unit or none; None means busy, which is not a failure."""
        ...

    async def release(self, grant: LeaseGrant) -> None: ...


class PipelineProbe(Protocol):
    """What the relay and the scheduler of a space have due, in one read with no transaction (06, 6.3)."""

    async def due(self, space_id: UUID) -> PipelineDue: ...


class DeadTaskRepository(Protocol):
    """Owned by the `pipeline` module; tasks that exhausted their attempts, in the cell database so a
    task of a space whose database is unavailable can still be buried."""

    async def bury(self, task: Task, attempts: int, error: str | None, now: datetime) -> None:
        """Copies a task that exhausted its attempts to `dead_tasks` (30 days)."""
        ...

    async def dead(
        self, space_id: UUID | None, name: TaskName | None, since: datetime | None, limit: int
    ) -> list[Task]: ...

    async def delete_dead(self, space_id: UUID, ids: Sequence[UUID]) -> None: ...
