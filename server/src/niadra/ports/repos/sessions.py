"""Sessions (activity windows) and proven verification level (space database), and the cell's
health marks (cell database) (5.4)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.vocabulary import Verification


class SessionRepository(Protocol):
    """Owned by the `sessions` module."""

    async def get_session(self, session_id: UUID) -> SessionRecord | None: ...

    async def last_closed_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        """The most recently closed session of a conversation or task."""
        ...

    async def sessions_of(self, events: Sequence[EventRecord]) -> dict[UUID, UUID]:
        """Event id to the id of the session it belongs to (`rules.session_of`); an event of no session,
        feedback or a system-of-record update without a conversation, is left out."""
        ...

    async def reopen(self, session_id: UUID, last_event_at: datetime) -> bool:
        """Closed to open again, counting the reopening; False when it was not closed."""
        ...

    async def open_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None: ...

    async def proven_level(
        self, *, conversation_id: str | None = None, task_id: str | None = None, at: datetime
    ) -> Verification | None:
        """The highest `verify` still valid for the open session (2.2)."""
        ...

    async def proof(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        at: datetime,
        minutes_by_channel: Mapping[str, int] | None = None,
    ) -> tuple[Verification, datetime | None] | None:
        """What the conversation's or task's latest session proves (`domain.sessions.rules.held_proof`),
        with the moment it stops being valid (None: while the session is open), for a read process to keep
        it (`app.read_state`). One statement."""
        ...

    async def create_session(self, space_id: UUID, session: SessionRecord) -> SessionRecord:
        """Opens `session`, or returns the session a concurrent writer opened first for the same
        conversation or task (one open session each, by a partial unique index)."""
        ...

    async def extend_session(
        self,
        session_id: UUID,
        last_event_at: datetime,
        profile_id: UUID | None,
        *,
        opened_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        """Moves `last_event_at` forward and `opened_at` back (a turn that arrived after later ones),
        records the end's time (the latest wins), fills a missing profile and cancels a pending idle
        close."""
        ...

    async def save_pending_end(
        self,
        space_id: UUID,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        ended_at: datetime,
        expires_at: datetime,
    ) -> None:
        """An end that found no session: kept for the turns still on their way (one per conversation or
        task; the latest end wins)."""
        ...

    async def pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None, now: datetime
    ) -> datetime | None:
        """The time of the conversation's or task's pending end, unless it expired."""
        ...

    async def drop_pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> None:
        """A session took the pending end over."""
        ...

    async def purge_pending_ends(self, now: datetime, *, limit: int) -> int:
        """Deletes expired pending ends of the database (system role); returns how many."""
        ...

    async def set_proof(self, session_id: UUID, level: Verification, until: datetime | None) -> None: ...

    async def close(
        self, session_id: UUID, *, closed_at: datetime, reason: str, initiated_by: str | None
    ) -> bool:
        """False when the session was already closed."""
        ...

    async def idle_sessions(
        self,
        minutes: Mapping[tuple[UUID, str], int],
        default_minutes: int,
        now: datetime,
        limit: int,
        *,
        quiet_minutes: int,
    ) -> list[tuple[UUID, SessionRecord]]:
        """Open sessions of the database quiet long enough, with no close requested yet; oldest first. A
        session waits `quiet_minutes` doubled for each time it reopened, and never longer than its
        inactivity: `minutes` keyed by (space, channel or `task`), `default_minutes` for the others
        (`domain.sessions.rules.quiet`)."""
        ...

    async def closed_since(
        self, since: datetime, *, after_id: UUID | None, limit: int
    ) -> list[SessionRecord]:
        """Closed sessions opened since `since`, in id order (ids are time-ordered), after `after_id`."""
        ...

    async def closed_for_profile(
        self, profile_id: UUID, closed_since: datetime, *, limit: int
    ) -> list[SessionRecord]:
        """The profile's sessions closed at or after `closed_since`, the latest `limit` of them, oldest
        first: the extraction's batch window."""
        ...

    async def expire_sessions(self, before: datetime, *, limit: int) -> list[UUID]:
        """Retention: deletes closed sessions last heard before `before`; returns their ids."""
        ...

    async def profiles_active_since(self, since: datetime, *, limit: int) -> list[UUID]:
        """Profiles with session activity since `since`: the population of rule dry-runs (20.5, 22.1)."""
        ...

    async def try_lock_sweeper(self) -> bool:
        """Transaction advisory lock of the database: one sweeper of the space at a time (5.4)."""
        ...

    async def request_close(self, sessions: Sequence[tuple[UUID, UUID]], reason: str) -> None:
        """Marks `(space_id, session_id)` pairs so the sweeper does not enqueue them twice."""
        ...

    async def session_ids_for(self, scope: ErasureScope) -> list[UUID]:
        """Ids of `rules.sessions_heard(scope)`, for the owners of per-session artifacts in the erasure
        cascade (8.4); read before the sessions themselves are erased. At most one person's sessions."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): the sessions of the profile, or of the conversation, and the
        conversation's pending end. A session holds the conversation id and the profile, which is enough
        to rebuild a contact history; a pending end names no profile, so only its conversation reaches
        it, and it expires with the idempotency window."""
        ...


class HealthRepository(Protocol):
    """Owned by the `sessions` module; the cell's health marks, in the cell database."""

    async def is_degraded(self, component: str) -> bool: ...

    async def mark_degraded(self, component: str, detail: str, at: datetime) -> None:
        """Every new failure moves `since`, so recovery waits for a quiet period."""
        ...

    async def clear_degraded(self, component: str, quiet_since: datetime, at: datetime) -> bool:
        """Marks healthy only if the last failure is older than `quiet_since`."""
        ...
