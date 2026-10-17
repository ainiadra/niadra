from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from niadra.app.sessions import CLOSES_PER_PASS, SWEEP_EVERY_S, SWEEP_IDLE_CAP_S, SessionService
from niadra.domain.common.errors import VerificationNotAllowed
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.sessions.rules import (
    PENDING_END_LIFETIME,
    inactivity,
    initiated_by,
    raised_proof,
    valid_proof,
)
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, Speaker, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell

T0 = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)
PROFILE = UUID(int=42)


def event(
    at: datetime,
    *,
    speaker: Speaker = Speaker.CUSTOMER,
    conversation: str | None = "conv-1",
    task: str | None = None,
    channel: str = "whatsapp",
) -> EventRecord:
    return EventRecord(
        event_id=uuid7(),
        kind=EventKind.MESSAGE,
        source_id=WHATSAPP_SOURCE,
        channel=channel,
        speaker=speaker,
        occurred_at=at,
        received_at=at,
        handle_ids=(uuid4(),),
        text="oi",
        conversation_id=conversation,
        task_id=task,
    )


def session(**overrides: object) -> SessionRecord:
    base = {
        "session_id": uuid7(),
        "kind": "conversation",
        "source_id": WHATSAPP_SOURCE,
        "channel": "whatsapp",
        "opened_at": T0,
        "last_event_at": T0,
        "conversation_id": "conv-1",
    }
    return SessionRecord(**{**base, **overrides})  # type: ignore[arg-type]


def test_inactivity_per_channel_and_for_tasks() -> None:
    minutes = {"whatsapp": 20, "task": 10}
    assert inactivity(minutes, kind="conversation", channel="whatsapp") == timedelta(minutes=20)
    assert inactivity(minutes, kind="task", channel="erp") == timedelta(minutes=10)
    assert inactivity(minutes, kind="conversation", channel="fax") == timedelta(minutes=30)


def test_initiated_by_is_the_first_message_speaker() -> None:
    s = session()
    assert (
        initiated_by(s, [event(T0 + timedelta(minutes=1), speaker=Speaker.AI_AGENT), event(T0)]) == "customer"
    )
    assert initiated_by(s, [event(T0, speaker=Speaker.HUMAN_AGENT)]) == "company"
    assert initiated_by(session(kind="task", conversation_id=None, task_id="t"), [event(T0)]) is None


def test_proof_only_rises_while_valid() -> None:
    proven = session(proven_level=Verification.V3, proven_until=T0 + timedelta(minutes=10))
    assert valid_proof(proven, T0) is Verification.V3
    assert valid_proof(proven, T0 + timedelta(minutes=10)) is None
    assert raised_proof(proven, Verification.V2, None, T0) == (Verification.V3, T0 + timedelta(minutes=10))
    assert raised_proof(proven, Verification.V2, None, T0 + timedelta(hours=1)) == (Verification.V2, None)


def service(cell: Cell) -> SessionService:
    return SessionService(cell.uow, cell.clock, cell.config)


def all_sessions(cell: Cell) -> list[SessionRecord]:
    return sorted(
        (s for _, s in cell.uow.store.data["sessions"]["sessions"].values()), key=lambda s: s.opened_at
    )


def outbox(cell: Cell, name: TaskName) -> list[Task]:
    return [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name == name]


