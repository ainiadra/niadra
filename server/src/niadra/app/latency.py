"""The Console's card of a space's read latency: the server time of its context reads, from
the series the read process exports by space, never from receipts on the database."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from niadra.contracts.metering import ContextLatencyReport, ContextLatencyWindow
from niadra.ports.infra import Clock, MetricsReader

log = logging.getLogger(__name__)

TARGET_MS = 100
SLO = 0.99
CACHE_S = 60.0
Window = Literal["24h", "7d"]
WINDOWS: tuple[Window, ...] = ("24h", "7d")
SERIES = "niadra_context_duration_seconds"


def queries(space_id: UUID, window: Window) -> dict[str, str]:
    """The instant queries of one window. The space id is a UUID, so it never escapes the label."""
    of = f'space="{space_id}"'
    count = f"sum(increase({SERIES}_count{{{of}}}[{window}]))"
    buckets = f"sum by (le) (increase({SERIES}_bucket{{{of}}}[{window}]))"
    return {
        "reads": count,
        "p50": f"histogram_quantile(0.5, {buckets})",
        "p95": f"histogram_quantile(0.95, {buckets})",
        "under": f'sum(increase({SERIES}_bucket{{{of},le="{TARGET_MS / 1000}"}}[{window}])) / {count}',
        "degraded": f'sum(increase({SERIES}_count{{{of},degraded="true"}}[{window}])) / {count}',
    }


@dataclass(frozen=True, slots=True)
class _Cached:
    until: float
    report: ContextLatencyReport


class ContextLatencyService:
    def __init__(self, reader: MetricsReader | None, clock: Clock, cache_s: float = CACHE_S) -> None:
        self._reader = reader
        self._clock = clock
        self._cache_s = cache_s
        self._cached: dict[UUID, _Cached] = {}

    async def report(self, space_id: UUID) -> ContextLatencyReport:
        now = self._clock.now()
        found = self._cached.get(space_id)
        if found is not None and found.until > now.timestamp():
            return found.report
        report = await self._read(space_id, now)
        self._cached[space_id] = _Cached(now.timestamp() + self._cache_s, report)
        return report

    async def _read(self, space_id: UUID, now: datetime) -> ContextLatencyReport:
        empty = ContextLatencyReport(available=False, target_ms=TARGET_MS, slo=SLO, windows=[], as_of=now)
        if self._reader is None:
            return empty
        wanted = {f"{w}:{name}": q for w in WINDOWS for name, q in queries(space_id, w).items()}
        try:
            values = await self._reader.scalars(wanted)
        except Exception:  # the card says the metrics are out; the Console page goes on
            log.warning("the cell's metrics did not answer the latency card")
            return empty
        return ContextLatencyReport(
            available=True,
            target_ms=TARGET_MS,
            slo=SLO,
            windows=[
                _window(w, {k.split(":", 1)[1]: v for k, v in values.items() if k.startswith(f"{w}:")})
                for w in WINDOWS
            ],
            as_of=now,
        )


def _window(window: Window, values: dict[str, float | None]) -> ContextLatencyWindow:
    reads = round(values.get("reads") or 0)
    if reads == 0:
        return ContextLatencyWindow(window=window, reads=0)
    under = values.get("under")
    return ContextLatencyWindow(
        window=window,
        reads=reads,
        p50_ms=_ms(values.get("p50")),
        p95_ms=_ms(values.get("p95")),
        under_target=_share(under),
        within_slo=under >= SLO if under is not None else None,
        error_budget_used=round((1 - under) / (1 - SLO), 3) if under is not None else None,
        degraded=_share(values.get("degraded")) or 0.0,
    )


def _ms(seconds: float | None) -> float | None:
    return round(seconds * 1000, 1) if seconds is not None else None


def _share(value: float | None) -> float | None:
    return round(min(max(value, 0.0), 1.0), 4) if value is not None else None
