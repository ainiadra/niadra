"""The vector columns of navigable items (16.2), shared by the repositories that own those tables.

A vector is sealed on its own, under `embedding_dek_version`, with the AAD of its row and column, so
storing one never rewrites the content columns.
"""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Table, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.compile.model import ItemKind
from niadra.domain.memory.vectors import NO_VECTOR_DIM, ItemVector, pack_half, unpack_half

COLUMN = "embedding_ct"
# What a row's vector columns become when its text changes: the next `embed` or backfill encodes it again.
NO_VECTOR: dict[str, Any] = {
    "embedding_ct": None,
    "embedding_model": None,
    "embedding_dim": None,
    "embedding_dek_version": None,
}


def vector_columns(table: Table) -> tuple[Any, ...]:
    return (table.c.embedding_ct, table.c.embedding_model, table.c.embedding_dek_version)


def needs_vector(table: Table, model: str | None) -> ColumnElement[bool]:
    """No vector yet or, when the current encoder is known, a vector of another one. Dimension 0 marks a
    fact that never gets a vector, and only facts carry it."""
    missing = table.c.embedding_dim.is_(None)
    if model is None:
        return missing
    stale = and_(table.c.embedding_dim > NO_VECTOR_DIM, table.c.embedding_model.is_distinct_from(model))
    return or_(missing, stale)


async def sealed_vector(
    sealer: Sealer, table: str, row: object, vector: Sequence[float], model: str
) -> dict[str, Any]:
    """The column values of a stored vector: half precision, sealed, with its encoder and dimension."""
    ct, version = await sealer.seal(pack_half(list(vector)), table=table, row=row, column=COLUMN)
    return {
        "embedding_ct": ct,
        "embedding_model": model,
        "embedding_dim": len(vector),
        "embedding_dek_version": version,
    }


async def opened_vector(sealer: Sealer, kind: ItemKind, table: str, row: Any, item_id: Any) -> ItemVector:
    data = await sealer.open_bytes(
        row.embedding_ct, row.embedding_dek_version, table=table, row=item_id, column=COLUMN
    )
    return ItemVector(kind, item_id, unpack_half(data), row.embedding_model)


async def store(
    db: AsyncSession,
    table: Table,
    where: Sequence[ColumnElement[bool]],
    values: dict[str, Any],
    fence: int | None,
) -> bool:
    """Writes the vector columns of one row; with a fence, only while the row's `change_seq` still matches."""
    stmt = update(table).where(*where)
    if fence is not None:
        stmt = stmt.where(table.c.change_seq == fence)
    result = cast(CursorResult[Any], await db.execute(stmt.values(**values)))
    return result.rowcount > 0
