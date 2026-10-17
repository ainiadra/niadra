"""The retention windows of memory on PostgreSQL: each owner deletes what expired, in batches."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.audit import AuditService
from niadra.domain.extract.result import MemoryRef
from niadra.domain.measure.normalize import normalize_value
from tests.integration.audit.test_receipts_pg import AT, draft, service
from tests.integration.sessions.test_pg_sessions import NOW, _session
from tests.unit.compile.factories import PHONE, episode, fact


async def test_facts_episodes_and_sessions_expire_by_their_own_clock(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    old = replace(fact(1, "old", "x", "contract"), last_confirmed_at=NOW - timedelta(days=400))
    kept = replace(fact(2, "plan", "fibra", "contract"), last_confirmed_at=NOW - timedelta(days=5))
    closed = _session("c-old", at=NOW - timedelta(days=100))
    recent = _session("c-new", at=NOW - timedelta(days=1))
    async with pg_uow(space) as uow:
        for f in (old, kept):
            await uow.memory.insert_fact(f, normalize_value(f.value))
            await uow.memory.add_mentions(MemoryRef(f.origin_handle_id, f.fact_id), [uuid4()])
        await uow.memory.add_episode_version(
            replace(episode(1, "billing", days_ago=0), ended_at=NOW - timedelta(days=500))
        )
        await uow.memory.add_episode_version(
            replace(episode(2, "billing", days_ago=0), ended_at=NOW - timedelta(days=3))
        )
        for s in (closed, recent):
            await uow.sessions.create_session(space, s)
            await uow.sessions.close(
                s.session_id, closed_at=s.last_event_at, reason="idle", initiated_by=None
            )
    cutoff = NOW - timedelta(days=365)
    async with pg_uow(space) as uow:
        facts = await uow.memory.expire_facts(cutoff, limit=10)
        episodes = await uow.memory.expire_episodes(cutoff, limit=10)
        sessions = await uow.sessions.expire_sessions(NOW - timedelta(days=30), limit=10)
    assert {r.table for r in facts} == {"facts", "fact_mentions"}
    assert [r.key["fact_id"] for r in facts if r.table == "facts"] == [str(old.fact_id)]
    assert [r.key["episode_id"] for r in episodes] == [str(episode(1, "billing", days_ago=0).episode_id)]
    assert sessions == [closed.session_id]
    async with pg_uow(space) as uow:
        assert [f.predicate for f in await uow.memory.facts_for([PHONE])] == ["plan"]
        assert await uow.sessions.get_session(recent.session_id) is not None


async def test_receipts_expire_by_whole_days_with_their_heads_and_roots(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    audit: AuditService = service(pg_uow)
    await audit.write([draft(space, i, producer=f"p-{space}") for i in range(1, 4)])
    day = AT.date()
    async with pg_uow(space) as uow:
        assert await uow.audit.expire_receipts(space, day, limit=10) == 0  # the day itself stays
        assert await uow.audit.expire_receipts(space, day + timedelta(days=1), limit=2) == 2
        assert await uow.audit.expire_receipts(space, day + timedelta(days=1), limit=2) == 1
    async with pg_uow(space) as uow:
        left = (await uow.session.execute(text("SELECT count(*) FROM receipts"))).scalar_one()
        heads = (await uow.session.execute(text("SELECT count(*) FROM receipt_heads"))).scalar_one()
    assert (left, heads) == (0, 0)
