"""PostgreSQL implementation of `AssistRepository`.

`proposal` holds the masked request, the proposed document and the checks; samples stay where
they are and only their ids are kept (22.4).
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.operate import AssistRun as AssistRunRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.configure.model import AssistRun, AssistStatus, AssistTask

T = cast(Table, AssistRunRow.__table__)


class PgAssistRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def add_run(self, run: AssistRun) -> None:
        await self._db.execute(
            insert(T).values(
                space_id=CURRENT_SPACE,
                run_id=run.run_id,
                task=run.task.value,
                requested_by=run.requested_by,
                created_at=run.created_at,
                **_mutable(run),
            )
        )

    async def get_run(self, run_id: UUID) -> AssistRun | None:
        stmt = select(T).where(T.c.space_id == CURRENT_SPACE, T.c.run_id == run_id)
        row = (await self._db.execute(stmt)).mappings().first()
        return _run(row) if row else None

    async def save_run(self, run: AssistRun) -> None:
        await self._db.execute(
            update(T).where(T.c.space_id == CURRENT_SPACE, T.c.run_id == run.run_id).values(**_mutable(run))
        )

    async def count_since(self, at: datetime) -> int:
        stmt = select(func.count()).where(T.c.space_id == CURRENT_SPACE, T.c.created_at >= at)
        return int((await self._db.execute(stmt)).scalar_one())

    async def list_runs(self, *, before_id: UUID | None, limit: int) -> list[AssistRun]:
        stmt = select(T).where(T.c.space_id == CURRENT_SPACE).order_by(T.c.run_id.desc()).limit(limit)
        if before_id is not None:
            stmt = stmt.where(T.c.run_id < before_id)
        return [_run(r) for r in (await self._db.execute(stmt)).mappings()]


def _mutable(run: AssistRun) -> dict[str, Any]:
    return {
        "status": run.status.value,
        "sample_ids": list(run.sample_ids),
        "diff_hash": run.diff_hash,
        "proposal": run.proposal,
    }


def _run(r: Any) -> AssistRun:
    return AssistRun(
        run_id=r["run_id"],
        task=AssistTask(r["task"]),
        status=AssistStatus(r["status"]),
        requested_by=r["requested_by"],
        created_at=r["created_at"],
        sample_ids=tuple(r["sample_ids"]),
        diff_hash=r["diff_hash"],
        proposal=dict(r["proposal"] or {}),
    )
