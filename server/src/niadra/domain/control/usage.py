"""The monthly usage statement a space's invoice is built from (5.15).

Cells push hourly aggregates to the control plane; a statement sums one UTC month per space. The price
is the contract's, applied outside the product: the statement says what was used, never what it costs.
"""

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.records import Space, UsageAggregate

BILLABLE = "session_billable"
"""A conversation or a task: the unit the price is set per thousand of."""


@dataclass(frozen=True, slots=True)
class StatementLine:
    month: str
    tenant_id: UUID
    space_id: UUID
    project_id: UUID
    environment: str
    slug: str
    billable: int
    ai_cost_micros: int
    metrics: dict[str, int] = field(default_factory=dict)


def month_range(month: str) -> tuple[datetime, datetime]:
    """`2026-09` as the UTC instants it starts and ends at."""
    try:
        year, mon = (int(part) for part in month.split("-"))
        start = datetime(year, mon, 1, tzinfo=UTC)
    except ValueError:
        raise InvalidInput("month is YYYY-MM") from None
    return start, datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=UTC)


def statement(
    month: str, spaces: Iterable[Space], aggregates: Iterable[UsageAggregate]
) -> list[StatementLine]:
    """One line per space, every space listed even without usage, ordered by tenant and slug."""
    totals: dict[UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    costs: dict[UUID, int] = defaultdict(int)
    for a in aggregates:
        totals[a.space_id][a.metric] += a.quantity
        costs[a.space_id] += a.cost_micros
    return [
        StatementLine(
            month=month,
            tenant_id=s.tenant_id,
            space_id=s.space_id,
            project_id=s.project_id,
            environment=s.environment.value,
            slug=s.slug,
            billable=totals[s.space_id].get(BILLABLE, 0),
            ai_cost_micros=costs[s.space_id],
            metrics=dict(sorted(totals[s.space_id].items())),
        )
        for s in sorted(spaces, key=lambda s: (str(s.tenant_id), s.slug))
    ]


def to_csv(lines: Sequence[StatementLine]) -> str:
    """The statement as CSV, one column per metric seen in the month."""
    metrics = sorted({m for line in lines for m in line.metrics})
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    columns = [
        "month",
        "tenant_id",
        "space_id",
        "project_id",
        "environment",
        "slug",
        "billable",
        "ai_cost_micros",
    ]
    writer.writerow([*columns, *metrics])
    for line in lines:
        writer.writerow(
            [
                line.month,
                line.tenant_id,
                line.space_id,
                line.project_id,
                line.environment,
                line.slug,
                line.billable,
                line.ai_cost_micros,
                *(line.metrics.get(m, 0) for m in metrics),
            ]
        )
    return out.getvalue()
