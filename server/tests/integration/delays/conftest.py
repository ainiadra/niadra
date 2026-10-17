"""Every path that delays a task, run against a real space database.

The tests of this package are the unit tests of those paths, collected again here with each delay also
written, first, to a space database cloned from the migrated template. The CHECK constraint on
`scheduled_tasks.reason` then judges the reason as it does in production, where a reason outside it
failed the insert and the broker redelivered the task until it dead-lettered (25/09).
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text

from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.domain.tasks import Task
from tests.integration.conftest import CloningUnitOfWork
from tests.unit.objects.fakes import FakePipeline

Schedule = Callable[[Any, Task, datetime, str], Awaitable[None]]


@pytest.fixture(autouse=True)
async def delays_on_postgres(
    pg_uow: CloningUnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[list[str]]:
    """The reasons stored in the space database, in order; a refused one fails the test at teardown
    too, in case the path under test swallows the error (the worker runtime logs it and nacks)."""
    stored: list[str] = []
    refused: list[Exception] = []

    def through_postgres(original: Schedule) -> Schedule:
        async def schedule(self: Any, task: Task, due_at: datetime, reason: str) -> None:
            try:
                async with pg_uow(task.space_id) as uow:
                    await uow.pipeline.schedule(task, due_at, reason)  # type: ignore[arg-type]
                async with pg_uow.system(task.space_id) as uow:
                    found = await uow.session.execute(
                        text("SELECT reason FROM scheduled_tasks WHERE id = :id"), {"id": task.id}
                    )
                    stored.append(found.scalar_one())
            except Exception as exc:
                refused.append(exc)
                raise
            await original(self, task, due_at, reason)

        return schedule

    for store in (InMemoryTaskStore, FakePipeline):
        monkeypatch.setattr(store, "schedule", through_postgres(store.schedule))
    yield stored
    assert not refused, f"the space database refused a delay: {refused!r}"
