from datetime import UTC, datetime, timedelta
from itertools import pairwise
from uuid import UUID

import pytest

from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.pipeline.lease import lease_units, profile_unit
from niadra.domain.pipeline.retry import (
    ATTEMPT_KEY,
    attempt_of,
    error_summary,
    retry_delay_s,
    with_attempt,
    without_attempt,
)
from niadra.domain.pipeline.schedule import (
    DAY_S,
    HOUR_S,
    MAINTENANCE_DAILY,
    MINUTE_S,
    WEEK_MS,
    ScheduleKey,
    first_slot,
    following_run,
    space_schedules,
    stale_schedules,
    week_starts,
)
from niadra.domain.tasks import Task, TaskName

SPACE = UUID("0192f5a0-0000-7000-8000-00000000a001")
P1 = UUID("0192f5a0-0000-7000-8000-000000000001")
P2 = UUID("0192f5a0-0000-7000-8000-000000000002")


def test_lease_units_follow_the_catalog() -> None:
    assert lease_units(Task(TaskName.EXTRACT, SPACE)) == ()
    assert lease_units(Task(TaskName.COMPILE, SPACE, profile_id=P1)) == (profile_unit(P1),)
    assert lease_units(Task(TaskName.EXPORT_RUN, SPACE)) == ("space",)


def test_the_minute_inference_never_holds_what_pushes_and_timers_wait_for() -> None:
    """`observe_config` runs every minute and may call the model to word new names; holding the space's lease
    it kept a system's state push (`apply_observations`) and the due timers waiting for it. Two runs of it for
    one space still exclude each other."""
    space = UUID(int=7)
    observe = set(lease_units(Task(TaskName.OBSERVE_CONFIG, space)))
    assert observe
    for name in (TaskName.APPLY_OBSERVATIONS, TaskName.FIRE_TIMERS):
        assert not observe & set(lease_units(Task(name, space)))
    assert observe == set(lease_units(Task(TaskName.OBSERVE_CONFIG, space)))


def test_multi_profile_leases_come_in_a_fixed_order_whatever_the_payload_order() -> None:
    a = Task(TaskName.IDENTITY_APPLY, SPACE, payload={"profile_ids": [str(P2), str(P1)]})
    b = Task(TaskName.IDENTITY_APPLY, SPACE, payload={"profile_ids": [str(P1)]}, profile_id=P2)
    assert lease_units(a) == lease_units(b) == tuple(sorted([profile_unit(P1), profile_unit(P2)]))


def test_a_session_task_of_no_customer_holds_no_lease() -> None:
    for name in (TaskName.CLOSE_SESSION, TaskName.GATE, TaskName.RECONCILE):
        assert lease_units(Task(name, SPACE)) == ()
        assert lease_units(Task(name, SPACE, profile_id=P1)) == (profile_unit(P1),)


def test_a_profile_task_without_a_profile_is_rejected() -> None:
    with pytest.raises(InvalidInput):
        lease_units(Task(TaskName.COMPILE, SPACE))
    with pytest.raises(InvalidInput):
        lease_units(Task(TaskName.IDENTITY_APPLY, SPACE))


def test_attempts_travel_in_the_payload_and_handlers_never_see_them() -> None:
    task = Task(TaskName.EXTRACT, SPACE, payload={"session_id": "s1"})
    assert attempt_of(task) == 1
    retried = with_attempt(task, 3)
    assert attempt_of(retried) == 3
    assert retried.id == task.id
    assert without_attempt(retried).payload == {"session_id": "s1"}
    assert ATTEMPT_KEY in Task.from_message(retried.to_message()).payload


def test_backoff_doubles_and_is_capped() -> None:
    assert [retry_delay_s(n) for n in (1, 2, 3)] == [5.0, 10.0, 20.0]
    assert retry_delay_s(30) == 900.0
    assert retry_delay_s(1, jitter=0.5) == pytest.approx(5.5)


def test_error_summary_never_keeps_foreign_messages() -> None:
    assert error_summary(ValueError("+55 11 99999-0000 is not valid")) == "ValueError"
    assert error_summary(NotFound("unknown session")) == "not_found: unknown session"


