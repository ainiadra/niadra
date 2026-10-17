"""PostgreSQL implementation of `ConfigRepository`, in the cell database (`uow.cell()`)."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.schema.platform import CellState, ConfigDocument, ConfigSnapshot
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.config import StoredDocument

CACHE_EPOCH = "cache_epoch"


class PgConfigRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def active_version(self, cell_id: str, *, lock: bool = False) -> int | None:
        stmt = select(ConfigSnapshot.version).where(
            ConfigSnapshot.cell_id == cell_id, ConfigSnapshot.slot == "active"
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def save_snapshot(
        self, cell_id: str, version: int, manifest: dict[str, Any], signature: bytes
    ) -> None:
        await self._db.execute(
            delete(ConfigSnapshot).where(ConfigSnapshot.cell_id == cell_id, ConfigSnapshot.slot == "previous")
        )
        await self._db.execute(
            update(ConfigSnapshot)
            .where(ConfigSnapshot.cell_id == cell_id, ConfigSnapshot.slot == "active")
            .values(slot="previous")
        )
        await self._db.execute(
            insert(ConfigSnapshot).values(
                cell_id=cell_id, slot="active", version=version, manifest=manifest, signature=signature
            )
        )

    async def replace_documents(self, doc_type: str, documents: Sequence[StoredDocument]) -> None:
        keep = [d.space_id for d in documents]
        await self._db.execute(
            delete(ConfigDocument).where(
                ConfigDocument.doc_type == doc_type, ConfigDocument.space_id.not_in(keep)
            )
        )
        if not documents:
            return
        stmt = insert(ConfigDocument).values(
            [
                {
                    "space_id": d.space_id,
                    "doc_type": d.doc_type,
                    "content_hash": d.content_hash,
                    "version": d.version,
                    "document": d.document,
                }
                for d in documents
            ]
        )
        # Unchanged documents keep their row untouched, which spares a write per space per snapshot.
        stmt = stmt.on_conflict_do_update(
            index_elements=[ConfigDocument.space_id, ConfigDocument.doc_type],
            set_={
                "content_hash": stmt.excluded.content_hash,
                "version": stmt.excluded.version,
                "document": stmt.excluded.document,
            },
            where=ConfigDocument.content_hash != stmt.excluded.content_hash,
        )
        await self._db.execute(stmt)

    async def documents(self, doc_type: str) -> list[StoredDocument]:
        stmt = (
            select(ConfigDocument)
            .where(ConfigDocument.doc_type == doc_type)
            .order_by(ConfigDocument.space_id)
        )
        return [
            StoredDocument(r.space_id, r.doc_type, r.content_hash, r.version, r.document)
            for r in (await self._db.execute(stmt)).scalars()
        ]

    async def document(self, space_id: UUID, doc_type: str) -> StoredDocument | None:
        row = (
            await self._db.execute(
                select(ConfigDocument).where(
                    ConfigDocument.space_id == space_id, ConfigDocument.doc_type == doc_type
                )
            )
        ).scalar_one_or_none()
        return (
            None
            if row is None
            else StoredDocument(row.space_id, row.doc_type, row.content_hash, row.version, row.document)
        )

    async def save_document(self, document: StoredDocument) -> None:
        stmt = insert(ConfigDocument).values(
            space_id=document.space_id,
            doc_type=document.doc_type,
            content_hash=document.content_hash,
            version=document.version,
            document=document.document,
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[ConfigDocument.space_id, ConfigDocument.doc_type],
                set_={
                    "content_hash": stmt.excluded.content_hash,
                    "version": stmt.excluded.version,
                    "document": stmt.excluded.document,
                },
            )
        )

    async def active_manifest(self, cell_id: str) -> dict[str, Any] | None:
        stmt = select(ConfigSnapshot.manifest).where(
            ConfigSnapshot.cell_id == cell_id, ConfigSnapshot.slot == "active"
        )
        manifest = (await self._db.execute(stmt)).scalar_one_or_none()
        return None if manifest is None else dict(manifest)

    async def cache_epoch(self) -> int:
        stmt = select(CellState.value).where(CellState.key == CACHE_EPOCH)
        return int((await self._db.execute(stmt)).scalar_one())

    async def bump_cache_epoch(self) -> int:
        stmt = (
            update(CellState)
            .where(CellState.key == CACHE_EPOCH)
            .values(value=CellState.value + 1, updated_at=func.now())
            .returning(CellState.value)
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def state(self, key: str) -> int | None:
        found = (
            await self._db.execute(select(CellState.value).where(CellState.key == key))
        ).scalar_one_or_none()
        return None if found is None else int(found)

    async def set_state(self, key: str, value: int) -> None:
        stmt = (
            insert(CellState)
            .values(key=key, value=value)
            .on_conflict_do_update(
                index_elements=[CellState.key], set_={"value": value, "updated_at": func.now()}
            )
        )
        await self._db.execute(stmt)
