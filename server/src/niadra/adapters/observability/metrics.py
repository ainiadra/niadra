"""Prometheus metrics shared by every deployable. Labels never carry customer data: a space is an
internal id, never a name, and only the context read's histogram and the per-space gauges carry it; a
route is its template, never the raw path; no subject, conversation, handle or key id is ever a label."""

import contextlib
import contextvars
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.ports.infra import DirectoryState

# The objectives' edges are buckets, so the burn-rate rules count them exactly: 35 ms, the context read's p95
# inside the VPC; 80 ms, the batch's acknowledgement; 100 ms, the published context read.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.035, 0.05, 0.08, 0.1, 0.15, 0.25, 0.5, 1, 2.5, 5)
CONTEXT_ROUTE = "/v1/context"
CONTEXT_SPACE_IDLE_S = 3600.0
"""A space with no context read for this long leaves the per-space series, as idle loops leave theirs."""

HTTP_REQUESTS = Counter("niadra_http_requests_total", "HTTP requests", ["route", "method", "status"])
HTTP_LATENCY = Histogram(
    "niadra_http_request_duration_seconds",
    "HTTP request latency",
    ["route", "method"],
    buckets=LATENCY_BUCKETS,
)
CONTEXT_STAGE = Histogram(
    "niadra_context_stage_seconds",
    "Time of each stage of a context read (privacy, resolve, pack, live, slots, blocks, receipt)",
    ["stage"],
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.035, 0.05, 0.1),
)
CONTEXT_PATH = Counter(
    "niadra_context_path_total",
    "Context reads by delivery path (T0 to T4) and degradation",
    ["path", "degraded"],
)
CONTEXT_BY_SPACE = Histogram(
    "niadra_context_duration_seconds",
    "Server time of a context read (/v1/context) by space and degradation",
    ["space", "degraded"],
    buckets=LATENCY_BUCKETS,
)
READ_DB_STATEMENTS = Counter(
    "niadra_read_db_statements_total",
    "Database statements the read process ran before answering a request, by the kind of read: a warm "
    "context read (resolved from the read state, served from the cache) runs none; the first read of a "
    "conversation (cold) resolves its customer on the database; none for a request that reads no context",
    ["route", "read"],
)
TASKS = Counter("niadra_tasks_total", "Tasks handled by the workers", ["task", "result"])
TASK_LATENCY = Histogram("niadra_task_duration_seconds", "Task handling time", ["task"])
DEAD_TASKS = Counter("niadra_dead_tasks_total", "Tasks moved to dead_tasks", ["task"])
OUTBOX_OLDEST = Gauge(
    "niadra_outbox_oldest_seconds", "Age of the oldest unpublished outbox row, over every space"
)
OUTBOX_OLDEST_BY_SPACE = Gauge(
    "niadra_outbox_oldest_by_space_seconds", "Age of the oldest unpublished outbox row", ["space"]
)
# Acknowledged batches waiting in `ingest_intake` to be stored (5.2); the relay's probe reads them.
INTAKE_PENDING = Gauge("niadra_intake_pending", "Acknowledged batches not yet stored, over every space")
INTAKE_OLDEST = Gauge(
    "niadra_intake_oldest_seconds", "Age of the oldest acknowledged batch not yet stored, over every space"
)
INTAKE_PENDING_BY_SPACE = Gauge(
    "niadra_intake_pending_by_space", "Acknowledged batches not yet stored", ["space"]
)
LOOP_INTERVAL = Gauge(
    "niadra_loop_interval_seconds", "Current wait of a per-space loop (idle ones back off)", ["space", "loop"]
)
SPACE_DATABASES = Gauge("niadra_space_databases", "Space databases in the directory", ["status"])
DIRECTORY_DRIFT = Gauge(
    "niadra_space_directory_drift", "Directory rows of spaces missing from the snapshot, not removed"
)
ORPHAN_DATABASES = Gauge(
    "niadra_space_orphan_databases", "Space databases on a server without a directory row"
)
SPACE_DATABASE_FAILURES = Gauge(
    "niadra_space_database_failures", "Consecutive failures to create a space database", ["space"]
)
DELETION_RECEIPTS_PENDING = Gauge(
    "niadra_deletion_receipts_pending", "Deletion receipts the control plane has not acknowledged"
)
RECEIPTS_LOST = Counter("niadra_receipts_lost_total", "Receipt sequence ranges that never arrived")
LLM_SPEND = Counter("niadra_llm_spend_micros_total", "LLM spend in micro-dollars", ["purpose"])
LLM_BUDGET_EXHAUSTED = Counter(
    "niadra_llm_budget_exhausted_total", "Calls refused because a daily LLM budget was spent", ["purpose"]
)


