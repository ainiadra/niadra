"""`IntakeLog` on PostgreSQL: `ingest_accept` as one autocommit statement on the space's own pool, so the
acknowledgement pays one round trip and one commit, with no BEGIN, no `set_config` of its own, no COMMIT
and no ping around it (5.2).

The space engines skip the pool's ping. A connection the pooler or the server closed fails the statement,
and it runs once more on a fresh connection: `ingest_accept` is idempotent by intake id, so even a call
that committed before its answer was lost answers the same the second time and stores nothing more."""

from sqlalchemy import exc

from niadra.adapters.outbound.postgres.engines import raise_unreachable
from niadra.adapters.outbound.postgres.ingest import ACCEPT, accept_params
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.domain.common.errors import Unavailable
from niadra.domain.ingest.intake import NewIntake


class PgIntakeLog:
    def __init__(self, registry: EngineRegistry) -> None:
        self._registry = registry

    async def accept(self, intake: NewIntake) -> set[str]:
        for attempt in range(2):
            try:
                return await self._accept(intake)
            except exc.DBAPIError as error:
                if attempt or not error.connection_invalidated:
                    raise_unreachable(error)
        raise AssertionError("unreachable")

    async def _accept(self, intake: NewIntake) -> set[str]:
        try:
            async with (
                self._registry.lease(intake.space_id, "app") as engine,
                engine.connect() as connection,
            ):
                autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
                known = (await autocommit.execute(ACCEPT, accept_params(intake))).scalar_one()
        except exc.TimeoutError:
            raise Unavailable("the database connection pool is busy", retry_after_s=1) from None
        return set(known)
