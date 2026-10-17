"""PostgreSQL implementation of `SpaceDirectoryRepository`, in the cell database."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.schema.platform import DeletionReceiptDraft
from niadra.adapters.outbound.postgres.schema.platform import SpaceDatabase as Row
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, Tier

_STATE = (
    "status",
    "schema_version",
    "status_detail",
    "failures",
    "retry_at",
    "ready_at",
    "deleted_at",
    "hash_modulus",
    "tier",
    "instance",
)


class PgSpaceDirectoryRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None = None) -> None:
        self._db = session
        self._sealer = sealer

    async def entries(self) -> list[SpaceDatabase]:
        rows = await self._db.execute(select(Row).order_by(Row.db_name))
        return [_entry(row) for row in rows.scalars()]

    async def get(self, space_id: UUID, *, lock: bool = False) -> SpaceDatabase | None:
        stmt = select(Row).where(Row.space_id == space_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        return None if row is None else _entry(row)

    async def insert(self, entry: SpaceDatabase, now: datetime) -> bool:
        values = {
            "space_id": entry.space_id,
            "tenant_id": entry.tenant_id,
            "environment": entry.environment,
            "db_name": entry.db_name,
            "created_at": entry.created_at or now,
            "updated_at": now,
        } | {name: _value(getattr(entry, name)) for name in _STATE}
        stmt = insert(Row).values(values).on_conflict_do_nothing().returning(Row.space_id)
        return (await self._db.execute(stmt)).scalar_one_or_none() is not None

    async def save(self, entry: SpaceDatabase, now: datetime) -> None:
        values = {name: _value(getattr(entry, name)) for name in _STATE} | {"updated_at": now}
        await self._db.execute(update(Row).where(Row.space_id == entry.space_id).values(values))

    async def draft_receipt(self, space_id: UUID, receipt: dict[str, Any], now: datetime) -> None:
        stmt = insert(DeletionReceiptDraft).values(space_id=space_id, receipt=receipt, created_at=now)
        await self._db.execute(stmt.on_conflict_do_nothing())

    async def unsent_receipts(self, limit: int) -> list[tuple[UUID, dict[str, Any]]]:
        stmt = (
            select(DeletionReceiptDraft.space_id, DeletionReceiptDraft.receipt)
            .where(DeletionReceiptDraft.sent_at.is_(None))
            .order_by(DeletionReceiptDraft.created_at)
            .limit(limit)
        )
        return [(space_id, dict(receipt)) for space_id, receipt in await self._db.execute(stmt)]

    async def mark_receipt_sent(self, space_id: UUID, now: datetime) -> None:
        await self._db.execute(
            update(DeletionReceiptDraft).where(DeletionReceiptDraft.space_id == space_id).values(sent_at=now)
        )

    async def pending_receipts(self) -> int:
        stmt = select(func.count()).where(DeletionReceiptDraft.sent_at.is_(None))
        return int(await self._db.scalar(stmt) or 0)

    async def receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
        if not space_ids:
            return {}
        stmt = select(DeletionReceiptDraft.space_id, DeletionReceiptDraft.receipt).where(
            DeletionReceiptDraft.space_id.in_(space_ids)
        )
        return {space_id: dict(receipt) for space_id, receipt in await self._db.execute(stmt)}


def _value(value: object) -> object:
    return value.value if isinstance(value, DbStatus | Tier) else value


def _entry(row: Row) -> SpaceDatabase:
    return SpaceDatabase(
        space_id=row.space_id,
        tenant_id=row.tenant_id,
        environment=row.environment,
        db_name=row.db_name,
        status=DbStatus(row.status),
        hash_modulus=row.hash_modulus,
        instance=row.instance,
        tier=Tier(row.tier),
        schema_version=row.schema_version,
        status_detail=row.status_detail,
        failures=row.failures,
        retry_at=row.retry_at,
        created_at=row.created_at,
        ready_at=row.ready_at,
        deleted_at=row.deleted_at,
    )
