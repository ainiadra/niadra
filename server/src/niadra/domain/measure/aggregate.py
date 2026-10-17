"""Daily counters, recontact and the rates the Console shows, with Wilson intervals (19.3, 19.4, 19.7)."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.measure.model import (
    AgentAnswer,
    ContextUseRow,
    DailyCounters,
    DailyKey,
    DailyRow,
    MeasuredSession,
    RecontactScope,
)
from niadra.domain.measure.slots import guard_violations

RECONTACT_WINDOW = timedelta(hours=72)
CUSTOMER_INITIATED = "customer"
DIMENSIONS = ("day", "source_id", "vendor", "channel", "view", "experiment_group")
"""`vendor` is not stored: it comes from the sources in the configuration when the report is built (19.4)."""


def row_counters(row: ContextUseRow) -> DailyCounters:
    measured = row.not_measured_reason is None
    return DailyCounters(
        deliveries=1,
        deliveries_used=1 if measured and row.used > 0 else 0,
        questions=row.questions if measured else 0,
        repeated=row.repeated if measured else 0,
        contradicted=row.contradicted if measured else 0,
        not_measured=0 if measured else 1,
        late_deliveries=1 if row.late else 0,
        selection_misses=sum(1 for item in row.items if item.get("selection_miss")),
        trimmed_deliveries=1 if row.tokens_saved > 0 else 0,
        tokens_saved=row.tokens_saved,
        guard_violations=guard_violations(row),
        object_rows=row.object_rows,
        object_rows_used=row.object_rows_used,
        instruction_violations=row.instruction_violations,
    )


def session_counters(session: MeasuredSession) -> DailyCounters:
    return DailyCounters(sessions=1, no_context=1 if session.no_context else 0)


def backing_counters(answers: Iterable[AgentAnswer]) -> dict[UUID, DailyCounters]:
    """Per agent source, the messages the SDK's backing check read and the values they stated with no
    source (13, 6.2). A message without a report counts in neither."""
    counted: dict[UUID, DailyCounters] = {}
    for answer in answers:
        if answer.checked is None:
            continue
        counters = counted.setdefault(answer.source_id, DailyCounters())
        counters.answers_checked += 1
        counters.unbacked_values += answer.unbacked
    return counted


def per_thousand(count: int, answers: int) -> float | None:
    """Unbacked values per thousand checked answers: the benchmark's line, per agent and vendor."""
    return round(1000 * count / answers, 1) if answers > 0 else None


def recontact_scope(
    previous: MeasuredSession, *, category: str | None, opened_at: datetime
) -> RecontactScope | None:
    """A customer-initiated session within 72 h of the end of another, same profile (19.7).

    The caller passes only earlier sessions of the same profile and only for customer-initiated sessions:
    a contact the company started never marks a recontact.
    """
    if not opened_at - RECONTACT_WINDOW <= previous.ended_at <= opened_at:
        return None
    if category is not None and previous.normalized_category == category:
        return RecontactScope.CATEGORY
    return RecontactScope.GENERAL


@dataclass(frozen=True, slots=True)
class Rate:
    value: float
    low: float
    high: float
    n: int


def wilson(successes: int, n: int, z: float = 1.96) -> Rate | None:
    """Wilson score interval: honest with few sessions, where the plain ratio swings from 0 to 1."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return Rate(value=p, low=max(0.0, centre - margin), high=min(1.0, centre + margin), n=n)


def group_daily(
    rows: Iterable[DailyRow], by: Sequence[str], vendors: Mapping[UUID, str | None] | None = None
) -> dict[tuple[object, ...], DailyCounters]:
    """Counters summed per group; rates are computed on the sums, never averaged (19.4). A source
    without a vendor groups under ""."""
    unknown = set(by) - set(DIMENSIONS)
    if unknown:
        raise ValueError(f"unknown dimension: {sorted(unknown)}")
    vendor_of = vendors or {}
    grouped: dict[tuple[object, ...], DailyCounters] = {}
    for row in rows:
        key = tuple(
            vendor_of.get(row.key.source_id) or "" if name == "vendor" else getattr(row.key, name)
            for name in by
        )
        grouped.setdefault(key, DailyCounters()).add(row.counters)
    return grouped


def session_key(session: MeasuredSession, view: str) -> DailyKey:
    return DailyKey(session.day, session.source_id, session.channel, view, session.experiment_group)
