"""PostgreSQL implementations of `SessionRepository` (space database) and `HealthRepository` (cell
database)."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Integer,
    Text,
    Uuid,
    and_,
    case,
    column,
    delete,
    func,
    literal,
    select,
    text,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.platform import CellHealth
from niadra.adapters.outbound.postgres.schema.write_path import Session, SessionEnd
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7_floor
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

# The predicates of the partial unique indexes: one open session per conversation and per task.
_OPEN_CONVERSATION = text("closed_at IS NULL AND conversation_id IS NOT NULL")
_OPEN_TASK = text("closed_at IS NULL AND task_id IS NOT NULL")
SWEEP_LOCK = 0x4E49_4144_5357_4550


class PgSessionRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def get_session(self, session_id: UUID) -> SessionRecord | None:
        row = (
            await self._db.execute(select(Session).where(Session.session_id == session_id))
        ).scalar_one_or_none()
        return _record(row) if row else None

    async def last_closed_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        stmt = select(Session).where(Session.space_id == CURRENT_SPACE, Session.closed_at.is_not(None))
        if task_id is not None:
            stmt = stmt.where(Session.task_id == task_id)
        elif conversation_id is not None:
            stmt = stmt.where(Session.conversation_id == conversation_id)
        else:
            return None
        row = (await self._db.execute(stmt.order_by(Session.closed_at.desc()).limit(1))).scalar_one_or_none()
        return _record(row) if row else None

    async def sessions_of(self, events: Sequence[EventRecord]) -> dict[UUID, UUID]:
        conversations = sorted({e.conversation_id for e in events if e.conversation_id})
        tasks = sorted({e.task_id for e in events if e.task_id})
        sessions: list[SessionRecord] = []
        # Two reads, not one with OR: the conversation read stays on its index.
        for key, wanted in ((Session.conversation_id, conversations), (Session.task_id, tasks)):
            if wanted:
                rows = await self._db.execute(
                    select(Session).where(Session.space_id == CURRENT_SPACE, key.in_(wanted))
                )
                sessions += [_record(r) for r in rows.scalars()]
        found = {}
        for event in events:
            if (session := session_of(event, sessions)) is not None:
                found[event.event_id] = session.session_id
        return found

    async def reopen(self, session_id: UUID, last_event_at: datetime) -> bool:
        stmt = (
            update(Session)
            .where(Session.session_id == session_id, Session.closed_at.is_not(None))
            .values(
                closed_at=None,
                close_reason=None,
                last_event_at=func.greatest(Session.last_event_at, last_event_at),
                reopened=Session.reopened + 1,
            )
            .returning(Session.session_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def open_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        # Explicit space filter, like every other query: row-level security is the second line, not the only.
        stmt = select(Session).where(Session.space_id == CURRENT_SPACE, Session.closed_at.is_(None))
        if task_id is not None:
            stmt = stmt.where(Session.task_id == task_id)
        elif conversation_id is not None:
            stmt = stmt.where(Session.conversation_id == conversation_id)
        else:
            return None
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        return _record(row) if row else None

    async def proven_level(
        self, *, conversation_id: str | None = None, task_id: str | None = None, at: datetime
    ) -> Verification | None:
        session = await self.open_session(conversation_id=conversation_id, task_id=task_id)
        return valid_proof(session, at) if session else None

    async def proof(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        at: datetime,
        minutes_by_channel: Mapping[str, int] | None = None,
    ) -> tuple[Verification, datetime | None] | None:
        # The open session, else the one closed last: one statement, as the open one alone was.
        stmt = select(Session).where(Session.space_id == CURRENT_SPACE)
        if task_id is not None:
            stmt = stmt.where(Session.task_id == task_id)
        elif conversation_id is not None:
            stmt = stmt.where(Session.conversation_id == conversation_id)
        else:
            return None
        latest = stmt.order_by(Session.closed_at.desc().nulls_first()).limit(1)
        row = (await self._db.execute(latest)).scalar_one_or_none()
        return held_proof(_record(row), at, minutes_by_channel or {}) if row else None

    async def create_session(self, space_id: UUID, session: SessionRecord) -> SessionRecord:
        stmt = insert(Session).values(
            space_id=space_id,
            session_id=session.session_id,
            kind=session.kind,
            conversation_id=session.conversation_id,
            task_id=session.task_id,
            source_id=session.source_id,
            channel=session.channel,
            profile_id=session.profile_id,
            opened_at=session.opened_at,
            last_event_at=session.last_event_at,
        )
        if session.task_id is not None:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[Session.space_id, Session.task_id], index_where=_OPEN_TASK
            )
        else:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[Session.space_id, Session.conversation_id], index_where=_OPEN_CONVERSATION
            )
        if (await self._db.execute(stmt.returning(Session.session_id))).first() is not None:
            return session
        # A concurrent batch opened it first; its commit released us from the index wait.
        existing = await self.open_session(conversation_id=session.conversation_id, task_id=session.task_id)
        assert existing is not None
        return existing

    async def extend_session(
        self,
        session_id: UUID,
        last_event_at: datetime,
        profile_id: UUID | None,
        *,
        opened_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "last_event_at": func.greatest(Session.last_event_at, last_event_at),
            "profile_id": func.coalesce(Session.profile_id, profile_id),
            "close_reason": case((Session.closed_at.is_(None), None), else_=Session.close_reason),
        }
        if opened_at is not None:
            values["opened_at"] = func.least(Session.opened_at, opened_at)
        if ended_at is not None:
            # GREATEST skips a NULL: the first end is taken as is.
            values["ended_at"] = func.greatest(Session.ended_at, ended_at)
        await self._db.execute(update(Session).where(Session.session_id == session_id).values(**values))

    async def save_pending_end(
        self,
        space_id: UUID,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        ended_at: datetime,
        expires_at: datetime,
    ) -> None:
        kind, thread = _thread(conversation_id, task_id)
        stmt = insert(SessionEnd).values(
            space_id=space_id, kind=kind, thread_id=thread, ended_at=ended_at, expires_at=expires_at
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SessionEnd.space_id, SessionEnd.kind, SessionEnd.thread_id],
            set_={
                "ended_at": func.greatest(SessionEnd.ended_at, stmt.excluded.ended_at),
                "expires_at": func.greatest(SessionEnd.expires_at, stmt.excluded.expires_at),
            },
        )
        await self._db.execute(stmt)

    async def pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None, now: datetime
    ) -> datetime | None:
        kind, thread = _thread(conversation_id, task_id)
        ended_at: datetime | None = await self._db.scalar(
            select(SessionEnd.ended_at).where(
                SessionEnd.space_id == CURRENT_SPACE,
                SessionEnd.kind == kind,
                SessionEnd.thread_id == thread,
                SessionEnd.expires_at > now,
            )
        )
        return ended_at

    async def drop_pending_end(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> None:
        kind, thread = _thread(conversation_id, task_id)
        await self._db.execute(
            delete(SessionEnd).where(
                SessionEnd.space_id == CURRENT_SPACE, SessionEnd.kind == kind, SessionEnd.thread_id == thread
            )
        )

    async def purge_pending_ends(self, now: datetime, *, limit: int) -> int:
        picked = select(SessionEnd.space_id, SessionEnd.kind, SessionEnd.thread_id).where(
            SessionEnd.expires_at <= now
        )
        stmt = (
            delete(SessionEnd)
            .where(
                tuple_(SessionEnd.space_id, SessionEnd.kind, SessionEnd.thread_id).in_(picked.limit(limit))
            )
            .returning(SessionEnd.thread_id)
        )
        return len((await self._db.execute(stmt)).all())

    async def set_proof(self, session_id: UUID, level: Verification, until: datetime | None) -> None:
        await self._db.execute(
            update(Session)
            .where(Session.session_id == session_id)
            .values(proven_level=level.value, proven_until=until)
        )

    async def close(
        self, session_id: UUID, *, closed_at: datetime, reason: str, initiated_by: str | None
    ) -> bool:
        stmt = (
            update(Session)
            .where(Session.session_id == session_id, Session.closed_at.is_(None))
            .values(closed_at=closed_at, close_reason=reason, initiated_by=initiated_by)
            .returning(Session.session_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def idle_sessions(
        self,
        minutes: Mapping[tuple[UUID, str], int],
        default_minutes: int,
        now: datetime,
        limit: int,
        *,
        quiet_minutes: int,
    ) -> list[tuple[UUID, SessionRecord]]:
        # The shortest wait bounds the scan on the partial index of open sessions.
        cutoff = now - timedelta(minutes=min([quiet_minutes, default_minutes, *minutes.values()]))
        stmt = select(Session).where(
            Session.closed_at.is_(None), Session.close_reason.is_(None), Session.last_event_at <= cutoff
        )
        limit_minutes: Any = literal(default_minutes)
        if minutes:
            rules = values(
                column("space_id", Uuid),
                column("channel", Text),
                column("minutes", Integer),
                name="rules",
            ).data([(space, channel, value) for (space, channel), value in minutes.items()])
            channel_key = case((Session.kind == "task", TASK_CHANNEL), else_=Session.channel)
            stmt = stmt.outerjoin(
                rules, and_(rules.c.space_id == Session.space_id, rules.c.channel == channel_key)
            )
            limit_minutes = func.coalesce(rules.c.minutes, default_minutes)
        # `rules.quiet`: the quiet minutes doubled for each reopen, never past the inactivity.
        waited = literal(quiet_minutes).op("<<")(func.least(Session.reopened, QUIET_DOUBLINGS))
        limit_minutes = func.least(limit_minutes, waited)
        stmt = (
            stmt.where(
                Session.last_event_at <= literal(now) - func.make_interval(0, 0, 0, 0, 0, limit_minutes)
            )
            .order_by(Session.last_event_at)
            .limit(limit)
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return [(row.space_id, _record(row)) for row in rows]

    async def closed_since(
        self, since: datetime, *, after_id: UUID | None, limit: int
    ) -> list[SessionRecord]:
        floor = uuid7_floor(int(since.timestamp() * 1000))
        stmt = select(Session).where(
            Session.space_id == CURRENT_SPACE,
            Session.session_id >= floor,
            Session.opened_at >= since,
            Session.closed_at.is_not(None),
        )
        if after_id is not None:
            stmt = stmt.where(Session.session_id > after_id)
        rows = await self._db.execute(stmt.order_by(Session.session_id).limit(limit))
        return [_record(r) for r in rows.scalars()]

    async def closed_for_profile(
        self, profile_id: UUID, closed_since: datetime, *, limit: int
    ) -> list[SessionRecord]:
        # The profile's index (space, profile, opened_at) narrows it to one customer's sessions.
        stmt = (
            select(Session)
            .where(
                Session.space_id == CURRENT_SPACE,
                Session.profile_id == profile_id,
                Session.closed_at >= closed_since,
            )
            .order_by(Session.closed_at.desc(), Session.session_id.desc())
            .limit(limit)
        )
        rows = await self._db.execute(stmt)
        return [_record(r) for r in reversed(rows.scalars().all())]

    async def expire_sessions(self, before: datetime, *, limit: int) -> list[UUID]:
        picked = (
            select(Session.session_id)
            .where(
                Session.space_id == CURRENT_SPACE,
                Session.closed_at.is_not(None),
                Session.last_event_at < before,
            )
            .limit(limit)
        )
        stmt = (
            delete(Session)
            .where(Session.space_id == CURRENT_SPACE, Session.session_id.in_(picked))
            .returning(Session.session_id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def profiles_active_since(self, since: datetime, *, limit: int) -> list[UUID]:
        stmt = (
            select(Session.profile_id)
            .where(Session.profile_id.is_not(None), Session.last_event_at >= since)
            .group_by(Session.profile_id)
            .order_by(Session.profile_id)
            .limit(limit)
        )
        return [row[0] for row in await self._db.execute(stmt)]

    async def try_lock_sweeper(self) -> bool:
        return bool(await self._db.scalar(select(func.pg_try_advisory_xact_lock(SWEEP_LOCK))))

    async def request_close(self, sessions: Sequence[tuple[UUID, UUID]], reason: str) -> None:
        if not sessions:
            return
        await self._db.execute(
            update(Session)
            .where(
                tuple_(Session.space_id, Session.session_id).in_(list(sessions)), Session.closed_at.is_(None)
            )
            .values(close_reason=reason)
        )

    async def session_ids_for(self, scope: ErasureScope) -> list[UUID]:
        target = sessions_heard(scope)
        if target is None:
            return []
        name, value = target
        match_ = Session.profile_id == value if name == "profile_id" else Session.conversation_id == value
        stmt = (
            select(Session.session_id)
            .where(Session.space_id == CURRENT_SPACE, match_)
            .order_by(Session.session_id)
        )
        return list((await self._db.execute(stmt)).scalars())

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        target = erasure_target(scope)
        if target is None:
            return []
        name, value = target
        match_ = Session.profile_id == value if name == "profile_id" else Session.conversation_id == value
        doomed = (
            select(Session.session_id)
            .where(Session.space_id == CURRENT_SPACE, match_)
            .limit(limit)
            .scalar_subquery()
        )
        stmt = (
            delete(Session)
            .where(Session.space_id == CURRENT_SPACE, Session.session_id.in_(doomed))
            .returning(Session.session_id, Session.change_seq)
        )
        erased = [
            ErasedRow("sessions", {"session_id": str(row.session_id)}, (), row.change_seq)
            for row in await self._db.execute(stmt)
        ]
        if name == "conversation_id" and len(erased) < limit:
            ends = await self._db.execute(
                delete(SessionEnd)
                .where(
                    SessionEnd.space_id == CURRENT_SPACE,
                    SessionEnd.kind == "conversation",
                    SessionEnd.thread_id == value,
                )
                .returning(SessionEnd.kind, SessionEnd.thread_id)
            )
            erased += [
                ErasedRow("session_ends", {"kind": kind, "thread_id": thread}) for kind, thread in ends
            ]
        return erased


def _thread(conversation_id: str | None, task_id: str | None) -> tuple[str, str]:
    """A task id wins over a conversation id, as `rules.session_kind` does."""
    if task_id is not None:
        return "task", task_id
    if conversation_id is None:
        raise ValueError("a pending end needs a conversation or a task")
    return "conversation", conversation_id


def _record(row: Session) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        kind=row.kind,
        source_id=row.source_id,
        channel=row.channel,
        opened_at=row.opened_at,
        last_event_at=row.last_event_at,
        conversation_id=row.conversation_id,
        task_id=row.task_id,
        profile_id=row.profile_id,
        closed_at=row.closed_at,
        close_reason=row.close_reason,
        proven_level=Verification(row.proven_level) if row.proven_level else None,
        proven_until=row.proven_until,
        initiated_by=row.initiated_by,
        extracted_version=row.extracted_version,
        reopened=row.reopened,
        ended_at=row.ended_at,
    )


class PgHealthRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def is_degraded(self, component: str) -> bool:
        state = await self._db.scalar(select(CellHealth.state).where(CellHealth.component == component))
        return state == "degraded"

    async def mark_degraded(self, component: str, detail: str, at: datetime) -> None:
        stmt = insert(CellHealth).values(component=component, state="degraded", since=at, detail=detail)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CellHealth.component],
            set_={"state": "degraded", "since": stmt.excluded.since, "detail": stmt.excluded.detail},
        )
        await self._db.execute(stmt)

    async def clear_degraded(self, component: str, quiet_since: datetime, at: datetime) -> bool:
        stmt = (
            update(CellHealth)
            .where(
                CellHealth.component == component,
                CellHealth.state == "degraded",
                CellHealth.since <= quiet_since,
            )
            .values(state="healthy", since=at, detail=None)
            .returning(CellHealth.component)
        )
        return (await self._db.execute(stmt)).first() is not None
