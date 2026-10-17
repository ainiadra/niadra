"""The in-memory TaskStore: the same coalescing and lease rules as PostgreSQL (5.5)."""

from datetime import UTC, datetime, timedelta

import pytest

from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.errors import LeaseLost
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


@pytest.fixture
def store() -> InMemoryTaskStore:
    return InMemoryTaskStore(MemoryStore())


async def test_a_burst_of_requests_enqueues_one_task(store: InMemoryTaskStore) -> None:
    profile = uuid7()
    for _ in range(200):
        await store.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))
    assert len(store.take(TaskName.COMPILE)) == 1


async def test_a_request_that_arrives_during_the_task_makes_it_run_again(store: InMemoryTaskStore) -> None:
    profile = uuid7()
    await store.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))
    seen = await store.begin_work(SPACE_ID, profile, TaskName.COMPILE)
    await store.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))
    assert len(store.take()) == 1  # the second request found one pending
    assert await store.finish_work(SPACE_ID, profile, TaskName.COMPILE, seen) is True
    seen = await store.begin_work(SPACE_ID, profile, TaskName.COMPILE)
    assert await store.finish_work(SPACE_ID, profile, TaskName.COMPILE, seen) is False
    await store.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))
    assert len(store.take()) == 1  # nothing pending any more: a new request enqueues again


async def test_done_never_moves_backwards(store: InMemoryTaskStore) -> None:
    profile = uuid7()
    for _ in range(3):
        await store.request_work(Task(TaskName.TRIGGER, SPACE_ID, profile_id=profile))
    await store.finish_work(SPACE_ID, profile, TaskName.TRIGGER, 3)
    await store.finish_work(SPACE_ID, profile, TaskName.TRIGGER, 1)
    await store.request_work(Task(TaskName.TRIGGER, SPACE_ID, profile_id=profile))
    assert len(store.take()) == 2


async def test_a_held_lease_is_busy_and_an_expired_one_is_taken_with_a_new_epoch(
    store: InMemoryTaskStore,
) -> None:
    first = await store.acquire(SPACE_ID, ["profile:a"], "w1", 60, NOW)
    assert first is not None
    assert first.epochs == {"profile:a": 1}
    assert await store.acquire(SPACE_ID, ["profile:a"], "w2", 60, NOW + timedelta(seconds=59)) is None
    second = await store.acquire(SPACE_ID, ["profile:a"], "w2", 60, NOW + timedelta(seconds=61))
    assert second is not None
    assert second.epochs == {"profile:a": 2}
    with pytest.raises(LeaseLost):
        await store.check_fence(SPACE_ID, "profile:a", "w1", 1)
    await store.check_fence(SPACE_ID, "profile:a", "w2", 2)
    assert await store.renew(first, 60, NOW + timedelta(seconds=62)) is False


async def test_multi_unit_acquire_is_all_or_nothing(store: InMemoryTaskStore) -> None:
    await store.acquire(SPACE_ID, ["profile:b"], "w1", 60, NOW)
    assert await store.acquire(SPACE_ID, ["profile:a", "profile:b"], "w2", 60, NOW) is None
    assert "profile:a" not in {k for _, k in store.leases}


async def test_release_only_drops_what_the_grant_still_holds(store: InMemoryTaskStore) -> None:
    old = await store.acquire(SPACE_ID, ["space"], "w1", 60, NOW)
    new = await store.acquire(SPACE_ID, ["space"], "w2", 60, NOW + timedelta(minutes=2))
    assert old is not None
    assert new is not None
    await store.release(old)
    await store.check_fence(SPACE_ID, "space", "w2", new.epochs["space"])
    await store.release(new)
    assert not store.leases


async def test_scheduled_tasks_move_only_when_due_and_in_due_order(store: InMemoryTaskStore) -> None:
    late, early = Task(TaskName.EXTRACT, SPACE_ID), Task(TaskName.GATE, SPACE_ID, profile_id=uuid7())
    await store.schedule(late, NOW + timedelta(seconds=10), DelayReason.RETRY)
    await store.schedule(early, NOW + timedelta(seconds=5), DelayReason.LEASE_BUSY)
    await store.schedule(early, NOW + timedelta(seconds=5), DelayReason.LEASE_BUSY)
    assert await store.move_due_scheduled(NOW, 10) == []
    assert await store.move_due_scheduled(NOW + timedelta(seconds=30), 10) == [early, late]
    assert store.scheduled == []


async def test_a_reason_the_database_would_refuse_is_refused_here_too(store: InMemoryTaskStore) -> None:
    # The CHECK on `scheduled_tasks.reason` lost these delays in production (25/09); the fake agrees with it.
    with pytest.raises(ValueError, match="busy_space"):
        await store.schedule(Task(TaskName.EXTRACT, SPACE_ID), NOW, "busy_space")  # type: ignore[arg-type]
    assert store.scheduled == []
