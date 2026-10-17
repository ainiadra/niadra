"""PostgreSQL implementation of `EventRepository`.

`events` and `event_handles` are range-partitioned by `event_id`, a UUIDv7 minted on arrival, so
every read by time also bounds `event_id` from below to prune partitions (06, 6.2).
"""

import typing
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import (
    ColumnElement,
    Date,
    LargeBinary,
    Table,
    Text,
    and_,
    cast,
    delete,
    exists,
    func,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.platform import Outbox, SpaceState
from niadra.adapters.outbound.postgres.schema.write_path import (
    Conversation,
    Event,
    EventHandle,
    EventKeys0,
    EventKeys1,
    EventKeys2,
    EventMention,
    IngestImport,
    IngestIntake,
    SourceHeartbeat,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7_floor
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.ingest.events import ConversationTouch, NewEvent, export_row
from niadra.domain.ingest.files import FileFormat, FileImport, ImportStatus
from niadra.domain.ingest.intake import IntakeRow, NewIntake
from niadra.domain.ingest.skew import SKEW_TOLERANCE
from niadra.domain.ingest.storage import event_blob_keys
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import by_key
from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.tasks import CATALOG, TaskName
from niadra.domain.vocabulary import EventKind, Speaker, Visibility

# One advisory lock per space database for whoever applies the intake log, so rows are stored in order.
INTAKE_LOCK = 0x4E49_4144_5241_4932
_CLAIM = text(
    "SELECT generation, known FROM claim_event_keys("
    "CAST(:space AS uuid), CAST(:lock AS bigint), CAST(:keys AS text[]), CAST(:events AS uuid[]),"
    " CAST(:received AS timestamptz))"
)
ACCEPT = text(
    "SELECT ingest_accept(CAST(:space AS uuid), CAST(:lock AS bigint), CAST(:intake AS uuid),"
    " CAST(:received AS timestamptz), CAST(:source AS uuid), CAST(:keys AS text[]), CAST(:events AS uuid[]),"
    " CAST(:body AS bytea), CAST(:dek AS integer), CAST(:queue AS text))"
)
_KEY_TABLES: tuple[Table, ...] = tuple(t.__table__ for t in (EventKeys0, EventKeys1, EventKeys2))  # type: ignore[misc]
# One advisory lock shared by every ingestion and taken exclusively by `dedupe_rotate` (5.2). Advisory
# locks are per database, so rotating one space never waits for another space's ingestion.
DEDUPE_LOCK = 0x4E49_4144_5241_4431
GENERATION_KEY = "dedupe_generation"
MASKING_KEY = "ingest_masking_fingerprint"
MENTIONS_KEY = "event_mentions_ready"
ROTATION_LOCK_TIMEOUT = "5s"
# One webhook payload usually yields a handful of events; reading this many per payload wanted keeps
# `raw_refs` on the newest slice of the source index instead of grouping the whole history.
_EVENTS_PER_PAYLOAD = 20
_MERGED_ALIASES: ColumnElement[Any] = literal_column(
    "ARRAY(SELECT DISTINCT a FROM unnest(conversations.aliases || excluded.aliases) AS a ORDER BY a)"
)


class PgEventRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        if not event_ids:
            return []
        stmt = select(Event).where(Event.space_id == CURRENT_SPACE, Event.event_id.in_(event_ids))
        rows = (await self._db.execute(stmt)).scalars().all()
        return sorted([await self._record(r) for r in rows], key=lambda r: (r.occurred_at, r.event_id))

    async def mark_content(self, event_ids: Sequence[UUID], marks: dict[str, Any]) -> None:
        if not event_ids:
            return
        events = typing.cast(Table, Event.__table__)
        meta = events.c.content_meta
        merged = func.coalesce(meta, text("'{}'::jsonb")).op("||")(cast(marks, JSONB))
        stmt = update(events).where(events.c.event_id.in_(event_ids)).values(content_meta=merged)
        await self._db.execute(stmt)

    async def events_after(
        self, after_id: UUID | None, before_id: UUID, limit: int
    ) -> list[tuple[EventRecord, str | None]]:
        stmt = select(Event).where(Event.space_id == CURRENT_SPACE, Event.event_id < before_id)
        if after_id is not None:
            stmt = stmt.where(Event.event_id > after_id)
        rows = (await self._db.execute(stmt.order_by(Event.event_id).limit(limit))).scalars().all()
        return [(await self._record(r), r.raw_ref) for r in rows]

    async def rewrite_content(self, records: Sequence[EventRecord]) -> None:
        events = typing.cast(Table, Event.__table__)
        for record in records:
            content_ct, fields_ct, dek_version = await self._seal(record)
            values: dict[str, Any] = {
                "content_ct": content_ct,
                "fields_ct": fields_ct,
                "dek_version": dek_version,
            }
            if record.action is not None:
                values["action"] = {k: v for k, v in record.action.items() if k != "result"}
            if masked := (record.content_meta or {}).get("masked"):
                meta = events.c.content_meta
                values["content_meta"] = func.coalesce(meta, text("'{}'::jsonb")).op("||")(
                    cast({"masked": masked}, JSONB)
                )
            await self._db.execute(
                update(events)
                .where(events.c.space_id == CURRENT_SPACE, events.c.event_id == record.event_id)
                .values(**values)
            )

    async def masked_counts(self, *, conversation_id: str | None, task_id: str | None) -> dict[str, int]:
        stmt = select(Event.content_meta["masked"]).where(
            Event.space_id == CURRENT_SPACE, Event.content_meta.has_key("masked")
        )
        if conversation_id is not None:
            stmt = stmt.where(Event.conversation_id == conversation_id)
        if task_id is not None:
            stmt = stmt.where(Event.task_id == task_id)
        totals: dict[str, int] = {}
        for (found,) in await self._db.execute(stmt):
            for kind, count in (found or {}).items():
                totals[kind] = totals.get(kind, 0) + int(count)
        return dict(sorted(totals.items()))

    async def masking_state(self) -> int | None:
        value = await self._db.scalar(select(SpaceState.value).where(SpaceState.key == MASKING_KEY))
        return int(value) if value is not None else None

    async def set_masking_state(self, fingerprint: int) -> None:
        stmt = insert(SpaceState).values(key=MASKING_KEY, value=fingerprint)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpaceState.key], set_={"value": fingerprint, "updated_at": func.now()}
            )
        )

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        # A stored `occurred_at` is never later than arrival plus the skew tolerance, which bounds the id.
        stmt = select(Event).where(
            Event.space_id == CURRENT_SPACE,
            Event.occurred_at >= since,
            Event.occurred_at <= until,
            Event.event_id >= _id_floor(since - SKEW_TOLERANCE),
        )
        if conversation_id is not None:
            stmt = stmt.where(Event.conversation_id == conversation_id)
        if task_id is not None:
            stmt = stmt.where(Event.task_id == task_id)
        rows = (await self._db.execute(stmt.order_by(Event.occurred_at, Event.event_id))).scalars().all()
        return [await self._record(r) for r in rows]

    async def events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int, *, before_id: UUID | None = None
    ) -> list[EventRecord]:
        if not handle_ids:
            return []
        floor = _id_floor(since)
        recent = (
            select(EventHandle.event_id)
            .where(
                EventHandle.space_id == CURRENT_SPACE,
                EventHandle.handle_id.in_(handle_ids),
                EventHandle.received_at >= since,
                EventHandle.event_id >= floor,
                *([EventHandle.event_id < before_id] if before_id is not None else []),
            )
            .distinct()
            .order_by(EventHandle.event_id.desc())
            .limit(limit)
        )
        stmt = select(Event).where(
            Event.space_id == CURRENT_SPACE, Event.event_id >= floor, Event.event_id.in_(recent)
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return sorted([await self._record(r) for r in rows], key=lambda r: (r.occurred_at, r.event_id))

    async def system_events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        if not handle_ids:
            return []
        floor = _id_floor(since)
        recent = (
            select(EventHandle.event_id)
            .join(Event, (Event.space_id == EventHandle.space_id) & (Event.event_id == EventHandle.event_id))
            .where(
                EventHandle.space_id == CURRENT_SPACE,
                EventHandle.handle_id.in_(handle_ids),
                EventHandle.received_at >= since,
                EventHandle.event_id >= floor,
                Event.event_id >= floor,
                Event.kind == EventKind.SYSTEM_EVENT.value,
            )
            .distinct()
            .order_by(EventHandle.event_id.desc())
            .limit(limit)
        )
        stmt = select(Event).where(
            Event.space_id == CURRENT_SPACE, Event.event_id >= floor, Event.event_id.in_(recent)
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        records = [await self._record(r) for r in rows]
        return sorted(records, key=lambda r: (r.occurred_at, r.event_id), reverse=True)

    async def recent_events(
        self, since: datetime, limit: int, *, after: UUID | None = None
    ) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.space_id == CURRENT_SPACE,
                Event.event_id >= _id_floor(since),
                Event.received_at >= since,
                *([Event.event_id > after] if after is not None else []),
            )
            .order_by(Event.event_id)
            .limit(limit)
        )
        return [await self._record(r) for r in (await self._db.execute(stmt)).scalars().all()]

    async def source_last_event_at(self, since: datetime) -> dict[UUID, datetime]:
        stmt = (
            select(SourceHeartbeat.source_id, func.max(SourceHeartbeat.last_event_at))
            .where(SourceHeartbeat.window_start >= since, SourceHeartbeat.last_event_at.is_not(None))
            .group_by(SourceHeartbeat.source_id)
        )
        return {source_id: last for source_id, last in await self._db.execute(stmt)}

    async def coverage(self, since: datetime) -> dict[UUID, list[SourceDay]]:
        day = cast(func.timezone("UTC", SourceHeartbeat.window_start), Date).label("day")
        stmt = (
            select(
                SourceHeartbeat.source_id,
                day,
                func.sum(SourceHeartbeat.sent),
                func.sum(SourceHeartbeat.received),
                func.sum(SourceHeartbeat.rejected),
                func.sum(SourceHeartbeat.unmapped),
                func.max(SourceHeartbeat.last_event_at),
            )
            .where(SourceHeartbeat.space_id == CURRENT_SPACE, SourceHeartbeat.window_start >= since)
            .group_by(SourceHeartbeat.source_id, day)
            .order_by(SourceHeartbeat.source_id, day)
        )
        found: dict[UUID, list[SourceDay]] = {}
        for source_id, on, sent, received, rejected, unmapped, last in await self._db.execute(stmt):
            found.setdefault(source_id, []).append(
                SourceDay(on, int(sent), int(received), int(rejected), int(unmapped), last)
            )
        return found

    async def received_since(self, since: datetime) -> bool:
        any_window = exists().where(
            SourceHeartbeat.space_id == CURRENT_SPACE,
            SourceHeartbeat.window_start >= since,
            SourceHeartbeat.received > 0,
        )
        return bool((await self._db.execute(select(any_window))).scalar_one())

    async def claim_keys(
        self, space_id: UUID, keys: Sequence[tuple[str, UUID]], received_at: datetime
    ) -> tuple[int, set[str]]:
        row = (
            await self._db.execute(
                _CLAIM,
                {
                    "space": space_id,
                    "lock": DEDUPE_LOCK,
                    "keys": [k for k, _ in keys],
                    "events": [e for _, e in keys],
                    "received": received_at,
                },
            )
        ).one()
        return int(row.generation), set(row.known)

    async def has_intake(self) -> bool:
        pending = select(IngestIntake.intake_id).where(IngestIntake.failed_at.is_(None)).limit(1)
        return bool(await self._db.scalar(select(pending.exists())))

    async def take_intake(self, limit: int) -> list[IntakeRow]:
        await self._db.execute(select(func.pg_advisory_xact_lock(INTAKE_LOCK)))
        return await self._oldest_intake(limit)

    async def try_take_intake(self, limit: int) -> list[IntakeRow] | None:
        if not (await self._db.execute(select(func.pg_try_advisory_xact_lock(INTAKE_LOCK)))).scalar_one():
            return None
        return await self._oldest_intake(limit)

    async def _oldest_intake(self, limit: int) -> list[IntakeRow]:
        return await self._intake_rows(IngestIntake.failed_at.is_(None), limit)

    async def _intake_rows(self, where: ColumnElement[bool], limit: int) -> list[IntakeRow]:
        rows = await self._db.execute(
            select(
                IngestIntake.intake_id,
                IngestIntake.received_at,
                IngestIntake.body_ct,
                IngestIntake.dek_version,
                IngestIntake.skip,
                IngestIntake.attempts,
                IngestIntake.source_id,
                IngestIntake.failed_at,
            )
            .where(where)
            .order_by(IngestIntake.seq)
            .limit(limit)
        )
        return [
            IntakeRow(
                r.intake_id,
                r.received_at,
                r.body_ct,
                r.dek_version,
                frozenset(r.skip),
                r.attempts,
                r.source_id,
                r.failed_at,
            )
            for r in rows
        ]

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        if not intake_ids:
            return
        await self._db.execute(delete(IngestIntake).where(IngestIntake.intake_id.in_(intake_ids)))
        await self._db.execute(delete(Outbox).where(Outbox.id.in_(intake_ids)))

    async def fail_intake(self, intake_id: UUID) -> int:
        stmt = (
            update(IngestIntake)
            .where(IngestIntake.intake_id == intake_id)
            .values(attempts=IngestIntake.attempts + 1)
            .returning(IngestIntake.attempts)
        )
        return int((await self._db.execute(stmt)).scalar_one_or_none() or 0)

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(IngestIntake).where(IngestIntake.intake_id == intake_id).values(failed_at=at)
        )
        await self._db.execute(delete(Outbox).where(Outbox.id == intake_id))

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[IntakeRow]:
        where: ColumnElement[bool] = IngestIntake.failed_at.is_not(None)
        if before is not None:
            where = and_(where, IngestIntake.received_at < before)
        if after is not None:
            mark = select(IngestIntake.seq).where(IngestIntake.intake_id == after).scalar_subquery()
            where = and_(where, IngestIntake.seq > mark)
        return await self._intake_rows(where, limit)

    async def count_failed_intake(self) -> int:
        kept = select(func.count()).select_from(IngestIntake).where(IngestIntake.failed_at.is_not(None))
        return int((await self._db.execute(kept)).scalar_one())

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        stmt = update(IngestIntake).where(IngestIntake.failed_at.is_not(None))
        if intake_ids is not None:
            stmt = stmt.where(IngestIntake.intake_id.in_(intake_ids))
        put_back = stmt.values(failed_at=None, attempts=0).returning(IngestIntake.intake_id)
        return [r.intake_id for r in await self._db.execute(put_back)]

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int) -> None:
        await self._db.execute(
            update(IngestIntake)
            .where(IngestIntake.intake_id == intake_id)
            .values(body_ct=body_ct, dek_version=dek_version)
        )

    async def release_keys(self, space_id: UUID, generation: int, keys: Sequence[str]) -> None:
        table = _key_table(generation)
        await self._db.execute(
            delete(table).where(table.c.space_id == space_id, table.c.idempotency_key.in_(keys))
        )

    async def rotate_dedupe(self, now: datetime, min_age: timedelta) -> int | None:
        await self._db.execute(text(f"SET LOCAL lock_timeout = '{ROTATION_LOCK_TIMEOUT}'"))
        await self._db.execute(select(func.pg_advisory_xact_lock(DEDUPE_LOCK)))
        state = (
            await self._db.execute(
                select(SpaceState.value, SpaceState.updated_at)
                .where(SpaceState.key == GENERATION_KEY)
                .with_for_update()
            )
        ).one()
        if now - state.updated_at < min_age:
            return None
        generation = int(state.value) + 1
        await self._db.execute(
            update(SpaceState)
            .where(SpaceState.key == GENERATION_KEY)
            .values(value=generation, updated_at=now)
        )
        # The retired table is neither the new current nor the new previous: nobody reads it.
        await self._db.execute(text(f"TRUNCATE {_key_table(generation + 1).name}"))
        return generation

    async def insert_events(self, events: Sequence[NewEvent]) -> None:
        if not events:
            return
        rows = [await self._event_row(e) for e in events]
        await self._db.execute(insert(Event), rows)
        handles = [
            {
                "space_id": e.space_id,
                "event_id": e.record.event_id,
                "handle_id": h,
                "received_at": e.record.received_at,
            }
            for e in events
            for h in e.record.handle_ids
        ]
        if handles:
            await self._db.execute(insert(EventHandle), handles)
        mentions = [
            {"space_id": e.space_id, "event_id": e.record.event_id, "tokens": sorted(e.mentions)}
            for e in events
            if e.mentions
        ]
        if mentions:
            await self._db.execute(insert(EventMention).values(mentions).on_conflict_do_nothing())

    async def events_naming(
        self, tokens: Sequence[bytes], *, besides: Sequence[UUID], after: UUID | None, limit: int
    ) -> list[UUID]:
        if not tokens:
            return []
        stmt = select(EventMention.event_id).where(
            EventMention.space_id == CURRENT_SPACE,
            EventMention.tokens.overlap(cast(sorted(tokens), ARRAY(LargeBinary))),
        )
        if besides:
            theirs = select(EventHandle.event_id).where(
                EventHandle.space_id == CURRENT_SPACE,
                EventHandle.event_id == EventMention.event_id,
                EventHandle.handle_id.in_(list(besides)),
            )
            stmt = stmt.where(~exists(theirs))
        if after is not None:
            stmt = stmt.where(EventMention.event_id > after)
        return list((await self._db.execute(stmt.order_by(EventMention.event_id).limit(limit))).scalars())

    async def index_mentions(self, mentions: Sequence[tuple[UUID, frozenset[bytes]]]) -> None:
        mentions = by_key(mentions)
        named = [{"space_id": CURRENT_SPACE, "event_id": e, "tokens": sorted(t)} for e, t in mentions if t]
        empty = [e for e, t in mentions if not t]
        if empty:
            await self._db.execute(
                delete(EventMention).where(
                    EventMention.space_id == CURRENT_SPACE, EventMention.event_id.in_(empty)
                )
            )
        if named:
            stmt = insert(EventMention).values(named)
            await self._db.execute(
                stmt.on_conflict_do_update(
                    index_elements=[EventMention.space_id, EventMention.event_id],
                    set_={"tokens": stmt.excluded.tokens},
                )
            )

    async def mentions_ready(self) -> bool:
        found = await self._db.scalar(select(SpaceState.value).where(SpaceState.key == MENTIONS_KEY))
        return found is not None

    async def mark_mentions_ready(self) -> None:
        stmt = insert(SpaceState).values(key=MENTIONS_KEY, value=1)
        await self._db.execute(stmt.on_conflict_do_nothing(index_elements=[SpaceState.key]))

    async def find_conversation(self, ids: Sequence[str]) -> str | None:
        stmt = (
            select(Conversation.conversation_id)
            .where(
                or_(
                    Conversation.conversation_id.in_(ids),
                    Conversation.aliases.overlap(cast(list(ids), ARRAY(Text))),
                )
            )
            .limit(1)
        )
        found: str | None = await self._db.scalar(stmt)
        return found

    async def known_conversation(self, conversation_id: str) -> tuple[UUID, ...] | None:
        found = (
            await self._db.execute(
                select(Conversation.primary_handle).where(
                    Conversation.space_id == CURRENT_SPACE, Conversation.conversation_id == conversation_id
                )
            )
        ).first()
        if found is None:
            return None
        return (found.primary_handle,) if found.primary_handle is not None else ()

    async def primary_handles(self, conversation_ids: Sequence[str]) -> dict[str, UUID]:
        if not conversation_ids:
            return {}
        rows = await self._db.execute(
            select(Conversation.conversation_id, Conversation.primary_handle).where(
                Conversation.space_id == CURRENT_SPACE,
                Conversation.conversation_id.in_(list(conversation_ids)),
                Conversation.primary_handle.is_not(None),
            )
        )
        return {r.conversation_id: r.primary_handle for r in rows}

    async def conversation_names(self, scope: ErasureScope) -> list[str]:
        target = _conversation_target(scope)
        if target is None:
            return []
        rows = await self._db.execute(
            select(Conversation.conversation_id, Conversation.aliases).where(
                Conversation.space_id == CURRENT_SPACE, target
            )
        )
        return sorted({name for row in rows for name in (row.conversation_id, *row.aliases)})

    async def upsert_conversation(self, space_id: UUID, touch: ConversationTouch) -> None:
        stmt = insert(Conversation).values(
            space_id=space_id,
            conversation_id=touch.conversation_id,
            aliases=sorted(touch.aliases),
            channel=touch.channel,
            primary_handle=touch.primary_handle,
            first_event_at=touch.first_event_at,
            last_event_at=touch.last_event_at,
            dirty=touch.dirty,
        )
        new, old = stmt.excluded, Conversation.__table__.c
        stmt = stmt.on_conflict_do_update(
            index_elements=[Conversation.space_id, Conversation.conversation_id],
            set_={
                "aliases": _MERGED_ALIASES,
                "primary_handle": func.coalesce(old.primary_handle, new.primary_handle),
                "first_event_at": func.least(old.first_event_at, new.first_event_at),
                "last_event_at": func.greatest(old.last_event_at, new.last_event_at),
                "dirty": or_(old.dirty, new.dirty),
            },
        )
        await self._db.execute(stmt)

    async def bump_heartbeat(
        self,
        space_id: UUID,
        source_id: UUID,
        window_start: datetime,
        *,
        sent: int | None = None,
        received: int = 0,
        rejected: int = 0,
        unmapped: int = 0,
        last_event_at: datetime | None = None,
    ) -> None:
        stmt = insert(SourceHeartbeat).values(
            space_id=space_id,
            source_id=source_id,
            window_start=window_start,
            sent=sent or 0,
            received=received,
            rejected=rejected,
            unmapped=unmapped,
            last_event_at=last_event_at,
        )
        new, old = stmt.excluded, SourceHeartbeat.__table__.c
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                SourceHeartbeat.space_id,
                SourceHeartbeat.source_id,
                SourceHeartbeat.window_start,
            ],
            set_={
                # The SDK may resend a window's counter; the largest value is the truest.
                "sent": func.greatest(old.sent, new.sent),
                "received": old.received + new.received,
                "rejected": old.rejected + new.rejected,
                "unmapped": old.unmapped + new.unmapped,
                "last_event_at": func.greatest(old.last_event_at, new.last_event_at),
            },
        )
        await self._db.execute(stmt)

    async def raw_refs(self, source_id: UUID, *, limit: int) -> list[tuple[UUID, str]]:
        stmt = (
            select(Event.event_id, Event.raw_ref)
            .where(Event.space_id == CURRENT_SPACE, Event.source_id == source_id, Event.raw_ref.is_not(None))
            .order_by(Event.event_id.desc())
            .limit(limit * _EVENTS_PER_PAYLOAD)
        )
        newest: dict[str, UUID] = {}
        for event_id, ref in await self._db.execute(stmt):
            newest.setdefault(ref, event_id)
        return [(event_id, ref) for ref, event_id in newest.items()][:limit]

    async def recent_messages(self, source_id: UUID, *, limit: int) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.space_id == CURRENT_SPACE,
                Event.source_id == source_id,
                Event.kind == EventKind.MESSAGE.value,
            )
            .order_by(Event.event_id.desc())
            .limit(limit)
        )
        return [await self._record(r) for r in (await self._db.execute(stmt)).scalars().all()]

    async def archivable_events(
        self, *, since: datetime, until: datetime, after_id: UUID | None, limit: int
    ) -> list[EventRecord]:
        stmt = select(Event).where(
            Event.space_id == CURRENT_SPACE,
            Event.event_id >= _id_floor(since),
            Event.event_id < _id_floor(until),
            Event.received_at >= since,
            Event.received_at < until,
        )
        if after_id is not None:
            stmt = stmt.where(Event.event_id > after_id)
        rows = (await self._db.execute(stmt.order_by(Event.event_id).limit(limit))).scalars().all()
        return [await self._record(r) for r in rows]

    async def purge_archived(self, *, since: datetime, until: datetime, limit: int) -> int:
        # The same range `archivable_events` read, bounded by the id floors so only the month's
        # partitions are touched.
        stmt = (
            select(Event.event_id)
            .where(
                Event.space_id == CURRENT_SPACE,
                Event.event_id >= _id_floor(since),
                Event.event_id < _id_floor(until),
                Event.received_at >= since,
                Event.received_at < until,
            )
            .order_by(Event.event_id)
            .limit(limit)
        )
        ids = list((await self._db.execute(stmt)).scalars().all())
        if not ids:
            return 0
        await self._db.execute(
            delete(EventHandle).where(EventHandle.space_id == CURRENT_SPACE, EventHandle.event_id.in_(ids))
        )
        await self._db.execute(
            delete(EventMention).where(EventMention.space_id == CURRENT_SPACE, EventMention.event_id.in_(ids))
        )
        await self._db.execute(delete(Event).where(Event.space_id == CURRENT_SPACE, Event.event_id.in_(ids)))
        return len(ids)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        rows = await self._erase_events(await self._target_event_ids(scope, limit))
        if len(rows) < limit:
            rows += await self._erase_conversations(scope, limit - len(rows))
        return rows

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table == "events":
            events = await self._changed(Event, after_seq, upto_seq, limit)
            return [{**export_row(await self._record(r)), "change_seq": r.change_seq} for r in events]
        if table == "conversations":
            conversations = await self._changed(Conversation, after_seq, upto_seq, limit)
            return [_conversation_row(r) for r in conversations]
        raise ValueError(f"ingest does not export {table}")

    async def create_import(self, space_id: UUID, record: FileImport) -> None:
        await self._db.execute(
            insert(IngestImport).values(space_id=space_id, **_import_values(record)).on_conflict_do_nothing()
        )

    async def get_import(self, import_id: UUID) -> FileImport | None:
        stmt = select(IngestImport).where(
            IngestImport.space_id == CURRENT_SPACE, IngestImport.import_id == import_id
        )
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        return _import(row) if row else None

    async def save_import(self, record: FileImport) -> None:
        stmt = (
            update(IngestImport)
            .where(IngestImport.space_id == CURRENT_SPACE, IngestImport.import_id == record.import_id)
            .values(**_import_values(record))
        )
        await self._db.execute(stmt)

    async def _changed[M: (Event, Conversation)](
        self, model: type[M], after_seq: int, upto_seq: int, limit: int
    ) -> Sequence[M]:
        stmt = (
            select(model)
            .where(
                model.space_id == CURRENT_SPACE, model.change_seq > after_seq, model.change_seq <= upto_seq
            )
            .order_by(model.change_seq)
            .limit(limit)
        )
        return (await self._db.execute(stmt)).scalars().all()

    async def _target_event_ids(self, scope: ErasureScope, limit: int) -> list[UUID]:
        if scope.kind is TargetKind.CONVERSATION:
            stmt = (
                select(Event.event_id)
                .where(Event.space_id == CURRENT_SPACE, Event.conversation_id == scope.conversation_id)
                .limit(limit)
            )
        elif scope.handle_ids:
            stmt = (
                select(EventHandle.event_id)
                .where(EventHandle.space_id == CURRENT_SPACE, EventHandle.handle_id.in_(scope.handle_ids))
                .distinct()
                .limit(limit)
            )
        else:
            return []
        return list((await self._db.execute(stmt)).scalars().all())

    async def _erase_events(self, event_ids: Sequence[UUID]) -> list[ErasedRow]:
        if not event_ids:
            return []
        ids = list(event_ids)
        await self._db.execute(
            delete(EventHandle).where(EventHandle.space_id == CURRENT_SPACE, EventHandle.event_id.in_(ids))
        )
        await self._db.execute(
            delete(EventMention).where(EventMention.space_id == CURRENT_SPACE, EventMention.event_id.in_(ids))
        )
        stmt = (
            delete(Event)
            .where(Event.space_id == CURRENT_SPACE, Event.event_id.in_(ids))
            .returning(Event.space_id, Event.event_id, Event.raw_ref, Event.content_meta, Event.change_seq)
        )
        return [
            ErasedRow(
                "events",
                {"event_id": str(row.event_id)},
                event_blob_keys(row.space_id, row.raw_ref, row.content_meta),
                row.change_seq,
            )
            for row in await self._db.execute(stmt)
        ]

    async def _erase_conversations(self, scope: ErasureScope, limit: int) -> list[ErasedRow]:
        """The conversation's own row for a conversation; for a person, the threads they started,
        whose ids and aliases are often their phone or e-mail."""
        target = _conversation_target(scope)
        if target is None:
            return []
        doomed = (
            select(Conversation.conversation_id)
            .where(Conversation.space_id == CURRENT_SPACE, target)
            .limit(limit)
            .scalar_subquery()
        )
        stmt = (
            delete(Conversation)
            .where(Conversation.space_id == CURRENT_SPACE, Conversation.conversation_id.in_(doomed))
            .returning(Conversation.conversation_id, Conversation.change_seq)
        )
        return [
            ErasedRow("conversations", {"conversation_id": row.conversation_id}, (), row.change_seq)
            for row in await self._db.execute(stmt)
        ]

    async def _event_row(self, new: NewEvent) -> dict[str, Any]:
        r = new.record
        content_ct, fields_ct, dek_version = await self._seal(r)
        action = {k: v for k, v in r.action.items() if k != "result"} if r.action is not None else None
        return {
            "space_id": new.space_id,
            "event_id": r.event_id,
            "idempotency_key": new.idempotency_key,
            "kind": r.kind.value,
            "canonical_type": r.canonical_type,
            "source_id": r.source_id,
            "channel": r.channel,
            "conversation_id": r.conversation_id,
            "task_id": r.task_id,
            "handle_ids": list(r.handle_ids),
            "subjects": list(r.subjects) or None,
            "object_refs": [
                {"type": o.type, "namespace": o.namespace, "id": o.external_id} for o in r.object_refs
            ]
            or None,
            "closes_ref": new.closes_ref,
            "action": action,
            "corrects_event_id": r.corrects_event_id,
            "speaker": r.speaker.value,
            "speaker_id": r.speaker_id,
            "direction": new.direction,
            "visibility": r.visibility.value,
            "verification_hint": new.verification_hint.value if new.verification_hint else None,
            "content_ct": content_ct,
            "content_meta": r.content_meta,
            "fields_ct": fields_ct,
            "voice": new.voice,
            "occurred_at": r.occurred_at,
            "received_at": r.received_at,
            "skew_corrected": new.skew_corrected,
            "raw_ref": new.raw_ref,
            "dek_version": dek_version,
        }

    async def _seal(self, record: EventRecord) -> tuple[bytes | None, bytes | None, int]:
        """Text in `content_ct`; structured fields and an action's free-text result in `fields_ct`."""
        sealer = self._require_sealer()
        private = _private(record)
        parts = (("content_ct", record.text), ("fields_ct", orjson.dumps(private) if private else None))
        # Both columns share one `dek_version`; a key rotation between the two calls reseals once.
        for _ in range(2):
            sealed = {
                column: await sealer.seal(value, table="events", row=record.event_id, column=column)
                for column, value in parts
                if value
            }
            versions = {version for _, version in sealed.values()}
            if len(versions) <= 1:
                break
        content = sealed.get("content_ct")
        fields = sealed.get("fields_ct")
        return (
            (content[0] if content else None),
            (fields[0] if fields else None),
            (versions.pop() if versions else 0),
        )

    async def _record(self, row: Event) -> EventRecord:
        sealer = self._require_sealer()
        text_ = (
            await sealer.open_text(
                row.content_ct, row.dek_version, table="events", row=row.event_id, column="content_ct"
            )
            if row.content_ct
            else None
        )
        private: dict[str, Any] = (
            orjson.loads(
                await sealer.open_bytes(
                    row.fields_ct, row.dek_version, table="events", row=row.event_id, column="fields_ct"
                )
            )
            if row.fields_ct
            else {}
        )
        action = dict(row.action) if row.action is not None else None
        if action is not None and "result" in private:
            action["result"] = private["result"]
        return EventRecord(
            event_id=row.event_id,
            kind=EventKind(row.kind),
            source_id=row.source_id,
            channel=row.channel,
            speaker=Speaker(row.speaker),
            occurred_at=row.occurred_at,
            received_at=row.received_at,
            handle_ids=tuple(row.handle_ids),
            text=text_,
            conversation_id=row.conversation_id,
            task_id=row.task_id,
            canonical_type=row.canonical_type,
            fields=private.get("fields") or {},
            object_refs=tuple(ObjectKey(o["type"], o["namespace"], o["id"]) for o in row.object_refs or ()),
            subjects=tuple(row.subjects or ()),
            action=action,
            visibility=Visibility(row.visibility),
            stt_confidence=(row.content_meta or {}).get("stt_confidence"),
            speaker_id=row.speaker_id,
            corrects_event_id=row.corrects_event_id,
            content_meta=row.content_meta,
        )

    def _require_sealer(self) -> Sealer:
        if self._sealer is None:
            raise RuntimeError("event content needs a space-scoped unit of work")
        return self._sealer


