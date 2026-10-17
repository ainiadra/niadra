"""In-memory `SessionRepository` for unit tests."""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.sessions.rules import (
    QUIET_DOUBLINGS,
    TASK_CHANNEL,
    erasure_target,
    held_proof,
    session_of,
    sessions_heard,
    valid_proof,
)
from niadra.domain.vocabulary import Verification


class InMemorySessionRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("sessions")
        self._sessions: dict[UUID, tuple[UUID, SessionRecord]] = self._s.setdefault("sessions", {})
        # (kind, thread id) to (ended_at, expires_at); one space per fake store, as per database.
        self._ends: dict[tuple[str, str], tuple[datetime, datetime]] = self._s.setdefault("ends", {})

    @property
    def all(self) -> list[SessionRecord]:
        return [s for _, s in self._sessions.values()]

    async def get_session(self, session_id: UUID) -> SessionRecord | None:
        found = self._sessions.get(session_id)
        return found[1] if found else None

    async def open_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        return self._open(None, conversation_id, task_id)

    async def proven_level(
        self, *, conversation_id: str | None = None, task_id: str | None = None, at: datetime
    ) -> Verification | None:
        session = self._open(None, conversation_id, task_id)
        return valid_proof(session, at) if session else None

    async def proof(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        at: datetime,
        minutes_by_channel: Mapping[str, int] | None = None,
    ) -> tuple[Verification, datetime | None] | None:
        session = self._open(None, conversation_id, task_id) or await self.last_closed_session(
            conversation_id=conversation_id, task_id=task_id
        )
        return held_proof(session, at, minutes_by_channel or {}) if session else None

    async def create_session(self, space_id: UUID, session: SessionRecord) -> SessionRecord:
        existing = self._open(space_id, session.conversation_id, session.task_id)
        if existing is not None:
            return existing
        self._sessions[session.session_id] = (space_id, session)
        return session

    async def extend_session(
        self,
        session_id: UUID,
        last_event_at: datetime,
        profile_id: UUID | None,
        *,
        opened_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        space_id, session = self._sessions[session_id]
        ends = [t for t in (session.ended_at, ended_at) if t is not None]
        self._sessions[session_id] = (
            space_id,
            dataclasses.replace(
                session,
                opened_at=min(session.opened_at, opened_at or session.opened_at),
                last_event_at=max(session.last_event_at, last_event_at),
                profile_id=session.profile_id or profile_id,
                close_reason=None if session.closed_at is None else session.close_reason,
                ended_at=max(ends) if ends else None,
            ),
        )

    async def save_pending_end(
        self,
        space_id: UUID,  # noqa: ARG002
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        ended_at: datetime,
        expires_at: datetime,
    ) -> None:
        key = _thread(conversation_id, task_id)
        found = self._ends.get(key)
        if found is not None:
            ended_at, expires_at = max(found[0], ended_at), max(found[1], expires_at)
        self._ends[key] = (ended_at, expires_at)

    async def pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None, now: datetime
    ) -> datetime | None:
        found = self._ends.get(_thread(conversation_id, task_id))
        return found[0] if found is not None and found[1] > now else None

    async def drop_pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> None:
        self._ends.pop(_thread(conversation_id, task_id), None)

    async def purge_pending_ends(self, now: datetime, *, limit: int) -> int:
        doomed = [key for key, (_, expires_at) in self._ends.items() if expires_at <= now][:limit]
        for key in doomed:
            del self._ends[key]
        return len(doomed)

    async def set_proof(self, session_id: UUID, level: Verification, until: datetime | None) -> None:
        space_id, session = self._sessions[session_id]
        self._sessions[session_id] = (
            space_id,
            dataclasses.replace(session, proven_level=level, proven_until=until),
        )

    async def last_closed_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        closed = [
            s
            for _, s in self._sessions.values()
            if s.closed_at is not None
            and (
                (task_id is not None and s.task_id == task_id)
                or (task_id is None and conversation_id is not None and s.conversation_id == conversation_id)
            )
        ]
        return max(closed, key=lambda s: s.closed_at or s.opened_at, default=None)

    async def sessions_of(self, events: Sequence[EventRecord]) -> dict[UUID, UUID]:
        found = {}
        for event in events:
            if (session := session_of(event, self.all)) is not None:
                found[event.event_id] = session.session_id
        return found

    async def reopen(self, session_id: UUID, last_event_at: datetime) -> bool:
        space_id, session = self._sessions[session_id]
        if session.closed_at is None:
            return False
        self._sessions[session_id] = (
            space_id,
            dataclasses.replace(
                session,
                closed_at=None,
                close_reason=None,
                last_event_at=max(session.last_event_at, last_event_at),
                reopened=session.reopened + 1,
            ),
        )
        return True

    async def close(
        self, session_id: UUID, *, closed_at: datetime, reason: str, initiated_by: str | None
    ) -> bool:
        space_id, session = self._sessions[session_id]
        if session.closed_at is not None:
            return False
        closed = dataclasses.replace(
            session, closed_at=closed_at, close_reason=reason, initiated_by=initiated_by
        )
        self._sessions[session_id] = (space_id, closed)
        return True

    async def idle_sessions(
        self,
        minutes: Mapping[tuple[UUID, str], int],
        default_minutes: int,
        now: datetime,
        limit: int,
        *,
        quiet_minutes: int,
    ) -> list[tuple[UUID, SessionRecord]]:
        def due(space_id: UUID, s: SessionRecord) -> bool:
            channel = TASK_CHANNEL if s.kind == "task" else s.channel
            waited = quiet_minutes << min(s.reopened, QUIET_DOUBLINGS)
            limit_ = timedelta(minutes=min(minutes.get((space_id, channel), default_minutes), waited))
            return s.closed_at is None and s.close_reason is None and now - s.last_event_at >= limit_

        found = [(space, s) for space, s in self._sessions.values() if due(space, s)]
        return sorted(found, key=lambda pair: pair[1].last_event_at)[:limit]

    async def closed_since(
        self, since: datetime, *, after_id: UUID | None, limit: int
    ) -> list[SessionRecord]:
        found = [
            s
            for _, s in self._sessions.values()
            if s.closed_at is not None
            and s.opened_at >= since
            and (after_id is None or s.session_id > after_id)
        ]
        return sorted(found, key=lambda s: s.session_id)[:limit]

    async def closed_for_profile(
        self, profile_id: UUID, closed_since: datetime, *, limit: int
    ) -> list[SessionRecord]:
        found = [
            s
            for _, s in self._sessions.values()
            if s.profile_id == profile_id and s.closed_at is not None and s.closed_at >= closed_since
        ]
        latest = sorted(found, key=lambda s: (s.closed_at, s.session_id), reverse=True)[:limit]
        return list(reversed(latest))

    async def expire_sessions(self, before: datetime, *, limit: int) -> list[UUID]:
        doomed = sorted(
            sid
            for sid, (_, s) in self._sessions.items()
            if s.closed_at is not None and s.last_event_at < before
        )[:limit]
        for sid in doomed:
            del self._sessions[sid]
        return doomed

    async def profiles_active_since(self, since: datetime, *, limit: int) -> list[UUID]:
        found = {
            s.profile_id for _, s in self._sessions.values() if s.profile_id and s.last_event_at >= since
        }
        return sorted(found)[:limit]

    async def try_lock_sweeper(self) -> bool:
        return True

    async def request_close(self, sessions: Sequence[tuple[UUID, UUID]], reason: str) -> None:
        for space_id, session_id in sessions:
            _, session = self._sessions[session_id]
            self._sessions[session_id] = (space_id, dataclasses.replace(session, close_reason=reason))

    async def session_ids_for(self, scope: ErasureScope) -> list[UUID]:
        target = sessions_heard(scope)
        if target is None:
            return []
        name, value = target
        return sorted(
            sid
            for sid, (_, s) in self._sessions.items()
            if (s.profile_id if name == "profile_id" else s.conversation_id) == value
        )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        target = erasure_target(scope)
        if target is None:
            return []
        name, value = target
        doomed = sorted(
            sid
            for sid, (_, s) in self._sessions.items()
            if (s.profile_id if name == "profile_id" else s.conversation_id) == value
        )[:limit]
        for session_id in doomed:
            del self._sessions[session_id]
        erased = [ErasedRow("sessions", {"session_id": str(sid)}) for sid in doomed]
        end = ("conversation", str(value))
        if name == "conversation_id" and len(erased) < limit and self._ends.pop(end, None) is not None:
            erased.append(ErasedRow("session_ends", {"kind": "conversation", "thread_id": str(value)}))
        return erased

    def _open(
        self, space_id: UUID | None, conversation_id: str | None, task_id: str | None
    ) -> SessionRecord | None:
        for space, session in self._sessions.values():
            if session.closed_at is not None or (space_id is not None and space != space_id):
                continue
            if task_id is not None and session.task_id == task_id:
                return session
            if task_id is None and conversation_id is not None and session.conversation_id == conversation_id:
                return session
        return None


def _thread(conversation_id: str | None, task_id: str | None) -> tuple[str, str]:
    if task_id is not None:
        return "task", task_id
    if conversation_id is None:
        raise ValueError("a pending end needs a conversation or a task")
    return "conversation", conversation_id


class InMemoryHealthRepository:
    """The cell's health marks; kept in the `sessions` namespace, which owns them."""

    def __init__(self, store: MemoryStore) -> None:
        self._health: dict[str, dict[str, Any]] = store.module("sessions").setdefault("health", {})

    async def is_degraded(self, component: str) -> bool:
        return self._health.get(component, {}).get("state") == "degraded"

    async def mark_degraded(self, component: str, detail: str, at: datetime) -> None:
        self._health[component] = {"state": "degraded", "since": at, "detail": detail}

    async def clear_degraded(self, component: str, quiet_since: datetime, at: datetime) -> bool:
        row = self._health.get(component)
        if row is None or row["state"] != "degraded" or row["since"] > quiet_since:
            return False
        self._health[component] = {"state": "healthy", "since": at, "detail": None}
        return True
