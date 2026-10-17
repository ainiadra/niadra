"""What the re-extraction pass reads from PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.extract.result import ExtractionRun, RunStatus
from tests.integration.sessions.test_pg_sessions import NOW, _session


async def test_closed_sessions_run_keys_and_the_extractor_state(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    closed = [_session(f"c-{i}") for i in range(3)]
    still_open = _session("c-open")
    async with pg_uow(space) as uow:
        for session in [*closed, still_open]:
            await uow.sessions.create_session(space, session)
        for session in closed:
            await uow.sessions.close(
                session.session_id, closed_at=NOW + timedelta(minutes=5), reason="idle", initiated_by=None
            )
        await uow.extract.save_run(ExtractionRun(closed[0].session_id, "x1.p4.s1", RunStatus.OK, NOW))
        await uow.extract.save_run(ExtractionRun(closed[0].session_id, "x1.p4.s2", RunStatus.FAILED, NOW))
        assert await uow.extract.extractor_state() == (None, None)
        await uow.extract.set_extractor_state(42, None)
        await uow.extract.set_extractor_state(43, NOW)
        await uow.extract.set_extractor_state(44, None)

    async with pg_uow(space) as uow:
        first = await uow.sessions.closed_since(NOW - timedelta(hours=1), after_id=None, limit=2)
        rest = await uow.sessions.closed_since(
            NOW - timedelta(hours=1), after_id=first[-1].session_id, limit=2
        )
        later = await uow.sessions.closed_since(NOW + timedelta(hours=1), after_id=None, limit=10)
        keys = await uow.extract.run_keys([s.session_id for s in closed])
        state = await uow.extract.extractor_state()

    ids = sorted(s.session_id for s in closed)
    assert [s.session_id for s in [*first, *rest]] == ids
    assert later == []
    assert sorted(keys[closed[0].session_id]) == [("x1.p4.s1", RunStatus.OK), ("x1.p4.s2", RunStatus.FAILED)]
    assert set(keys) == {closed[0].session_id}
    assert state == (44, NOW)
