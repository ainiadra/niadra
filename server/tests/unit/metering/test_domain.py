from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from niadra.domain.metering import (
    UsageCount,
    UsageRow,
    across_sources,
    by_day,
    hour_floor,
    hours_to_roll,
    merge_counts,
    usage_event_id,
)

H = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)


def test_hours_are_utc_and_the_previous_hour_is_recounted() -> None:
    local = datetime(2026, 9, 22, 10, 42, 7, tzinfo=timezone(timedelta(hours=-3)))
    assert hour_floor(local) == H
    assert hours_to_roll(H + timedelta(seconds=3)) == [H - timedelta(hours=1), H]


def test_merge_sums_by_metric_and_drops_zeros() -> None:
    merged = merge_counts(
        [UsageCount("context_read", 3), UsageCount("extraction_run", 1, 900)],
        [UsageCount("context_read", 2), UsageCount("export_bytes", 0)],
    )
    assert merged == [UsageCount("context_read", 5, 0), UsageCount("extraction_run", 1, 900)]


def test_usage_event_ids_are_deterministic_per_natural_key() -> None:
    a = usage_event_id("export_bytes", "run-1", "facts.parquet")
    assert a == usage_event_id("export_bytes", "run-1", "facts.parquet")
    assert a != usage_event_id("export_bytes", "run-1", "episodes.parquet")
    assert a.startswith("export_bytes:")


def test_daily_view_sums_the_hours_of_each_utc_day() -> None:
    rows = [
        UsageRow(H, "context_read", 2, 0),
        UsageRow(H + timedelta(hours=5), "context_read", 3, 0),
        UsageRow(H + timedelta(hours=12), "context_read", 1, 0),
    ]
    day = datetime(2026, 9, 22, tzinfo=UTC)
    assert by_day(rows) == [
        UsageRow(day, "context_read", 5, 0),
        UsageRow(day + timedelta(days=1), "context_read", 1, 0),
    ]


def test_counts_stay_apart_per_source_until_summed_across_sources() -> None:
    a, b = UUID(int=1), UUID(int=2)
    merged = merge_counts([UsageCount("context_read", 3, 0, a)], [UsageCount("context_read", 2, 0, a)])
    merged += merge_counts([UsageCount("context_read", 4, 0, b), UsageCount("export_run", 1)])
    assert merged == [
        UsageCount("context_read", 5, 0, a),
        UsageCount("context_read", 4, 0, b),
        UsageCount("export_run", 1, 0, None),
    ]
    rows = [UsageRow(H, c.metric, c.quantity, c.cost_micros, c.source_id) for c in merged]
    assert across_sources(rows) == [UsageRow(H, "context_read", 9, 0), UsageRow(H, "export_run", 1, 0)]
    assert [r.source_id for r in by_day(rows)] == [a, b, None]
