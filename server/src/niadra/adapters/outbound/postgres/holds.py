"""PostgreSQL implementation of `LegalHoldRepository` (`legal_holds`)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.signals import LegalHoldRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.holds import LegalHoldRecord

H = cast(Table, LegalHoldRow.__table__)


def _hold(r: Any) -> LegalHoldRecord:
    return LegalHoldRecord(
        hold_id=r.hold_id,
        scope=r.scope,
        created_at=r.created_at,
        created_by=r.created_by,
        reason_ct=bytes(r.reason_ct),
        dek_version=r.dek_version,
        handle_id=r.handle_id,
        object_id=r.object_id,
        conversation_hmac=bytes(r.conversation_hmac) if r.conversation_hmac is not None else None,
        status=r.status,
        released_at=r.released_at,
        released_by=r.released_by,
        release_reason_ct=bytes(r.release_reason_ct) if r.release_reason_ct is not None else None,
    )


class PgLegalHoldRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002
        self._db = session

    async def add(self, hold: LegalHoldRecord) -> LegalHoldRecord:
        values = {
            "space_id": CURRENT_SPACE,
            "hold_id": hold.hold_id,
            "scope": hold.scope,
            "handle_id": hold.handle_id,
            "object_id": hold.object_id,
            "conversation_hmac": hold.conversation_hmac,
            "reason_ct": hold.reason_ct,
            "status": hold.status,
            "created_at": hold.created_at,
            "created_by": hold.created_by,
            "dek_version": hold.dek_version,
        }
        await self._db.execute(pg_insert(H).values(values).on_conflict_do_nothing())
        stored = await self.get(hold.hold_id)
        assert stored is not None
        return stored

    async def get(self, hold_id: UUID) -> LegalHoldRecord | None:
        row = (
            await self._db.execute(select(H).where(H.c.space_id == CURRENT_SPACE, H.c.hold_id == hold_id))
        ).first()
        return _hold(row) if row is not None else None

    async def release(
        self, hold_id: UUID, *, at: datetime, by: str, reason_ct: bytes
    ) -> LegalHoldRecord | None:
        stmt = (
            update(H)
            .where(H.c.space_id == CURRENT_SPACE, H.c.hold_id == hold_id, H.c.status == "active")
            .values(status="released", released_at=at, released_by=by, release_reason_ct=reason_ct)
            .returning(*H.c)
        )
        row = (await self._db.execute(stmt)).first()
        return _hold(row) if row is not None else None

    async def active(self) -> list[LegalHoldRecord]:
        stmt = select(H).where(H.c.space_id == CURRENT_SPACE, H.c.status == "active").order_by(H.c.hold_id)
        return [_hold(r) for r in await self._db.execute(stmt)]

    async def holds_on(
        self, handle_ids: Sequence[UUID], conversation_hmacs: Sequence[bytes]
    ) -> list[LegalHoldRecord]:
        if not handle_ids and not conversation_hmacs:
            return []
        stmt = (
            select(H)
            .where(
                H.c.space_id == CURRENT_SPACE,
                or_(H.c.handle_id.in_(list(handle_ids)), H.c.conversation_hmac.in_(list(conversation_hmacs))),
            )
            .order_by(H.c.hold_id)
        )
        return [_hold(r) for r in await self._db.execute(stmt)]
