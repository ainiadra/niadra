"""The notification feed on PostgreSQL (7.5): numbered under the space's feed lock, read by sequence."""

import typing
from datetime import datetime
from uuid import UUID

import orjson
from sqlalchemy import Table, Text, delete, or_, select, text
from sqlalchemy import cast as cast_
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.turns import NotificationFeed
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.notify.feed import FeedEvent, NewFeedEvent
from niadra.domain.notify.webhooks import erasure_needles
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope

# The space's feed lock (its database's own): held to the commit, so a later number never commits before an
# earlier one, and the next number is read after the lock, by a statement of its own.
FEED_LOCK = 0x4E49_4144_4645_4544
LOCK = text("SELECT pg_advisory_xact_lock(CAST(:lock AS bigint))")
_SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"
# The next number, unless the body names an id an erasure fenced off (`needle:`): the event about the target
# that was still in the queue when it was erased never joins the feed.
APPEND = text(
    "INSERT INTO notification_feed (space_id, seq, event_id, type, created_at, body)"
    f" SELECT {_SPACE}, coalesce((SELECT max(seq) FROM notification_feed WHERE space_id = {_SPACE}), 0) + 1,"
    "  CAST(:event_id AS uuid), :type, :at, CAST(:body AS jsonb)"
    " WHERE NOT EXISTS (SELECT 1 FROM turn_tombstones"
    f"  WHERE space_id = {_SPACE} AND mark LIKE 'needle:%'"
    "  AND strpos(CAST(:body AS text), substr(mark, 8)) > 0)"
    " ON CONFLICT (space_id, event_id) DO NOTHING RETURNING seq"
)
SINCE = text(
    f"SELECT h.head, f.seq, f.body FROM (SELECT coalesce(max(seq), 0) AS head FROM notification_feed"
    f" WHERE space_id = {_SPACE}) AS h LEFT JOIN LATERAL (SELECT seq, body FROM notification_feed"
    f" WHERE space_id = {_SPACE} AND seq > :after ORDER BY seq LIMIT :limit) AS f ON true ORDER BY f.seq"
)
_F = typing.cast(Table, NotificationFeed.__table__)


class PgFeedRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002
        self._db = session

    async def append(self, event: NewFeedEvent) -> int | None:
        await self._db.execute(LOCK, {"lock": FEED_LOCK})
        params = {
            "event_id": event.event_id,
            "type": event.type,
            "at": event.created_at,
            "body": orjson.dumps(event.body).decode(),
        }
        seq = (await self._db.execute(APPEND, params)).scalar_one_or_none()
        return int(seq) if seq is not None else None

    async def since(self, after: int, limit: int) -> tuple[list[FeedEvent], int]:
        rows = (await self._db.execute(SINCE, {"after": after, "limit": limit})).all()
        events = [FeedEvent(int(r.seq), dict(r.body)) for r in rows if r.seq is not None]
        return events, int(rows[0].head) if rows else 0

    async def purge_before(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(_F).where(_F.c.space_id == CURRENT_SPACE, _F.c.created_at < before)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        needles = erasure_needles(scope)
        if not needles:
            return []
        body = cast_(_F.c.body, Text)
        doomed = (
            select(_F.c.seq)
            .where(_F.c.space_id == CURRENT_SPACE, or_(*(body.contains(n, autoescape=True) for n in needles)))
            .order_by(_F.c.seq)
            .limit(limit)
        )
        stmt = (
            delete(_F)
            .where(_F.c.space_id == CURRENT_SPACE, _F.c.seq.in_(doomed.scalar_subquery()))
            .returning(_F.c.event_id)
        )
        gone: list[UUID] = sorted((await self._db.execute(stmt)).scalars())
        return [ErasedRow("notification_feed", {"event_id": str(e)}) for e in gone]