async def test_touch_opens_then_extends(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        opened, new = await sessions.touch(uow, SPACE_ID, [event(T0)], PROFILE)
        extended, again = await sessions.touch(uow, SPACE_ID, [event(T0 + timedelta(minutes=5))], None)

    assert opened is not None
    assert extended is not None
    assert (new, again) == (True, False)
    assert opened.session_id == extended.session_id
    [stored] = all_sessions(cell)
    assert (stored.last_event_at, stored.profile_id) == (T0 + timedelta(minutes=5), PROFILE)


async def test_a_proof_before_the_turns_never_says_whose_conversation_it_is(cell: Cell) -> None:
    # A person logs in for their company and then talks about it: the proof opens the session, and the
    # company named by the turns is its subject, never the person who proved.
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        proved = await sessions.record_verification(
            uow,
            SPACE_ID,
            source_id=WHATSAPP_SOURCE,
            channel="whatsapp",
            conversation_id="conv-1",
            task_id=None,
            level=Verification.V2,
            ceiling=Verification.V3,
            valid_until=None,
            at=T0,
        )
        talked, new = await sessions.touch(uow, SPACE_ID, [event(T0 + timedelta(minutes=1))], PROFILE)
    assert proved.profile_id is None
    assert talked is not None
    assert (new, talked.session_id, talked.profile_id) == (False, proved.session_id, PROFILE)
    [stored] = all_sessions(cell)
    assert (stored.profile_id, stored.proven_level) == (PROFILE, Verification.V2)


async def test_a_gap_longer_than_the_channel_inactivity_rolls_the_session(cell: Cell) -> None:
    sessions = service(cell)
    events = [event(T0), event(T0 + timedelta(minutes=45))]
    async with cell.uow(SPACE_ID) as uow:
        await uow.ingest.insert_events([NewEvent(SPACE_ID, e, str(e.event_id)) for e in events])
        await sessions.touch(uow, SPACE_ID, events, PROFILE)

    old, new = all_sessions(cell)
    assert old.closed_at is not None
    assert old.close_reason == "idle"
    assert old.initiated_by == "customer"
    assert new.closed_at is None
    assert new.opened_at == T0 + timedelta(minutes=45)
    [gate] = outbox(cell, TaskName.GATE)
    assert gate.payload == {"session_id": str(old.session_id)}
    assert gate.profile_id == PROFILE


async def test_task_sessions_are_keyed_by_task(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        opened, _ = await sessions.touch(
            uow, SPACE_ID, [event(T0, conversation=None, task="task-1", channel="erp")], None
        )

    assert opened is not None
    assert (opened.kind, opened.task_id, opened.conversation_id) == (
        "task",
        "task-1",
        None,
    )


async def test_sweeper_requests_close_once_and_the_task_closes(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.touch(uow, SPACE_ID, [event(T0)], PROFILE)
        await uow.ingest.insert_events([NewEvent(SPACE_ID, event(T0), "k1")])
    cell.clock.at = T0 + timedelta(minutes=21)

    assert await sessions.sweep_idle(SPACE_ID) == 1
    assert await sessions.sweep_idle(SPACE_ID) == 0  # already requested
    [close] = outbox(cell, TaskName.CLOSE_SESSION)
    await sessions.close_session(close)

    [closed] = all_sessions(cell)
    assert (closed.close_reason, closed.initiated_by) == ("idle", "customer")
    assert len(outbox(cell, TaskName.GATE)) == 1
    await sessions.close_session(close)  # idempotent
    assert len(outbox(cell, TaskName.GATE)) == 1


async def test_activity_after_the_sweep_cancels_the_idle_close(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.touch(uow, SPACE_ID, [event(T0)], PROFILE)
    cell.clock.at = T0 + timedelta(minutes=21)
    await sessions.sweep_idle(SPACE_ID)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.touch(uow, SPACE_ID, [event(T0 + timedelta(minutes=19))], None)  # sent late

    [close] = outbox(cell, TaskName.CLOSE_SESSION)
    await sessions.close_session(close)

    [still_open] = all_sessions(cell)
    assert still_open.closed_at is None
    assert still_open.close_reason is None


async def test_explicit_end_closes_without_waiting(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.touch(uow, SPACE_ID, [event(T0)], PROFILE)
        await sessions.request_close(uow, SPACE_ID, conversation_id="conv-1", task_id=None, reason="ended")
    cell.clock.at = T0 + timedelta(minutes=1)

    [close] = outbox(cell, TaskName.CLOSE_SESSION)
    await sessions.close_session(close)

    assert all_sessions(cell)[0].close_reason == "ended"


async def test_verification_is_capped_by_the_ceiling_and_never_lowered(cell: Cell) -> None:
    sessions = service(cell)
    common = {
        "source_id": WHATSAPP_SOURCE,
        "channel": "whatsapp",
        "conversation_id": "conv-1",
        "task_id": None,
        "ceiling": Verification.V3,
        "at": T0,
    }
    async with cell.uow(SPACE_ID) as uow:
        await sessions.record_verification(uow, SPACE_ID, level=Verification.V3, valid_until=None, **common)  # type: ignore[arg-type]
        lowered = await sessions.record_verification(
            uow, SPACE_ID, level=Verification.V1, valid_until=None, **common
        )  # type: ignore[arg-type]
        with pytest.raises(VerificationNotAllowed):
            await sessions.record_verification(
                uow, SPACE_ID, level=Verification.V4, valid_until=None, **common
            )  # type: ignore[arg-type]
        proven = await uow.sessions.proven_level(conversation_id="conv-1", at=T0)

    assert lowered.proven_level is Verification.V3
    assert proven is Verification.V3


async def test_cell_health_recovers_only_after_a_quiet_period(cell: Cell) -> None:
    sessions = service(cell)
    await sessions.mark_degraded("hot_layer", "write failed")

    assert not await sessions.clear_degraded("hot_layer", timedelta(seconds=10))
    cell.clock.advance(seconds=11)
    assert await sessions.clear_degraded("hot_layer", timedelta(seconds=10))
    assert not await sessions.is_degraded("hot_layer")


async def test_a_task_ended_before_its_turns_closes_the_session_they_open(cell: Cell) -> None:
    sessions = service(cell)
    cell.clock.at = T0
    async with cell.uow(SPACE_ID) as uow:
        await sessions.request_close(
            uow, SPACE_ID, conversation_id=None, task_id="task-1", reason="ended", ended_at=T0
        )
        opened, _ = await sessions.touch(
            uow, SPACE_ID, [event(T0, conversation=None, task="task-1", channel="erp")], None
        )

    assert opened is not None
    assert opened.ended_at == T0
    [close] = outbox(cell, TaskName.CLOSE_SESSION)
    assert close.payload == {"session_id": str(opened.session_id), "reason": "ended"}
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.sessions.pending_end(task_id="task-1", now=T0) is None


async def test_an_end_left_waiting_expires_with_the_idempotency_window(cell: Cell) -> None:
    sessions = service(cell)
    cell.clock.at = T0
    async with cell.uow(SPACE_ID) as uow:
        await sessions.request_close(
            uow, SPACE_ID, conversation_id="conv-1", task_id=None, reason="ended", ended_at=T0
        )
    cell.clock.at = T0 + PENDING_END_LIFETIME
    async with cell.uow(SPACE_ID) as uow:
        opened, _ = await sessions.touch(uow, SPACE_ID, [event(T0 - timedelta(minutes=1))], None)
    assert opened is not None
    assert opened.ended_at is None
    assert outbox(cell, TaskName.CLOSE_SESSION) == []
    # The sweep of the space database takes the expired row away.
    await sessions.sweep_idle(SPACE_ID)
    assert cell.uow.store.data["sessions"]["ends"] == {}


async def test_an_end_keeps_the_latest_time_the_session_holds(cell: Cell) -> None:
    # A clock behind the turns' still closes the whole session, as before ends kept a time.
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.touch(uow, SPACE_ID, [event(T0), event(T0 + timedelta(minutes=5))], PROFILE)
        await sessions.request_close(
            uow, SPACE_ID, conversation_id="conv-1", task_id=None, reason="ended", ended_at=T0
        )
    [stored] = all_sessions(cell)
    assert stored.ended_at == T0 + timedelta(minutes=5)


async def test_a_handoff_without_a_session_leaves_nothing_waiting(cell: Cell) -> None:
    sessions = service(cell)
    async with cell.uow(SPACE_ID) as uow:
        await sessions.request_close(uow, SPACE_ID, conversation_id="conv-1", task_id=None, reason="handoff")
    assert cell.uow.store.data["sessions"].get("ends", {}) == {}


async def test_a_burst_of_quiet_sessions_closes_a_few_at_a_time_the_longest_quiet_first(cell: Cell) -> None:
    # Bench cell, 09/10/2026: one sweep closed every quiet session of the accuracy pass at once, and their
    # extraction took the node's two cores while the history searches ran.
    sessions = service(cell)
    burst = CLOSES_PER_PASS * 2 + 5
    async with cell.uow(SPACE_ID) as uow:
        for k in range(burst):
            await sessions.touch(
                uow, SPACE_ID, [event(T0 + timedelta(seconds=k), conversation=f"conv-{k}")], PROFILE
            )
    cell.clock.at = T0 + timedelta(minutes=21)

    passes = [await sessions.sweep_idle(SPACE_ID) for _ in range(4)]

    assert passes == [CLOSES_PER_PASS, CLOSES_PER_PASS, 5, 0]
    closes = outbox(cell, TaskName.CLOSE_SESSION)
    first = {c.payload["session_id"] for c in closes[:CLOSES_PER_PASS]}
    oldest = {str(s.session_id) for s in all_sessions(cell)[:CLOSES_PER_PASS]}
    assert first == oldest
    # About three closes a second per space at most, and a session nobody ended waits a minute at most for
    # the sweep that closes it.
    assert CLOSES_PER_PASS / SWEEP_EVERY_S <= 3
    assert SWEEP_IDLE_CAP_S <= 60
