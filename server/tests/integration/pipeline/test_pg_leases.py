"""A task's lease in one autocommit statement each way (5.5): the same guarantees as the transactional
path, all units or none, fencing by epoch, and nothing left behind by a busy attempt."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from niadra.adapters.outbound.postgres.leases import PgLeaseStore
from niadra.domain.common.errors import LeaseLost
from niadra.domain.common.ids import uuid7
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


async def held(uow: CloningUnitOfWork, space: object) -> list[tuple[str, str, int]]:
    async with uow.system(space) as tx:  # type: ignore[arg-type]
        rows = await tx.session.execute(text("SELECT unit_key, owner, epoch FROM queue_leases ORDER BY 1"))
        return [tuple(r) for r in rows.all()]  # type: ignore[misc]


async def test_all_units_or_none_and_a_busy_attempt_leaves_nothing(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    await pg_uow.ensure(space)
    leases = PgLeaseStore(pg_uow.inner._registry)
    first = await leases.acquire(space, ["profile:b"], "w1", 60, NOW)
    assert first is not None
    assert first.epochs == {"profile:b": 1}
    assert await leases.acquire(space, ["profile:b", "profile:a"], "w2", 60, NOW) is None
    assert await held(pg_uow, space) == [("profile:b", "w1", 1)]
    await leases.release(first)
    both = await leases.acquire(space, ["profile:b", "profile:a"], "w2", 60, NOW)
    assert both is not None
    assert both.epochs == {"profile:a": 1, "profile:b": 1}
    await leases.release(both)
    assert await held(pg_uow, space) == []


async def test_an_expired_lease_is_taken_with_a_new_epoch_and_fences_the_old_owner(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid7()
    await pg_uow.ensure(space)
    leases = PgLeaseStore(pg_uow.inner._registry)
    old = await leases.acquire(space, ["profile:p"], "w1", 60, NOW)
    assert old is not None
    new = await leases.acquire(space, ["profile:p"], "w2", 60, NOW + timedelta(seconds=61))
    assert new is not None
    assert new.epochs == {"profile:p": 2}
    async with pg_uow(space) as tx:
        with pytest.raises(LeaseLost):
            await tx.pipeline.check_fence(space, "profile:p", "w1", old.epochs["profile:p"])
    async with pg_uow(space) as tx:
        await tx.pipeline.check_fence(space, "profile:p", "w2", 2)
    await leases.release(old)  # an old grant gives back nothing
    assert await held(pg_uow, space) == [("profile:p", "w2", 2)]


async def test_only_one_of_many_racing_workers_wins(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    await pg_uow.ensure(space)
    leases = PgLeaseStore(pg_uow.inner._registry)
    grants = await asyncio.gather(
        *(leases.acquire(space, ["profile:q", "profile:p"], f"w{i}", 60, NOW) for i in range(4))
    )
    assert len([g for g in grants if g is not None]) == 1
    assert len(await held(pg_uow, space)) == 2
