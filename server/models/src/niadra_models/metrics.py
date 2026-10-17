"""Prometheus text exposition without a client library: counters and histograms, updated on the loop.

Every update happens on the event loop thread (the middleware and the batch workers), so no lock is
needed. Autoscaling reads the queue depth and the latency per model (06 4.5).
"""

import bisect
from collections import defaultdict
from collections.abc import Callable, Iterable

LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
BATCH_BUCKETS = (1, 2, 4, 8, 16, 32, 64, 128, 256)

Labels = tuple[tuple[str, str], ...]


class Histogram:
    def __init__(self, buckets: Iterable[float]) -> None:
        self.buckets = tuple(buckets)
        self.counts = [0] * (len(self.buckets) + 1)
        self.total = 0.0

    def observe(self, value: float) -> None:
        self.counts[bisect.bisect_left(self.buckets, value)] += 1
        self.total += value


class Metrics:
    def __init__(self) -> None:
        self._requests: defaultdict[Labels, int] = defaultdict(int)
        self._latency: dict[Labels, Histogram] = {}
        self._batch: dict[Labels, Histogram] = {}
        self._gauges: dict[str, Callable[[], dict[str, int]]] = {}

    def request(self, endpoint: str, status: int, seconds: float) -> None:
        self._requests[(("endpoint", endpoint), ("status", str(status)))] += 1
        key = (("endpoint", endpoint),)
        self._latency.setdefault(key, Histogram(LATENCY_BUCKETS)).observe(seconds)

    def batch(self, model: str, items: int) -> None:
        self._batch.setdefault((("model", model),), Histogram(BATCH_BUCKETS)).observe(items)

    def gauge(self, name: str, read: Callable[[], dict[str, int]]) -> None:
        """A gauge read at scrape time, one sample per model."""
        self._gauges[name] = read

    def render(self) -> str:
        lines = ["# TYPE niadra_models_requests_total counter"]
        lines += [f"niadra_models_requests_total{_fmt(k)} {v}" for k, v in sorted(self._requests.items())]
        lines += _histogram("niadra_models_request_duration_seconds", self._latency)
        lines += _histogram("niadra_models_batch_items", self._batch)
        for name, read in sorted(self._gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines += [f"{name}{_fmt((('model', m),))} {v}" for m, v in sorted(read().items())]
        return "\n".join(lines) + "\n"


def _histogram(name: str, series: dict[Labels, Histogram]) -> list[str]:
    lines = [f"# TYPE {name} histogram"]
    for labels, h in sorted(series.items()):
        cumulative = 0
        for bound, count in zip((*h.buckets, float("inf")), h.counts, strict=True):
            cumulative += count
            le = "+Inf" if bound == float("inf") else repr(bound)
            lines.append(f"{name}_bucket{_fmt((*labels, ('le', le)))} {cumulative}")
        lines.append(f"{name}_sum{_fmt(labels)} {h.total}")
        lines.append(f"{name}_count{_fmt(labels)} {cumulative}")
    return lines


def _fmt(labels: Labels) -> str:
    escaped = (v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") for _, v in labels)
    return "{" + ",".join(f'{k}="{v}"' for (k, _), v in zip(labels, escaped, strict=True)) + "}"