def _private(record: EventRecord) -> dict[str, Any]:
    private: dict[str, Any] = {}
    if record.fields:
        private["fields"] = record.fields
    if record.action and record.action.get("result"):
        private["result"] = record.action["result"]
    return private


def accept_params(intake: NewIntake) -> dict[str, Any]:
    return {
        "space": intake.space_id,
        "lock": DEDUPE_LOCK,
        "intake": intake.intake_id,
        "received": intake.received_at,
        "source": intake.source_id,
        "keys": [k for k, _ in intake.claims],
        "events": [e for _, e in intake.claims],
        "body": intake.body_ct,
        "dek": intake.dek_version,
        "queue": CATALOG[TaskName.APPLY_INTAKE].queue.value,
    }


def _key_table(generation: int) -> Table:
    return _KEY_TABLES[generation % len(_KEY_TABLES)]


def _conversation_target(scope: ErasureScope) -> Any:
    """The conversations an erasure takes: its own, or the threads the person started."""
    if scope.kind is TargetKind.CONVERSATION:
        return Conversation.conversation_id == scope.conversation_id
    if scope.handle_ids:
        return Conversation.primary_handle.in_(scope.handle_ids)
    return None


def _id_floor(at: datetime) -> UUID:
    return uuid7_floor(int(at.timestamp() * 1000))


