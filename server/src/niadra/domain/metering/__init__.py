"""Usage counted by the hour (5.15).

Most metrics are counted from tables that already exist; only what has no source table
(exported bytes, assistant tokens) is recorded as a `usage_events` row with a deterministic id,
so a retried writer never counts twice.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

HOUR = timedelta(hours=1)
# Receipts reach the writer a few seconds late and extraction finishes after the session:
# every rollup recounts the previous hour too, and the upsert makes that free of double counting.
HOURS_RECOUNTED = 2


class Metric(StrEnum):
    SESSION_BILLABLE = "session_billable"
    CONTEXT_READ = "context_read"
    HISTORY_CALL = "history_call"
    EVENT_INGESTED = "event_ingested"
    EXTRACTION_RUN = "extraction_run"
    EXTRACTION_TOKENS = "extraction_tokens"
    MEASURE_RUN = "measure_run"
    TRIGGER_FIRED = "trigger_fired"
    WEBHOOK_DELIVERED = "webhook_delivered"
    EXPORT_RUN = "export_run"
    EXPORT_BYTES = "export_bytes"
    ASSISTANT_TOKENS = "assistant_tokens"
    REVIEW_SAMPLE = "review_sample"


# Metrics without a source table; everything else is counted by the rollup.
EVENT_METRICS = frozenset({Metric.EXPORT_BYTES, Metric.ASSISTANT_TOKENS})


@dataclass(frozen=True, slots=True)
class UsageCount:
    metric: str
    quantity: int
    cost_micros: int = 0
    source_id: UUID | None = None
    """The agent or system the usage belongs to; None for work of the space itself (exports, triggers)."""


@dataclass(frozen=True, slots=True)
class UsageRow:
    hour: datetime
    metric: str
    quantity: int
    cost_micros: int
    source_id: UUID | None = None


class Granularity(StrEnum):
    HOUR = "hour"
    DAY = "day"


def hour_floor(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hours_to_roll(at: datetime, recount: int = HOURS_RECOUNTED) -> list[datetime]:
    """The hours a rollup running at `at` recounts: the current hour so far and the ones before it."""
    current = hour_floor(at)
    return [current - i * HOUR for i in reversed(range(recount))]


def merge_counts(*groups: Iterable[UsageCount]) -> list[UsageCount]:
    quantity: dict[tuple[str, UUID | None], int] = defaultdict(int)
    cost: dict[tuple[str, UUID | None], int] = defaultdict(int)
    for group in groups:
        for count in group:
            key = (count.metric, count.source_id)
            quantity[key] += count.quantity
            cost[key] += count.cost_micros
    return [
        UsageCount(metric, quantity[(metric, source)], cost[(metric, source)], source)
        for metric, source in sorted(quantity, key=_metric_then_source)
        if quantity[(metric, source)] or cost[(metric, source)]
    ]


def usage_event_id(metric: str, *natural_key: object) -> str:
    """Deterministic id from what the usage is about, e.g. (`export_bytes`, run_id, file)."""
    digest = hashlib.sha256("\x1f".join([metric, *map(str, natural_key)]).encode()).hexdigest()
    return f"{metric}:{digest[:32]}"


def by_day(rows: Iterable[UsageRow]) -> list[UsageRow]:
    return _regroup(rows, day=True, per_source=True)


def across_sources(rows: Iterable[UsageRow]) -> list[UsageRow]:
    """One row per period and metric, the sources summed: the space's total."""
    return _regroup(rows, day=False, per_source=False)


def _regroup(rows: Iterable[UsageRow], *, day: bool, per_source: bool) -> list[UsageRow]:
    quantity: dict[tuple[datetime, str, UUID | None], int] = defaultdict(int)
    cost: dict[tuple[datetime, str, UUID | None], int] = defaultdict(int)
    for row in rows:
        start = (
            row.hour.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) if day else row.hour
        )
        key = (start, row.metric, row.source_id if per_source else None)
        quantity[key] += row.quantity
        cost[key] += row.cost_micros
    return [
        UsageRow(start, metric, quantity[(start, metric, source)], cost[(start, metric, source)], source)
        for start, metric, source in sorted(quantity, key=lambda k: (k[0], *_metric_then_source(k[1:])))
    ]


def _metric_then_source(key: tuple[str, UUID | None]) -> tuple[str, str]:
    return key[0], str(key[1] or "")
