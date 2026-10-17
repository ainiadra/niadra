"""PostgreSQL implementations of `TaskStore` (space database) and `DeadTaskRepository` (cell database)
(5.5)."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.schema.platform import (
    DeadTask,
    Outbox,
    ProfileWork,
    QueueLease,
    Schedule,
    ScheduledTask,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.errors import LeaseLost
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.lease import LeaseGrant
from niadra.domain.pipeline.schedule import DueSchedule, ScheduleKey
from niadra.domain.tasks import Task, TaskName

# Any constant works as long as no other advisory lock in the database uses it. Advisory locks are per
# database, so each space database has its own scheduler lock.
SCHEDULER_LOCK_KEY = 0x6E696164_7261_01


class PgTaskStore:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer
        self._enqueued: list[Task] = []
        self._scheduled_at: datetime | None = None

    @property
    def enqueued(self) -> list[Task]:
        return self._enqueued

    @property
    def scheduled_at(self) -> datetime | None:
        return self._scheduled_at

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        if not tasks:
            return
        rows = [_task_row(t) for t in tasks]
        stmt = insert(Outbox).values(rows).on_conflict_do_nothing().returning(Outbox.id)
        inserted = set((await self._db.execute(stmt)).scalars())
        self._enqueued.extend(t for t in tasks if t.id in inserted)

    async def schedule(self, task: Task, due_at: datetime, reason: DelayReason) -> None:
        row = {**_task_row(task), "due_at": due_at, "reason": DelayReason(reason).value}
        await self._db.execute(insert(ScheduledTask).values(row).on_conflict_do_nothing())
        self._scheduled_at = min(due_at, self._scheduled_at or due_at)

    async def request_work(self, task: Task) -> None:
        assert task.profile_id is not None, "coalesced tasks are per profile"
        # The unconditional upsert keeps the row locked until commit, so a finishing task's
        # `finish_work` waits for this request and sees it (5.5).
        stmt = (
            insert(ProfileWork)
            .values(
                space_id=task.space_id, profile_id=task.profile_id, task=task.name.value, requested=1, done=0
            )
            .on_conflict_do_update(
                index_elements=[ProfileWork.space_id, ProfileWork.profile_id, ProfileWork.task],
                set_={"requested": ProfileWork.requested + 1},
            )
            .returning(ProfileWork.requested, ProfileWork.done)
        )
        requested, done = (await self._db.execute(stmt)).one()
        if requested - 1 == done:
            await self.enqueue([task])

    async def begin_work(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        stmt = select(ProfileWork.requested).where(*_work_key(space_id, profile_id, name))
        return int((await self._db.execute(stmt)).scalar_one_or_none() or 0)

    async def finish_work(self, space_id: UUID, profile_id: UUID, name: TaskName, seen: int) -> bool:
        stmt = (
            update(ProfileWork)
            .where(*_work_key(space_id, profile_id, name))
            .values(done=func.greatest(ProfileWork.done, seen))
            .returning(ProfileWork.requested)
        )
        requested = (await self._db.execute(stmt)).scalar_one_or_none()
        return requested is not None and requested > seen

    async def work_done(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        stmt = select(ProfileWork.done).where(*_work_key(space_id, profile_id, name))
        return int((await self._db.execute(stmt)).scalar_one_or_none() or 0)

    async def acquire(
        self, space_id: UUID, unit_keys: Sequence[str], owner: str, ttl_s: int, now: datetime
    ) -> LeaseGrant | None:
        expires_at = now + timedelta(seconds=ttl_s)
        epochs: dict[str, int] = {}
        # Sorted order: two workers taking overlapping sets wait on each other instead of deadlocking.
        for key in sorted(set(unit_keys)):
            stmt = (
                insert(QueueLease)
                .values(space_id=space_id, unit_key=key, owner=owner, epoch=1, expires_at=expires_at)
                .on_conflict_do_update(
                    index_elements=[QueueLease.space_id, QueueLease.unit_key],
                    set_={"owner": owner, "epoch": QueueLease.epoch + 1, "expires_at": expires_at},
                    where=or_(QueueLease.expires_at <= now, QueueLease.owner == owner),
                )
                .returning(QueueLease.epoch)
            )
            epoch = (await self._db.execute(stmt)).scalar_one_or_none()
            if epoch is None:
                if epochs:
                    await self.release(LeaseGrant(space_id, owner, epochs))
                return None
            epochs[key] = epoch
        return LeaseGrant(space_id, owner, epochs)

    async def renew(self, grant: LeaseGrant, ttl_s: int, now: datetime) -> bool:
        stmt = (
            update(QueueLease)
            .where(QueueLease.space_id == grant.space_id, _held(grant))
            .values(expires_at=now + timedelta(seconds=ttl_s))
            .returning(QueueLease.unit_key)
        )
        renewed = set((await self._db.execute(stmt)).scalars())
        return renewed == set(grant.epochs)

    async def release(self, grant: LeaseGrant) -> None:
        await self._db.execute(delete(QueueLease).where(QueueLease.space_id == grant.space_id, _held(grant)))

    async def check_fence(self, space_id: UUID, unit_key: str, owner: str, epoch: int) -> None:
        stmt = (
            select(QueueLease.owner, QueueLease.epoch)
            .where(QueueLease.space_id == space_id, QueueLease.unit_key == unit_key)
            .with_for_update()
        )
        row = (await self._db.execute(stmt)).one_or_none()
        if row is None or (row.owner, row.epoch) != (owner, epoch):
            raise LeaseLost("another worker holds the lease")

    async def take_stale_outbox(self, older_than: datetime, limit: int) -> list[Task]:
        stmt = (
            select(Outbox)
            .where(Outbox.created_at < older_than)
            .order_by(Outbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_task(row) for row in (await self._db.execute(stmt)).scalars()]

    async def delete_outbox(self, ids: Sequence[UUID]) -> None:
        if ids:
            await self._db.execute(delete(Outbox).where(Outbox.id.in_(ids)))

    async def move_due_scheduled(self, now: datetime, limit: int) -> list[Task]:
        due = (
            select(ScheduledTask.id)
            .where(ScheduledTask.due_at <= now)
            .order_by(ScheduledTask.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = delete(ScheduledTask).where(ScheduledTask.id.in_(due)).returning(*ScheduledTask.__table__.c)
        return [_task(row) for row in (await self._db.execute(stmt)).all()]

    async def try_scheduler_lock(self) -> bool:
        # A transaction lock, not a session lock: behind PgBouncer a session lock would pin
        # the server connection (06, 6.3).
        stmt = select(func.pg_try_advisory_xact_lock(SCHEDULER_LOCK_KEY))
        return bool((await self._db.execute(stmt)).scalar_one())

    async def due_schedules(self, now: datetime, limit: int) -> list[DueSchedule]:
        stmt = (
            select(
                Schedule.space_id,
                Schedule.task,
                Schedule.next_run,
                Schedule.every_seconds,
                Schedule.key,
                Schedule.payload,
            )
            .where(Schedule.next_run <= now)
            .order_by(Schedule.next_run)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [
            DueSchedule(r.space_id, TaskName(r.task), r.next_run, r.every_seconds, r.key, r.payload)
            for r in (await self._db.execute(stmt)).all()
        ]

    async def set_next_run(self, space_id: UUID, name: TaskName, next_run: datetime, key: str = "") -> None:
        stmt = (
            update(Schedule)
            .where(Schedule.space_id == space_id, Schedule.task == name.value, Schedule.key == key)
            .values(next_run=next_run)
        )
        await self._db.execute(stmt)

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
        body = dict(payload or {})
        stmt = insert(Schedule).values(
            space_id=space_id,
            task=name.value,
            key=key,
            next_run=first_run,
            every_seconds=every_seconds,
            timezone=timezone,
            payload=body,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Schedule.space_id, Schedule.task, Schedule.key],
            set_={"every_seconds": every_seconds, "timezone": timezone, "payload": body},
        )
        await self._db.execute(stmt)

    async def delete_schedule(self, space_id: UUID, name: TaskName, key: str = "") -> None:
        await self._db.execute(
            delete(Schedule).where(
                Schedule.space_id == space_id, Schedule.task == name.value, Schedule.key == key
            )
        )

    async def list_schedules(self) -> list[ScheduleKey]:
        rows = await self._db.execute(select(Schedule.space_id, Schedule.task, Schedule.key))
        return [ScheduleKey(r.space_id, TaskName(r.task), r.key) for r in rows]

    async def ensure_week_partitions(self, parents: Sequence[str], week_starts_ms: Sequence[int]) -> None:
        for parent in parents:
            for start in week_starts_ms:
                await self._db.execute(
                    text("SELECT ensure_week_partition(CAST(:parent AS regclass), :start)"),
                    {"parent": parent, "start": start},
                )


class PgDeadTaskRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def bury(self, task: Task, attempts: int, error: str | None, now: datetime) -> None:
        stmt = insert(DeadTask).values(
            space_id=task.space_id,
            id=task.id,
            task=task.name.value,
            queue=task.queue,
            payload=task.to_message(),
            attempts=attempts,
            last_error=error,
            dead_at=now,
        )
        await self._db.execute(stmt.on_conflict_do_nothing())

    async def dead(
        self, space_id: UUID | None, name: TaskName | None, since: datetime | None, limit: int
    ) -> list[Task]:
        stmt = select(DeadTask.payload).order_by(DeadTask.dead_at).limit(limit)
        if space_id is not None:
            stmt = stmt.where(DeadTask.space_id == space_id)
        if name is not None:
            stmt = stmt.where(DeadTask.task == name.value)
        if since is not None:
            stmt = stmt.where(DeadTask.dead_at >= since)
        return [Task.from_message(message) for message in (await self._db.execute(stmt)).scalars()]

    async def delete_dead(self, space_id: UUID, ids: Sequence[UUID]) -> None:
        if ids:
            await self._db.execute(
                delete(DeadTask).where(DeadTask.space_id == space_id, DeadTask.id.in_(ids))
            )


def _task_row(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "space_id": task.space_id,
        "task": task.name.value,
        "queue": task.queue,
        "profile_id": task.profile_id,
        "payload": task.payload,
    }


def _task(row: Any) -> Task:
    return Task(
        id=row.id,
        name=TaskName(row.task),
        space_id=row.space_id,
        profile_id=row.profile_id,
        payload=dict(row.payload),
    )


def _work_key(space_id: UUID, profile_id: UUID, name: TaskName) -> tuple[Any, ...]:
    return (
        ProfileWork.space_id == space_id,
        ProfileWork.profile_id == profile_id,
        ProfileWork.task == name.value,
    )


def _held(grant: LeaseGrant) -> Any:
    return or_(
        *(
            and_(QueueLease.unit_key == key, QueueLease.owner == grant.owner, QueueLease.epoch == epoch)
            for key, epoch in grant.epochs.items()
        )
    )
