"""Coverage per source (5.2): what the SDK says it sent against what the cell received, per UTC day."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class SourceDay:
    day: date
    sent: int
    received: int
    rejected: int
    unmapped: int
    last_event_at: datetime | None


def coverage_since(now: datetime, days: int) -> datetime:
    """Midnight UTC of the first day of the window, so every day in it is whole except today."""
    first = (now - timedelta(days=days - 1)).date()
    return datetime(first.year, first.month, first.day, tzinfo=now.tzinfo)