# Freshness targets of the docs: memory within a minute of the session's end, an action or system
# event in memory within 10 seconds; the alerts read the p95 of each.
MEMORY_READY = Histogram(
    "niadra_memory_ready_seconds",
    "From a session's end to its memory reconciled",
    buckets=(5, 10, 20, 30, 45, 60, 90, 120, 300, 600, 1800),
)
MEASUREMENT_READY = Histogram(
    "niadra_measurement_ready_seconds",
    "From a session's end to its first context-use measurement",
    buckets=(5, 10, 20, 30, 45, 60, 90, 120, 300, 600, 1800),
)
OBJECT_EVENT_APPLIED = Histogram(
    "niadra_object_event_applied_seconds",
    "From an action's or system event's arrival to its effect on objects and open items",
    buckets=(0.25, 0.5, 1, 2, 3, 5, 7.5, 10, 15, 30, 60),
)

READ_CHANNEL = Histogram(
    "niadra_read_channel_seconds",
    "Time of one retrieval channel of a read, by outcome (ran, skipped, off, empty)",
    ["channel", "status"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.1),
)
RETRIEVAL_CACHE = Counter(
    "niadra_retrieval_cache_total", "Per-customer retrieval cache events of the read process", ["event"]
)
RETRIEVAL_CACHE_BYTES = Gauge("niadra_retrieval_cache_bytes", "Bytes the retrieval cache holds (estimate)")
RETRIEVAL_CACHE_ENTRIES = Gauge("niadra_retrieval_cache_entries", "Customers the retrieval cache holds")


class PrometheusTelemetry:
    """The `Telemetry` and `SpaceTelemetry` ports on the process's Prometheus registry."""

    def __init__(self) -> None:
        self._lags: dict[UUID, float] = {}
        self._intake: dict[UUID, tuple[int, float]] = {}
        self._loops: dict[UUID, set[str]] = {}
        self._failing: set[UUID] = set()

    def receipts_lost(self, count: int) -> None:
        RECEIPTS_LOST.inc(count)

    def llm_spent(self, purpose: str, cost_micros: int) -> None:
        LLM_SPEND.labels(purpose).inc(max(0, cost_micros))

    def llm_budget_exhausted(self, purpose: str) -> None:
        LLM_BUDGET_EXHAUSTED.labels(purpose).inc()

    def memory_ready(self, seconds: float) -> None:
        MEMORY_READY.observe(max(0.0, seconds))

    def object_event_applied(self, seconds: float) -> None:
        OBJECT_EVENT_APPLIED.observe(max(0.0, seconds))

    def measurement_ready(self, seconds: float) -> None:
        MEASUREMENT_READY.observe(max(0.0, seconds))

    def channel(self, channel: str, status: str, seconds: float) -> None:
        READ_CHANNEL.labels(channel, status).observe(max(0.0, seconds))

    def retrieval_cache(self, event: str, bytes_used: int, entries: int) -> None:
        RETRIEVAL_CACHE.labels(event).inc()
        RETRIEVAL_CACHE_BYTES.set(bytes_used)
        RETRIEVAL_CACHE_ENTRIES.set(entries)

    def directory(self, state: DirectoryState) -> None:
        for status in {"creating", "migrating", "ready", "maintenance", "failed", "deleting", "deleted"}:
            SPACE_DATABASES.labels(status).set(state.by_status.get(status, 0))
        DIRECTORY_DRIFT.set(state.drift)
        ORPHAN_DATABASES.set(state.orphans)
        DELETION_RECEIPTS_PENDING.set(state.pending_receipts)
        for space_id in self._failing - set(state.failures):
            SPACE_DATABASE_FAILURES.remove(str(space_id))
        for space_id, failures in state.failures.items():
            SPACE_DATABASE_FAILURES.labels(str(space_id)).set(failures)
        self._failing = set(state.failures)

    def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None:
        self._loops.setdefault(space_id, set()).add(loop)
        LOOP_INTERVAL.labels(str(space_id), loop).set(seconds)

    def intake_backlog(self, space_id: UUID, pending: int, oldest_s: float) -> None:
        self._intake[space_id] = (pending, oldest_s)
        INTAKE_PENDING_BY_SPACE.labels(str(space_id)).set(pending)
        INTAKE_PENDING.set(sum(p for p, _ in self._intake.values()))
        INTAKE_OLDEST.set(max(o for _, o in self._intake.values()))

    def outbox_lag(self, space_id: UUID, seconds: float) -> None:
        self._lags[space_id] = seconds
        OUTBOX_OLDEST_BY_SPACE.labels(str(space_id)).set(seconds)
        OUTBOX_OLDEST.set(max(self._lags.values()))

    def forget_space(self, space_id: UUID) -> None:
        """A space whose loops stopped leaves the per-space series instead of freezing at a last value."""
        self._lags.pop(space_id, None)
        OUTBOX_OLDEST.set(max(self._lags.values(), default=0.0))
        self._intake.pop(space_id, None)
        INTAKE_PENDING.set(sum(p for p, _ in self._intake.values()))
        INTAKE_OLDEST.set(max((o for _, o in self._intake.values()), default=0.0))
        for gauge, labels in (
            (OUTBOX_OLDEST_BY_SPACE, [(str(space_id),)]),
            (INTAKE_PENDING_BY_SPACE, [(str(space_id),)]),
            (LOOP_INTERVAL, [(str(space_id), loop) for loop in sorted(self._loops.pop(space_id, set()))]),
        ):
            for values in labels:
                with contextlib.suppress(KeyError):
                    gauge.remove(*values)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


