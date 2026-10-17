"""PostgreSQL implementation of `TriggerRepository`. Firings hold ids and evidence pointers only."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Row,
    Table,
    column,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.operate import TriggerFiring
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind, origin_step
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.firing import Firing

_F = cast(Table, TriggerFiring.__table__)


class PgTriggerRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def add_firing(self, firing: Firing) -> bool:
        result = await self._db.execute(
            insert(_F)
            .values(
                space_id=CURRENT_SPACE,
                firing_id=firing.firing_id,
                rule_id=firing.rule_id,
                rule_version=firing.rule_version,
                profile_id=firing.profile_id,
                window_key=firing.window_key,
                idempotency_key=firing.idempotency_key,
                evidence=[e.to_json() for e in firing.evidence],
                membership_version=firing.membership_version,
                late=firing.late,
                fired_at=firing.fired_at,
            )
            .on_conflict_do_nothing(index_elements=["space_id", "rule_id", "profile_id", "window_key"])
            .returning(_F.c.firing_id)
        )
        return result.first() is not None

    async def count_since(self, since: datetime) -> int:
        query = (
            select(func.count()).select_from(_F).where(_F.c.space_id == CURRENT_SPACE, _F.c.fired_at >= since)
        )
        return int((await self._db.execute(query)).scalar_one())

    async def firings_for(self, profile_id: UUID, *, since: datetime) -> list[Firing]:
        rows = await self._db.execute(
            select(_F)
            .where(
                _F.c.space_id == CURRENT_SPACE,
                _F.c.profile_id == profile_id,
                _F.c.retracted_at.is_(None),
                _F.c.fired_at >= since,
            )
            .order_by(_F.c.fired_at.desc(), _F.c.firing_id.desc())
        )
        return [_firing(r) for r in rows]

    async def retract(self, firing_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(_F)
            .where(_F.c.space_id == CURRENT_SPACE, _F.c.firing_id == firing_id)
            .values(retracted_at=at)
        )

    async def list_firings(
        self,
        *,
        rule_id: str | None = None,
        profile_id: UUID | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Firing]:
        query = select(_F).where(_F.c.space_id == CURRENT_SPACE)
        if rule_id is not None:
            query = query.where(_F.c.rule_id == rule_id)
        if profile_id is not None:
            query = query.where(_F.c.profile_id == profile_id)
        if before is not None:
            query = query.where(tuple_(_F.c.fired_at, _F.c.firing_id) < tuple_(*map(literal, before)))
        rows = await self._db.execute(
            query.order_by(_F.c.fired_at.desc(), _F.c.firing_id.desc()).limit(limit)
        )
        return [_firing(r) for r in rows]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        hit = _erasure_filter(scope)
        if hit is None:
            return []
        doomed = (
            select(_F.c.firing_id)
            .where(_F.c.space_id == CURRENT_SPACE, hit)
            .order_by(_F.c.firing_id)
            .limit(limit)
        )
        stmt = (
            delete(_F)
            .where(_F.c.space_id == CURRENT_SPACE, _F.c.firing_id.in_(doomed.scalar_subquery()))
            .returning(_F.c.firing_id, _F.c.change_seq)
        )
        gone = sorted((await self._db.execute(stmt)).all())
        return [
            ErasedRow("trigger_firings", {"firing_id": str(r.firing_id)}, change_seq=r.change_seq)
            for r in gone
        ]

    async def purge_before(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(_F).where(_F.c.space_id == CURRENT_SPACE, _F.c.fired_at < before)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


def _erasure_filter(scope: ErasureScope) -> ColumnElement[bool] | None:
    """SQL form of `firing.erased_by`: evidence is a JSON array of {kind, id, origin_handle_id}."""
    evidence = func.jsonb_array_elements(_F.c.evidence).table_valued(column("value", JSONB)).alias("e")
    if not origin_step(scope):
        ids = [str(e) for e in scope.event_ids]
        return exists(
            select(literal(1))
            .select_from(evidence)
            .where(evidence.c.value["kind"].astext == "event", evidence.c.value["id"].astext.in_(ids))
        )
    if scope.kind is TargetKind.CONVERSATION:
        return None
    handles = [str(h) for h in scope.handle_ids]
    by_evidence = exists(
        select(literal(1))
        .select_from(evidence)
        .where(evidence.c.value["origin_handle_id"].astext.in_(handles))
    )
    if scope.kind is TargetKind.PROFILE and scope.profile_id is not None:
        return or_(_F.c.profile_id == scope.profile_id, by_evidence)
    return by_evidence


def _firing(r: Row[Any]) -> Firing:
    return Firing(
        firing_id=r.firing_id,
        rule_id=r.rule_id,
        rule_version=r.rule_version,
        profile_id=r.profile_id,
        window_key=r.window_key,
        idempotency_key=bytes(r.idempotency_key),
        evidence=tuple(EvidenceRef.from_json(e) for e in r.evidence),
        membership_version=r.membership_version,
        fired_at=r.fired_at,
        late=r.late,
        retracted_at=r.retracted_at,
    )
