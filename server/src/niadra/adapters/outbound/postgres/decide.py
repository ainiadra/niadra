"""PostgreSQL implementation of `DecisionRepository`."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.write_path import Decision
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.decide import DecisionRecord, DecisionTask

DECISIONS = cast(Table, Decision.__table__)


class PgDecisionRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def record(self, decisions: Sequence[DecisionRecord]) -> None:
        if not decisions:
            return
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "decision_id": d.decision_id,
                "task": d.task.value,
                "input_hash": d.input_hash,
                "model": d.model,
                "model_version": d.model_version,
                "label": d.label,
                "probability": d.probability,
                "threshold": d.threshold,
                "subject_ref": d.subject_ref,
                "created_at": d.created_at,
            }
            for d in decisions
        ]
        await self._db.execute(insert(DECISIONS).values(rows).on_conflict_do_nothing())

    async def answered(self, task: DecisionTask, model: str, subject_refs: Sequence[str]) -> set[str]:
        if not subject_refs:
            return set()
        found = await self._db.execute(
            select(DECISIONS.c.subject_ref)
            .where(
                DECISIONS.c.task == task.value,
                DECISIONS.c.subject_ref.in_(list(subject_refs)),
                DECISIONS.c.model == model,
            )
            .distinct()
        )
        return {row[0] for row in found}
