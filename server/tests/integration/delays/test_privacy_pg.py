"""The continuous export path's delays on a real space database (see `test_paths_pg.py`)."""

from niadra.domain.pipeline.delay import DelayReason
from niadra.testing.cell import Cell
from tests.unit.privacy import test_exports as exports
from tests.unit.privacy.conftest import destinations, make_privacy, world  # the path's fixtures


async def test_export_settle(
    cell: Cell, world, make_privacy, destinations, delays_on_postgres: list[str]
) -> None:
    await exports.test_a_run_observes_the_sequence_then_exports_after_it_settles(
        cell, world, make_privacy, destinations
    )
    assert delays_on_postgres[:2] == [DelayReason.EXPORT_SETTLE] * 2
    assert set(delays_on_postgres) == {DelayReason.EXPORT_SETTLE}
