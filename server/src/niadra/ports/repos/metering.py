"""Hourly usage counted from source tables, and usage events without a source table (5.15)."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.metering import UsageCount, UsageRow
from niadra.domain.metering.prompt_cache import ModelUsageRow


class UsageRepository(Protocol):
    """Owned by the `metering` module; no other module reads or writes these tables (4.3)."""

    async def count_sources(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]:
        """A read model over the source tables (events, receipts, sessions, extraction runs, ...), per
        metric and source where the table knows the source."""
        ...

    async def sum_events(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]: ...

    async def replace_hour(self, space_id: UUID, hour: datetime, counts: Sequence[UsageCount]) -> None:
        """Idempotent: the hour's rows become exactly `counts`."""
        ...

    async def record_event(
        self,
        space_id: UUID,
        usage_id: str,
        metric: str,
        quantity: int,
        cost_micros: int,
        occurred_at: datetime,
    ) -> bool:
        """False when `usage_id` was already recorded."""
        ...

    async def hourly(
        self,
        space_id: UUID,
        since: datetime,
        until: datetime,
        metrics: Sequence[str] | None = None,
        *,
        source_id: UUID | None = None,
    ) -> list[UsageRow]:
        """One row per hour, metric and source, ordered by hour, metric and source."""
        ...

    async def add_model_usage(self, rows: Sequence[ModelUsageRow]) -> None:
        """Adds each row's counters to its day, source, provider and model (the agents' prompt cache)."""
        ...

    async def model_usage(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ModelUsageRow]:
        """One row per day, source, provider and model, ordered by those."""
        ...
