"""Source coverage on a `context()` answer (5.2, 5.11): which sources feed the memory, and which went quiet.

The pack says up to when it reflects the memory (`as_of`); coverage says whether that memory is still
being fed. A source whose last event is older than `SILENT_AFTER` is `silent`; a source never seen in
the lookback is left out, since a reader-only agent sends nothing and is not silent.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

SILENT_AFTER = timedelta(minutes=30)
LOOKBACK = timedelta(days=7)
REFRESH = timedelta(seconds=30)
"""Every read would ask the replica otherwise; half a minute is far below `SILENT_AFTER`."""


@dataclass(frozen=True, slots=True)
class SourceStatus:
    source_id: UUID
    status: str
    last_event_at: datetime


def source_coverage(
    last_event_at: Mapping[UUID, datetime],
    configured: Collection[UUID],
    now: datetime,
    silent_after: timedelta = SILENT_AFTER,
) -> list[SourceStatus]:
    """Configured sources only: one removed from the configuration no longer feeds this space."""
    return [
        SourceStatus(source_id, "silent" if now - last >= silent_after else "ok", last)
        for source_id, last in sorted(last_event_at.items(), key=lambda kv: str(kv[0]))
        if source_id in configured
    ]
