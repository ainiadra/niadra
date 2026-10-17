"""The purpose rows of `PgMemoryRepository` (13, 6.6): tagging, expiry by purpose and objections.

Kept apart from `memory.py` so the reads and writes of memory stay readable; the class below is mixed into
the repository and uses its session. Only attributes are read here, never sealed content.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, func, literal, not_, null, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import delete_keys, delete_where
from niadra.adapters.outbound.postgres.schema.memory import (
    EpisodeRow,
    FactMention,
    FactRelation,
    FactRow,
    OpenItemRow,
    PurposeObjection,
    SettledValueRow,
)
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.domain.privacy.erasure import ErasedRow
from niadra.domain.privacy.purposes import ItemKey, ItemKind, Tagged, Untagged, tags_from_json, tags_to_json

FACTS = cast(Table, FactRow.__table__)
MENTIONS = cast(Table, FactMention.__table__)
RELATIONS = cast(Table, FactRelation.__table__)
EPISODES = cast(Table, EpisodeRow.__table__)
ITEMS = cast(Table, OpenItemRow.__table__)
VALUES = cast(Table, SettledValueRow.__table__)
OBJECTIONS = cast(Table, PurposeObjection.__table__)

TABLES: dict[ItemKind, tuple[Table, str, str]] = {
    ItemKind.FACT: (FACTS, "fact_id", "last_confirmed_at"),
    ItemKind.EPISODE: (EPISODES, "episode_id", "ended_at"),
    ItemKind.OPEN_ITEM: (ITEMS, "item_id", "closed_at"),
    ItemKind.VALUE: (VALUES, "value_id", "said_at"),
}
"""Per kind: the table, its id column and the time a purpose's retention runs from."""
_READ: dict[ItemKind, tuple[str, ...]] = {
    ItemKind.FACT: ("category",),
    ItemKind.EPISODE: ("normalized_category", "session_id", "source_id"),
    ItemKind.OPEN_ITEM: ("origin",),
    ItemKind.VALUE: ("session_id", "source_id", "episode_id", "evidence_event_ids"),
}
"""The attributes that decide a row's purposes; never a sealed column."""

# Every purpose the row carries has a cutoff, and the row's anchor is before each of them: the last
# purpose to end decides. `{}` (no purpose) and NULL (not tagged) never match.
_EXPIRED = """
{table}.purposes IS NOT NULL AND {table}.purposes <> '{{}}'::jsonb AND {table}.{anchor} IS NOT NULL
AND NOT EXISTS (
  SELECT 1 FROM jsonb_object_keys({table}.purposes) AS k(name)
  WHERE (CAST(:cuts AS jsonb) ->> k.name) IS NULL
     OR {table}.{anchor} >= (CAST(:cuts AS jsonb) ->> k.name)::timestamptz
)
"""


