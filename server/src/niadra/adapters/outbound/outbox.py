"""Publish right after commit, so the outbox only holds rows for moments (5.5).

`PublishingUnitOfWorkFactory` wraps the real factory: once a transaction commits, the tasks it
enqueued go to the broker in the background. Their outbox rows are deleted in batches, one statement
per space every half second rather than one transaction per commit, still well before the relay would
take them as left behind (2 s). If the publish takes longer than the timeout or fails, the rows stay and
the doorbell wakes the space's relay for them; a transaction that scheduled a task for later rings it
for that time (06, 6.3).
"""

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.domain.pipeline.due import STRAGGLER_AGE
from niadra.domain.tasks import Task
from niadra.ports.infra import Doorbell, TaskBus
from niadra.ports.uow import CellUnitOfWork, SpaceUnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

PUBLISH_TIMEOUT_S = 0.1
DELETE_EVERY_S = 0.5


class OutboxPublisher:
    def __init__(
        self,
        bus: TaskBus,
        uow: UnitOfWorkFactory,
        timeout_s: float = PUBLISH_TIMEOUT_S,
        *,
        doorbell: Doorbell | None = None,
        delete_every_s: float = DELETE_EVERY_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._bus = bus
        # The unwrapped factory: deleting rows must not publish anything itself.
        self._uow = uow
        self._timeout_s = timeout_s
        self._doorbell = doorbell
        self._delete_every_s = delete_every_s
        self._now = now
        self._pending: set[asyncio.Task[None]] = set()
        self._published: dict[UUID, list[UUID]] = defaultdict(list)
        self._deleting: asyncio.Task[None] | None = None

    def publish_committed(self, tasks: Sequence[Task]) -> None:
        if not tasks:
            return
        self._background(self._publish(list(tasks)))

    def ring_committed(self, space_id: UUID, at: datetime | None) -> None:
        """A committed transaction scheduled a task for `at`: the space's relay wakes then."""
        if at is not None and self._doorbell is not None:
            self._background(self._ring([space_id], at))

    async def drain(self) -> None:
        """For shutdown and tests: waits for the background publishes and deletes what they sent now."""
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        if self._deleting is not None:
            self._deleting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._deleting
            self._deleting = None
        await self.delete_published()

    async def delete_published(self) -> None:
        batch, self._published = self._published, defaultdict(list)
        for space_id, ids in batch.items():
            await self._delete(space_id, ids)

    def _background(self, work: Coroutine[Any, Any, None]) -> None:
        job = asyncio.create_task(work)
        self._pending.add(job)
        job.add_done_callback(self._pending.discard)

    async def _publish(self, tasks: list[Task]) -> None:
        try:
            async with asyncio.timeout(self._timeout_s):
                await self._bus.publish(tasks)
        except Exception:
            log.info("publish after commit deferred to the relay", extra={"tasks": len(tasks)})
            if self._doorbell is not None:
                await self._ring(sorted({t.space_id for t in tasks}), self._now() + STRAGGLER_AGE)
            return
        for task in tasks:
            self._published[task.space_id].append(task.id)
        if self._deleting is None or self._deleting.done():
            self._deleting = asyncio.create_task(self._delete_soon())

    async def _delete_soon(self) -> None:
        await asyncio.sleep(self._delete_every_s)
        await self.delete_published()

    async def _delete(self, space_id: UUID, ids: list[UUID]) -> None:
        with contextlib.suppress(Exception):
            # A row left behind only means the relay publishes a duplicate; tasks are idempotent.
            async with self._uow(space_id) as uow:
                await uow.pipeline.delete_outbox(ids)

    async def _ring(self, spaces: Sequence[UUID], at: datetime) -> None:
        assert self._doorbell is not None
        for space_id in spaces:
            try:
                await self._doorbell.ring(space_id, at)
            except Exception:
                # The relay still comes back at its cap; the doorbell only makes it sooner.
                log.info("doorbell ring failed", extra={"space_id": str(space_id)})
                return


class PublishingUnitOfWorkFactory:
    """Wraps the space transactions, which are the only ones with an outbox."""

    def __init__(self, inner: UnitOfWorkFactory, publisher: OutboxPublisher) -> None:
        self._inner = inner
        self._publisher = publisher

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[SpaceUnitOfWork]:
        return self._publishing(space_id, self._inner(space_id))

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[SpaceUnitOfWork]:
        return self._publishing(space_id, self._inner.system(space_id))

    def cell(self) -> AbstractAsyncContextManager[CellUnitOfWork]:
        return self._inner.cell()

    @asynccontextmanager
    async def _publishing(
        self, space_id: UUID, transaction: AbstractAsyncContextManager[SpaceUnitOfWork]
    ) -> AsyncIterator[SpaceUnitOfWork]:
        async with transaction as uow:
            yield uow
        self._publisher.publish_committed(uow.pipeline.enqueued)
        self._publisher.ring_committed(space_id, uow.pipeline.scheduled_at)
