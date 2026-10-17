"""Publish right after commit; the relay only sees what that left behind (5.5)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.adapters.outbound.outbox import OutboxPublisher, PublishingUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell


def wrap(
    cell: Cell, bus: object | None = None, timeout_s: float = 0.1
) -> tuple[PublishingUnitOfWorkFactory, OutboxPublisher]:
    publisher = OutboxPublisher(bus or cell.bus, cell.uow, timeout_s)  # type: ignore[arg-type]
    return PublishingUnitOfWorkFactory(cell.uow, publisher), publisher


async def test_committed_tasks_are_published_and_their_rows_deleted(cell: Cell) -> None:
    uow, publisher = wrap(cell)
    task = Task(TaskName.GATE, SPACE_ID, profile_id=uuid7())
    async with uow(SPACE_ID) as tx:
        await tx.pipeline.enqueue([task])
    await publisher.drain()
    assert cell.bus.take() == [task]
    assert InMemoryTaskStore(cell.uow.store).outbox == []


async def test_a_rolled_back_transaction_publishes_nothing(cell: Cell) -> None:
    uow, publisher = wrap(cell)

    async def failing_use_case() -> None:
        async with uow(SPACE_ID) as tx:
            await tx.pipeline.enqueue([Task(TaskName.EXTRACT, SPACE_ID)])
            raise RuntimeError("use case failed")

    with pytest.raises(RuntimeError):
        await failing_use_case()
    await publisher.drain()
    assert cell.bus.published == []
    assert InMemoryTaskStore(cell.uow.store).outbox == []


async def test_a_slow_broker_leaves_the_rows_for_the_relay(cell: Cell) -> None:
    class SlowBus:
        async def publish(self, tasks: object) -> None:
            await asyncio.sleep(1)

    uow, publisher = wrap(cell, SlowBus(), timeout_s=0.01)
    task = Task(TaskName.EXTRACT, SPACE_ID)
    async with uow(SPACE_ID) as tx:
        await tx.pipeline.enqueue([task])
    await publisher.drain()
    assert InMemoryTaskStore(cell.uow.store).outbox == [task]


async def test_coalesced_requests_publish_only_the_task_that_was_enqueued(cell: Cell) -> None:
    uow, publisher = wrap(cell)
    profile = uuid7()
    for _ in range(3):
        async with uow(SPACE_ID) as tx:
            await tx.pipeline.request_work(Task(TaskName.COMPILE, SPACE_ID, profile_id=profile))
    await publisher.drain()
    assert len(cell.bus.take(TaskName.COMPILE)) == 1


class Doorbell:
    def __init__(self) -> None:
        self.rings: list[tuple[UUID, datetime]] = []

    async def ring(self, space_id: UUID, at: datetime) -> None:
        self.rings.append((space_id, at))

    async def due(self, now: datetime) -> list[UUID]:
        return []


async def test_a_transaction_that_schedules_for_later_rings_the_doorbell_for_that_time(cell: Cell) -> None:
    doorbell = Doorbell()
    publisher = OutboxPublisher(cell.bus, cell.uow, doorbell=doorbell)  # type: ignore[arg-type]
    uow = PublishingUnitOfWorkFactory(cell.uow, publisher)
    later, sooner = cell.clock.now() + timedelta(seconds=30), cell.clock.now() + timedelta(seconds=5)
    async with uow(SPACE_ID) as tx:
        await tx.pipeline.schedule(Task(TaskName.EXTRACT, SPACE_ID), later, "retry")
        await tx.pipeline.schedule(Task(TaskName.MEASURE, SPACE_ID), sooner, "retry")
    async with uow(SPACE_ID) as tx:
        await tx.pipeline.enqueue([Task(TaskName.GATE, SPACE_ID)])
    await publisher.drain()
    assert doorbell.rings == [(SPACE_ID, sooner)]


async def test_a_publish_that_fails_rings_the_doorbell_for_the_relay(cell: Cell) -> None:
    class DownBus:
        async def publish(self, tasks: object) -> None:
            raise ConnectionError("broker down")

    doorbell, now = Doorbell(), datetime(2026, 9, 28, 12, tzinfo=UTC)
    publisher = OutboxPublisher(DownBus(), cell.uow, doorbell=doorbell, now=lambda: now)  # type: ignore[arg-type]
    uow = PublishingUnitOfWorkFactory(cell.uow, publisher)
    async with uow(SPACE_ID) as tx:
        await tx.pipeline.enqueue([Task(TaskName.GATE, SPACE_ID)])
    await publisher.drain()
    assert doorbell.rings == [(SPACE_ID, now + timedelta(seconds=2))]
    assert len(InMemoryTaskStore(cell.uow.store).outbox) == 1


async def test_published_rows_are_deleted_together_after_a_short_wait(cell: Cell) -> None:
    deletes: list[int] = []
    publisher = OutboxPublisher(cell.bus, cell.uow, delete_every_s=0.05)  # type: ignore[arg-type]
    uow = PublishingUnitOfWorkFactory(cell.uow, publisher)
    real = InMemoryTaskStore.delete_outbox

    async def counted(self: InMemoryTaskStore, ids: object) -> None:
        deletes.append(len(ids))  # type: ignore[arg-type]
        await real(self, ids)  # type: ignore[arg-type]

    InMemoryTaskStore.delete_outbox = counted  # type: ignore[method-assign]
    try:
        for _ in range(3):
            async with uow(SPACE_ID) as tx:
                await tx.pipeline.enqueue([Task(TaskName.GATE, SPACE_ID)])
        await asyncio.sleep(0.2)
    finally:
        InMemoryTaskStore.delete_outbox = real  # type: ignore[method-assign]
    assert deletes == [3]
    assert InMemoryTaskStore(cell.uow.store).outbox == []
