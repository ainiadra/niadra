"""The pattern rules a space was last derived with, kept in `space_state`."""

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7


async def test_the_fingerprint_round_trips_and_is_replaced(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with pg_uow.system(space) as uow:
        assert await uow.traits.rules_fingerprint() is None
        await uow.traits.set_rules_fingerprint(2**62 + 5)
    async with pg_uow.system(space) as uow:
        assert await uow.traits.rules_fingerprint() == 2**62 + 5
        await uow.traits.set_rules_fingerprint(7)
        assert await uow.traits.rules_fingerprint() == 7
        assert await uow.traits.profiles_with_traits(limit=10) == []
