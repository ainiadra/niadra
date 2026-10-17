"""In-memory `UsageRepository` for unit tests.

The source tables belong to other modules, so the in-memory read model counts what tests
seed with `seed_source` instead of reaching into their stores.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.metering import UsageCount, UsageRow, merge_counts
from niadra.domain.metering.prompt_cache import ModelUsageCounters, ModelUsageKey, ModelUsageRow


class InMemoryUsageRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("metering")
        self._s.setdefault("sources", [])
        self._s.setdefault("events", {})
        self._s.setdefault("hourly", {})
        self._models: dict[ModelUsageKey, ModelUsageCounters] = self._s.setdefault("models", {})

    def seed_source(
        self,
        space_id: UUID,
        at: datetime,
        metric: str,
        quantity: int = 1,
        cost_micros: int = 0,
        source_id: UUID | None = None,
    ) -> None:
        self._s["sources"].append((space_id, at, UsageCount(metric, quantity, cost_micros, source_id)))

    async def count_sources(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]:
        return merge_counts(c for s, at, c in self._s["sources"] if s == space_id and start <= at < end)

    async def sum_events(self, space_id: UUID, start: datetime, end: datetime) -> list[UsageCount]:
        return merge_counts(
            UsageCount(metric, quantity, cost)
            for (s, _), (metric, quantity, cost, at) in self._s["events"].items()
            if s == space_id and start <= at < end
        )

    async def replace_hour(self, space_id: UUID, hour: datetime, counts: Sequence[UsageCount]) -> None:
        hourly = self._s["hourly"]
        for key in [k for k in hourly if k[:2] == (space_id, hour)]:
            del hourly[key]
        for count in counts:
            hourly[(space_id, hour, count.metric, count.source_id)] = (count.quantity, count.cost_micros)

    async def record_event(
        self,
        space_id: UUID,
        usage_id: str,
        metric: str,
        quantity: int,
        cost_micros: int,
        occurred_at: datetime,
    ) -> bool:
        if (space_id, usage_id) in self._s["events"]:
            return False
        self._s["events"][(space_id, usage_id)] = (metric, quantity, cost_micros, occurred_at)
        return True

    async def hourly(
        self,
        space_id: UUID,
        since: datetime,
        until: datetime,
        metrics: Sequence[str] | None = None,
        *,
        source_id: UUID | None = None,
    ) -> list[UsageRow]:
        rows = [
            UsageRow(hour, metric, quantity, cost, source)
            for (s, hour, metric, source), (quantity, cost) in self._s["hourly"].items()
            if s == space_id
            and since <= hour < until
            and (metrics is None or metric in metrics)
            and (source_id is None or source == source_id)
        ]
        return sorted(rows, key=lambda r: (r.hour, r.metric, str(r.source_id or "")))

    async def add_model_usage(self, rows: Sequence[ModelUsageRow]) -> None:
        for row in rows:
            self._models.setdefault(row.key, ModelUsageCounters()).add(row.counters)

    async def model_usage(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ModelUsageRow]:
        found = [
            ModelUsageRow(key, replace(counters))
            for key, counters in self._models.items()
            if since <= key.day <= until and (source_ids is None or key.source_id in source_ids)
        ]
        return sorted(found, key=lambda r: (r.key.day, str(r.key.source_id), r.key.provider, r.key.model))