@dataclass(frozen=True, slots=True)
class ReadSummary:
    """What a context route leaves in the request's state for this module: the read's own stage times (ms),
    its delivery path and whether it was served degraded."""

    stages: Mapping[str, float]
    path: str
    degraded: bool
    warm: bool = False
    """Resolved from the read state and served from the cache (T0): no statement belongs to it."""


@dataclass
class RequestStats:
    """One request's statements, counted until its answer starts: what runs after it (a warm-up the read
    started) is off the read's path."""

    statements: int = 0
    open: bool = True


_REQUEST: contextvars.ContextVar[RequestStats | None] = contextvars.ContextVar("niadra_request", default=None)


def start_request() -> RequestStats:
    stats = RequestStats()
    _REQUEST.set(stats)
    return stats


def count_statements() -> None:
    """Counts the statements of the process's requests (`READ_DB_STATEMENTS`): the read role's, whose hot
    path runs none. SQLAlchemy's asyncio bridge runs each statement with the awaiting task's context."""
    if not event.contains(Engine, "before_cursor_execute", _counted):
        event.listen(Engine, "before_cursor_execute", _counted)
    READ_DB_STATEMENTS.labels(CONTEXT_ROUTE, "warm")


def _counted(*_: Any) -> None:
    stats = _REQUEST.get()
    if stats is not None and stats.open:
        stats.statements += 1


@dataclass
class ContextReads:
    """The per-space series of the context read, and the forgetting of the spaces that stopped reading."""

    seen: dict[str, float] = field(default_factory=dict)
    swept_at: float = 0.0

    def observe(self, space: str, read: ReadSummary, seconds: float) -> None:
        degraded = "true" if read.degraded else "false"
        CONTEXT_BY_SPACE.labels(space, degraded).observe(seconds)
        now = time.monotonic()
        self.seen[space] = now
        if now - self.swept_at >= 60:
            self.swept_at = now
            for idle in [s for s, at in self.seen.items() if now - at > CONTEXT_SPACE_IDLE_S]:
                self.forget_space(idle)

    def forget_space(self, space: str) -> None:
        self.seen.pop(space, None)
        for degraded in ("true", "false"):
            with contextlib.suppress(KeyError):
                CONTEXT_BY_SPACE.remove(space, degraded)


CONTEXT_READS = ContextReads()


def context_read(route: str, space: str | None, read: ReadSummary, seconds: float) -> None:
    for stage, ms in read.stages.items():
        CONTEXT_STAGE.labels(stage).observe(ms / 1000)
    CONTEXT_PATH.labels(read.path, "true" if read.degraded else "false").inc()
    if route == CONTEXT_ROUTE and space is not None:
        CONTEXT_READS.observe(space, read, seconds)


def read_kind(read: ReadSummary | None) -> str:
    """The `read` label of `READ_DB_STATEMENTS`."""
    if read is None:
        return "none"
    return "warm" if read.warm else "cold"