def test_following_run_skips_missed_slots_instead_of_bursting() -> None:
    start = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    assert following_run(start, 3600, start - timedelta(minutes=1)) == start
    assert following_run(start, 3600, start) == start + timedelta(hours=1)
    assert following_run(start, 3600, start + timedelta(hours=5, minutes=10)) == start + timedelta(hours=6)


def test_week_starts_are_aligned_like_the_partition_names() -> None:
    now_ms = int(datetime(2026, 9, 23, 15, tzinfo=UTC).timestamp() * 1000)
    starts = week_starts(now_ms)
    assert len(starts) == 5
    assert all(s % WEEK_MS == 0 for s in starts)
    assert starts[0] <= now_ms < starts[1]
    assert [b - a for a, b in pairwise(starts)] == [WEEK_MS] * 4


AFTER = datetime(2026, 9, 23, 7, 40, tzinfo=UTC)


def test_every_space_gets_its_minute_hourly_and_daily_work_and_one_run_per_export() -> None:
    specs = space_schedules(SPACE, [("lake", 6), ("siem", 1)], AFTER)
    periods = {(s.task, s.key): s.every_seconds for s in specs}
    assert periods == {
        (TaskName.OBSERVE_CONFIG, ""): MINUTE_S,
        (TaskName.USAGE_ROLLUP, ""): HOUR_S,
        (TaskName.ROLLUP, ""): DAY_S,
        (TaskName.AUDIT_ANCHOR, ""): DAY_S,
        (TaskName.RETENTION_SWEEP, ""): DAY_S,
        (TaskName.IDENTITY_SUGGEST, ""): DAY_S,
        (TaskName.ARCHIVE_EVENTS, ""): DAY_S,
        (TaskName.PARTITION_MAINTENANCE, ""): DAY_S,
        (TaskName.DEDUPE_ROTATE, ""): DAY_S,
        (TaskName.EXPORT_RUN, "lake"): 6 * HOUR_S,
        (TaskName.EXPORT_RUN, "siem"): HOUR_S,
    }
    exports = {s.key: dict(s.payload) for s in specs if s.task is TaskName.EXPORT_RUN}
    assert exports == {"lake": {"schedule_id": "lake"}, "siem": {"schedule_id": "siem"}}
    assert {s.space_id for s in specs} == {SPACE}


def test_first_runs_start_at_the_next_boundary_with_a_stable_bounded_spread() -> None:
    midnight = datetime(2026, 9, 24, tzinfo=UTC)
    daily = first_slot(SPACE, TaskName.AUDIT_ANCHOR, "", DAY_S, AFTER)
    assert midnight <= daily < midnight + timedelta(hours=1)
    hourly = first_slot(SPACE, TaskName.USAGE_ROLLUP, "", HOUR_S, AFTER)
    assert datetime(2026, 9, 23, 8, tzinfo=UTC) <= hourly < datetime(2026, 9, 23, 8, 7, 30, tzinfo=UTC)
    assert first_slot(SPACE, TaskName.AUDIT_ANCHOR, "", DAY_S, AFTER) == daily
    spreads = {first_slot(UUID(int=i), TaskName.AUDIT_ANCHOR, "", DAY_S, AFTER) for i in range(1, 50)}
    assert len(spreads) > 40


def test_every_space_runs_its_own_partition_upkeep_and_dedupe_rotation_daily() -> None:
    specs = [s for s in space_schedules(SPACE, [], AFTER) if s.task in MAINTENANCE_DAILY]
    assert sorted(s.task for s in specs) == [TaskName.DEDUPE_ROTATE, TaskName.PARTITION_MAINTENANCE]
    assert {(s.space_id, s.every_seconds) for s in specs} == {(SPACE, DAY_S)}


def test_stale_schedules_are_the_space_s_removed_exports_only() -> None:
    desired = space_schedules(SPACE, [("lake", 24)], AFTER)
    existing = [
        *(s.ident for s in desired),
        ScheduleKey(SPACE, TaskName.EXPORT_RUN, "old-bucket"),
        ScheduleKey(SPACE, TaskName.HOT_REBUILD),
        # Another space's rows live in another database; a stray one is never this space's to delete.
        ScheduleKey(UUID(int=7), TaskName.RETENTION_SWEEP),
    ]
    assert stale_schedules(SPACE, desired, existing) == [
        ScheduleKey(SPACE, TaskName.EXPORT_RUN, "old-bucket")
    ]
