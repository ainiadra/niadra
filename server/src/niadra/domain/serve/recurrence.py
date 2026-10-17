"""How many times something happened, counted over typed episodes (16.2).

The search's recurrence block, the pack's "From history" line and the trait rules (20.1) all
count through these functions, over the same window and the same episodes, so the three never
disagree (16.3).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from niadra.domain.records import Episode


@dataclass(frozen=True, slots=True)
class CategoryCount:
    category: str
    occurrences: int
    last_at: datetime
    last_outcome: str
    last_resolution: str | None


def count_by_category(episodes: Iterable[Episode], *, since: datetime | None = None) -> list[CategoryCount]:
    """Occurrences per `normalized_category`, most frequent first, ties by name."""
    grouped: dict[str, list[Episode]] = {}
    for episode in episodes:
        if since is None or episode.started_at >= since:
            grouped.setdefault(episode.normalized_category, []).append(episode)
    counts = []
    for category, items in grouped.items():
        ordered = sorted(items, key=lambda e: (e.started_at, str(e.episode_id)))
        last = ordered[-1]
        counts.append(CategoryCount(category, len(items), last.started_at, last.outcome, last.resolution))
    return sorted(counts, key=lambda c: (-c.occurrences, c.category))


RECURRENCE_WINDOW_DAYS = 90
"""One window for every "how many times": the pack's "From history" line, the search's `recurrence` block
and the default of the recurring-complaint pattern (16.2, 20.1)."""
RECURRENCE_INTENT = "complaint"
"""And one filter: what recurs is a complaint that came back, which is also what the pattern counts."""


def recurrence(
    episodes: Iterable[Episode],
    category: str,
    *,
    now: datetime,
    window_days: int = RECURRENCE_WINDOW_DAYS,
    intent: str = RECURRENCE_INTENT,
) -> CategoryCount | None:
    since = now - timedelta(days=window_days)
    counted = (e for e in episodes if e.intent == intent)
    return next((c for c in count_by_category(counted, since=since) if c.category == category), None)
