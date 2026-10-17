"""The day of the space's last nightly consolidation pass, in `space_state`."""

from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory


async def test_the_consolidated_day_round_trips_and_is_replaced(pg_uow: PgUnitOfWorkFactory) -> None:
    space_id = uuid4()
    async with pg_uow.system(space_id) as uow:
        assert await uow.memory.consolidated_day() is None
        await uow.memory.set_consolidated_day(739_890)
        await uow.memory.set_consolidated_day(739_891)
    async with pg_uow.system(space_id) as uow:
        assert await uow.memory.consolidated_day() == 739_891
        assert await uow.traits.rules_fingerprint() is None
