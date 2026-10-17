"""Retention by data class (5.14.0, 22.5) of what the governance modules keep; the cold archive calendar."""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID

from niadra.domain.common.ids import uuid7_ms


class DataClass(StrEnum):
    PRIVACY_FEED = "privacy_feed"
    READ_INVALIDATIONS = "read_invalidations"
    EXPORT_RUNS = "export_runs"
    TOMBSTONES = "tombstones"
    COLD_EVENTS = "cold_events"
    REVIEW_SAMPLES = "review_samples"
    TRIGGER_FIRINGS = "trigger_firings"
    WEBHOOK_DELIVERIES = "webhook_deliveries"
    FACTS = "facts"
    EPISODES = "episodes"
    OPEN_ITEMS = "open_items"
    RECEIPTS = "receipts"
    SESSIONS = "sessions"
    RAW_CONTENT = "raw_content"


DEFAULT_RETENTION: dict[DataClass, timedelta] = {
    DataClass.PRIVACY_FEED: timedelta(hours=72),
    # Read processes poll the last 120 s; an hour is room for a sweep that runs late.
    DataClass.READ_INVALIDATIONS: timedelta(hours=1),
    DataClass.EXPORT_RUNS: timedelta(days=395),
    # Tombstones must outlive the slowest export schedule; 13 months matches the run history (22.5).
    DataClass.TOMBSTONES: timedelta(days=395),
    DataClass.REVIEW_SAMPLES: timedelta(days=365),
    DataClass.TRIGGER_FIRINGS: timedelta(days=395),
    # Retries stop after 24 h and dead ones go after 7 days (7.5); the rest is the Console's recent log.
    DataClass.WEBHOOK_DELIVERIES: timedelta(days=30),
}

# What a tenant may change. The feed, runs and tombstones keep the cell's promises to read processes and
# export consumers, and firings are what stops a trigger from firing twice on the same evidence (22.1),
# so their windows are the cell's, not the tenant's.
# Memory kept until the tenant sets a window (memory never cuts): a fact last confirmed, an
# episode that ended, an open item resolved, a conversation session last heard, a receipt read, or an event in
# the cold archive (and a row set aside that would have become one) before it leaves for good. An open item
# still open never expires. Events used to go after five years by default. So is raw content: the payloads as
# they came (an unmapped one too), media, and the raw interactions measurement reads; an erasure reaches them
# whatever the window.
KEPT_UNTIL_SET = frozenset(
    {
        DataClass.FACTS,
        DataClass.EPISODES,
        DataClass.OPEN_ITEMS,
        DataClass.RECEIPTS,
        DataClass.SESSIONS,
        DataClass.RAW_CONTENT,
        DataClass.COLD_EVENTS,
    }
)
TENANT_TUNABLE = frozenset(
    {DataClass.COLD_EVENTS, DataClass.REVIEW_SAMPLES, DataClass.WEBHOOK_DELIVERIES, *KEPT_UNTIL_SET}
)
MIN_DAYS: dict[DataClass, int] = {
    # Measurement reads a conversation's receipts and sessions, and a late turn reopens a session, days
    # after it ended; the audit anchor reads yesterday's receipts.
    DataClass.RECEIPTS: 30,
    DataClass.SESSIONS: 30,
    # A mapping published within a week remaps what came meanwhile (5.2); the nightly pass reads yesterday's
    # raw interactions.
    DataClass.RAW_CONTENT: 7,
}
RAW_CONTENT_KINDS = ("raw", "media", "measure/raw")
"""Where raw content lives in object storage, each under `<kind>/<space>/<handle or _>/`."""


def raw_content_prefixes(space_id: UUID) -> list[str]:
    return [f"{kind}/{space_id}/" for kind in RAW_CONTENT_KINDS]


def raw_content_at(space_id: UUID, key: str) -> tuple[str, datetime] | None:
    """The handle segment of a raw content object and the latest it may have been written, by its key: the
    end of the day a payload's or an interaction's key names, an upload's id's time. None for a key of another
    shape."""
    for kind in RAW_CONTENT_KINDS:
        if not key.startswith(prefix := f"{kind}/{space_id}/"):
            continue
        owner, _, rest = key.removeprefix(prefix).partition("/")
        stamp = rest.split("/", 1)[0]
        try:
            if kind == "media":
                return owner, datetime.fromtimestamp(uuid7_ms(UUID(stamp)) / 1000, UTC)
            return owner, datetime.combine(date.fromisoformat(stamp) + timedelta(days=1), time(), UTC)
        except ValueError:
            return None
    return None


def cutoffs(now: datetime, overrides: Mapping[str, int] | None = None) -> dict[DataClass, datetime]:
    """The instant before which each class expires; `overrides` are the tenant's days per tunable class.
    A class kept until set appears only once the tenant sets it."""
    days = {DataClass(k): v for k, v in (overrides or {}).items() if k in TENANT_TUNABLE}
    found = {
        data_class: now - (timedelta(days=days[data_class]) if data_class in days else keep)
        for data_class, keep in DEFAULT_RETENTION.items()
    }
    found |= {c: now - timedelta(days=days[c]) for c in KEPT_UNTIL_SET if c in days}
    return found


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=UTC)
    end = datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=UTC)
    return start, end


def month_to_archive(now: datetime, hot_days: int) -> str:
    """The newest month that ended before the hot window: its events leave Postgres for the cold archive."""
    edge = (now - timedelta(days=hot_days)).astimezone(UTC).date()
    return month_key(edge.replace(day=1) - timedelta(days=1))


def month_expired(month: str, cutoff: datetime) -> bool:
    return month_bounds(month)[1] <= cutoff
