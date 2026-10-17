"""`LeaseStore` on PostgreSQL: a task's lease taken and given back as one autocommit statement each, on the
space's application pool (5.5). `lease_acquire` pins the space and takes every unit or none; a unit busy
under another owner answers NULL and leaves nothing behind. Like the intake statement, a call runs once
more on a fresh connection when the pooler or the server closed the pooled one; taking a lease again
under the same owner only moves its epoch, and giving back one that is gone deletes nothing.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import TextClause, exc, text
from sqlalchemy.engine import Result

from niadra.adapters.outbound.postgres.engines import raise_unreachable
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.domain.common.errors import Unavailable
from niadra.domain.pipeline.lease import LeaseGrant

ACQUIRE = text(
    "SELECT lease_acquire(CAST(:space AS uuid), CAST(:keys AS text[]), :owner,"
    " CAST(:now AS timestamptz), CAST(:expires AS timestamptz))"
)
RELEASE = text(
    "SELECT lease_release(CAST(:space AS uuid), CAST(:keys AS text[]), :owner, CAST(:epochs AS bigint[]))"
)


class PgLeaseStore:
    def __init__(self, registry: EngineRegistry) -> None:
        self._registry = registry

    async def acquire(
        self, space_id: UUID, unit_keys: list[str], owner: str, ttl_s: int, now: datetime
    ) -> LeaseGrant | None:
        keys = sorted(set(unit_keys))
        params: dict[str, Any] = {
            "space": str(space_id),
            "keys": keys,
            "owner": owner,
            "now": now,
            "expires": now + timedelta(seconds=ttl_s),
        }
        epochs = await self._once_more(space_id, lambda r: r.scalar_one(), ACQUIRE, params)
        if epochs is None:
            return None
        return LeaseGrant(space_id, owner, dict(zip(keys, (int(e) for e in epochs), strict=True)))

    async def release(self, grant: LeaseGrant) -> None:
        keys = list(grant.unit_keys)
        params: dict[str, Any] = {
            "space": str(grant.space_id),
            "keys": keys,
            "owner": grant.owner,
            "epochs": [grant.epochs[k] for k in keys],
        }
        await self._once_more(grant.space_id, lambda _: None, RELEASE, params)

    async def _once_more[T](
        self, space_id: UUID, read: Callable[[Result[Any]], T], statement: TextClause, params: dict[str, Any]
    ) -> T:
        for attempt in range(2):
            try:
                return await self._run(space_id, read, statement, params)
            except exc.DBAPIError as error:
                if attempt or not error.connection_invalidated:
                    raise_unreachable(error)
        raise AssertionError("unreachable")

    async def _run[T](
        self, space_id: UUID, read: Callable[[Result[Any]], T], statement: TextClause, params: dict[str, Any]
    ) -> T:
        try:
            async with self._registry.lease(space_id, "app") as engine, engine.connect() as connection:
                autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
                return read(await autocommit.execute(statement, params))
        except exc.TimeoutError:
            raise Unavailable("the database connection pool is busy", retry_after_s=1) from None
