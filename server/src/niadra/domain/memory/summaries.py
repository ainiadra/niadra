"""Tail summaries (5.9): the deterministic version, built from episodes without any LLM.

The short one is the last few episodes; the long one adds how often each normalized category
came back. Both only change when a newer episode exists, so the nightly job is incremental.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from niadra.domain.records import Episode

SHORT_EPISODES = 3
LONG_EPISODES = 10
LEVELS = ("short", "long")


@dataclass(frozen=True, slots=True)
class TailState:
    text: str
    version: int
    covers_until: datetime
    membership_version: int


def tail_summaries(episodes: Sequence[Episode], timezone: str) -> dict[str, str]:
    """`{"short": ..., "long": ...}` from current episodes in any order."""
    newest = sorted(episodes, key=lambda e: (e.ended_at, e.episode_id), reverse=True)
    tz = ZoneInfo(timezone)

    def line(e: Episode) -> str:
        day = e.ended_at.astimezone(tz).date().isoformat()
        return f"{day} {e.channel} {e.normalized_category}: {e.summary} [{e.outcome}]"

    counts = Counter(e.normalized_category for e in newest)
    recurrence = ", ".join(
        f"{name} x{n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return {
        "short": "\n".join(line(e) for e in newest[:SHORT_EPISODES]),
        "long": "\n".join([recurrence, *(line(e) for e in newest[:LONG_EPISODES])]),
    }
