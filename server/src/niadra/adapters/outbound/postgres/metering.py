"""PostgreSQL implementation of `UsageRepository`.

`count_sources` is the named read model of 5.15: it counts from the other modules' tables
instead of writing a usage row per event, which would double the writer's load (06, 6.2).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, literal, null, or_, select, union_all
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.operate import (
    ContextUse,
    ExportRun,
    ModelUsageDaily,
    ReviewSample,
    TriggerFiring,
    UsageEvent,
    UsageHourly,
    WebhookDelivery,
)
from niadra.adapters.outbound.postgres.schema.read_path import Receipt
from niadra.adapters.outbound.postgres.schema.write_path import Event, ExtractionRun, Session
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7_floor
from niadra.domain.metering import Metric, UsageCount, UsageRow
from niadra.domain.metering.prompt_cache import ModelUsageCounters, ModelUsageKey, ModelUsageRow

# Ids are UUIDv7 minted on arrival: a bound on the id prunes the weekly partitions, and the
# margin covers clock skew between the process that minted the id and the timestamp column.
ID_MARGIN = timedelta(minutes=10)
READ_KINDS = ("read", "history")
HOLDOUT_GROUPS = ("control", "shadow")
"""Receipts labeled `<experiment>:<group>` with these groups were served nothing (domain/serve/holdout)."""
# `usage_hourly.source_id` is in the primary key, so the space's own work is stored under the nil id.
NO_SOURCE = UUID(int=0)


class PgUsageRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def count_sources(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]:
        stmt = union_all(*_source_counts(space_id, start, end))
        rows = (await self._db.execute(stmt)).all()
        return [
            UsageCount(r.metric, int(r.quantity), int(r.cost), r.source_id)
            for r in rows
            if r.quantity or r.cost
        ]

    async def sum_events(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]:
        stmt = (
            select(UsageEvent.metric, func.sum(UsageEvent.quantity), func.sum(UsageEvent.cost_micros))
            .where(
                UsageEvent.space_id == space_id, UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end
            )
            .group_by(UsageEvent.metric)
            .order_by(UsageEvent.metric)
        )
        return [UsageCount(metric, int(q), int(c)) for metric, q, c in (await self._db.execute(stmt)).all()]

    async def replace_hour(self, space_id: UUID, hour: datetime, counts: Sequence[UsageCount]) -> None:
        await self._db.execute(
            delete(UsageHourly).where(UsageHourly.space_id == space_id, UsageHourly.hour == hour)
        )
        if counts:
            rows = [
                {
                    "space_id": space_id,
                    "hour": hour,
                    "metric": c.metric,
                    "source_id": c.source_id or NO_SOURCE,
                    "quantity": c.quantity,
                    "cost_micros": c.cost_micros,
                }
                for c in counts
            ]
            await self._db.execute(insert(UsageHourly).values(rows))

    async def record_event(
        self,
        space_id: UUID,
        usage_id: str,
        metric: str,
        quantity: int,
        cost_micros: int,
        occurred_at: datetime,
    ) -> bool:
        stmt = (
            insert(UsageEvent)
            .values(
                space_id=space_id,
                usage_id=usage_id,
                metric=metric,
                quantity=quantity,
                cost_micros=cost_micros,
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing()
            .returning(UsageEvent.usage_id)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none() is not None

    async def hourly(
        self,
        space_id: UUID,
        since: datetime,
        until: datetime,
        metrics: Sequence[str] | None = None,
        *,
        source_id: UUID | None = None,
    ) -> list[UsageRow]:
        stmt = (
            select(UsageHourly)
            .where(UsageHourly.space_id == space_id, UsageHourly.hour >= since, UsageHourly.hour < until)
            .order_by(UsageHourly.hour, UsageHourly.metric, UsageHourly.source_id)
        )
        if metrics is not None:
            stmt = stmt.where(UsageHourly.metric.in_(metrics))
        if source_id is not None:
            stmt = stmt.where(UsageHourly.source_id == source_id)
        return [
            UsageRow(
                r.hour, r.metric, r.quantity, r.cost_micros, None if r.source_id == NO_SOURCE else r.source_id
            )
            for r in (await self._db.execute(stmt)).scalars()
        ]

    async def add_model_usage(self, rows: Sequence[ModelUsageRow]) -> None:
        for row in sorted(rows, key=_model_order):
            values = {name: getattr(row.counters, name) for name in _MODEL_COUNTERS}
            stmt = insert(ModelUsageDaily).values(
                space_id=CURRENT_SPACE,
                day=row.key.day,
                source_id=row.key.source_id,
                provider=row.key.provider,
                model=row.key.model,
                **values,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["space_id", "day", "source_id", "provider", "model"],
                set_={name: getattr(ModelUsageDaily, name) + stmt.excluded[name] for name in values},
            )
            await self._db.execute(stmt)

    async def model_usage(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ModelUsageRow]:
        m = ModelUsageDaily
        stmt = (
            select(m)
            .where(m.space_id == CURRENT_SPACE, m.day >= since, m.day <= until)
            .order_by(m.day, m.source_id, m.provider, m.model)
        )
        if source_ids is not None:
            stmt = stmt.where(m.source_id.in_(list(source_ids)))
        return [
            ModelUsageRow(
                ModelUsageKey(r.day, r.source_id, r.provider, r.model),
                ModelUsageCounters(*(int(getattr(r, name)) for name in _MODEL_COUNTERS)),
            )
            for r in (await self._db.execute(stmt)).scalars()
        ]


_MODEL_COUNTERS = ("calls", "prompt_tokens", "cached_tokens", "cache_write_tokens")


def _model_order(row: ModelUsageRow) -> tuple[date, str, str, str]:
    # One order for every writer, so two batches adding to the same rows never deadlock.
    return (row.key.day, str(row.key.source_id), row.key.provider, row.key.model)


def _source_counts(space_id: UUID, start: datetime, end: datetime) -> list[Any]:
    def count(
        metric: Metric,
        table: Any,
        *where: Any,
        quantity: Any = None,
        cost: Any = None,
        source: Any = None,
        via_session: bool = False,
    ) -> Any:
        """One row per source when the table knows it (directly or through its session), else one row."""
        if via_session:
            source = Session.source_id
        stmt = select(
            literal(metric.value).label("metric"),
            (source if source is not None else null().cast(PG_UUID(as_uuid=True))).label("source_id"),
            (quantity if quantity is not None else func.count()).label("quantity"),
            func.coalesce(cost, 0).label("cost"),
        ).select_from(table)
        if via_session:
            # An erased session leaves its runs counted without a source.
            stmt = stmt.outerjoin(
                Session, and_(Session.space_id == table.space_id, Session.session_id == table.session_id)
            )
        stmt = stmt.where(table.space_id == space_id, *where)
        return stmt.group_by(source) if source is not None else stmt

    def within(column: Any) -> Any:
        return and_(column >= start, column < end)

    receipts_from = Receipt.receipt_id >= _id_floor(start - ID_MARGIN)
    # Any agent's read in the conversation or task counts, once; a read of the control group
    # delivered nothing and never does, the navigation's included (19.7).
    agent_read = exists().where(
        Receipt.space_id == Session.space_id,
        receipts_from,
        Receipt.kind.in_(READ_KINDS),
        Receipt.source_id.is_not(None),
        or_(
            Receipt.experiment_group.is_(None),
            and_(*(Receipt.experiment_group.not_like(f"%:{group}") for group in HOLDOUT_GROUPS)),
        ),
        or_(
            and_(Session.conversation_id.is_not(None), Receipt.conversation_id == Session.conversation_id),
            and_(Session.task_id.is_not(None), Receipt.task_id == Session.task_id),
        ),
        Receipt.read_at >= Session.opened_at,
        Receipt.read_at <= Session.closed_at,
    )
    return [
        count(
            Metric.EVENT_INGESTED,
            Event,
            within(Event.received_at),
            Event.event_id >= _id_floor(start - ID_MARGIN),
            Event.event_id < _id_floor(end + ID_MARGIN),
            source=Event.source_id,
        ),
        count(
            Metric.CONTEXT_READ,
            Receipt,
            within(Receipt.read_at),
            receipts_from,
            Receipt.kind == "read",
            source=Receipt.source_id,
        ),
        count(
            Metric.HISTORY_CALL,
            Receipt,
            within(Receipt.read_at),
            receipts_from,
            Receipt.kind == "history",
            source=Receipt.source_id,
        ),
        # Billed once, in the hour the session closed, and only with an agent read: sessions of
        # an experiment's control group read nothing (18.6, 19.7).
        count(
            Metric.SESSION_BILLABLE, Session, within(Session.closed_at), agent_read, source=Session.source_id
        ),
        count(
            Metric.EXTRACTION_RUN,
            ExtractionRun,
            within(ExtractionRun.created_at),
            cost=func.sum(ExtractionRun.cost_micros),
            via_session=True,
        ),
        count(
            Metric.EXTRACTION_TOKENS,
            ExtractionRun,
            within(ExtractionRun.created_at),
            quantity=func.coalesce(func.sum(ExtractionRun.input_tokens + ExtractionRun.output_tokens), 0),
            via_session=True,
        ),
        count(Metric.MEASURE_RUN, ContextUse, within(ContextUse.measured_at), source=ContextUse.source_id),
        count(Metric.TRIGGER_FIRED, TriggerFiring, within(TriggerFiring.fired_at)),
        count(
            Metric.WEBHOOK_DELIVERED,
            WebhookDelivery,
            within(WebhookDelivery.delivered_at),
            WebhookDelivery.status == "delivered",
        ),
        count(Metric.EXPORT_RUN, ExportRun, within(ExportRun.finished_at), ExportRun.error.is_(None)),
        count(Metric.REVIEW_SAMPLE, ReviewSample, within(ReviewSample.created_at), via_session=True),
    ]


def _id_floor(at: datetime) -> UUID:
    return uuid7_floor(int(at.timestamp() * 1000))
