"""`PipelineProbe` on PostgreSQL: what a space's relay and scheduler have due, and what waits in its intake,
as one autocommit statement on the application role's pool (06, 6.3): `pipeline_due` pins the space and
reads the minimums, so the pool the space's requests and tasks use serves the relay too, and a quiet
space's database gets nothing else from the loops process, once per pass, with no BEGIN and no COMMIT.
It reads a count and minimums of tables other modules own (`ingest_intake` is the ingest module's) and
never writes. Like the intake statement, it runs once more on a fresh connection when the pooler or the
server closed the pooled one.
"""

from uuid import UUID

from sqlalchemy import exc, text

from niadra.adapters.outbound.postgres.engines import raise_unreachable
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.domain.common.errors import Unavailable
from niadra.domain.pipeline.due import PipelineDue

DUE = text("SELECT * FROM pipeline_due(CAST(:space AS uuid))")


class PgPipelineProbe:
    def __init__(self, registry: EngineRegistry) -> None:
        self._registry = registry

    async def due(self, space_id: UUID) -> PipelineDue:
        for attempt in range(2):
            try:
                return await self._due(space_id)
            except exc.DBAPIError as error:
                if attempt or not error.connection_invalidated:
                    raise_unreachable(error)
        raise AssertionError("unreachable")

    async def _due(self, space_id: UUID) -> PipelineDue:
        try:
            async with (
                self._registry.lease(space_id, "app") as engine,
                engine.connect() as connection,
            ):
                autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
                row = (await autocommit.execute(DUE, {"space": str(space_id)})).one()
        except exc.TimeoutError:
            raise Unavailable("the database connection pool is busy", retry_after_s=1) from None
        return PipelineDue(
            outbox_oldest=row.outbox_oldest,
            scheduled_due=row.scheduled_due,
            schedule_due=row.schedule_due,
            intake_pending=int(row.intake_pending or 0),
            intake_oldest=row.intake_oldest,
        )
