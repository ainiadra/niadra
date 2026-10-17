"""Session rules: inactivity per channel, who initiated, proven verification level (5.4, 2.2)."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from niadra.domain.common.errors import InvalidInput, VerificationNotAllowed
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.vocabulary import EventKind, Speaker, Verification

DEFAULT_INACTIVITY_MINUTES = 30
QUIET_MINUTES = 3
"""How long a conversation or task nobody ended stays quiet before it closes and its memory is extracted.
The channel's inactivity still decides what is one activity: a turn within it reopens the same session,
which extracts again at its next quiet close. Before this, a conversation nobody ended reached the
other agents only after its 30 minutes of inactivity (juridico-zero, 07/10/2026: the lawyer's promises
were extracted 32 minutes after his last turn)."""
QUIET_DOUBLINGS = 10
"""Each reopen doubles the quiet a session waits for, up to the channel's inactivity: a conversation answered
every five minutes closed on every pause and was extracted whole again after each answer, six model calls for
one half-hour exchange. The first pause still reaches memory within minutes; the activity's later turns, at
its next longer pause."""
TASK_CHANNEL = "task"
HOT_LAYER = "hot_layer"
PENDING_END_LIFETIME = timedelta(days=30)
"""How long an end that arrived before any turn of its session waits for them: the idempotency window,
the longest an SDK's retry of a turn is still recognized as the same item (5.2)."""


class CloseReason:
    IDLE = "idle"
    ENDED = "ended"
    HANDOFF = "handoff"


def session_of(event: EventRecord, sessions: Iterable[SessionRecord]) -> SessionRecord | None:
    """The session an event belongs to: the one of its conversation or task whose window holds its time,
    as a session reads its own events (`events_between`); the latest opened when windows touch."""
    held = [
        s
        for s in sessions
        if (s.conversation_id or s.task_id)
        and (s.conversation_id is None or s.conversation_id == event.conversation_id)
        and (s.task_id is None or s.task_id == event.task_id)
        and s.opened_at <= event.occurred_at <= (s.closed_at or s.last_event_at)
    ]
    return max(held, key=lambda s: (s.opened_at, s.session_id), default=None)


def session_kind(*, task_id: str | None) -> str:
    """A task id wins over a conversation id: the internal agent's task is the billable unit (18.6)."""
    return "task" if task_id else "conversation"


def inactivity(minutes_by_channel: Mapping[str, int], *, kind: str, channel: str) -> timedelta:
    key = TASK_CHANNEL if kind == "task" else channel
    return timedelta(minutes=minutes_by_channel.get(key, DEFAULT_INACTIVITY_MINUTES))


def quiet(minutes_by_channel: Mapping[str, int], *, kind: str, channel: str, reopened: int = 0) -> timedelta:
    """When a session nobody ended closes: after `QUIET_MINUTES`, doubled for each time it reopened
    (`QUIET_DOUBLINGS`), and never after the channel's inactivity."""
    waited = timedelta(minutes=QUIET_MINUTES << min(reopened, QUIET_DOUBLINGS))
    return min(inactivity(minutes_by_channel, kind=kind, channel=channel), waited)


def is_idle(session: SessionRecord, now: datetime, limit: timedelta) -> bool:
    return now - session.last_event_at >= limit


def recorded_end(session: SessionRecord, ended_at: datetime) -> datetime:
    """The end kept on an open session: never before a turn it already holds, so a clock behind the
    turns' (or an end sent late) still closes the whole session, as it did before ends kept a time."""
    return max(ended_at, session.last_event_at, session.ended_at or ended_at)


def after_end(session: SessionRecord, at: datetime) -> bool:
    """Whether a turn at `at` falls after the recorded end of `session`, and so starts a new one."""
    return session.ended_at is not None and at > session.ended_at


def initiated_by(session: SessionRecord, events: Sequence[EventRecord]) -> str | None:
    """`customer` when the first message came from the customer; active contact does not count (19.7)."""
    if session.kind == "task":
        return None
    first = next(
        (e for e in sorted(events, key=lambda e: e.occurred_at) if e.kind is EventKind.MESSAGE), None
    )
    if first is None:
        return None
    return "customer" if first.speaker is Speaker.CUSTOMER else "company"


def valid_proof(session: SessionRecord, at: datetime) -> Verification | None:
    if session.proven_level is None or (session.proven_until is not None and session.proven_until <= at):
        return None
    return session.proven_level


def held_proof(
    session: SessionRecord, at: datetime, minutes_by_channel: Mapping[str, int]
) -> tuple[Verification, datetime | None] | None:
    """The proof a conversation's reads take from its latest session, with when it stops holding (None:
    while the session is open). An open session's; and a session closed on a quiet that nobody ended, while
    a turn now would still reopen it (the channel's inactivity since its last event): a quiet close
    is when memory extracts the turns (`QUIET_MINUTES`), not the end of the conversation. A call proved at
    its start and read for minutes without a turn lost its proof 3 minutes in, and every read after ran at
    V0, its items withheld and opened as not found (benchmark, 09/10/2026)."""
    until = session.proven_until
    if session.closed_at is not None:
        if session.close_reason != CloseReason.IDLE or session.ended_at is not None:
            return None
        limit = inactivity(minutes_by_channel, kind=session.kind, channel=session.channel)
        reopens_until = session.last_event_at + limit
        until = reopens_until if until is None else min(until, reopens_until)
        if until <= at:
            return None
    level = valid_proof(session, at)
    return (level, until) if level is not None else None


def raised_proof(
    session: SessionRecord, level: Verification, until: datetime | None, at: datetime
) -> tuple[Verification, datetime | None]:
    """A new `verify` only raises the level; a lower one never erases a higher proof still valid (2.2)."""
    current = valid_proof(session, at)
    if current is not None and current.rank > level.rank:
        return current, session.proven_until
    return level, until


def check_verify_level(level: Verification, ceiling: Verification) -> None:
    """`verify` above the source's ceiling is refused, so a leaked agent key cannot declare V4 (2.2)."""
    if level is Verification.NO_CUSTOMER:
        raise InvalidInput("verify takes a customer level")
    if level.rank > ceiling.rank:
        raise VerificationNotAllowed("the level is above the source's ceiling")


def erasure_target(scope: ErasureScope) -> tuple[str, object] | None:
    """Which sessions a `forget` reaches (8.4): a profile's, or a conversation's. Forgetting one handle
    keeps the profile, and its sessions with it."""
    if scope.kind is TargetKind.CONVERSATION and scope.conversation_id:
        return "conversation_id", scope.conversation_id
    if scope.kind is TargetKind.PROFILE and scope.profile_id:
        return "profile_id", scope.profile_id
    return None


def sessions_heard(scope: ErasureScope) -> tuple[str, object] | None:
    """Sessions whose derived artifacts (extraction results) may hold the target's words. A session
    does not record which handle spoke, so a handle's erasure reaches every session of its profile."""
    if scope.kind is TargetKind.CONVERSATION:
        return ("conversation_id", scope.conversation_id) if scope.conversation_id else None
    return ("profile_id", scope.profile_id) if scope.profile_id else None
