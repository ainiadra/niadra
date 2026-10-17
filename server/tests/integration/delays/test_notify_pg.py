"""The webhook delivery path's delay on a real space database (see `test_paths_pg.py`)."""

from niadra.domain.pipeline.delay import DelayReason
from tests.unit.notify import test_service as notify
from tests.unit.notify.test_service import world  # the path's fixture
from tests.unit.objects.fakes import World


async def test_webhook_retry(world: World, delays_on_postgres: list[str]) -> None:
    await notify.test_failures_retry_with_backoff_until_24_hours_then_die(world)
    assert delays_on_postgres
    assert set(delays_on_postgres) == {DelayReason.RETRY}
