"""Wrapped data keys in the cell database (`data_keys`, 5.14.1).

Uses the cell database's engine directly: the key vault sits below the unit of work (the sealer needs
it to open a transaction's rows), so it cannot use the unit of work itself. The keys never live in a
space's database, so a dump of that database holds only ciphertext.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.outbound.postgres.schema.platform import DataKey
from niadra.domain.common.errors import Conflict


class PgWrappedKeyStore:
    def __init__(self, engine: AsyncEngine, kek_id: str) -> None:
        self._engine = engine
        self._kek_id = kek_id

    async def load(self, space_id: UUID) -> dict[int, bytes]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                select(DataKey.version, DataKey.wrapped).where(DataKey.space_id == space_id)
            )
            return {version: wrapped for version, wrapped in rows}

    async def load_wrapped(self, space_id: UUID) -> dict[int, tuple[bytes, str]]:
        """Each version with the id of the key that wraps it."""
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                select(DataKey.version, DataKey.wrapped, DataKey.kek_id).where(DataKey.space_id == space_id)
            )
            return {version: (wrapped, kek_id) for version, wrapped, kek_id in rows}

    async def save(self, space_id: UUID, version: int, wrapped: bytes, kek_id: str | None = None) -> None:
        stmt = (
            insert(DataKey)
            .values(space_id=space_id, version=version, wrapped=wrapped, kek_id=kek_id or self._kek_id)
            .on_conflict_do_nothing()
            .returning(DataKey.version)
        )
        async with self._engine.begin() as conn:
            if (await conn.execute(stmt)).scalar_one_or_none() is not None:
                return
            stored = await conn.scalar(
                select(DataKey.wrapped).where(DataKey.space_id == space_id, DataKey.version == version)
            )
        if stored != wrapped:
            # Another process created this version first; the caller must reload, never use its own key.
            raise Conflict("the data key version already exists")

    async def destroy(self, space_id: UUID) -> list[int]:
        stmt = delete(DataKey).where(DataKey.space_id == space_id).returning(DataKey.version)
        async with self._engine.begin() as conn:
            return sorted((await conn.execute(stmt)).scalars())
