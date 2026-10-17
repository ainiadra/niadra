"""What every owner of personal data does for `privacy`: batched deletes for the erasure cascade (8.4)
and incremental reads for the continuous export (22.3).

A delete batch picks at most `limit` keys and deletes exactly those, so the transaction stays short
(6) and a retried batch only finds what is left.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Select, Table, delete, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.domain.privacy.erasure import ErasedRow


async def delete_where(
    db: AsyncSession, table: Table, key: Sequence[str], where: Sequence[Any], limit: int
) -> list[ErasedRow]:
    """Deletes at most `limit` rows of the pinned space that match `where`."""
    if limit <= 0:
        return []
    picked: Select[Any] = (
        select(*(table.c[k] for k in key)).where(table.c.space_id == CURRENT_SPACE, *where).limit(limit)
    )
    return await _delete(db, table, key, tuple_(*(table.c[k] for k in key)).in_(picked))


async def delete_keys(
    db: AsyncSession, table: Table, key: Sequence[str], values: Sequence[Sequence[Any]]
) -> list[ErasedRow]:
    """Deletes the rows whose `key` columns hold one of `values`."""
    if not values:
        return []
    return await _delete(db, table, key, tuple_(*(table.c[k] for k in key)).in_([tuple(v) for v in values]))


async def _delete(db: AsyncSession, table: Table, key: Sequence[str], match: Any) -> list[ErasedRow]:
    seq = table.c.change_seq if "change_seq" in table.c else literal(None)
    stmt = (
        delete(table)
        .where(table.c.space_id == CURRENT_SPACE, match)
        .returning(*(table.c[k] for k in key), seq.label("change_seq"))
    )
    rows = (await db.execute(stmt)).all()
    return [
        ErasedRow(
            table.name,
            {k: str(v) for k, v in zip(key, row[: len(key)], strict=True)},
            change_seq=row.change_seq,
        )
        for row in rows
    ]


async def changed_rows(
    db: AsyncSession, table: Table, *, after_seq: int, upto_seq: int, limit: int
) -> list[Row[Any]]:
    """Rows of the pinned space with `after_seq < change_seq <= upto_seq`, in `change_seq` order."""
    stmt = (
        select(table)
        .where(
            table.c.space_id == CURRENT_SPACE, table.c.change_seq > after_seq, table.c.change_seq <= upto_seq
        )
        .order_by(table.c.change_seq)
        .limit(limit)
    )
    return list((await db.execute(stmt)).all())
