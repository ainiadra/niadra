"""PostgreSQL implementation of `WebhookRepository`. Bodies hold ids and pointers only (7.5)."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, Table, Text, delete, func, literal, or_, select, tuple_, update
from sqlalchemy import cast as cast_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.operate import WebhookDelivery
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.notify.webhooks import Delivery, DeliveryStatus, EndpointHealth, erasure_needles
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope

_D = cast(Table, WebhookDelivery.__table__)
_FAILED = (DeliveryStatus.FAILING.value, DeliveryStatus.DEAD.value)


class PgWebhookRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def add_delivery(self, delivery: Delivery) -> None:
        await self._db.execute(
            insert(_D)
            .values(
                space_id=CURRENT_SPACE,
                delivery_id=delivery.delivery_id,
                endpoint_id=delivery.endpoint_id,
                event_type=delivery.event_type,
                body=delivery.body,
                body_hash=delivery.body_hash,
                attempts=delivery.attempts,
                next_attempt_at=delivery.next_attempt_at,
                status=delivery.status.value,
                last_status_code=delivery.last_status_code,
                last_error=delivery.last_error,
                created_at=delivery.created_at,
                delivered_at=delivery.delivered_at,
            )
            .on_conflict_do_nothing()
        )

    async def get_delivery(self, delivery_id: UUID) -> Delivery | None:
        row = (
            await self._db.execute(
                select(_D).where(_D.c.space_id == CURRENT_SPACE, _D.c.delivery_id == delivery_id)
            )
        ).first()
        return _delivery(row) if row else None

    async def save_attempt(self, delivery: Delivery) -> None:
        await self._db.execute(
            update(_D)
            .where(_D.c.space_id == CURRENT_SPACE, _D.c.delivery_id == delivery.delivery_id)
            .values(
                status=delivery.status.value,
                attempts=delivery.attempts,
                next_attempt_at=delivery.next_attempt_at,
                last_status_code=delivery.last_status_code,
                last_error=delivery.last_error,
                delivered_at=delivery.delivered_at,
            )
        )

    async def list_deliveries(
        self,
        *,
        status: DeliveryStatus | None = None,
        endpoint_id: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        query = select(_D).where(_D.c.space_id == CURRENT_SPACE)
        if status is not None:
            query = query.where(_D.c.status == status.value)
        if endpoint_id is not None:
            query = query.where(_D.c.endpoint_id == endpoint_id)
        if before is not None:
            query = query.where(tuple_(_D.c.created_at, _D.c.delivery_id) < tuple_(*map(literal, before)))
        rows = await self._db.execute(
            query.order_by(_D.c.created_at.desc(), _D.c.delivery_id.desc()).limit(limit)
        )
        return [_delivery(r) for r in rows]

    async def endpoint_health(self, endpoint_id: str) -> EndpointHealth:
        mine = (_D.c.space_id == CURRENT_SPACE) & (_D.c.endpoint_id == endpoint_id)
        last_success = (await self._db.execute(select(func.max(_D.c.delivered_at)).where(mine))).scalar()
        failed = select(func.min(_D.c.created_at)).where(mine, _D.c.status.in_(_FAILED))
        if last_success is not None:
            failed = failed.where(_D.c.created_at > last_success)
        return EndpointHealth(last_success, (await self._db.execute(failed)).scalar())

    async def purge_dead(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(_D).where(
                _D.c.space_id == CURRENT_SPACE,
                _D.c.status == DeliveryStatus.DEAD.value,
                _D.c.created_at < before,
            )
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        needles = erasure_needles(scope)
        if not needles:
            return []
        # Bodies are small and carry ids only (7.5); a text match over the space's recent deliveries finds
        # the pseudonym and handle ids wherever the event put them.
        text = cast_(_D.c.body, Text)
        doomed = (
            select(_D.c.delivery_id)
            .where(_D.c.space_id == CURRENT_SPACE, or_(*(text.contains(n, autoescape=True) for n in needles)))
            .order_by(_D.c.delivery_id)
            .limit(limit)
        )
        stmt = (
            delete(_D)
            .where(_D.c.space_id == CURRENT_SPACE, _D.c.delivery_id.in_(doomed.scalar_subquery()))
            .returning(_D.c.delivery_id)
        )
        gone = sorted((await self._db.execute(stmt)).scalars())
        return [ErasedRow("webhook_deliveries", {"delivery_id": str(d)}) for d in gone]

    async def purge_before(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(_D).where(_D.c.space_id == CURRENT_SPACE, _D.c.created_at < before)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


def _delivery(r: Row[Any]) -> Delivery:
    return Delivery(
        delivery_id=r.delivery_id,
        endpoint_id=r.endpoint_id,
        event_type=r.event_type,
        body=dict(r.body),
        body_hash=bytes(r.body_hash),
        status=DeliveryStatus(r.status),
        created_at=r.created_at,
        attempts=r.attempts,
        next_attempt_at=r.next_attempt_at,
        last_status_code=r.last_status_code,
        last_error=r.last_error,
        delivered_at=r.delivered_at,
    )
