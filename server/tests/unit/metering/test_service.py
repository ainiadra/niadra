"""The hourly rollup and usage events (5.15), on the in-memory unit of work."""

from datetime import UTC, datetime, timedelta

import pytest

from niadra.adapters.inbound.workers.metering import handlers
from niadra.adapters.memory.metering import InMemoryUsageRepository
from niadra.app.metering import MeteringService, record_usage_in
from niadra.domain.common.errors import InvalidInput
from niadra.domain.metering import Granularity, Metric, UsageRow
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell

H = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)


@pytest.fixture
def service(cell: Cell) -> MeteringService:
    return MeteringService(cell.uow, cell.clock)


def repo(cell: Cell) -> InMemoryUsageRepository:
    return InMemoryUsageRepository(cell.uow.store)


async def record(
    cell: Cell, metric: Metric, quantity: int, key: list[str], at: datetime | None = None
) -> bool:
    async with cell.uow(SPACE_ID) as uow:
        return await record_usage_in(uow, SPACE_ID, metric, quantity, key, 0, at or cell.clock.now())


async def usage(service: MeteringService) -> list[UsageRow]:
    return await service.usage(SPACE_ID, H - timedelta(hours=3), H + timedelta(hours=3))


async def test_rollup_counts_the_current_and_previous_hour(cell: Cell, service: MeteringService) -> None:
    repo(cell).seed_source(SPACE_ID, H + timedelta(minutes=5), Metric.CONTEXT_READ.value, 4)
    repo(cell).seed_source(
        SPACE_ID, H - timedelta(minutes=10), Metric.EXTRACTION_RUN.value, 2, cost_micros=1800
    )
    repo(cell).seed_source(
        SPACE_ID, H - timedelta(hours=2), Metric.CONTEXT_READ.value, 9
    )  # outside the window
    await record(cell, Metric.EXPORT_BYTES, 2048, ["run-1", "facts"], H)

    task = Task(
        TaskName.USAGE_ROLLUP, SPACE_ID, payload={"scheduled_for": (H + timedelta(minutes=1)).isoformat()}
    )
    await handlers(type("C", (), {"metering": service})())[TaskName.USAGE_ROLLUP](task)

    assert await usage(service) == [
        UsageRow(H - timedelta(hours=1), Metric.EXTRACTION_RUN.value, 2, 1800),
        UsageRow(H, Metric.CONTEXT_READ.value, 4, 0),
        UsageRow(H, Metric.EXPORT_BYTES.value, 2048, 0),
    ]


async def test_rollup_is_idempotent_and_picks_up_late_rows(cell: Cell, service: MeteringService) -> None:
    repo(cell).seed_source(SPACE_ID, H + timedelta(minutes=1), Metric.CONTEXT_READ.value)
    await service.rollup_hour(SPACE_ID, H)
    await service.rollup_hour(SPACE_ID, H)
    assert [r.quantity for r in await usage(service)] == [1]
    repo(cell).seed_source(SPACE_ID, H + timedelta(minutes=59), Metric.CONTEXT_READ.value)  # a late receipt
    await service.rollup_hour(SPACE_ID, H + timedelta(minutes=30))
    assert [r.quantity for r in await usage(service)] == [2]


async def test_an_explicit_hour_backfills_only_that_hour(cell: Cell, service: MeteringService) -> None:
    old = H - timedelta(days=3)
    repo(cell).seed_source(SPACE_ID, old + timedelta(minutes=7), Metric.SESSION_BILLABLE.value, 3)
    await service.usage_rollup(Task(TaskName.USAGE_ROLLUP, SPACE_ID, payload={"hour": old.isoformat()}))
    rows = await service.usage(SPACE_ID, old, old + timedelta(hours=1))
    assert rows == [UsageRow(old, Metric.SESSION_BILLABLE.value, 3, 0)]


async def test_usage_events_count_once_and_only_for_metrics_without_a_source_table(cell: Cell) -> None:
    assert await record(cell, Metric.ASSISTANT_TOKENS, 900, ["assist-1"]) is True
    assert await record(cell, Metric.ASSISTANT_TOKENS, 900, ["assist-1"]) is False
    with pytest.raises(InvalidInput, match="source table"):
        await record(cell, Metric.CONTEXT_READ, 1, ["r1"])
    with pytest.raises(InvalidInput):
        await record(cell, Metric.EXPORT_BYTES, -1, ["r1"])


async def test_daily_granularity_and_range_limits(cell: Cell, service: MeteringService) -> None:
    for hours in (0, 1, 30):
        repo(cell).seed_source(SPACE_ID, H + timedelta(hours=hours), Metric.EVENT_INGESTED.value, 10)
        await service.rollup_hour(SPACE_ID, H + timedelta(hours=hours))
    days = await service.usage(SPACE_ID, H - timedelta(days=1), H + timedelta(days=3), Granularity.DAY)
    assert [(r.hour.day, r.quantity) for r in days] == [(22, 20), (23, 10)]
    with pytest.raises(InvalidInput, match="too long"):
        await service.usage(SPACE_ID, H - timedelta(days=40), H, Granularity.HOUR)
    with pytest.raises(InvalidInput, match="after"):
        await service.usage(SPACE_ID, H, H)
    with pytest.raises(InvalidInput, match="timezone"):
        await service.usage(SPACE_ID, H.replace(tzinfo=None), H + timedelta(hours=1))
