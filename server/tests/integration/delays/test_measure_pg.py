"""The measure paths' delays on a real space database (see `test_paths_pg.py`)."""

from niadra.domain.pipeline.delay import DelayReason
from tests.unit.measure import test_service as measure
from tests.unit.measure.test_service import World, world  # the paths' fixture


async def test_transfer_window(world: World, delays_on_postgres: list[str]) -> None:
    await measure.test_measure_asks_for_review_and_schedules_the_transfer_phase(world)
    assert delays_on_postgres == [DelayReason.TRANSFER_WINDOW]


async def test_measure_waits_for_the_receipt_watermark(world: World, delays_on_postgres: list[str]) -> None:
    await measure.test_measure_waits_for_the_receipt_watermark(world)
    assert delays_on_postgres == [DelayReason.RECEIPT_WATERMARK]


async def test_transfer_waits_for_the_receipt_watermark(world: World, delays_on_postgres: list[str]) -> None:
    await measure.TestTransfer().test_waits_for_receipts_past_the_window(world)
    assert delays_on_postgres == [DelayReason.TRANSFER_WINDOW, DelayReason.RECEIPT_WATERMARK]