class PurposeRows:
    _db: AsyncSession

    async def untagged(self, *, handle_ids: Sequence[UUID] | None, limit: int) -> list[Untagged]:
        if handle_ids is not None and not handle_ids:
            return []
        found: list[Untagged] = []
        for kind, (table, id_column, _) in TABLES.items():
            where = [table.c.space_id == CURRENT_SPACE, table.c.purposes.is_(None)]
            if handle_ids is not None:
                where.append(table.c.origin_handle_id.in_(handle_ids))
            if kind is ItemKind.EPISODE:
                where.append(table.c.is_current)
            columns = [table.c[name] for name in ("origin_handle_id", id_column, *_READ[kind])]
            rows = (await self._db.execute(select(*columns).where(*where).limit(limit))).all()
            found += [_untagged(kind, row, id_column) for row in rows]
        facts = [u for u in found if u.key.kind is ItemKind.FACT]
        if facts:
            evidence = await self._evidence([(u.key.origin_handle_id, u.key.item_id) for u in facts])
            found = [
                Untagged(
                    u.key,
                    u.category,
                    event_ids=tuple(evidence.get((u.key.origin_handle_id, u.key.item_id), ())),
                )
                if u.key.kind is ItemKind.FACT
                else u
                for u in found
            ]
        return found

    async def set_purposes(self, tagged: Sequence[Tagged]) -> None:
        for row in tagged:
            table, id_column, _ = TABLES[row.key.kind]
            await self._db.execute(
                update(table)
                .where(
                    table.c.space_id == CURRENT_SPACE,
                    table.c.origin_handle_id == row.key.origin_handle_id,
                    table.c[id_column] == row.key.item_id,
                )
                .values(purposes=tags_to_json(row.tags) if row.tags is not None else {})
            )

    async def untag_empty(self, *, limit: int) -> int:
        reset = 0
        for table, id_column, _ in TABLES.values():
            if reset >= limit:
                break
            key = tuple_(table.c.origin_handle_id, table.c[id_column])
            picked = (
                select(table.c.origin_handle_id, table.c[id_column])
                .where(table.c.space_id == CURRENT_SPACE, text(f"{table.name}.purposes = '{{}}'::jsonb"))
                .limit(limit - reset)
            )
            result = await self._db.execute(
                update(table)
                .where(table.c.space_id == CURRENT_SPACE, key.in_(picked))
                .values(purposes=null())
                .returning(table.c.origin_handle_id)
            )
            reset += len(result.all())
        return reset

    async def purpose_expired(self, cutoffs: Mapping[str, datetime], *, limit: int) -> list[ItemKey]:
        if not cutoffs:
            return []
        cuts = json.dumps({name: at.isoformat() for name, at in sorted(cutoffs.items())})
        found: list[ItemKey] = []
        for kind, (table, id_column, anchor) in TABLES.items():
            if len(found) >= limit:
                break
            where: list[Any] = [
                table.c.space_id == CURRENT_SPACE,
                text(_EXPIRED.format(table=table.name, anchor=anchor)),
            ]
            if kind is ItemKind.EPISODE:
                where.append(table.c.is_current)
            stmt = (
                select(table.c.origin_handle_id, table.c[id_column])
                .where(*where)
                .limit(limit - len(found))
                .params(cuts=cuts)
            )
            found += [ItemKey(kind, o, i) for o, i in await self._db.execute(stmt)]
        return found

    async def tagged_with(self, handle_ids: Sequence[UUID], purpose: str) -> list[Tagged]:
        if not handle_ids:
            return []
        found: list[Tagged] = []
        for kind, (table, id_column, _) in TABLES.items():
            where = [
                table.c.space_id == CURRENT_SPACE,
                table.c.origin_handle_id.in_(handle_ids),
                table.c.purposes.has_key(purpose),
            ]
            if kind is ItemKind.EPISODE:
                where.append(table.c.is_current)
            rows = await self._db.execute(
                select(table.c.origin_handle_id, table.c[id_column], table.c.purposes).where(*where)
            )
            found += [Tagged(ItemKey(kind, o, i), tags_from_json(p)) for o, i, p in rows]
        return found

    async def remove_items(self, keys: Sequence[ItemKey]) -> list[ErasedRow]:
        erased: list[ErasedRow] = []
        facts = [(k.origin_handle_id, k.item_id) for k in keys if k.kind is ItemKind.FACT]
        if facts:
            erased += await delete_keys(
                self._db,
                MENTIONS,
                ("origin_handle_id", "fact_id", "event_id"),
                [
                    (o, f, e)
                    for o, f, e in await self._db.execute(
                        select(MENTIONS.c.origin_handle_id, MENTIONS.c.fact_id, MENTIONS.c.event_id).where(
                            MENTIONS.c.space_id == CURRENT_SPACE,
                            tuple_(MENTIONS.c.origin_handle_id, MENTIONS.c.fact_id).in_(facts),
                        )
                    )
                ],
            )
            r = RELATIONS.c
            await self._db.execute(
                RELATIONS.delete().where(
                    r.space_id == CURRENT_SPACE,
                    tuple_(r.a_handle_id, r.a_fact_id).in_(facts)
                    | tuple_(r.b_handle_id, r.b_fact_id).in_(facts),
                )
            )
            erased += await delete_keys(self._db, FACTS, ("origin_handle_id", "fact_id"), facts)
        episodes = [(k.origin_handle_id, k.item_id) for k in keys if k.kind is ItemKind.EPISODE]
        if episodes:
            versions = await self._db.execute(
                select(EPISODES.c.origin_handle_id, EPISODES.c.episode_id, EPISODES.c.version).where(
                    EPISODES.c.space_id == CURRENT_SPACE,
                    tuple_(EPISODES.c.origin_handle_id, EPISODES.c.episode_id).in_(episodes),
                )
            )
            erased += await delete_keys(
                self._db,
                EPISODES,
                ("origin_handle_id", "episode_id", "version"),
                [tuple(v) for v in versions],
            )
        for kind, table, key in (
            (ItemKind.OPEN_ITEM, ITEMS, ("origin_handle_id", "item_id")),
            (ItemKind.VALUE, VALUES, ("origin_handle_id", "value_id")),
        ):
            rows = [(k.origin_handle_id, k.item_id) for k in keys if k.kind is kind]
            if rows:
                erased += await delete_keys(self._db, table, key, rows)
        return erased

    async def add_objection(self, handle_ids: Sequence[UUID], purpose: str, at: datetime) -> None:
        if not handle_ids:
            return
        rows = [
            {"space_id": CURRENT_SPACE, "handle_id": h, "purpose": purpose, "objected_at": at}
            for h in dict.fromkeys(handle_ids)
        ]
        await self._db.execute(insert(OBJECTIONS).values(rows).on_conflict_do_nothing())

    async def objections(self, handle_ids: Sequence[UUID]) -> dict[UUID, frozenset[str]]:
        found: dict[UUID, set[str]] = {}
        for handle_id, purpose, _ in await self.objection_list(handle_ids):
            found.setdefault(handle_id, set()).add(purpose)
        return {h: frozenset(p) for h, p in found.items()}

    async def objection_list(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, str, datetime]]:
        if not handle_ids:
            return []
        o = OBJECTIONS.c
        rows = await self._db.execute(
            select(o.handle_id, o.purpose, o.objected_at)
            .where(o.space_id == CURRENT_SPACE, o.handle_id.in_(handle_ids))
            .order_by(o.handle_id, o.purpose)
        )
        return [(h, p, at) for h, p, at in rows]

    async def objected_handles(self, *, after: tuple[UUID, str] | None, limit: int) -> list[tuple[UUID, str]]:
        o = OBJECTIONS.c
        stmt = select(o.handle_id, o.purpose).where(o.space_id == CURRENT_SPACE)
        if after is not None:
            stmt = stmt.where(tuple_(o.handle_id, o.purpose) > tuple_(literal(after[0]), literal(after[1])))
        rows = await self._db.execute(stmt.order_by(o.handle_id, o.purpose).limit(limit))
        return [(h, p) for h, p in rows]

    async def purpose_state(self, key: str) -> int | None:
        found: int | None = await self._db.scalar(select(SpaceState.value).where(SpaceState.key == key))
        return found

    async def set_purpose_state(self, key: str, value: int) -> None:
        stmt = insert(SpaceState).values(key=key, value=value)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpaceState.key], set_={"value": value, "updated_at": func.now()}
            )
        )

    async def _erase_objections(self, handles: Sequence[UUID], limit: int) -> list[ErasedRow]:
        return await delete_where(
            self._db, OBJECTIONS, ("handle_id", "purpose"), [OBJECTIONS.c.handle_id.in_(handles)], limit
        )

    async def _evidence(self, facts: Sequence[tuple[UUID, UUID]]) -> dict[tuple[UUID, UUID], list[UUID]]:
        m = MENTIONS.c
        rows = await self._db.execute(
            select(m.origin_handle_id, m.fact_id, m.event_id)
            .where(
                m.space_id == CURRENT_SPACE,
                tuple_(m.origin_handle_id, m.fact_id).in_(facts),
                not_(m.feedback),
            )
            .order_by(m.event_id)
        )
        found: dict[tuple[UUID, UUID], list[UUID]] = {}
        for origin, fact_id, event_id in rows:
            found.setdefault((origin, fact_id), []).append(event_id)
        return found


def _untagged(kind: ItemKind, row: Any, id_column: str) -> Untagged:
    key = ItemKey(kind, row.origin_handle_id, getattr(row, id_column))
    if kind is ItemKind.FACT:
        return Untagged(key, row.category)
    if kind is ItemKind.EPISODE:
        return Untagged(key, row.normalized_category, session_id=row.session_id, source_id=row.source_id)
    if kind is ItemKind.VALUE:
        return Untagged(
            key,
            session_id=row.session_id,
            source_id=row.source_id,
            episode_id=row.episode_id,
            event_ids=tuple(row.evidence_event_ids),
        )
    origin = row.origin
    return Untagged(
        key,
        session_id=_uuid(origin.get("session_id")),
        source_id=_uuid(origin.get("source_id")),
        event_ids=tuple(u for e in origin.get("event_ids") or () if (u := _uuid(e)) is not None),
    )


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
