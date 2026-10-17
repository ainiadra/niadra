"""The trigger path's delay on a real space database (see `test_paths_pg.py`)."""

from niadra.domain.pipeline.delay import DelayReason
from tests.unit.objects.fakes import World
from tests.unit.triggers import test_service as triggers
from tests.unit.triggers.test_service import marina, world  # the path's fixtures


async def test_rate_limited(world: World, marina, delays_on_postgres: list[str]) -> None:
    await triggers.test_the_tenant_cap_defers_evaluation(world, marina)
    assert delays_on_postgres == [DelayReason.RATE_LIMITED]
