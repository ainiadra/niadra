"""PostgreSQL implementation of `ReviewRepository`. Samples hold ids and verdicts, never content."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.operate import ReviewSample
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.review.sampling import ReviewSampleRecord

T = cast(Table, ReviewSample.__table__)


class PgReviewRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def has_sample(self, session_id: UUID) -> bool:
        stmt = select(exists().where(T.c.space_id == CURRENT_SPACE, T.c.session_id == session_id))
        return bool((await self._db.execute(stmt)).scalar())

    async def add_sample(self, sample: ReviewSampleRecord) -> None:
        await self._db.execute(
            insert(T).values(
                space_id=CURRENT_SPACE,
                sample_id=sample.sample_id,
                session_id=sample.session_id,
                profile_id=sample.profile_id,
                conversation_id=sample.conversation_id,
                condition=sample.condition,
                purpose=sample.purpose,
                verdicts=list(sample.verdicts),
                reviewer_id=sample.reviewer_id,
                created_at=sample.created_at,
                reviewed_at=sample.reviewed_at,
            )
        )

    async def count_since(self, at: datetime) -> int:
        stmt = select(func.count()).where(T.c.space_id == CURRENT_SPACE, T.c.created_at >= at)
        return int((await self._db.execute(stmt)).scalar_one())

    async def get_sample(self, sample_id: UUID) -> ReviewSampleRecord | None:
        stmt = select(T).where(T.c.space_id == CURRENT_SPACE, T.c.sample_id == sample_id)
        row = (await self._db.execute(stmt)).mappings().first()
        return _sample(row) if row else None

    async def drop_sample(self, sample_id: UUID) -> None:
        await self._db.execute(
            delete(T).where(
                T.c.space_id == CURRENT_SPACE, T.c.sample_id == sample_id, T.c.reviewed_at.is_(None)
            )
        )

    async def pending(self, limit: int) -> list[ReviewSampleRecord]:
        stmt = (
            select(T)
            .where(T.c.space_id == CURRENT_SPACE, T.c.reviewed_at.is_(None))
            .order_by(T.c.created_at, T.c.sample_id)
            .limit(limit)
        )
        return [_sample(r) for r in (await self._db.execute(stmt)).mappings()]

    async def reviewed_since(self, at: datetime, limit: int) -> list[ReviewSampleRecord]:
        stmt = (
            select(T)
            .where(T.c.space_id == CURRENT_SPACE, T.c.reviewed_at >= at)
            .order_by(T.c.reviewed_at.desc(), T.c.sample_id.desc())
            .limit(limit)
        )
        return [_sample(r) for r in (await self._db.execute(stmt)).mappings()]

    async def add_verdicts(
        self, sample_id: UUID, verdicts: Sequence[dict[str, Any]], reviewer_id: UUID, at: datetime
    ) -> None:
        await self._db.execute(
            update(T)
            .where(T.c.space_id == CURRENT_SPACE, T.c.sample_id == sample_id)
            .values(verdicts=T.c.verdicts.concat(list(verdicts)), reviewer_id=reviewer_id, reviewed_at=at)
        )

    async def verdicts_since(self, at: datetime) -> list[dict[str, Any]]:
        stmt = (
            select(T.c.verdicts)
            .where(T.c.space_id == CURRENT_SPACE, T.c.reviewed_at >= at)
            .order_by(T.c.sample_id)
        )
        return [dict(v) for verdicts in (await self._db.execute(stmt)).scalars() for v in verdicts]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.kind is TargetKind.CONVERSATION:
            if scope.conversation_id is None:
                return []
            hit = T.c.conversation_id == scope.conversation_id
        elif scope.profile_id is not None:
            hit = T.c.profile_id == scope.profile_id
        else:
            return []
        doomed = (
            select(T.c.sample_id)
            .where(T.c.space_id == CURRENT_SPACE, hit)
            .order_by(T.c.sample_id)
            .limit(limit)
        )
        stmt = (
            delete(T)
            .where(T.c.space_id == CURRENT_SPACE, T.c.sample_id.in_(doomed.scalar_subquery()))
            .returning(T.c.sample_id)
        )
        gone = sorted((await self._db.execute(stmt)).scalars())
        return [ErasedRow("review_samples", {"sample_id": str(sample_id)}) for sample_id in gone]

    async def purge_before(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(T).where(T.c.space_id == CURRENT_SPACE, T.c.created_at < before)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


def _sample(r: Any) -> ReviewSampleRecord:
    return ReviewSampleRecord(
        sample_id=r["sample_id"],
        session_id=r["session_id"],
        condition=r["condition"],
        purpose=r["purpose"],
        created_at=r["created_at"],
        verdicts=tuple(r["verdicts"]),
        reviewer_id=r["reviewer_id"],
        reviewed_at=r["reviewed_at"],
        profile_id=r["profile_id"],
        conversation_id=r["conversation_id"],
    )
