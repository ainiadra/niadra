"""Session use cases: open and extend activity windows, record proof, close idle ones (5.4).

`touch`, `record_verification` and `request_close` run inside the caller's transaction (the
ingestion); `sweep_idle` and `close_session` open their own.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.common.errors import NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.sessions.rules import (
    DEFAULT_INACTIVITY_MINUTES,
    PENDING_END_LIFETIME,
    QUIET_MINUTES,
    CloseReason,
    after_end,
    check_verify_level,
    inactivity,
    initiated_by,
    is_idle,
    quiet,
    raised_proof,
    recorded_end,
    session_kind,
)
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Verification
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

SWEEP_LIMIT = 500
CLOSES_PER_PASS = 30
"""Quiet sessions one sweep closes in a space; the next pass comes `SWEEP_EVERY_S` later while some are left,
oldest first. Each close starts that session's whole write side (gate, extraction, reconcile, compile,
embeddings), and the sweep closed every due session at once: on the benchmark cell of 09/10/2026, 539 closes
and 481 extractions in one minute took the two cores of the node, and the history searches of that minute
went from a p95 of 30 to 260 ms while the database stayed at 9% CPU. The next minute, 172 extractions (about
3 a second) left them at 50 ms. 30 every 10 s is 3 a second per space: a burst of 500 is memory within 3
minutes of going quiet, as the reads go on."""
SWEEP_EVERY_S = 10.0
"""How often the sweep passes while it finds quiet sessions (`entrypoints.loops`)."""
SWEEP_IDLE_CAP_S = 60.0
"""How rarely it passes once it finds none: a session nobody ended closes within a minute of its quiet
(`QUIET_MINUTES`). It backed off to 300 s, and the quiet sessions of those 5 minutes closed in one pass."""
LATE_WINDOW = timedelta(minutes=30)
"""How long after a session closed a late turn still joins it, or the channel's inactivity when
longer: a session closes once quiet (`QUIET_MINUTES`), and a reply within its channel's inactivity, an e-mail
two hours later, is still the same activity."""


class SessionService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def touch(
        self, uow: UnitOfWork, space_id: UUID, events: Sequence[EventRecord], profile_id: UUID | None
    ) -> tuple[SessionRecord | None, bool]:
        """Opens or extends the session of one conversation or task with the batch's events for it,
        and says whether this batch opened it.

        A gap longer than the channel's inactivity inside the batch (late sends through the batch
        API) closes the old session and opens a new one, as the sweeper would have. So does a turn after
        the session's recorded end. A turn at or before it, whenever it arrives, joins the session and
        closes it again: batches reach the cell out of order.
        """
        if not events:
            return None, False
        ordered = sorted(events, key=lambda e: e.occurred_at)
        first = ordered[0]
        if not (first.conversation_id or first.task_id):
            return None, False
        kind = session_kind(task_id=first.task_id)
        conversation_id, task_id = _thread(kind, first.conversation_id, first.task_id)
        limit = inactivity(self._minutes(space_id), kind=kind, channel=first.channel)
        session = await uow.sessions.open_session(conversation_id=conversation_id, task_id=task_id)
        if session is None:
            session = await self._reopen_for_late(uow, kind, first, limit)
        pending = await uow.sessions.pending_end(
            conversation_id=conversation_id, task_id=task_id, now=self._clock.now()
        )
        opened = False
        for event in ordered:
            at = event.occurred_at
            if session is not None and (after_end(session, at) or at - session.last_event_at > limit):
                reason = CloseReason.ENDED if after_end(session, at) else CloseReason.IDLE
                await self._settle(uow, session, profile_id)
                await self._close(uow, space_id, session, reason)
                session = None
            if session is None:
                session = await uow.sessions.create_session(space_id, _new_session(kind, event, profile_id))
                opened = True
            elif at > session.last_event_at:
                session = dataclasses.replace(session, last_event_at=at)
            elif at < session.opened_at and await self._joins_before(uow, session, at, limit):
                session = dataclasses.replace(session, opened_at=at)
            if pending is not None and session.ended_at is None and session.last_event_at <= pending:
                # The end arrived first; its turns are here now.
                session = dataclasses.replace(session, ended_at=pending)
                await uow.sessions.drop_pending_end(conversation_id=conversation_id, task_id=task_id)
                pending = None
        assert session is not None
        await self._settle(uow, session, profile_id)
        session = dataclasses.replace(session, profile_id=session.profile_id or profile_id)
        if session.ended_at is not None:
            await uow.pipeline.enqueue([_close_task(space_id, session, CloseReason.ENDED)])
        return session, opened

    async def _settle(self, uow: UnitOfWork, session: SessionRecord, profile_id: UUID | None) -> None:
        await uow.sessions.extend_session(
            session.session_id,
            session.last_event_at,
            profile_id,
            opened_at=session.opened_at,
            ended_at=session.ended_at,
        )

    async def _joins_before(
        self, uow: UnitOfWork, session: SessionRecord, at: datetime, limit: timedelta
    ) -> bool:
        """A turn earlier than its session's first joins its window when it belongs to that activity and
        not to the session before, which already read everything up to its last event."""
        if session.opened_at - at > limit:
            return False
        before = await uow.sessions.last_closed_session(
            conversation_id=session.conversation_id, task_id=session.task_id
        )
        return before is None or before.last_event_at < at

    async def _reopen_for_late(
        self, uow: UnitOfWork, kind: str, event: EventRecord, limit: timedelta
    ) -> SessionRecord | None:
        """A turn that arrives within 30 minutes of its session's close (or the channel's inactivity, when
        longer), and belongs to that activity (it happened within the channel's inactivity of its events,
        and not after the end the session recorded), reopens the session instead of starting a new one: the
        next close extracts again and the episode gets a new version."""
        closed = await uow.sessions.last_closed_session(**_key(kind, event.conversation_id, event.task_id))
        if (
            closed is None
            or closed.closed_at is None
            or event.received_at - closed.closed_at > max(LATE_WINDOW, limit)
            or event.occurred_at - closed.last_event_at > limit
            or closed.opened_at - event.occurred_at > limit
            or after_end(closed, event.occurred_at)
        ):
            return None
        if not await uow.sessions.reopen(closed.session_id, event.occurred_at):
            return None
        return dataclasses.replace(
            closed,
            closed_at=None,
            close_reason=None,
            last_event_at=max(closed.last_event_at, event.occurred_at),
            reopened=closed.reopened + 1,
        )

    async def record_verification(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        *,
        source_id: UUID,
        channel: str,
        conversation_id: str | None,
        task_id: str | None,
        level: Verification,
        ceiling: Verification,
        valid_until: datetime | None,
        at: datetime,
    ) -> SessionRecord:
        """Raises the proven level of the conversation's or task's open session, opening one if needed.

        A proof says who proved what in this conversation; it never says whose memory the conversation is. A
        session it opens has no subject until its first turn names one, so a person who logs in for their
        company (`verify` on the lawyer's login, turns about the firm) leaves the conversation on the firm."""
        check_verify_level(level, ceiling)
        kind = session_kind(task_id=task_id)
        session = await uow.sessions.open_session(**_key(kind, conversation_id, task_id))
        if session is None:
            opened = SessionRecord(
                session_id=uuid7(),
                kind=kind,
                source_id=source_id,
                channel=channel,
                opened_at=at,
                last_event_at=at,
                conversation_id=None if kind == "task" else conversation_id,
                task_id=task_id if kind == "task" else None,
            )
            session = await uow.sessions.create_session(space_id, opened)
        proven, until = raised_proof(session, level, valid_until, at)
        await uow.sessions.set_proof(session.session_id, proven, until)
        return dataclasses.replace(session, proven_level=proven, proven_until=until)

    async def request_close(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        *,
        conversation_id: str | None,
        task_id: str | None,
        reason: str,
        ended_at: datetime | None = None,
    ) -> SessionRecord | None:
        """`conversation.ended`, `task.ended` and handoffs close through the `close_session` task (4.5).

        An end keeps its time on the session, for the turns still on their way. An end that finds no
        open session waits for its turns (`PENDING_END_LIFETIME`): they close as soon as they arrive.
        """
        conversation_id, task_id = _thread(session_kind(task_id=task_id), conversation_id, task_id)
        session = await uow.sessions.open_session(conversation_id=conversation_id, task_id=task_id)
        if session is None:
            if ended_at is not None and (conversation_id or task_id):
                await uow.sessions.save_pending_end(
                    space_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    ended_at=ended_at,
                    expires_at=self._clock.now() + PENDING_END_LIFETIME,
                )
            return None
        if ended_at is not None:
            session = dataclasses.replace(session, ended_at=recorded_end(session, ended_at))
            await self._settle(uow, session, None)
        await uow.pipeline.enqueue([_close_task(space_id, session, reason)])
        return session

    async def sweep_idle(
        self,
        space_id: UUID,
        now: datetime | None = None,
        limit: int = SWEEP_LIMIT,
        closes: int = CLOSES_PER_PASS,
    ) -> int:
        """Enqueues `close_session` for at most `closes` of the space's sessions quiet long enough (`quiet`),
        the longest quiet first: their memory is extracted then, and a later turn of the same activity reopens
        them. The rest wait for the next pass, so a burst reaches the workers at a pace the reads
        of the space live beside."""
        now = now or self._clock.now()
        minutes = {(space_id, channel): value for channel, value in self._minutes(space_id).items()}
        # As the application role, like every per-space loop step: a space database has two server
        # connections, and PgBouncer hands a freed one to the next client of the same role, so a system-role
        # pass waited behind the application's queue until its 10 s ran out (28/09/2026).
        async with self._uow(space_id) as uow:
            if not await uow.sessions.try_lock_sweeper():
                return 0
            await uow.sessions.purge_pending_ends(now, limit=limit)
            due = await uow.sessions.idle_sessions(
                minutes, DEFAULT_INACTIVITY_MINUTES, now, min(limit, closes), quiet_minutes=QUIET_MINUTES
            )
            if due:
                await uow.sessions.request_close(
                    [(space, s.session_id) for space, s in due], CloseReason.IDLE
                )
                await uow.pipeline.enqueue([_close_task(space, s, CloseReason.IDLE) for space, s in due])
        return len(due)

    async def close_session(self, task: Task) -> None:
        session_id = UUID(task.payload["session_id"])
        reason = str(task.payload["reason"])
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            if session is None or session.closed_at is not None:
                return
            if reason == CloseReason.IDLE and not is_idle(
                session, self._clock.now(), self._quiet(task.space_id, session)
            ):
                # Activity resumed after the sweeper looked. Its request may have landed after the turn that
                # resumed it, which no longer clears it: cleared here, or the sweeper skips the session for
                # good and, with no later turn, it never closes.
                await uow.sessions.extend_session(session.session_id, session.last_event_at, None)
                return
            await self._close(uow, task.space_id, session, reason)

    async def mark_degraded(self, component: str, detail: str) -> None:
        async with self._uow.cell() as uow:
            await uow.health.mark_degraded(component, detail, self._clock.now())

    async def clear_degraded(self, component: str, quiet_for: timedelta) -> bool:
        now = self._clock.now()
        async with self._uow.cell() as uow:
            return await uow.health.clear_degraded(component, now - quiet_for, now)

    async def is_degraded(self, component: str) -> bool:
        async with self._uow.cell() as uow:
            return await uow.health.is_degraded(component)

    async def _close(self, uow: UnitOfWork, space_id: UUID, session: SessionRecord, reason: str) -> None:
        events = await uow.ingest.events_between(
            **_key(session.kind, session.conversation_id, session.task_id),
            since=session.opened_at,
            until=session.last_event_at,
        )
        closed = await uow.sessions.close(
            session.session_id,
            closed_at=self._clock.now(),
            reason=reason,
            initiated_by=initiated_by(session, events),
        )
        if closed:
            gate = Task(
                TaskName.GATE,
                space_id,
                {"session_id": str(session.session_id)},
                profile_id=session.profile_id,
            )
            await uow.pipeline.enqueue([gate])

    def _quiet(self, space_id: UUID, session: SessionRecord) -> timedelta:
        return quiet(
            self._minutes(space_id), kind=session.kind, channel=session.channel, reopened=session.reopened
        )

    def _minutes(self, space_id: UUID) -> Mapping[str, int]:
        try:
            return self._config.space(space_id).settings.session_inactivity_minutes
        except NotFound:
            return {}


def _key(kind: str, conversation_id: str | None, task_id: str | None) -> dict[str, str | None]:
    return {"task_id": task_id} if kind == "task" else {"conversation_id": conversation_id}


def _thread(kind: str, conversation_id: str | None, task_id: str | None) -> tuple[str | None, str | None]:
    """`_key` as (conversation id, task id), for calls that take more than the thread."""
    return (None, task_id) if kind == "task" else (conversation_id, None)


def _new_session(kind: str, event: EventRecord, profile_id: UUID | None) -> SessionRecord:
    return SessionRecord(
        session_id=uuid7(),
        kind=kind,
        source_id=event.source_id,
        channel=event.channel,
        opened_at=event.occurred_at,
        last_event_at=event.occurred_at,
        conversation_id=None if kind == "task" else event.conversation_id,
        task_id=event.task_id if kind == "task" else None,
        profile_id=profile_id,
    )


def warm_task(space_id: UUID, session: SessionRecord) -> Task:
    """The compiler readies the pack the conversation's agent reads first (5.11), before it asks."""
    assert session.profile_id is not None
    return Task(
        TaskName.COMPILE, space_id, {"session_id": str(session.session_id)}, profile_id=session.profile_id
    )


def _close_task(space_id: UUID, session: SessionRecord, reason: str) -> Task:
    payload = {"session_id": str(session.session_id), "reason": reason}
    return Task(TaskName.CLOSE_SESSION, space_id, payload, profile_id=session.profile_id)
