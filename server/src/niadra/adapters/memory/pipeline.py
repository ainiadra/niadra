"""In-memory `TaskStore` for unit tests.

It has no commit latency, so every outbox row counts as stale for the relay, and the
scheduler lock is always free.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.errors import LeaseLost
from niadra.domain.common.ids import uuid7_ms
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.due import PipelineDue
from niadra.domain.pipeline.lease import LeaseGrant
from niadra.domain.pipeline.schedule import DueSchedule, ScheduleKey
from niadra.domain.tasks import Task, TaskName


class InMemoryTaskStore:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("pipeline")
        for name, empty in (
            ("outbox", list),
            ("scheduled", list),
            ("work", dict),
            ("leases", dict),
            ("dead", dict),
            ("schedules", dict),
            ("partitions", set),
        ):
            self._s.setdefault(name, empty())
        self._enqueued: list[Task] = []
        self._scheduled_at: datetime | None = None

    @property
    def outbox(self) -> list[Task]:
        rows: list[Task] = self._s["outbox"]
        return rows

    @property
    def scheduled(self) -> list[tuple[Task, datetime, str]]:
        rows: list[tuple[Task, datetime, str]] = self._s["scheduled"]
        return rows

    @property
    def leases(self) -> dict[tuple[UUID, str], tuple[str, int, datetime]]:
        rows: dict[tuple[UUID, str], tuple[str, int, datetime]] = self._s["leases"]
        return rows

    @property
    def partitions(self) -> set[tuple[str, int]]:
        rows: set[tuple[str, int]] = self._s["partitions"]
        return rows

    @property
    def enqueued(self) -> list[Task]:
        return self._enqueued

    @property
    def scheduled_at(self) -> datetime | None:
        return self._scheduled_at

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        known = {t.id for t in self.outbox}
        fresh = [t for t in tasks if t.id not in known]
        self.outbox.extend(fresh)
        self._enqueued.extend(fresh)

    async def schedule(self, task: Task, due_at: datetime, reason: DelayReason) -> None:
        reason = DelayReason(reason)  # the column's CHECK refuses any other value
        if all(t.id != task.id for t, _, _ in self.scheduled):
            self.scheduled.append((task, due_at, reason))
        self._scheduled_at = min(due_at, self._scheduled_at or due_at)

    async def request_work(self, task: Task) -> None:
        assert task.profile_id is not None, "coalesced tasks are per profile"
        key = (task.space_id, task.profile_id, task.name)
        requested, done = self._s["work"].get(key, (0, 0))
        self._s["work"][key] = (requested + 1, done)
        if requested == done:
            await self.enqueue([task])

    async def begin_work(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        return int(self._s["work"].get((space_id, profile_id, name), (0, 0))[0])

    async def finish_work(self, space_id: UUID, profile_id: UUID, name: TaskName, seen: int) -> bool:
        key = (space_id, profile_id, name)
        requested, done = self._s["work"].get(key, (seen, seen))
        self._s["work"][key] = (requested, max(done, seen))
        return bool(requested > seen)

    async def work_done(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        return int(self._s["work"].get((space_id, profile_id, name), (0, 0))[1])

    async def acquire(
        self, space_id: UUID, unit_keys: Sequence[str], owner: str, ttl_s: int, now: datetime
    ) -> LeaseGrant | None:
        keys = sorted(set(unit_keys))
        for key in keys:
            held = self.leases.get((space_id, key))
            if held and held[0] != owner and held[2] > now:
                return None
        epochs = {}
        for key in keys:
            held = self.leases.get((space_id, key))
            epochs[key] = held[1] + 1 if held else 1
            self.leases[(space_id, key)] = (owner, epochs[key], now + timedelta(seconds=ttl_s))
        return LeaseGrant(space_id, owner, epochs)

    async def renew(self, grant: LeaseGrant, ttl_s: int, now: datetime) -> bool:
        if not all(self._holds(grant, key) for key in grant.epochs):
            return False
        for key, epoch in grant.epochs.items():
            self.leases[(grant.space_id, key)] = (grant.owner, epoch, now + timedelta(seconds=ttl_s))
        return True

    async def release(self, grant: LeaseGrant) -> None:
        for key in grant.epochs:
            if self._holds(grant, key):
                del self.leases[(grant.space_id, key)]

    async def check_fence(self, space_id: UUID, unit_key: str, owner: str, epoch: int) -> None:
        held = self.leases.get((space_id, unit_key))
        if held is None or held[:2] != (owner, epoch):
            raise LeaseLost("another worker holds the lease")

    async def take_stale_outbox(self, older_than: datetime, limit: int) -> list[Task]:  # noqa: ARG002
        return list(self.outbox[:limit])

    async def delete_outbox(self, ids: Sequence[UUID]) -> None:
        doomed = set(ids)
        self._s["outbox"] = [t for t in self.outbox if t.id not in doomed]

    async def move_due_scheduled(self, now: datetime, limit: int) -> list[Task]:
        due = sorted((row for row in self.scheduled if row[1] <= now), key=lambda row: row[1])[:limit]
        moved = {t.id for t, _, _ in due}
        self._s["scheduled"] = [row for row in self.scheduled if row[0].id not in moved]
        return [t for t, _, _ in due]

    async def try_scheduler_lock(self) -> bool:
        return True

    async def due_schedules(self, now: datetime, limit: int) -> list[DueSchedule]:
        rows = [
            DueSchedule(
                k.space_id, k.task, row["next_run"], row["every_seconds"], k.key, dict(row["payload"])
            )
            for k, row in self._s["schedules"].items()
            if row["next_run"] <= now
        ]
        return sorted(rows, key=lambda r: r.next_run)[:limit]

    async def set_next_run(self, space_id: UUID, name: TaskName, next_run: datetime, key: str = "") -> None:
        self._s["schedules"][ScheduleKey(space_id, name, key)]["next_run"] = next_run

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
        row: dict[str, Any] = self._s["schedules"].setdefault(
            ScheduleKey(space_id, name, key), {"next_run": first_run}
        )
        row.update(every_seconds=every_seconds, timezone=timezone, payload=dict(payload or {}))

    async def delete_schedule(self, space_id: UUID, name: TaskName, key: str = "") -> None:
        self._s["schedules"].pop(ScheduleKey(space_id, name, key), None)

    async def list_schedules(self) -> list[ScheduleKey]:
        return list(self._s["schedules"])

    async def ensure_week_partitions(self, parents: Sequence[str], week_starts_ms: Sequence[int]) -> None:
        self.partitions.update((parent, start) for parent in parents for start in week_starts_ms)

    def take(self, name: TaskName | None = None) -> list[Task]:
        taken = [t for t in self.outbox if name is None or t.name == name]
        self._s["outbox"] = [t for t in self.outbox if t not in taken]
        return taken

    def _holds(self, grant: LeaseGrant, key: str) -> bool:
        held = self.leases.get((grant.space_id, key))
        return held is not None and held[:2] == (grant.owner, grant.epochs[key])


class InMemoryDeadTaskRepository:
    """Dead tasks, kept in the `pipeline` namespace, which owns them."""

    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("pipeline")
        self._s.setdefault("dead", {})

    async def bury(self, task: Task, attempts: int, error: str | None, now: datetime) -> None:
        self._s["dead"].setdefault((task.space_id, task.id), (task, attempts, error, now))

    async def dead(
        self, space_id: UUID | None, name: TaskName | None, since: datetime | None, limit: int
    ) -> list[Task]:
        rows: list[tuple[Task, int, str | None, datetime]] = sorted(
            self._s["dead"].values(), key=lambda r: r[3]
        )
        return [
            task
            for task, _, _, dead_at in rows
            if (space_id is None or task.space_id == space_id)
            and (name is None or task.name == name)
            and (since is None or dead_at >= since)
        ][:limit]

    async def delete_dead(self, space_id: UUID, ids: Sequence[UUID]) -> None:
        for task_id in ids:
            self._s["dead"].pop((space_id, task_id), None)


class InMemoryPipelineProbe:
    """`PipelineProbe` over the in-memory store, for unit tests. An outbox row counts as written at its
    UUIDv7's time."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def due(self, space_id: UUID) -> PipelineDue:
        pipeline = self._store.module("pipeline")
        outbox = [uuid7_ms(t.id) for t in pipeline.get("outbox", []) if t.space_id == space_id]
        scheduled = [due for t, due, _ in pipeline.get("scheduled", []) if t.space_id == space_id]
        schedules = [
            row["next_run"] for key, row in pipeline.get("schedules", {}).items() if key.space_id == space_id
        ]
        intake = [r for (s, _), r in self._store.module("ingest").get("intake", {}).items() if s == space_id]
        return PipelineDue(
            outbox_oldest=datetime.fromtimestamp(min(outbox) / 1000, UTC) if outbox else None,
            scheduled_due=min(scheduled, default=None),
            schedule_due=min(schedules, default=None),
            intake_pending=len(intake),
            intake_oldest=min((r.received_at for r in intake), default=None),
        )
