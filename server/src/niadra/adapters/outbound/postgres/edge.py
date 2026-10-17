"""PostgreSQL implementations of `SourceKeyRepository` (cell database) and `IdempotencyRepository`
(space database)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.schema.platform import RequestIdempotency, SourceKey
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.edge.limits import IdempotencyRecord
from niadra.domain.vocabulary import Scope


class PgSourceKeyRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def find_by_fast_hash(self, fast_hash: bytes) -> SourceKeyRow | None:
        row = (
            await self._db.execute(select(SourceKey).where(SourceKey.fast_hash == fast_hash))
        ).scalar_one_or_none()
        return _row(row) if row else None

    async def key_id_exists(self, key_id: str) -> bool:
        return bool(await self._db.scalar(select(exists().where(SourceKey.key_id == key_id))))

    async def upsert_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> int:
        if not rows:
            return 0
        stmt = insert(SourceKey).values(
            [
                {
                    "space_id": space_id,
                    "key_id": r.key_id,
                    "source_id": r.source_id,
                    "fast_hash": r.fast_hash,
                    "slow_hash": r.slow_hash,
                    "scopes": sorted(r.scopes),
                    "status": r.status,
                    "version": r.version,
                }
                for r in rows
            ]
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=[SourceKey.space_id, SourceKey.key_id],
            set_={
                c: stmt.excluded[c]
                for c in ("source_id", "fast_hash", "slow_hash", "scopes", "status", "version")
            },
            where=SourceKey.version < stmt.excluded.version,
        ).returning(SourceKey.key_id)
        return len((await self._db.execute(upsert)).all())

    async def key_versions(self, space_id: UUID, key_ids: Sequence[str]) -> dict[str, int]:
        if not key_ids:
            return {}
        rows = await self._db.execute(
            select(SourceKey.key_id, SourceKey.version).where(
                SourceKey.space_id == space_id, SourceKey.key_id.in_(list(key_ids))
            )
        )
        return {key_id: version for key_id, version in rows.all()}

    async def revoke(self, key_id: str) -> bool:
        stmt = (
            update(SourceKey)
            .where(SourceKey.key_id == key_id, SourceKey.status != "revoked")
            .values(status="revoked")
            .returning(SourceKey.key_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def source_key_ids(self, space_id: UUID, source_id: UUID) -> list[str]:
        stmt = (
            select(SourceKey.key_id)
            .where(SourceKey.space_id == space_id, SourceKey.source_id == source_id)
            .order_by(SourceKey.key_id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def delete_space(self, space_id: UUID) -> int:
        stmt = delete(SourceKey).where(SourceKey.space_id == space_id).returning(SourceKey.key_id)
        return len((await self._db.execute(stmt)).all())


class PgIdempotencyRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def get_idempotency(self, idem_key: str) -> IdempotencyRecord | None:
        row = (
            await self._db.execute(select(RequestIdempotency).where(RequestIdempotency.idem_key == idem_key))
        ).scalar_one_or_none()
        if row is None:
            return None
        return IdempotencyRecord(row.idem_key, row.body_hash, row.status, row.response, row.created_at)

    async def save_idempotency(
        self, space_id: UUID, record: IdempotencyRecord, expired_before: datetime
    ) -> bool:
        values = {
            "space_id": space_id,
            "idem_key": record.idem_key,
            "body_hash": record.body_hash,
            "status": record.status,
            "response": record.response,
            "created_at": record.created_at,
        }
        stmt = insert(RequestIdempotency).values(values)
        upsert = stmt.on_conflict_do_update(
            index_elements=[RequestIdempotency.space_id, RequestIdempotency.idem_key],
            set_={k: stmt.excluded[k] for k in ("body_hash", "status", "response", "created_at")},
            where=RequestIdempotency.created_at < expired_before,
        ).returning(RequestIdempotency.idem_key)
        return (await self._db.execute(upsert)).first() is not None


def _row(row: SourceKey) -> SourceKeyRow:
    return SourceKeyRow(
        space_id=row.space_id,
        key_id=row.key_id,
        source_id=row.source_id,
        fast_hash=row.fast_hash,
        slow_hash=row.slow_hash,
        scopes=frozenset(Scope(s) for s in row.scopes),
        status=row.status,
        version=row.version,
    )
