"""The measured conversations query over PostgreSQL, and its route ahead of the path route."""

from datetime import UTC, datetime, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import MeasuredSession
from niadra.entrypoints.openapi import cell_document

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)


async def test_keyset_pages_newest_first_within_the_period(pg_uow: PgUnitOfWorkFactory) -> None:
    space, source, other = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        for i in range(4):
            at = T0 + timedelta(hours=i)
            await uow.measure.put_session(
                MeasuredSession(uuid7(), "m1", f"c{i}", source, "voice", None, "", at.date(), at, at, False)
            )
        await uow.measure.put_session(
            MeasuredSession(uuid7(), "m1", "x", other, "voice", None, "", T0.date(), T0, T0, False)
        )
    async with pg_uow(space) as uow:
        first = await uow.measure.measured_sessions(
            source_ids=[source], since=T0, until=T0 + timedelta(days=1), before=None, limit=2
        )
        after = (first[-1].ended_at, first[-1].session_id)
        rest = await uow.measure.measured_sessions(
            source_ids=[source], since=T0, until=T0 + timedelta(days=1), before=after, limit=10
        )
        everyone = await uow.measure.measured_sessions(
            source_ids=None, since=T0, until=T0 + timedelta(hours=1), before=None, limit=10
        )
    assert [s.conversation_key for s in first + rest] == ["c3", "c2", "c1", "c0"]
    assert sorted(s.conversation_key for s in everyone) == ["c0", "x"]


def test_the_list_route_is_not_taken_for_a_conversation_id() -> None:
    paths = list(cell_document()["paths"])
    assert paths.index("/v1/context-use/conversations") < paths.index("/v1/context-use/{conversation_id}")
