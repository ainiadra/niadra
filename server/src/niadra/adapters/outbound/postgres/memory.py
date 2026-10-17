"""PostgreSQL implementation of `MemoryRepository`.

Every query filters by `origin_handle_id`, the hash-partition key, so it only touches the
partitions of the profile's handles (L6). Content columns are sealed with the space key; a row
with two sealed columns stores one `dek_version`, so both are sealed under the same version.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    CursorResult,
    Integer,
    Row,
    Select,
    String,
    Table,
    Text,
    and_,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    tuple_,
    union_all,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB, array, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.keyset import pair
from niadra.adapters.outbound.postgres.memory_purposes import PurposeRows
from niadra.adapters.outbound.postgres.mentions import with_mentions
from niadra.adapters.outbound.postgres.privacy_rows import changed_rows, delete_keys, delete_where
from niadra.adapters.outbound.postgres.schema.memory import (
    EpisodeRow,
    FactMention,
    FactRelation,
    FactRow,
    OpenItemFeedbackEventRow,
    OpenItemRow,
    ProfileAttrRow,
    SettledValueRow,
    TailSummary,
)
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.vectors import (
    NO_VECTOR,
    needs_vector,
    opened_vector,
    sealed_vector,
    store,
    vector_columns,
)
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.erasure import held_by_feedback_only, plan_evidence_erasure
from niadra.domain.memory.facts import Relation, RelationOrigin
from niadra.domain.memory.items import CLOSED_BY
from niadra.domain.memory.summaries import TailState
from niadra.domain.memory.values import SettledValue, ValueOrigin, ValueType
from niadra.domain.memory.vectors import NO_VECTOR_DIM, ItemVector, PendingVector
from niadra.domain.privacy.erasure import OPEN_ITEM_FEEDBACK, ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.purposes import tags_from_json
from niadra.domain.records import Episode, Fact, ObjectKey, OpenItem, ProfileAttr
from niadra.domain.vocabulary import (
    FactStatus,
    MemoryAudience,
    OpenItemKind,
    OpenItemStatus,
    RelationType,
    Sensitivity,
    Speaker,
    SpokenBy,
)

FACT_VALUE_HMAC = "fact_value"
SETTLED_VALUE_HMAC = "settled_value"
FACTS = cast(Table, FactRow.__table__)
MENTIONS = cast(Table, FactMention.__table__)
RELATIONS = cast(Table, FactRelation.__table__)
EPISODES = cast(Table, EpisodeRow.__table__)
ITEMS = cast(Table, OpenItemRow.__table__)
ITEM_FEEDBACK = cast(Table, OpenItemFeedbackEventRow.__table__)
ATTRS = cast(Table, ProfileAttrRow.__table__)
TAILS = cast(Table, TailSummary.__table__)
VALUES = cast(Table, SettledValueRow.__table__)
_FACT_KEY = ("origin_handle_id", "fact_id")
_MENTION_KEY = ("origin_handle_id", "fact_id", "event_id")
_EPISODE_KEY = ("origin_handle_id", "episode_id", "version")
_ATTR_KEY = ("origin_handle_id", "slot", "source_id")
_TAIL_KEY = ("profile_id", "level")
_VALUE_KEY = ("origin_handle_id", "value_id")

CONSOLIDATED_DAY = "memory_consolidated_day"
"""A counter row of `space_state`: the local day of the space's last nightly consolidation pass."""


