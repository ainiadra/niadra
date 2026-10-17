"""`PgSessionRepository` against PostgreSQL: one open session per conversation, idle sweep by
channel, proof, cell health and row-level security."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.vocabulary import EventKind, Speaker, Verification

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def _session(
    conversation: str | None = "conv-1",
    *,
    task: str | None = None,
    channel: str = "whatsapp",
    at: datetime = NOW,
) -> SessionRecord:
    return SessionRecord(
        session_id=uuid7(),
        kind="task" if task else "conversation",
        source_id=UUID(int=1),
        channel=channel,
        opened_at=at,
        last_event_at=at,
        conversation_id=None if task else conversation,
        task_id=task,
    )


async def test_create_extend_prove_and_close(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        opened = await uow.sessions.create_session(space, _session())
        again = await uow.sessions.create_session(space, _session())
        await uow.sessions.extend_session(opened.session_id, NOW + timedelta(minutes=3), profile)
        await uow.sessions.extend_session(opened.session_id, NOW + timedelta(minutes=1), None)
        await uow.sessions.set_proof(opened.session_id, Verification.V3, NOW + timedelta(minutes=10))
        proven = await uow.sessions.proven_level(conversation_id="conv-1", at=NOW + timedelta(minutes=5))
        expired = await uow.sessions.proven_level(conversation_id="conv-1", at=NOW + timedelta(minutes=11))
        closed = await uow.sessions.close(
            opened.session_id, closed_at=NOW, reason="ended", initiated_by="customer"
        )
        closed_twice = await uow.sessions.close(
            opened.session_id, closed_at=NOW, reason="ended", initiated_by=None
        )
        stored = await uow.sessions.get_session(opened.session_id)
        reopened = await uow.sessions.create_session(space, _session())

    assert again.session_id == opened.session_id
    assert (proven, expired) == (Verification.V3, None)
    assert (closed, closed_twice) == (True, False)
    assert stored is not None
    assert (stored.last_event_at, stored.profile_id, stored.initiated_by) == (
        NOW + timedelta(minutes=3),
        profile,
        "customer",
    )
    assert reopened.session_id != opened.session_id


async def test_concurrent_writers_share_one_open_session(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()

    async def open_one() -> UUID:
        async with pg_uow(space) as uow:
            created = await uow.sessions.create_session(space, _session("race"))
            await asyncio.sleep(0.1)
            return created.session_id

    first, second = await asyncio.gather(open_one(), open_one())
    assert first == second


async def test_idle_sweep_uses_each_space_and_channel_inactivity(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        chat = await uow.sessions.create_session(
            space, _session("chat-1", channel="whatsapp", at=NOW - timedelta(minutes=25))
        )
        mail = await uow.sessions.create_session(
            space, _session("mail-1", channel="email", at=NOW - timedelta(minutes=25))
        )
        task = await uow.sessions.create_session(
            space, _session(task="t-1", channel="erp", at=NOW - timedelta(minutes=11))
        )

    minutes = {(space, "whatsapp"): 20, (space, "email"): 240, (space, "task"): 10}
    async with pg_uow.system(space) as uow:
        assert await uow.sessions.try_lock_sweeper()
        due = await uow.sessions.idle_sessions(minutes, 30, NOW, 100, quiet_minutes=600)
        mine = [s.session_id for owner, s in due if owner == space]
        await uow.sessions.request_close([(space, s) for s in mine], "idle")
        again = [
            s
            for owner, s in await uow.sessions.idle_sessions(minutes, 30, NOW, 100, quiet_minutes=600)
            if owner == space
        ]

    assert sorted(mine) == sorted([chat.session_id, task.session_id])
    assert mail.session_id not in mine
    assert again == []

    async with pg_uow(space) as uow:
        await uow.sessions.extend_session(chat.session_id, NOW, None)
        resumed = await uow.sessions.get_session(chat.session_id)
    assert resumed is not None
    assert resumed.close_reason is None


async def test_only_one_sweeper_at_a_time(pg_uow: PgUnitOfWorkFactory) -> None:
    space, other = uuid4(), uuid4()
    await pg_uow.ensure(space)
    await pg_uow.ensure(other)
    holding = asyncio.Event()
    release = asyncio.Event()

    async def first() -> bool:
        async with pg_uow.system(space) as uow:
            locked = await uow.sessions.try_lock_sweeper()
            holding.set()
            await release.wait()
            return locked

    task = asyncio.create_task(first())
    await holding.wait()
    async with pg_uow.system(space) as uow:
        second = await uow.sessions.try_lock_sweeper()
    # One sweeper per space database: another space sweeps at the same time.
    async with pg_uow.system(other) as uow:
        elsewhere = await uow.sessions.try_lock_sweeper()
    release.set()
    assert await task
    assert not second
    assert elsewhere


async def test_cell_health_marks(pg_uow: PgUnitOfWorkFactory) -> None:
    async with pg_uow.cell() as uow:
        await uow.health.mark_degraded("hot_layer_test", "write failed", NOW)
        early = await uow.health.clear_degraded("hot_layer_test", NOW - timedelta(seconds=1), NOW)
        degraded = await uow.health.is_degraded("hot_layer_test")
        cleared = await uow.health.clear_degraded("hot_layer_test", NOW, NOW + timedelta(seconds=10))
        healthy = not await uow.health.is_degraded("hot_layer_test")

    assert (early, degraded, cleared, healthy) == (False, True, True, True)


async def test_row_level_security_isolates_sessions(restricted_uow: PgUnitOfWorkFactory) -> None:
    mine, theirs = uuid4(), uuid4()
    async with restricted_uow(mine) as uow:
        created = await uow.sessions.create_session(mine, _session("private"))

    async with restricted_uow(theirs) as uow:
        assert await uow.sessions.open_session(conversation_id="private") is None
        assert await uow.sessions.get_session(created.session_id) is None


async def test_a_closed_session_reopens_once_and_counts_it(pg_uow: PgUnitOfWorkFactory) -> None:
    # A late turn reopens the last closed session of its conversation.
    space = uuid4()
    async with pg_uow(space) as uow:
        first = await uow.sessions.create_session(space, _session("conv-late"))
        await uow.sessions.close(first.session_id, closed_at=NOW, reason="idle", initiated_by=None)
        found = await uow.sessions.last_closed_session(conversation_id="conv-late")
        assert found is not None
        assert found.session_id == first.session_id
        assert await uow.sessions.reopen(first.session_id, NOW + timedelta(minutes=4))
        assert not await uow.sessions.reopen(first.session_id, NOW)
        reopened = await uow.sessions.open_session(conversation_id="conv-late")
    assert reopened is not None
    assert (reopened.session_id, reopened.reopened, reopened.last_event_at) == (
        first.session_id,
        1,
        NOW + timedelta(minutes=4),
    )


async def test_each_event_belongs_to_the_session_whose_window_holds_it(pg_uow: PgUnitOfWorkFactory) -> None:
    # The distinct sessions of a fact's evidence; a conversation reopened after an idle
    # close has two sessions, and an event of no conversation belongs to none.
    space = uuid4()
    async with pg_uow(space) as uow:
        first = await uow.sessions.create_session(space, _session("conv-9"))
        await uow.sessions.extend_session(first.session_id, NOW + timedelta(minutes=5), None)
        await uow.sessions.close(
            first.session_id, closed_at=NOW + timedelta(minutes=35), reason="idle", initiated_by=None
        )
        second = await uow.sessions.create_session(space, _session("conv-9", at=NOW + timedelta(hours=2)))
        task = await uow.sessions.create_session(space, _session(task="t-1", at=NOW))
        events = [
            _event("conv-9", NOW + timedelta(minutes=2)),
            _event("conv-9", NOW + timedelta(hours=2)),
            _event(None, NOW, task="t-1"),
            _event(None, NOW),
            _event("conv-other", NOW),
        ]
        found = await uow.sessions.sessions_of(events)
    assert found == {
        events[0].event_id: first.session_id,
        events[1].event_id: second.session_id,
        events[2].event_id: task.session_id,
    }


def _event(conversation: str | None, at: datetime, *, task: str | None = None) -> EventRecord:
    return EventRecord(
        event_id=uuid7(),
        kind=EventKind.MESSAGE,
        source_id=UUID(int=1),
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=(),
        conversation_id=conversation,
        task_id=task,
    )


async def test_a_session_keeps_its_end_and_opens_earlier_for_a_late_first_turn(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid4()
    later = NOW + timedelta(minutes=2)
    async with pg_uow(space) as uow:
        opened = await uow.sessions.create_session(space, _session("conv-end", at=NOW + timedelta(minutes=1)))
        await uow.sessions.extend_session(opened.session_id, NOW, None, opened_at=NOW, ended_at=later)
        # A second, earlier end never moves the recorded one back; a later opening never moves it forward.
        await uow.sessions.extend_session(
            opened.session_id, NOW, None, opened_at=later, ended_at=NOW + timedelta(seconds=30)
        )
        stored = await uow.sessions.get_session(opened.session_id)
    assert stored is not None
    assert (stored.opened_at, stored.last_event_at, stored.ended_at) == (
        NOW,
        NOW + timedelta(minutes=1),
        later,
    )


async def test_an_end_before_any_turn_waits_until_it_expires(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    expires = NOW + timedelta(days=30)
    async with pg_uow(space) as uow:
        await uow.sessions.save_pending_end(space, conversation_id="c-e", ended_at=NOW, expires_at=expires)
        # The latest end wins, and a task of the same id is another thread.
        await uow.sessions.save_pending_end(
            space, conversation_id="c-e", ended_at=NOW + timedelta(minutes=1), expires_at=expires
        )
        await uow.sessions.save_pending_end(space, task_id="c-e", ended_at=NOW, expires_at=NOW)
        found = await uow.sessions.pending_end(conversation_id="c-e", now=NOW)
        expired = await uow.sessions.pending_end(task_id="c-e", now=NOW)
        missing = await uow.sessions.pending_end(conversation_id="c-other", now=NOW)
    async with pg_uow.system(space) as uow:
        purged = await uow.sessions.purge_pending_ends(NOW, limit=10)
    async with pg_uow(space) as uow:
        kept = await uow.sessions.pending_end(conversation_id="c-e", now=NOW)
        await uow.sessions.drop_pending_end(conversation_id="c-e")
        dropped = await uow.sessions.pending_end(conversation_id="c-e", now=NOW)
    assert (found, expired, missing) == (NOW + timedelta(minutes=1), None, None)
    assert purged == 1
    assert (kept, dropped) == (NOW + timedelta(minutes=1), None)


async def test_row_level_security_isolates_pending_ends(restricted_uow: PgUnitOfWorkFactory) -> None:
    mine, theirs = uuid4(), uuid4()
    async with restricted_uow(mine) as uow:
        await uow.sessions.save_pending_end(
            mine, conversation_id="private", ended_at=NOW, expires_at=NOW + timedelta(days=1)
        )
    async with restricted_uow(theirs) as uow:
        assert await uow.sessions.pending_end(conversation_id="private", now=NOW) is None


async def test_a_profiles_recently_closed_sessions_come_oldest_first_and_the_latest_only(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    # The extraction's batch window: one customer's sessions that closed together.
    space, profile, other = uuid4(), uuid4(), uuid4()
    closed_at = []
    async with pg_uow(space) as uow:
        for n in range(4):
            opened = await uow.sessions.create_session(space, _session(f"conv-{n}"))
            await uow.sessions.extend_session(opened.session_id, NOW, profile)
            at = NOW + timedelta(minutes=n)
            await uow.sessions.close(opened.session_id, closed_at=at, reason="ended", initiated_by=None)
            closed_at.append((at, opened.session_id))
        still_open = await uow.sessions.create_session(space, _session("conv-open"))
        await uow.sessions.extend_session(still_open.session_id, NOW, profile)
        someone_else = await uow.sessions.create_session(space, _session("conv-else"))
        await uow.sessions.extend_session(someone_else.session_id, NOW, other)
        await uow.sessions.close(someone_else.session_id, closed_at=NOW, reason="ended", initiated_by=None)
        latest = await uow.sessions.closed_for_profile(profile, NOW + timedelta(minutes=1), limit=2)
        window = await uow.sessions.closed_for_profile(profile, NOW, limit=10)

    assert [s.session_id for s in latest] == [sid for _, sid in closed_at[2:]]
    assert [s.session_id for s in window] == [sid for _, sid in closed_at]


async def test_a_session_waits_the_quiet_doubled_for_each_reopen_never_past_its_inactivity(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        fresh = await uow.sessions.create_session(space, _session("quiet-0", at=NOW - timedelta(minutes=10)))
        twice = await uow.sessions.create_session(space, _session("quiet-2", at=NOW - timedelta(minutes=10)))
        await uow.session.execute(
            text("UPDATE sessions SET reopened = 2 WHERE session_id = :s"), {"s": twice.session_id}
        )
    minutes = {(space, "whatsapp"): 30}
    async with pg_uow.system(space) as uow:
        due = [
            s.session_id
            for owner, s in await uow.sessions.idle_sessions(minutes, 30, NOW, 100, quiet_minutes=3)
            if owner == space
        ]
        later = [
            s.session_id
            for owner, s in await uow.sessions.idle_sessions(
                minutes, 30, NOW + timedelta(minutes=3), 100, quiet_minutes=3
            )
            if owner == space
        ]
    # Ten minutes quiet: past 3 for the first close, not yet the 12 a session reopened twice waits.
    assert due == [fresh.session_id]
    assert sorted(later) == sorted([fresh.session_id, twice.session_id])
