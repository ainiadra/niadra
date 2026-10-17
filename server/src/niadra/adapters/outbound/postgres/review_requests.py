"""PostgreSQL implementation of `ReviewRequestRepository` (`review_requests`)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, delete, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import DateTime

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.signals import ReviewRequestRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.reviews import ReviewRequestRecord

Q = cast(Table, ReviewRequestRow.__table__)


def _request(r: Any) -> ReviewRequestRecord:
    return ReviewRequestRecord(
        request_id=r.request_id,
        profile_id=r.profile_id,
        reason_ct=bytes(r.reason_ct),
        dek_version=r.dek_version,
        created_at=r.created_at,
        decision_receipt_id=r.decision_receipt_id,
        explanation=dict(r.explanation),
        status=r.status,
        outcome=r.outcome,
        resolution_ct=bytes(r.resolution_ct) if r.resolution_ct is not None else None,
        resolved_at=r.resolved_at,
        resolved_by=r.resolved_by,
    )


class PgReviewRequestRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002
        self._db = session

    async def add(self, request: ReviewRequestRecord) -> ReviewRequestRecord:
        values = {
            "space_id": CURRENT_SPACE,
            "request_id": request.request_id,
            "profile_id": request.profile_id,
            "decision_receipt_id": request.decision_receipt_id,
            "reason_ct": request.reason_ct,
            "explanation": dict(request.explanation),
            "status": request.status,
            "created_at": request.created_at,
            "dek_version": request.dek_version,
        }
        await self._db.execute(pg_insert(Q).values(values).on_conflict_do_nothing())
        stored = await self.get(request.request_id)
        assert stored is not None
        return stored

    async def get(self, request_id: UUID) -> ReviewRequestRecord | None:
        stmt = select(Q).where(Q.c.space_id == CURRENT_SPACE, Q.c.request_id == request_id)
        row = (await self._db.execute(stmt)).first()
        return _request(row) if row is not None else None

    async def page(
        self, *, status: str | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[ReviewRequestRecord]:
        stmt = select(Q).where(Q.c.space_id == CURRENT_SPACE)
        if status is not None:
            stmt = stmt.where(Q.c.status == status)
        if before is not None:
            at, request_id = before
            edge = tuple_(literal(at, DateTime(timezone=True)), literal(request_id))
            stmt = stmt.where(tuple_(Q.c.created_at, Q.c.request_id) < edge)
        stmt = stmt.order_by(Q.c.created_at.desc(), Q.c.request_id.desc()).limit(limit)
        return [_request(r) for r in await self._db.execute(stmt)]

    async def resolve(
        self, request_id: UUID, *, outcome: str, resolution_ct: bytes | None, at: datetime, by: str
    ) -> ReviewRequestRecord | None:
        stmt = (
            update(Q)
            .where(Q.c.space_id == CURRENT_SPACE, Q.c.request_id == request_id, Q.c.status == "open")
            .values(
                status="resolved",
                outcome=outcome,
                resolution_ct=resolution_ct,
                resolved_at=at,
                resolved_by=by,
            )
            .returning(*Q.c)
        )
        row = (await self._db.execute(stmt)).first()
        return _request(row) if row is not None else None

    async def for_profiles(self, profile_ids: Sequence[UUID]) -> list[ReviewRequestRecord]:
        if not profile_ids:
            return []
        stmt = (
            select(Q)
            .where(Q.c.space_id == CURRENT_SPACE, Q.c.profile_id.in_(list(profile_ids)))
            .order_by(Q.c.created_at, Q.c.request_id)
        )
        return [_request(r) for r in await self._db.execute(stmt)]

    async def purge_resolved(self, before: datetime, *, limit: int) -> int:
        picked = (
            select(Q.c.request_id)
            .where(Q.c.space_id == CURRENT_SPACE, Q.c.status == "resolved", Q.c.resolved_at < before)
            .limit(limit)
        )
        stmt = (
            delete(Q)
            .where(Q.c.space_id == CURRENT_SPACE, Q.c.request_id.in_(picked))
            .returning(Q.c.request_id)
        )
        return len((await self._db.execute(stmt)).all())
