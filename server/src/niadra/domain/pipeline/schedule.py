"""Time-based work: recurring schedules and weekly partition upkeep (4.5, 06 6.2)."""

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.domain.tasks import TaskName

WEEK_MS = 7 * 24 * 3600 * 1000
PARTITIONED_BY_WEEK = ("events", "event_handles", "receipts", "contact_log")
WEEKS_AHEAD = 4


MINUTE_S = 60
HOUR_S = 3600
DAY_S = 24 * HOUR_S
# Recurring work of every space, registered from the configuration snapshot (4.5).
SPACE_MINUTELY = (TaskName.OBSERVE_CONFIG,)
"""The observed types of a space follow its traffic within a minute; a run with no change
since the last reads one index and stops."""
SPACE_HOURLY = (TaskName.USAGE_ROLLUP,)
SPACE_DAILY = (
    TaskName.ROLLUP,
    TaskName.AUDIT_ANCHOR,
    TaskName.RETENTION_SWEEP,
    TaskName.IDENTITY_SUGGEST,
    # Per space, not cell-wide: the handler archives one space's month and keeps that space's index.
    TaskName.ARCHIVE_EVENTS,
)
# Upkeep of the space's own database: weekly partitions ahead of time, the dedupe tables' rotation.
MAINTENANCE_DAILY = (TaskName.PARTITION_MAINTENANCE, TaskName.DEDUPE_ROTATE)
MANAGED = frozenset({*SPACE_MINUTELY, *SPACE_HOURLY, *SPACE_DAILY, *MAINTENANCE_DAILY, TaskName.EXPORT_RUN})
MAX_SPREAD_S = HOUR_S


@dataclass(frozen=True, slots=True)
class ScheduleKey:
    """One row of `schedules`; `key` tells apart several schedules of one task, like export schedules."""

    space_id: UUID
    task: TaskName
    key: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    space_id: UUID
    task: TaskName
    every_seconds: int
    first_run: datetime
    key: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ident(self) -> ScheduleKey:
        return ScheduleKey(self.space_id, self.task, self.key)


@dataclass(frozen=True, slots=True)
class DueSchedule:
    space_id: UUID
    task: TaskName
    next_run: datetime
    every_seconds: int
    key: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


def first_slot(space_id: UUID, task: TaskName, key: str, every_seconds: int, after: datetime) -> datetime:
    """The first boundary of the period after `after` (midnight UTC for daily work, the hour for hourly),
    plus a stable offset per schedule so a cell's spaces do not all start in the same second."""
    epoch_s = int(after.timestamp())
    boundary = (epoch_s // every_seconds + 1) * every_seconds
    digest = hashlib.sha256(f"{space_id}:{task.value}:{key}".encode()).digest()
    spread = int.from_bytes(digest[:4], "big") % max(min(every_seconds // 8, MAX_SPREAD_S), 1)
    return datetime.fromtimestamp(boundary + spread, UTC)


def space_schedules(
    space_id: UUID, exports: Iterable[tuple[str, int]], after: datetime
) -> list[ScheduleSpec]:
    """What one space runs on time, its database's upkeep included; `exports` are its export schedules
    as (schedule_id, interval_hours)."""
    periodic = (
        [(task, MINUTE_S) for task in SPACE_MINUTELY]
        + [(task, HOUR_S) for task in SPACE_HOURLY]
        + [(task, DAY_S) for task in (*SPACE_DAILY, *MAINTENANCE_DAILY)]
    )
    specs = [
        ScheduleSpec(space_id, task, every, first_slot(space_id, task, "", every, after))
        for task, every in periodic
    ]
    for schedule_id, hours in exports:
        every = hours * HOUR_S
        specs.append(
            ScheduleSpec(
                space_id,
                TaskName.EXPORT_RUN,
                every,
                first_slot(space_id, TaskName.EXPORT_RUN, schedule_id, every, after),
                key=schedule_id,
                payload={"schedule_id": schedule_id},
            )
        )
    return specs


def stale_schedules(
    space_id: UUID, desired: Iterable[ScheduleSpec], existing: Iterable[ScheduleKey]
) -> list[ScheduleKey]:
    """Rows of the space the configuration no longer asks for, such as a removed export schedule. Only
    the tasks this module manages go; any other schedule, and any other space's, is left alone."""
    wanted = {spec.ident for spec in desired}
    return [row for row in existing if row.space_id == space_id and row.task in MANAGED and row not in wanted]


def following_run(next_run: datetime, every_seconds: int, now: datetime) -> datetime:
    """The first slot after `now`: a scheduler that was down skips missed slots instead of bursting."""
    if every_seconds <= 0:
        raise ValueError("every_seconds must be positive")
    step = timedelta(seconds=every_seconds)
    if next_run > now:
        return next_run
    missed = math.floor((now - next_run) / step) + 1
    return next_run + missed * step


def week_starts(now_ms: int, ahead: int = WEEKS_AHEAD) -> list[int]:
    """The current week and the next `ahead`, aligned to the epoch like `ensure_week_partition` names them."""
    current = now_ms // WEEK_MS * WEEK_MS
    return [current + i * WEEK_MS for i in range(ahead + 1)]
