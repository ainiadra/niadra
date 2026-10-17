"""The index of what derived memory and turn recordings name a person by (`domain.privacy.mentions`): rows
by their store and the row their ciphertext is bound to, objects by their key, each with the keyed hashes of
the identifiers its text writes. A lookup is one statement over the GIN index, whatever the size of the
space."""

import json
from collections.abc import Collection, Sequence
from typing import Any
from typing import cast as as_any

from sqlalchemy import LargeBinary, and_, cast, delete, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.schema.write_path import ObjectMention, TextMention
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.mentions import MentionKey, by_key

_UNION = "ARRAY(SELECT DISTINCT t FROM unnest({table}.tokens || excluded.tokens) AS t)"


NAMED_CTE = (
    "named_mentions AS (INSERT INTO text_mentions AS m (space_id, store, row_key, tokens)"
    " SELECT CAST(current_setting('niadra.space_id') AS uuid), r.store, r.row_key,"
    " ARRAY(SELECT decode(x, 'hex') FROM jsonb_array_elements_text(r.tokens) AS x)"
    " FROM jsonb_to_recordset(CAST(:named AS jsonb)) AS r(store text, row_key text, tokens jsonb)"
    " ON CONFLICT (space_id, store, row_key) DO UPDATE SET"
    " tokens = ARRAY(SELECT DISTINCT x FROM unnest(m.tokens || EXCLUDED.tokens) AS x))"
)
"""For a statement written as text: the index entries in `:named` (`named_param`), written by it."""


def named_param(entries: Sequence[tuple[MentionKey, frozenset[bytes]]]) -> str:
    return json.dumps(
        [
            {"store": k.store, "row_key": k.row, "tokens": sorted(t.hex() for t in tokens)}
            for k, tokens in by_key(entries)
            if tokens
        ]
    )


def mentions_insert(entries: Sequence[tuple[MentionKey, frozenset[bytes]]]) -> Insert | None:
    """The index entries as one insert, each row's tokens added to what it named before."""
    named = [
        {"space_id": CURRENT_SPACE, "store": k.store, "row_key": k.row, "tokens": sorted(t)}
        for k, t in by_key(entries)
        if t
    ]
    if not named:
        return None
    stmt = insert(TextMention).values(named)
    return stmt.on_conflict_do_update(
        index_elements=[TextMention.space_id, TextMention.store, TextMention.row_key],
        set_={"tokens": text(_UNION.format(table="text_mentions"))},
    )


def with_entries[S](stmt: S, entries: Sequence[tuple[MentionKey, frozenset[bytes]]]) -> S:
    """`stmt` carrying these index entries as a CTE: one statement writes the rows and what they name."""
    named = mentions_insert(entries)
    return stmt if named is None else as_any(Any, stmt).add_cte(named.cte("named_mentions"))


async def with_mentions[S](stmt: S, sealer: Sealer | None) -> S:
    """`stmt` carrying, as a CTE, what the rows sealed so far in the transaction name a person by: a write
    with a statement budget indexes its rows without a statement of its own (`domain.privacy.mentions`)."""
    if sealer is None or not sealer.written:
        return stmt
    return with_entries(stmt, await sealer.take_mentions())


class PgMentionRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002 (the repositories' shape)
        self._db = session

    async def index(
        self, entries: Sequence[tuple[MentionKey, frozenset[bytes]]], *, replace: bool = False
    ) -> None:
        """Each row's tokens: added to what the row named before (another version of an episode keeps its
        own), or, with `replace`, instead of it; a row that names no one any more leaves the index."""
        entries = by_key(entries)
        if not replace:
            if (added := mentions_insert(entries)) is not None:
                await self._db.execute(added)
            return
        await self.drop([k for k, t in entries if not t])
        named = [
            {"space_id": CURRENT_SPACE, "store": k.store, "row_key": k.row, "tokens": sorted(t)}
            for k, t in entries
            if t
        ]
        if not named:
            return
        stmt = insert(TextMention).values(named)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[TextMention.space_id, TextMention.store, TextMention.row_key],
                set_={"tokens": stmt.excluded.tokens},
            )
        )

    async def drop(self, keys: Sequence[MentionKey]) -> None:
        if keys:
            await self._db.execute(
                delete(TextMention).where(
                    TextMention.space_id == CURRENT_SPACE,
                    tuple_(TextMention.store, TextMention.row_key).in_([(k.store, k.row) for k in keys]),
                )
            )

    async def naming(
        self,
        tokens: Sequence[bytes],
        plain: frozenset[str],  # noqa: ARG002 (for the in-memory index)
        *,
        stores: Collection[str],
        after: MentionKey | None,
        limit: int,
    ) -> list[MentionKey]:
        """The rows whose text names any of `tokens`, in key order after `after` (`plain` is for the
        in-memory index, which keeps no hashes)."""
        if not tokens:
            return []
        stmt = select(TextMention.store, TextMention.row_key).where(
            TextMention.space_id == CURRENT_SPACE,
            TextMention.store.in_(sorted(stores)),
            TextMention.tokens.overlap(cast(sorted(tokens), ARRAY(LargeBinary))),
        )
        if after is not None:
            stmt = stmt.where(
                or_(
                    TextMention.store > after.store,
                    and_(TextMention.store == after.store, TextMention.row_key > after.row),
                )
            )
        rows = await self._db.execute(stmt.order_by(TextMention.store, TextMention.row_key).limit(limit))
        return [MentionKey(store, row) for store, row in rows]

    async def index_objects(
        self, entries: Sequence[tuple[str, frozenset[bytes]]], *, replace: bool = True
    ) -> None:
        """Each object's tokens: instead of what it named before (one that names no one leaves the index), or
        added to it without `replace`."""
        entries = by_key(entries)
        if replace:
            await self.drop_objects([k for k, t in entries if not t])
        named = [{"space_id": CURRENT_SPACE, "key": k, "tokens": sorted(t)} for k, t in entries if t]
        if not named:
            return
        stmt = insert(ObjectMention).values(named)
        tokens = stmt.excluded.tokens if replace else text(_UNION.format(table="object_mentions"))
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[ObjectMention.space_id, ObjectMention.key], set_={"tokens": tokens}
            )
        )

    async def drop_objects(self, keys: Sequence[str]) -> None:
        if keys:
            await self._db.execute(
                delete(ObjectMention).where(
                    ObjectMention.space_id == CURRENT_SPACE, ObjectMention.key.in_(keys)
                )
            )

    async def objects_naming(
        self, tokens: Sequence[bytes], *, prefix: str, after: str | None, limit: int
    ) -> list[str]:
        if not tokens:
            return []
        stmt = select(ObjectMention.key).where(
            ObjectMention.space_id == CURRENT_SPACE,
            ObjectMention.key.startswith(prefix, autoescape=True),
            ObjectMention.tokens.overlap(cast(sorted(tokens), ARRAY(LargeBinary))),
        )
        if after is not None:
            stmt = stmt.where(ObjectMention.key > after)
        return list((await self._db.execute(stmt.order_by(ObjectMention.key).limit(limit))).scalars())

    async def ready(self, key: str) -> bool:
        return await self._db.scalar(select(SpaceState.value).where(SpaceState.key == key)) is not None

    async def mark_ready(self, key: str) -> None:
        stmt = insert(SpaceState).values(key=key, value=1)
        await self._db.execute(stmt.on_conflict_do_nothing(index_elements=[SpaceState.key]))