class PgMemoryRepository(PurposeRows):
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def facts_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[FactStatus] = (FactStatus.ACTIVE,)
    ) -> list[Fact]:
        if not handle_ids:
            return []
        rows = await self._db.execute(
            select(FACTS)
            .where(*_facts_of(handle_ids), FACTS.c.status.in_([s.value for s in statuses]))
            .order_by(FACTS.c.valid_at, FACTS.c.fact_id)
        )
        return await self._facts(list(rows))

    async def episodes_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 200,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[Episode]:
        if not handle_ids:
            return []
        stmt = select(EPISODES).where(
            EPISODES.c.space_id == CURRENT_SPACE,
            EPISODES.c.origin_handle_id.in_(handle_ids),
            EPISODES.c.is_current,
        )
        if since is not None:
            stmt = stmt.where(EPISODES.c.ended_at >= since)
        if before is not None:
            stmt = stmt.where(tuple_(EPISODES.c.ended_at, EPISODES.c.episode_id) < pair(before))
        rows = await self._db.execute(
            stmt.order_by(EPISODES.c.ended_at.desc(), EPISODES.c.episode_id.desc()).limit(limit)
        )
        return [await self._episode(r) for r in rows]

    async def get_episode(self, episode_id: UUID) -> Episode | None:
        row = await self._first(
            select(EPISODES).where(
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.episode_id == episode_id,
                EPISODES.c.is_current,
            )
        )
        return await self._episode(row) if row else None

    async def episode_versions(self, origin_handle_id: UUID, episode_id: UUID) -> list[Episode]:
        rows = await self._db.execute(
            select(EPISODES)
            .where(
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.origin_handle_id == origin_handle_id,
                EPISODES.c.episode_id == episode_id,
            )
            .order_by(EPISODES.c.version)
        )
        return [await self._episode(r) for r in rows]

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]:
        if not handle_ids:
            return []
        rows = await self._db.execute(
            select(ITEMS)
            .where(
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.origin_handle_id.in_(handle_ids),
                ITEMS.c.status.in_([s.value for s in statuses]),
            )
            .order_by(ITEMS.c.created_at, ITEMS.c.item_id)
        )
        return [await self._item(r) for r in rows]

    async def open_items_due(self, until: datetime, *, limit: int) -> list[OpenItem]:
        # Served by the partial index `ix_open_items_due` (status = 'open'); crosses every partition.
        rows = await self._db.execute(
            select(ITEMS)
            .where(
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.status == OpenItemStatus.OPEN.value,
                ITEMS.c.due_at <= until,
            )
            .order_by(ITEMS.c.due_at.desc(), ITEMS.c.item_id.desc())
            .limit(limit)
        )
        return [await self._item(r) for r in rows]

    async def object_facts(
        self, keys: Sequence[ObjectKey], handle_ids: Sequence[UUID], *, per_object: int
    ) -> list[Fact]:
        if not keys or not handle_ids:
            return []
        ref = FACTS.c.object_ref
        # Served by `ix_facts_object_ref`; only the facts bound to an object carry an audience.
        about = tuple_(ref["type"].astext, ref["namespace"].astext, ref["external_id"].astext).in_(
            [(k.type, k.namespace, k.external_id) for k in keys]
        )
        rows = await self._db.execute(
            select(FACTS)
            .where(
                *_facts_of(handle_ids),
                ref.is_not(None),
                about,
                FACTS.c.audience.is_not(None),
                FACTS.c.status == FactStatus.ACTIVE.value,
            )
            .order_by(FACTS.c.last_confirmed_at.desc(), FACTS.c.fact_id)
            .limit(per_object * len(keys))
        )
        # What was said about an object is shown and counted, never traced to its turns here: no mentions.
        return await self._facts(list(rows), evidence=False)

    async def object_items(
        self,
        keys: Sequence[ObjectKey],
        handle_ids: Sequence[UUID],
        *,
        per_object: int,
        kept_since: datetime | None = None,
    ) -> list[OpenItem]:
        if not keys or not handle_ids:
            return []
        i = ITEMS.c
        live = i.status.in_([OpenItemStatus.OPEN.value, OpenItemStatus.OVERDUE.value])
        wanted = (
            live
            if kept_since is None
            else or_(live, and_(i.status == OpenItemStatus.RESOLVED.value, i.closed_at >= kept_since))
        )
        rows = await self._db.execute(
            select(ITEMS)
            .where(
                i.space_id == CURRENT_SPACE,
                i.origin_handle_id.in_(handle_ids),
                # Served by `ix_open_items_object`.
                tuple_(i.object_type, i.object_namespace, i.object_external_id).in_(
                    [(k.type, k.namespace, k.external_id) for k in keys]
                ),
                i.audience.is_not(None),
                wanted,
            )
            # The open ones first: a kept one never takes an open one's room.
            .order_by(live.desc(), i.created_at.desc(), i.item_id)
            .limit(per_object * len(keys))
        )
        return [await self._item(r) for r in rows]

    async def consolidated_day(self) -> int | None:
        found: int | None = await self._db.scalar(
            select(SpaceState.value).where(SpaceState.key == CONSOLIDATED_DAY)
        )
        return found

    async def set_consolidated_day(self, day: int) -> None:
        stmt = insert(SpaceState).values(key=CONSOLIDATED_DAY, value=day)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpaceState.key], set_={"value": day, "updated_at": func.now()}
            )
        )

    async def attrs_for(self, handle_ids: Sequence[UUID]) -> list[ProfileAttr]:
        if not handle_ids:
            return []
        rows = await self._db.execute(
            select(ATTRS)
            .where(ATTRS.c.space_id == CURRENT_SPACE, ATTRS.c.origin_handle_id.in_(handle_ids))
            .order_by(ATTRS.c.slot, ATTRS.c.observed_at)
        )
        return [await self._attr(r) for r in rows]

    async def _attr(self, r: Row[Any]) -> ProfileAttr:
        return ProfileAttr(
            origin_handle_id=r.origin_handle_id,
            slot=r.slot,
            value=await self._open(r.value_ct, r.dek_version, "profile_attrs", _attr_row(r), "value_ct"),
            source_id=r.source_id,
            observed_at=r.observed_at,
            times_seen=r.times_seen,
        )

    async def name_rows(
        self, handle_ids: Sequence[UUID], names: Sequence[str]
    ) -> tuple[list[Fact], list[ProfileAttr]]:
        if not handle_ids or not names:
            return [], []
        facts = await self._db.execute(
            select(FACTS)
            .where(
                *_facts_of(handle_ids),
                FACTS.c.predicate.in_(list(names)),
                FACTS.c.status == FactStatus.ACTIVE.value,
            )
            .order_by(FACTS.c.valid_at, FACTS.c.fact_id)
        )
        attrs = await self._db.execute(
            select(ATTRS)
            .where(
                ATTRS.c.space_id == CURRENT_SPACE,
                ATTRS.c.origin_handle_id.in_(handle_ids),
                ATTRS.c.slot.in_(list(names)),
            )
            .order_by(ATTRS.c.slot, ATTRS.c.observed_at)
        )
        # A label is not traced to its turns: no mentions read.
        return await self._facts(list(facts), evidence=False), [await self._attr(r) for r in attrs]

    async def facts_valued(self, predicates: Sequence[str], dedupe_value: str, *, limit: int) -> list[Fact]:
        if not predicates or not dedupe_value:
            return []
        rows = await self._db.execute(
            select(FACTS)
            .where(
                FACTS.c.space_id == CURRENT_SPACE,
                FACTS.c.predicate.in_(list(predicates)),
                FACTS.c.value_hmac == await self._s().hmac(FACT_VALUE_HMAC, dedupe_value),
                FACTS.c.status == FactStatus.ACTIVE.value,
            )
            .order_by(FACTS.c.last_confirmed_at.desc(), FACTS.c.fact_id)
            .limit(limit)
        )
        return await self._facts(list(rows), evidence=False)

    async def tail_summary(self, profile_id: UUID, level: str) -> str | None:
        state = await self.tail_state(profile_id, level)
        return state.text if state else None

    async def relations_for(
        self, handle_ids: Sequence[UUID], *, origin: RelationOrigin | None = None
    ) -> list[Relation]:
        if not handle_ids:
            return []
        stmt = select(RELATIONS).where(
            RELATIONS.c.space_id == CURRENT_SPACE, RELATIONS.c.a_handle_id.in_(handle_ids)
        )
        if origin is not None:
            stmt = stmt.where(RELATIONS.c.origin == origin)
        rows = await self._db.execute(stmt)
        return [
            Relation(
                MemoryRef(r.a_handle_id, r.a_fact_id),
                MemoryRef(r.b_handle_id, r.b_fact_id),
                RelationType(r.relation),
                origin=_relation_origin(r.origin),
            )
            for r in rows
        ]

    async def episode_vectors(self, episode_ids: Sequence[UUID]) -> list[ItemVector]:
        if not episode_ids:
            return []
        rows = await self._db.execute(
            select(EPISODES.c.episode_id, *vector_columns(EPISODES)).where(
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.episode_id.in_(list(episode_ids)),
                EPISODES.c.is_current,
                EPISODES.c.embedding_ct.is_not(None),
            )
        )
        return [await opened_vector(self._s(), ItemKind.EPISODE, "episodes", r, r.episode_id) for r in rows]

    async def vectors_for(self, handle_ids: Sequence[UUID]) -> list[ItemVector]:
        if not handle_ids:
            return []
        # The three kinds in one statement: a history search reads them with every read of its rows.
        kinds = (
            (ItemKind.FACT, FACTS, FACTS.c.fact_id, FACTS.c.status == FactStatus.ACTIVE.value),
            (ItemKind.EPISODE, EPISODES, EPISODES.c.episode_id, EPISODES.c.is_current),
            (ItemKind.OPEN_ITEM, ITEMS, ITEMS.c.item_id, None),
        )
        parts = [
            select(
                literal_column(str(n), Integer).label("part"),
                table.c.origin_handle_id.label("origin"),
                key.label("item_id"),
                *vector_columns(table),
            ).where(
                table.c.space_id == CURRENT_SPACE,
                table.c.origin_handle_id.in_(handle_ids),
                table.c.embedding_ct.is_not(None),
                *([] if current is None else [current]),
            )
            for n, (_, table, key, current) in enumerate(kinds)
        ]
        query = union_all(*parts)
        rows = await self._db.execute(
            query.order_by(*(query.selected_columns[c] for c in ("part", "origin", "item_id")))
        )
        return [
            await opened_vector(self._s(), kinds[r.part][0], kinds[r.part][1].name, r, r.item_id)
            for r in rows
        ]

    async def values_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        types: Sequence[ValueType] | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[SettledValue]:
        if not handle_ids:
            return []
        stmt = select(VALUES).where(*_values_of(handle_ids))
        if types is not None:
            stmt = stmt.where(VALUES.c.type.in_([t.value for t in types]))
        if since is not None:
            stmt = stmt.where(VALUES.c.said_at >= since)
        rows = await self._db.execute(
            stmt.order_by(VALUES.c.said_at.desc(), VALUES.c.value_id.desc()).limit(limit)
        )
        return [await self._value(r) for r in rows]

    async def values_by_keys(self, handle_ids: Sequence[UUID], keys: Sequence[str]) -> list[SettledValue]:
        wanted = [k for k in dict.fromkeys(keys) if k]
        if not handle_ids or not wanted:
            return []
        digests = [await self._s().hmac(SETTLED_VALUE_HMAC, k) for k in wanted]
        rows = await self._db.execute(
            select(VALUES)
            .where(*_values_of(handle_ids), VALUES.c.value_hmac.in_(digests))
            .order_by(VALUES.c.said_at.desc(), VALUES.c.value_id.desc())
        )
        return [await self._value(r) for r in rows]

    async def insert_values(self, values: Sequence[SettledValue]) -> int:
        new = 0
        for value in values:
            ct, version = await self._s().seal(
                value.raw, table="settled_values", row=value.value_id, column="raw_ct"
            )
            stmt = (
                insert(VALUES)
                .values(
                    space_id=CURRENT_SPACE,
                    origin_handle_id=value.origin_handle_id,
                    value_id=value.value_id,
                    type=value.type.value,
                    raw_ct=ct,
                    value_hmac=await self._s().hmac(SETTLED_VALUE_HMAC, value.key),
                    said_at=value.said_at,
                    speaker=value.speaker.value,
                    session_id=value.session_id,
                    conversation_id=value.conversation_id,
                    channel=value.channel,
                    source_id=value.source_id,
                    episode_id=value.episode_id,
                    object_ref=_key_json(value.object_ref),
                    evidence_event_ids=list(value.evidence_event_ids),
                    origin=value.origin.value,
                    created_at=value.created_at,
                    dek_version=version,
                )
                .on_conflict_do_nothing()
                .returning(VALUES.c.value_id)
            )
            new += (await self._db.scalar(await with_mentions(stmt, self._sealer))) is not None
        return new

    async def insert_fact(self, fact: Fact, dedupe_value: str) -> bool:
        ct, version = await self._s().seal(fact.value, table="facts", row=fact.fact_id, column="value_ct")
        stmt = (
            insert(FACTS)
            .values(
                space_id=CURRENT_SPACE,
                origin_handle_id=fact.origin_handle_id,
                fact_id=fact.fact_id,
                origin_conversation_id=fact.origin_conversation_id,
                predicate=fact.predicate,
                namespace=fact.namespace,
                value_ct=ct,
                value_hmac=await self._s().hmac(FACT_VALUE_HMAC, dedupe_value),
                category=fact.category,
                sensitivity=fact.sensitivity.value,
                speaker=fact.speaker.value,
                confidence=fact.confidence,
                valid_at=fact.valid_at,
                invalid_at=fact.invalid_at,
                created_at=fact.created_at,
                expired_at=fact.expired_at,
                status=fact.status.value,
                times_seen=fact.times_seen,
                last_confirmed_at=fact.last_confirmed_at,
                about_handle_id=fact.about_handle_id,
                object_ref=_key_json(fact.object_ref),
                mention_handle_ids=list(fact.mention_handle_ids),
                dek_version=version,
                valid_until=fact.valid_until,
                sessions_seen=fact.sessions_seen,
                audience=fact.audience.value if fact.audience else None,
            )
            .on_conflict_do_nothing()
            .returning(FACTS.c.fact_id)
        )
        return (await self._db.scalar(stmt)) is not None

    async def find_fact(self, origin_handle_id: UUID, predicate: str, dedupe_value: str) -> Fact | None:
        row = await self._first(
            select(FACTS).where(
                *_facts_of([origin_handle_id]),
                FACTS.c.predicate == predicate,
                FACTS.c.value_hmac == await self._s().hmac(FACT_VALUE_HMAC, dedupe_value),
            )
        )
        return (await self._facts([row]))[0] if row else None

    async def update_fact(self, fact: Fact) -> None:
        await self._db.execute(
            update(FACTS)
            .where(*_facts_of([fact.origin_handle_id]), FACTS.c.fact_id == fact.fact_id)
            .values(
                status=fact.status.value,
                speaker=fact.speaker.value,
                confidence=fact.confidence,
                valid_at=fact.valid_at,
                invalid_at=fact.invalid_at,
                expired_at=fact.expired_at,
                last_confirmed_at=fact.last_confirmed_at,
                times_seen=fact.times_seen,
                mention_handle_ids=list(fact.mention_handle_ids),
                valid_until=fact.valid_until,
                sessions_seen=fact.sessions_seen,
                audience=fact.audience.value if fact.audience else None,
            )
        )

    async def add_mentions(self, ref: MemoryRef, event_ids: Sequence[UUID], *, feedback: bool = False) -> int:
        if not event_ids:
            return 0
        ids = list(dict.fromkeys(event_ids))
        marked = set(ids) if feedback else await self._feedback_among(ids)
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "origin_handle_id": ref.origin_handle_id,
                "fact_id": ref.id,
                "event_id": e,
                "feedback": e in marked,
            }
            for e in ids
        ]
        inserted = await self._db.execute(
            insert(MENTIONS).values(rows).on_conflict_do_nothing().returning(MENTIONS.c.event_id)
        )
        return len(inserted.all())

    async def add_relations(self, relations: Sequence[Relation]) -> None:
        if not relations:
            return
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "a_handle_id": r.a.origin_handle_id,
                "a_fact_id": r.a.id,
                "b_handle_id": r.b.origin_handle_id,
                "b_fact_id": r.b.id,
                "relation": r.type.value,
                "origin": r.origin,
            }
            for r in dict.fromkeys(relations)
        ]
        await self._db.execute(insert(RELATIONS).values(rows).on_conflict_do_nothing())

    async def current_episode(self, handle_ids: Sequence[UUID], session_id: UUID) -> Episode | None:
        if not handle_ids:
            return None
        row = await self._first(
            select(EPISODES).where(
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.origin_handle_id.in_(handle_ids),
                EPISODES.c.session_id == session_id,
                EPISODES.c.is_current,
            )
        )
        return await self._episode(row) if row else None

    async def add_episode_version(self, episode: Episode, replaces: Episode | None = None) -> None:
        if replaces is not None:
            await self._db.execute(
                update(EPISODES)
                .where(
                    EPISODES.c.space_id == CURRENT_SPACE,
                    EPISODES.c.origin_handle_id == replaces.origin_handle_id,
                    EPISODES.c.episode_id == replaces.episode_id,
                    EPISODES.c.version == replaces.version,
                )
                .values(is_current=False)
            )
        sealed, version = await self._seal_row(
            "episodes",
            episode.episode_id,
            {"summary_ct": episode.summary, "resolution_ct": episode.resolution},
        )
        await self._db.execute(
            insert(EPISODES)
            .values(
                space_id=CURRENT_SPACE,
                origin_handle_id=episode.origin_handle_id,
                episode_id=episode.episode_id,
                version=episode.version,
                session_id=episode.session_id,
                conversation_id=episode.conversation_id,
                channel=episode.channel,
                source_id=episode.source_id,
                about_handle_id=episode.about_handle_id,
                summary_ct=sealed["summary_ct"],
                intent=episode.intent,
                category=episode.category,
                normalized_category=episode.normalized_category,
                outcome=episode.outcome,
                resolution_ct=sealed["resolution_ct"],
                sentiment=episode.sentiment,
                initiated_by=episode.initiated_by,
                spoken_by=episode.spoken_by.value if episode.spoken_by else None,
                started_at=episode.started_at,
                ended_at=episode.ended_at,
                is_current=True,
                dek_version=version,
                matter=episode.matter,
                matter_label=episode.matter_label,
                object_refs=[_key_json(k) for k in episode.object_refs] or None,
            )
            .on_conflict_do_nothing()
        )

    async def insert_open_item(self, item: OpenItem) -> bool:
        ct, version = await self._s().seal(
            item.description, table="open_items", row=item.item_id, column="description_ct"
        )
        stmt = (
            insert(ITEMS)
            .values(
                space_id=CURRENT_SPACE,
                origin_handle_id=item.origin_handle_id,
                item_id=item.item_id,
                about_handle_id=item.about_handle_id,
                description_ct=ct,
                owner=item.owner,
                due_at=item.due_at,
                status=item.status.value,
                origin=item.origin,
                object_type=item.object.type if item.object else None,
                object_namespace=item.object.namespace if item.object else None,
                object_external_id=item.object.external_id if item.object else None,
                expected_operation=item.expected_operation,
                closed_by_action_id=item.closed_by_action_id,
                closed_at=item.closed_at,
                created_at=item.created_at,
                dek_version=version,
                kind=item.kind.value,
                audience=item.audience.value if item.audience else None,
            )
            .on_conflict_do_nothing()
            .returning(ITEMS.c.item_id)
        )
        return (await self._db.scalar(stmt)) is not None

    async def update_open_item(self, item: OpenItem, *, closed_by_event: UUID | None = None) -> None:
        link: dict[str, Any] = {"closed_by_event_id": closed_by_event} if closed_by_event is not None else {}
        # What closed it lives in its origin: kept as the item says, dropped when it reopens. The rest of
        # the origin is the write path's own and stays as stored.
        if (closed_by := item.origin.get(CLOSED_BY)) is not None:
            link["origin"] = ITEMS.c.origin.op("||")(sql_cast({CLOSED_BY: closed_by}, JSONB))
        elif item.status in (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE):
            link["origin"] = ITEMS.c.origin.op("-")(literal(CLOSED_BY, Text))
        await self._db.execute(
            update(ITEMS)
            .where(
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.origin_handle_id == item.origin_handle_id,
                ITEMS.c.item_id == item.item_id,
            )
            .values(
                status=item.status.value,
                closed_by_action_id=item.closed_by_action_id,
                closed_at=item.closed_at,
                **link,
            )
        )

    async def note_item_feedback(self, links: Sequence[tuple[UUID, UUID]]) -> int:
        pairs = sorted(set(links))
        if not pairs:
            return 0
        stmt = (
            insert(ITEM_FEEDBACK)
            .values([{"space_id": CURRENT_SPACE, "item_id": i, "event_id": e} for i, e in pairs])
            .on_conflict_do_nothing()
            .returning(ITEM_FEEDBACK.c.event_id)
        )
        return len((await self._db.execute(stmt)).all())

    async def put_attr(self, attr: ProfileAttr) -> None:
        ct, version = await self._s().seal(
            attr.value, table="profile_attrs", row=_attr_row(attr), column="value_ct"
        )
        stmt = insert(ATTRS).values(
            space_id=CURRENT_SPACE,
            origin_handle_id=attr.origin_handle_id,
            slot=attr.slot,
            source_id=attr.source_id,
            value_ct=ct,
            observed_at=attr.observed_at,
            times_seen=attr.times_seen,
            dek_version=version,
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    ATTRS.c.space_id,
                    ATTRS.c.origin_handle_id,
                    ATTRS.c.slot,
                    ATTRS.c.source_id,
                ],
                set_={c: stmt.excluded[c] for c in ("value_ct", "observed_at", "times_seen", "dek_version")},
            )
        )

    async def tail_state(self, profile_id: UUID, level: str) -> TailState | None:
        row = await self._first(
            select(TAILS).where(
                TAILS.c.space_id == CURRENT_SPACE, TAILS.c.profile_id == profile_id, TAILS.c.level == level
            )
        )
        if row is None:
            return None
        text = await self._open(
            row.text_ct, row.dek_version, "tail_summaries", f"{profile_id}:{level}", "text_ct"
        )
        return TailState(text, row.version, row.covers_until, row.membership_version)

    async def put_tail_summary(self, profile_id: UUID, level: str, state: TailState) -> None:
        ct, version = await self._s().seal(
            state.text, table="tail_summaries", row=f"{profile_id}:{level}", column="text_ct"
        )
        stmt = insert(TAILS).values(
            space_id=CURRENT_SPACE,
            profile_id=profile_id,
            level=level,
            membership_version=state.membership_version,
            version=state.version,
            text_ct=ct,
            covers_until=state.covers_until,
            dek_version=version,
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[TAILS.c.space_id, TAILS.c.profile_id, TAILS.c.level],
                set_={
                    c: stmt.excluded[c]
                    for c in ("membership_version", "version", "text_ct", "covers_until", "dek_version")
                },
            )
        )

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        found: list[PendingVector] = []
        for table, extra in (
            (FACTS, [FACTS.c.status == FactStatus.ACTIVE.value]),
            (EPISODES, [EPISODES.c.is_current]),
            (ITEMS, [ITEMS.c.status.in_([OpenItemStatus.OPEN.value, OpenItemStatus.OVERDUE.value])]),
        ):
            if len(found) >= limit:
                break
            where = [table.c.space_id == CURRENT_SPACE, needs_vector(table, model), *extra]
            if handle_ids is not None:
                where.append(table.c.origin_handle_id.in_(handle_ids))
            rows = list(
                await self._db.execute(
                    select(table).where(*where).order_by(*table.primary_key.columns).limit(limit - len(found))
                )
            )
            if table is FACTS:
                found += [PendingVector(f) for f in await self._facts(rows)]
            elif table is EPISODES:
                found += [PendingVector(await self._episode(r)) for r in rows]
            else:
                # A restatement may reword an item in place: its vector lands only on the text it was made of.
                found += [PendingVector(await self._item(r), fence=r.change_seq) for r in rows]
        return found

    async def set_vector(self, pending: PendingVector, vector: Sequence[float] | None, model: str) -> bool:
        record = pending.record
        if isinstance(record, Fact):
            return await self._set_fact_vector(record, vector, model)
        assert vector is not None, "only a structured fact goes without a vector"
        if isinstance(record, Episode):
            where = [
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.origin_handle_id == record.origin_handle_id,
                EPISODES.c.episode_id == record.episode_id,
                EPISODES.c.version == record.version,
            ]
            values = await sealed_vector(self._s(), "episodes", record.episode_id, vector, model)
            return await store(self._db, EPISODES, where, values, pending.fence)
        if isinstance(record, OpenItem):
            where = [
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.origin_handle_id == record.origin_handle_id,
                ITEMS.c.item_id == record.item_id,
            ]
            values = await sealed_vector(self._s(), "open_items", record.item_id, vector, model)
            return await store(self._db, ITEMS, where, values, pending.fence)
        raise ValueError(f"memory holds no {pending.kind.value} rows")

    async def _set_fact_vector(self, fact: Fact, vector: Sequence[float] | None, model: str) -> bool:
        where = [*_facts_of([fact.origin_handle_id]), FACTS.c.fact_id == fact.fact_id]
        if vector is None:
            marked = await self._db.execute(update(FACTS).where(*where).values(embedding_dim=NO_VECTOR_DIM))
            return cast(CursorResult[Any], marked).rowcount > 0
        values = await sealed_vector(self._s(), "facts", fact.fact_id, vector, model)
        return await store(self._db, FACTS, where, values, None)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return await self._erase_evidence(scope.event_ids, limit)
        erased: list[ErasedRow] = []
        for step in self._origin_steps(scope):
            erased += await step(limit - len(erased))
            if len(erased) >= limit:
                break
        return erased

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        source = {"facts": FACTS, "fact_mentions": MENTIONS, "episodes": EPISODES, "open_items": ITEMS}.get(
            table
        )
        if source is None:
            raise ValueError(f"memory does not export {table}")
        rows = await changed_rows(self._db, source, after_seq=after_seq, upto_seq=upto_seq, limit=limit)
        if table == "facts":
            facts = await self._facts(rows)
            return [
                export_row(f, r.change_seq, drop=("evidence_event_ids",))
                for f, r in zip(facts, rows, strict=True)
            ]
        if table == "episodes":
            return [
                {**export_row(await self._episode(r), r.change_seq), "is_current": r.is_current} for r in rows
            ]
        if table == "open_items":
            return [export_row(await self._item(r), r.change_seq) for r in rows]
        return [
            {
                "origin_handle_id": r.origin_handle_id,
                "fact_id": r.fact_id,
                "event_id": r.event_id,
                "change_seq": r.change_seq,
            }
            for r in rows
        ]

    async def expire_facts(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        keys = [
            (o, f)
            for o, f in await self._db.execute(
                select(FACTS.c.origin_handle_id, FACTS.c.fact_id)
                .where(FACTS.c.space_id == CURRENT_SPACE, FACTS.c.last_confirmed_at < before)
                .limit(limit)
            )
        ]
        if not keys:
            return []
        m = MENTIONS.c
        mentions = await self._db.execute(
            MENTIONS.delete()
            .where(m.space_id == CURRENT_SPACE, tuple_(m.origin_handle_id, m.fact_id).in_(keys))
            .returning(m.origin_handle_id, m.fact_id, m.event_id, m.change_seq)
        )
        erased = [
            ErasedRow(
                "fact_mentions",
                {"origin_handle_id": str(o), "fact_id": str(f), "event_id": str(e)},
                change_seq=seq,
            )
            for o, f, e, seq in mentions
        ]
        return erased + await self._delete_facts(keys)

    async def expire_episodes(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        erased = await delete_where(self._db, EPISODES, _EPISODE_KEY, [EPISODES.c.ended_at < before], limit)
        return erased + await delete_where(
            self._db, VALUES, _VALUE_KEY, [VALUES.c.said_at < before], limit - len(erased)
        )

    async def expire_items(self, closed_before: datetime, *, limit: int) -> list[ErasedRow]:
        i = ITEMS.c
        return await self._delete_items(
            [i.status == OpenItemStatus.RESOLVED.value, i.closed_at < closed_before], limit
        )

    async def _erase_evidence(self, event_ids: Sequence[UUID], limit: int) -> list[ErasedRow]:
        """Mentions of erased events go; a fact goes with the last mention the customer's evidence left
        (L17), its feedback mentions with it, and so do the rows only that feedback holds; an open item
        goes when any event it was extracted from is erased, since its description paraphrases that
        event."""
        evidence, feedback = await self._evidence_mentioning(event_ids, limit)
        plan = plan_evidence_erasure(
            evidence, set(event_ids).__contains__, limit, lambda e: e not in feedback
        )
        erased = await delete_keys(
            self._db, MENTIONS, _MENTION_KEY, [(o, f, e) for (o, f), e in plan.evidence]
        )
        erased += await self._delete_facts(plan.items)
        gone = set(plan.items)
        named = {e for key, e in plan.evidence if key in gone and e in feedback}
        seen = set(named)
        while named:
            others, marked = await self._evidence_mentioning(list(named), None)
            doomed = held_by_feedback_only(others, marked.__contains__)
            named = {e for key in doomed for e in others[key]} - seen
            seen |= named
            erased += await delete_keys(
                self._db, MENTIONS, _MENTION_KEY, [(*key, e) for key in doomed for e in others[key]]
            )
            erased += await self._delete_facts(doomed)
        erased += await self._delete_items(
            [ITEMS.c.origin["event_ids"].has_any(array([str(e) for e in event_ids]))], limit - len(erased)
        )
        erased += await delete_where(
            self._db,
            VALUES,
            _VALUE_KEY,
            [VALUES.c.evidence_event_ids.overlap(array(list(event_ids)))],
            limit - len(erased),
        )
        return erased

    async def _delete_items(self, where: Sequence[Any], limit: int) -> list[ErasedRow]:
        """At most `limit` open items, and a row (`OPEN_ITEM_FEEDBACK`) per feedback event that names one of
        them: the one that resolved it and the resolutions it refused (`open_item_feedback_events`, whose
        pairs leave with it). Privacy rewrites those events in the same batch (8.4)."""
        if limit <= 0:
            return []
        i = ITEMS.c
        picked = select(i.origin_handle_id, i.item_id).where(i.space_id == CURRENT_SPACE, *where).limit(limit)
        returned = (
            i.origin_handle_id,
            i.item_id,
            i.change_seq,
            i.closed_by_event_id,
            i.object_type,
            i.object_namespace,
            i.object_external_id,
            i.audience,
        )
        gone = await self._db.execute(
            delete(ITEMS)
            .where(i.space_id == CURRENT_SPACE, tuple_(i.origin_handle_id, i.item_id).in_(picked))
            .returning(*returned)
        )
        erased: list[ErasedRow] = []
        events: set[UUID] = set()
        items: list[UUID] = []
        for origin, item_id, seq, event_id, kind, namespace, external_id, audience in gone:
            key = {"origin_handle_id": str(origin), "item_id": str(item_id)}
            bound = (
                _ref_text(ObjectKey(kind, namespace, external_id))
                if kind and namespace and external_id and audience is not None
                else None
            )
            erased.append(ErasedRow("open_items", key, change_seq=seq, object_ref=bound))
            items.append(item_id)
            if event_id is not None:
                events.add(event_id)
        if items:
            # The resolutions the item refused name it too; their pairs go with it, ids only.
            f = ITEM_FEEDBACK.c
            refused = await self._db.execute(
                delete(ITEM_FEEDBACK)
                .where(f.space_id == CURRENT_SPACE, f.item_id.in_(items))
                .returning(f.event_id)
            )
            events.update(refused.scalars())
        erased += [ErasedRow(OPEN_ITEM_FEEDBACK, {"event_id": str(e)}) for e in sorted(events, key=str)]
        return erased

    async def _feedback_among(self, event_ids: Sequence[UUID]) -> set[UUID]:
        """The events already marked as feedback on some row, so a copied mention keeps its kind."""
        m = MENTIONS.c
        found = await self._db.execute(
            select(m.event_id)
            .where(m.space_id == CURRENT_SPACE, m.event_id.in_(event_ids), m.feedback)
            .distinct()
        )
        return set(found.scalars())

    async def _evidence_mentioning(
        self, event_ids: Sequence[UUID], limit: int | None
    ) -> tuple[dict[tuple[UUID, UUID], set[UUID]], set[UUID]]:
        """Every mention of up to `limit` facts that mention one of `event_ids`, and which of their events
        are feedback."""
        m = MENTIONS.c
        touched = (
            select(m.origin_handle_id, m.fact_id)
            .where(m.space_id == CURRENT_SPACE, m.event_id.in_(event_ids))
            .distinct()
            .limit(limit)
        )
        rows = await self._db.execute(
            select(m.origin_handle_id, m.fact_id, m.event_id, m.feedback).where(
                m.space_id == CURRENT_SPACE, tuple_(m.origin_handle_id, m.fact_id).in_(touched)
            )
        )
        evidence: dict[tuple[UUID, UUID], set[UUID]] = defaultdict(set)
        feedback: set[UUID] = set()
        for origin, fact_id, event_id, is_feedback in rows:
            evidence[(origin, fact_id)].add(event_id)
            if is_feedback:
                feedback.add(event_id)
        return evidence, feedback

    def _origin_steps(self, scope: ErasureScope) -> list[Callable[[int], Awaitable[list[ErasedRow]]]]:
        tails: list[Callable[[int], Awaitable[list[ErasedRow]]]] = []
        if scope.profile_id is not None:
            profile_id = scope.profile_id
            # Tail summaries are written from episodes, so any erasure of the profile invalidates them.
            tails.append(
                lambda room: delete_where(
                    self._db, TAILS, _TAIL_KEY, [TAILS.c.profile_id == profile_id], room
                )
            )
        if scope.kind is TargetKind.CONVERSATION:
            where = [EPISODES.c.conversation_id == scope.conversation_id]
            said = [VALUES.c.conversation_id == scope.conversation_id]
            if scope.handle_ids:
                where.append(EPISODES.c.origin_handle_id.in_(scope.handle_ids))
                said.append(VALUES.c.origin_handle_id.in_(scope.handle_ids))
            return [
                lambda room: delete_where(self._db, EPISODES, _EPISODE_KEY, where, room),
                lambda room: delete_where(self._db, VALUES, _VALUE_KEY, said, room),
                *tails,
            ]
        handles = list(scope.handle_ids)
        if not handles:
            return tails
        return [
            lambda room: delete_where(
                self._db, MENTIONS, _MENTION_KEY, [MENTIONS.c.origin_handle_id.in_(handles)], room
            ),
            lambda room: self._erase_facts_of(handles, room),
            lambda room: delete_where(
                self._db, EPISODES, _EPISODE_KEY, [EPISODES.c.origin_handle_id.in_(handles)], room
            ),
            lambda room: self._delete_items([ITEMS.c.origin_handle_id.in_(handles)], room),
            lambda room: delete_where(
                self._db, VALUES, _VALUE_KEY, [VALUES.c.origin_handle_id.in_(handles)], room
            ),
            lambda room: delete_where(
                self._db, ATTRS, _ATTR_KEY, [ATTRS.c.origin_handle_id.in_(handles)], room
            ),
            lambda room: self._erase_objections(handles, room),
            *tails,
        ]

    async def _erase_facts_of(self, handles: Sequence[UUID], limit: int) -> list[ErasedRow]:
        f = FACTS.c
        picked = await self._db.execute(
            select(f.origin_handle_id, f.fact_id, f.object_ref, f.audience)
            .where(*_facts_of(handles))
            .limit(limit)
        )
        rows = picked.all()
        # A fact bound to an object names it on the receipt.
        bound = {
            str(r.fact_id): _ref_text(ObjectKey(**r.object_ref))
            for r in rows
            if r.object_ref and r.audience is not None
        }
        erased = await self._delete_facts([(r.origin_handle_id, r.fact_id) for r in rows])
        return [replace(e, object_ref=bound.get(e.key.get("fact_id", ""))) for e in erased]

    async def _delete_facts(self, keys: Sequence[tuple[UUID, UUID]]) -> list[ErasedRow]:
        """Facts and the relations that name them; their vectors live in the same row (5.14)."""
        if not keys:
            return []
        r = RELATIONS.c
        await self._db.execute(
            RELATIONS.delete().where(
                r.space_id == CURRENT_SPACE,
                or_(
                    tuple_(r.a_handle_id, r.a_fact_id).in_(keys), tuple_(r.b_handle_id, r.b_fact_id).in_(keys)
                ),
            )
        )
        return await delete_keys(self._db, FACTS, _FACT_KEY, keys)

    # The withholding pass (`app/withhold.py`): every row in key order, then the rewrite of what it scrubs.

    async def facts_page(self, after: UUID | None, limit: int) -> list[Fact]:
        stmt = select(FACTS).where(FACTS.c.space_id == CURRENT_SPACE)
        if after is not None:
            stmt = stmt.where(FACTS.c.fact_id > after)
        return await self._facts(list(await self._db.execute(stmt.order_by(FACTS.c.fact_id).limit(limit))))

    async def episodes_page(self, after: tuple[UUID, int] | None, limit: int) -> list[Episode]:
        stmt = select(EPISODES).where(EPISODES.c.space_id == CURRENT_SPACE)
        if after is not None:
            key = tuple_(literal(after[0], PG_UUID(as_uuid=True)), literal(after[1], Integer))
            stmt = stmt.where(tuple_(EPISODES.c.episode_id, EPISODES.c.version) > key)
        rows = await self._db.execute(stmt.order_by(EPISODES.c.episode_id, EPISODES.c.version).limit(limit))
        return [await self._episode(r) for r in rows]

    async def facts_by_ids(self, fact_ids: Sequence[UUID]) -> list[Fact]:
        if not fact_ids:
            return []
        stmt = select(FACTS).where(FACTS.c.space_id == CURRENT_SPACE, FACTS.c.fact_id.in_(list(fact_ids)))
        return await self._facts(list(await self._db.execute(stmt.order_by(FACTS.c.fact_id))))

    async def items_by_ids(self, item_ids: Sequence[UUID]) -> list[OpenItem]:
        if not item_ids:
            return []
        stmt = select(ITEMS).where(ITEMS.c.space_id == CURRENT_SPACE, ITEMS.c.item_id.in_(list(item_ids)))
        return [await self._item(r) for r in await self._db.execute(stmt.order_by(ITEMS.c.item_id))]

    async def episodes_by_ids(self, episode_ids: Sequence[UUID]) -> list[Episode]:
        if not episode_ids:
            return []
        stmt = select(EPISODES).where(
            EPISODES.c.space_id == CURRENT_SPACE, EPISODES.c.episode_id.in_(list(episode_ids))
        )
        rows = await self._db.execute(stmt.order_by(EPISODES.c.episode_id, EPISODES.c.version))
        return [await self._episode(r) for r in rows]

    async def tails_page(
        self, after: tuple[UUID, str] | None, limit: int
    ) -> list[tuple[UUID, str, TailState]]:
        stmt = select(TAILS).where(TAILS.c.space_id == CURRENT_SPACE)
        if after is not None:
            key = tuple_(literal(after[0], PG_UUID(as_uuid=True)), literal(after[1], String))
            stmt = stmt.where(tuple_(TAILS.c.profile_id, TAILS.c.level) > key)
        rows = await self._db.execute(stmt.order_by(TAILS.c.profile_id, TAILS.c.level).limit(limit))
        found: list[tuple[UUID, str, TailState]] = []
        for r in rows:
            row = f"{r.profile_id}:{r.level}"
            text = await self._open(r.text_ct, r.dek_version, "tail_summaries", row, "text_ct")
            found.append(
                (r.profile_id, r.level, TailState(text, r.version, r.covers_until, r.membership_version))
            )
        return found

    async def items_page(self, after: UUID | None, limit: int) -> list[OpenItem]:
        stmt = select(ITEMS).where(ITEMS.c.space_id == CURRENT_SPACE)
        if after is not None:
            stmt = stmt.where(ITEMS.c.item_id > after)
        return [
            await self._item(r) for r in await self._db.execute(stmt.order_by(ITEMS.c.item_id).limit(limit))
        ]

    async def values_page(self, after: UUID | None, limit: int) -> list[SettledValue]:
        stmt = select(VALUES).where(VALUES.c.space_id == CURRENT_SPACE)
        if after is not None:
            stmt = stmt.where(VALUES.c.value_id > after)
        rows = await self._db.execute(stmt.order_by(VALUES.c.value_id).limit(limit))
        return [await self._value(r) for r in rows]

    async def values_by_ids(self, value_ids: Sequence[UUID]) -> list[SettledValue]:
        if not value_ids:
            return []
        stmt = select(VALUES).where(
            VALUES.c.space_id == CURRENT_SPACE, VALUES.c.value_id.in_(list(value_ids))
        )
        return [await self._value(r) for r in await self._db.execute(stmt)]

    async def rewrite_value(self, value: SettledValue) -> None:
        """A settled value sealed again as it is now, found from then on by what it says now."""
        ct, version = await self._s().seal(
            value.raw, table="settled_values", row=value.value_id, column="raw_ct"
        )
        await self._db.execute(
            update(VALUES)
            .where(VALUES.c.space_id == CURRENT_SPACE, VALUES.c.value_id == value.value_id)
            .values(
                raw_ct=ct,
                value_hmac=await self._s().hmac(SETTLED_VALUE_HMAC, value.key),
                dek_version=version,
            )
        )

    async def attrs_page(self, after: tuple[UUID, str, UUID] | None, limit: int) -> list[ProfileAttr]:
        a = ATTRS.c
        stmt = select(ATTRS).where(a.space_id == CURRENT_SPACE)
        if after is not None:
            origin, slot, source = after
            key = tuple_(
                literal(origin, PG_UUID(as_uuid=True)),
                literal(slot, String),
                literal(source, PG_UUID(as_uuid=True)),
            )
            stmt = stmt.where(tuple_(a.origin_handle_id, a.slot, a.source_id) > key)
        rows = await self._db.execute(stmt.order_by(a.origin_handle_id, a.slot, a.source_id).limit(limit))
        return [
            ProfileAttr(
                origin_handle_id=r.origin_handle_id,
                slot=r.slot,
                value=await self._open(r.value_ct, r.dek_version, "profile_attrs", _attr_row(r), "value_ct"),
                source_id=r.source_id,
                observed_at=r.observed_at,
                times_seen=r.times_seen,
            )
            for r in rows
        ]

    async def rewrite_fact(self, fact: Fact, dedupe_value: str) -> bool:
        hmac = await self._s().hmac(FACT_VALUE_HMAC, dedupe_value)
        taken = await self._db.scalar(
            select(FACTS.c.fact_id).where(
                *_facts_of([fact.origin_handle_id]),
                FACTS.c.predicate == fact.predicate,
                FACTS.c.value_hmac == hmac,
                FACTS.c.fact_id != fact.fact_id,
            )
        )
        if taken is not None:
            return False
        ct, version = await self._s().seal(fact.value, table="facts", row=fact.fact_id, column="value_ct")
        await self._db.execute(
            update(FACTS)
            .where(*_facts_of([fact.origin_handle_id]), FACTS.c.fact_id == fact.fact_id)
            .values(
                predicate=fact.predicate,
                namespace=fact.namespace,
                value_ct=ct,
                value_hmac=hmac,
                dek_version=version,
                category=fact.category,
                sensitivity=fact.sensitivity.value,
                **NO_VECTOR,
            )
        )
        return True

    async def rewrite_episode(self, episode: Episode) -> None:
        sealed, version = await self._seal_row(
            "episodes",
            episode.episode_id,
            {"summary_ct": episode.summary, "resolution_ct": episode.resolution},
        )
        await self._db.execute(
            update(EPISODES)
            .where(
                EPISODES.c.space_id == CURRENT_SPACE,
                EPISODES.c.origin_handle_id == episode.origin_handle_id,
                EPISODES.c.episode_id == episode.episode_id,
                EPISODES.c.version == episode.version,
            )
            .values(
                summary_ct=sealed["summary_ct"],
                resolution_ct=sealed["resolution_ct"],
                dek_version=version,
                category=episode.category,
                normalized_category=episode.normalized_category,
                **NO_VECTOR,
            )
        )

    async def rewrite_item(self, item: OpenItem) -> None:
        ct, version = await self._s().seal(
            item.description, table="open_items", row=item.item_id, column="description_ct"
        )
        await self._db.execute(
            update(ITEMS)
            .where(
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.origin_handle_id == item.origin_handle_id,
                ITEMS.c.item_id == item.item_id,
            )
            .values(
                description_ct=ct,
                dek_version=version,
                expected_operation=item.expected_operation,
                **NO_VECTOR,
            )
        )

    async def restate_item(self, item: OpenItem, *, reworded: bool = False) -> None:
        sealed: dict[str, Any] = {}
        if reworded:
            ct, version = await self._s().seal(
                item.description, table="open_items", row=item.item_id, column="description_ct"
            )
            sealed = {"description_ct": ct, "dek_version": version, **NO_VECTOR}
        await self._db.execute(
            update(ITEMS)
            .where(
                ITEMS.c.space_id == CURRENT_SPACE,
                ITEMS.c.origin_handle_id == item.origin_handle_id,
                ITEMS.c.item_id == item.item_id,
            )
            .values(
                due_at=item.due_at,
                object_type=item.object.type if item.object else None,
                object_namespace=item.object.namespace if item.object else None,
                object_external_id=item.object.external_id if item.object else None,
                expected_operation=item.expected_operation,
                about_handle_id=item.about_handle_id,
                kind=item.kind.value,
                audience=item.audience.value if item.audience else None,
                **sealed,
            )
        )

    async def remove_attr(self, attr: ProfileAttr) -> None:
        await self._db.execute(
            delete(ATTRS).where(
                ATTRS.c.space_id == CURRENT_SPACE,
                ATTRS.c.origin_handle_id == attr.origin_handle_id,
                ATTRS.c.slot == attr.slot,
                ATTRS.c.source_id == attr.source_id,
            )
        )

    async def _facts(self, rows: Sequence[Row[Any]], *, evidence: bool = True) -> list[Fact]:
        if not rows:
            return []
        found: dict[tuple[UUID, UUID], list[UUID]] = defaultdict(list)
        if evidence:
            mentions = await self._db.execute(
                select(MENTIONS.c.origin_handle_id, MENTIONS.c.fact_id, MENTIONS.c.event_id)
                .where(
                    MENTIONS.c.space_id == CURRENT_SPACE,
                    MENTIONS.c.origin_handle_id.in_({r.origin_handle_id for r in rows}),
                    MENTIONS.c.fact_id.in_([r.fact_id for r in rows]),
                )
                .order_by(MENTIONS.c.event_id)
            )
            for origin, fact_id, event_id in mentions:
                found[(origin, fact_id)].append(event_id)
        return [
            Fact(
                fact_id=r.fact_id,
                origin_handle_id=r.origin_handle_id,
                predicate=r.predicate,
                value=await self._open(r.value_ct, r.dek_version, "facts", r.fact_id, "value_ct"),
                category=r.category,
                sensitivity=Sensitivity(r.sensitivity),
                speaker=Speaker(r.speaker),
                confidence=r.confidence,
                valid_at=r.valid_at,
                created_at=r.created_at,
                status=FactStatus(r.status),
                last_confirmed_at=r.last_confirmed_at,
                namespace=r.namespace,
                invalid_at=r.invalid_at,
                expired_at=r.expired_at,
                times_seen=r.times_seen,
                origin_conversation_id=r.origin_conversation_id,
                about_handle_id=r.about_handle_id,
                object_ref=ObjectKey(**r.object_ref) if r.object_ref else None,
                mention_handle_ids=tuple(r.mention_handle_ids),
                evidence_event_ids=tuple(found[(r.origin_handle_id, r.fact_id)]),
                valid_until=r.valid_until,
                sessions_seen=r.sessions_seen,
                purposes=tags_from_json(r.purposes),
                audience=MemoryAudience(r.audience) if r.audience else None,
            )
            for r in rows
        ]

    async def _episode(self, r: Row[Any]) -> Episode:
        return Episode(
            episode_id=r.episode_id,
            version=r.version,
            origin_handle_id=r.origin_handle_id,
            session_id=r.session_id,
            channel=r.channel,
            source_id=r.source_id,
            summary=await self._open(r.summary_ct, r.dek_version, "episodes", r.episode_id, "summary_ct"),
            intent=r.intent,
            category=r.category,
            normalized_category=r.normalized_category,
            outcome=r.outcome,
            started_at=r.started_at,
            ended_at=r.ended_at,
            resolution=await self._open(
                r.resolution_ct, r.dek_version, "episodes", r.episode_id, "resolution_ct"
            )
            if r.resolution_ct
            else None,
            sentiment=r.sentiment,
            initiated_by=r.initiated_by,
            conversation_id=r.conversation_id,
            about_handle_id=r.about_handle_id,
            purposes=tags_from_json(r.purposes),
            spoken_by=SpokenBy(r.spoken_by) if r.spoken_by else None,
            matter=r.matter,
            matter_label=r.matter_label,
            object_refs=tuple(ObjectKey(**k) for k in r.object_refs or ()),
        )

    async def _item(self, r: Row[Any]) -> OpenItem:
        obj = (
            ObjectKey(r.object_type, r.object_namespace, r.object_external_id)
            if r.object_type and r.object_namespace and r.object_external_id
            else None
        )
        return OpenItem(
            item_id=r.item_id,
            origin_handle_id=r.origin_handle_id,
            description=await self._open(
                r.description_ct, r.dek_version, "open_items", r.item_id, "description_ct"
            ),
            owner=r.owner,
            status=OpenItemStatus(r.status),
            created_at=r.created_at,
            origin=r.origin,
            due_at=r.due_at,
            about_handle_id=r.about_handle_id,
            object=obj,
            expected_operation=r.expected_operation,
            closed_by_action_id=r.closed_by_action_id,
            closed_at=r.closed_at,
            purposes=tags_from_json(r.purposes),
            kind=OpenItemKind(r.kind),
            audience=MemoryAudience(r.audience) if r.audience else None,
        )

    async def _value(self, r: Row[Any]) -> SettledValue:
        return SettledValue(
            value_id=r.value_id,
            origin_handle_id=r.origin_handle_id,
            type=ValueType(r.type),
            raw=await self._open(r.raw_ct, r.dek_version, "settled_values", r.value_id, "raw_ct"),
            said_at=r.said_at,
            speaker=Speaker(r.speaker),
            session_id=r.session_id,
            channel=r.channel,
            source_id=r.source_id,
            created_at=r.created_at,
            evidence_event_ids=tuple(r.evidence_event_ids),
            conversation_id=r.conversation_id,
            episode_id=r.episode_id,
            object_ref=ObjectKey(**r.object_ref) if r.object_ref else None,
            origin=ValueOrigin(r.origin),
            purposes=tags_from_json(r.purposes),
        )

    async def _seal_row(
        self, table: str, row: object, values: Mapping[str, str | bytes | None]
    ) -> tuple[dict[str, bytes | None], int]:
        """Seals the columns of one row under one key version, again if a rotation lands in between."""
        while True:
            sealed: dict[str, bytes | None] = {}
            versions = set()
            for column, value in values.items():
                if value is None:
                    sealed[column] = None
                    continue
                ct, version = await self._s().seal(value, table=table, row=row, column=column)
                sealed[column] = ct
                versions.add(version)
            if len(versions) <= 1:
                return sealed, versions.pop() if versions else 0

    async def _open(self, ct: bytes, version: int, table: str, row: object, column: str) -> str:
        return await self._s().open_text(ct, version, table=table, row=row, column=column)

    async def _first(self, stmt: Select[Any]) -> Row[Any] | None:
        return (await self._db.execute(stmt.limit(1))).first()

    def _s(self) -> Sealer:
        assert self._sealer is not None, "memory is read and written inside a space transaction"
        return self._sealer


def _facts_of(handle_ids: Sequence[UUID]) -> tuple[Any, ...]:
    return (FACTS.c.space_id == CURRENT_SPACE, FACTS.c.origin_handle_id.in_(handle_ids))


def _values_of(handle_ids: Sequence[UUID]) -> tuple[Any, ...]:
    return (VALUES.c.space_id == CURRENT_SPACE, VALUES.c.origin_handle_id.in_(handle_ids))


def _ref_text(key: ObjectKey) -> str:
    return f"{key.type}:{key.namespace}:{key.external_id}"


def _key_json(key: ObjectKey | None) -> dict[str, str] | None:
    return {"type": key.type, "namespace": key.namespace, "external_id": key.external_id} if key else None


def _attr_row(a: ProfileAttr | Row[Any]) -> str:
    return f"{a.origin_handle_id}:{a.slot}:{a.source_id}"


def _relation_origin(value: str) -> RelationOrigin:
    if value == "rule":
        return "rule"
    if value == "band":
        return "band"
    return "model"