def _conversation_row(row: Conversation) -> dict[str, Any]:
    return {
        "conversation_id": row.conversation_id,
        "aliases": list(row.aliases),
        "channel": row.channel,
        "primary_handle": row.primary_handle,
        "first_event_at": row.first_event_at,
        "last_event_at": row.last_event_at,
        "change_seq": row.change_seq,
    }


def _import_values(record: FileImport) -> dict[str, Any]:
    return {
        "import_id": record.import_id,
        "source_id": record.source_id,
        "key_id": record.key_id,
        "scopes": sorted(record.scopes),
        "format": record.format.value,
        "status": record.status.value,
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "header": list(record.header) if record.header is not None else None,
        "part_rows": list(record.part_rows),
        "next_part": record.next_part,
        "next_row": record.next_row,
        "accepted": record.accepted,
        "duplicates": record.duplicates,
        "rejected": record.rejected,
        "errors": list(record.errors),
        "created_at": record.created_at,
        "finished_at": record.finished_at,
    }


def _import(row: IngestImport) -> FileImport:
    return FileImport(
        import_id=row.import_id,
        source_id=row.source_id,
        key_id=row.key_id,
        scopes=frozenset(row.scopes),
        format=FileFormat(row.format),
        status=ImportStatus(row.status),
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        part_rows=tuple(row.part_rows),
        created_at=row.created_at,
        header=tuple(row.header) if row.header is not None else None,
        next_part=row.next_part,
        next_row=row.next_row,
        accepted=row.accepted,
        duplicates=row.duplicates,
        rejected=row.rejected,
        errors=tuple(row.errors),
        finished_at=row.finished_at,
    )
