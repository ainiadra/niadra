"""The per-space loops of the `loops` deployable: relay and scheduler, idle-session sweeper and the
bounded maintenance passes (6).

Each space database has its own outbox, schedules and sessions, so every serving space gets its own
coroutines. A loop that finds work polls again at its base interval; one that finds nothing doubles its
wait up to a cap. The relay goes further (06, 6.3): one statement tells it when the space next has
anything due, it sleeps until then, and the doorbell wakes it sooner for a task scheduled or an outbox
row left behind in the meantime. With PgBouncer closing a server connection after 10 idle seconds, a
quiet space's database then holds no connection between passes, so the connection budget of the
instance goes to the spaces that are active. A semaphore bounds how many passes touch databases at the
same moment, and new spaces start at random moments, so a restart does not open every database at once.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.domain.common.errors import Unavailable
from niadra.domain.pipeline.due import PipelineDue
from niadra.ports.infra import Doorbell

log = logging.getLogger(__name__)

REFRESH_S = 5.0
DEFAULT_CONCURRENCY = 4
DOORBELL_EVERY_S = 0.5

Sleep = Callable[[asyncio.Event, float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Pass:
    """What a step found, and when known, how soon its space has more due."""

    found: int
    due_in_s: float | None = None


@dataclass(frozen=True, slots=True)
class LoopSpec:
    name: str
    base_s: float
    cap_s: float
    step: Callable[[UUID], Awaitable[int | Pass]]
    """One pass over one space; returns how much work it found."""
    wakes: bool = False
    """The doorbell wakes this loop (the relay)."""


class LoopTelemetry(Protocol):
    def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None: ...

    def outbox_lag(self, space_id: UUID, seconds: float) -> None: ...

    def intake_backlog(self, space_id: UUID, pending: int, oldest_s: float) -> None: ...

    def forget_space(self, space_id: UUID) -> None: ...


class RelayPasses(Protocol):
    async def relay_step(self, space_id: UUID) -> "RelayResult": ...


class RelayResult(Protocol):
    @property
    def found(self) -> int: ...

    @property
    def due(self) -> PipelineDue | None: ...

    @property
    def next_in_s(self) -> float | None: ...


class RelayStep:
    """The relay's pass, which also reports what the probe read: the outbox lag and the intake backlog."""

    def __init__(
        self,
        pipeline: RelayPasses,
        telemetry: LoopTelemetry | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pipeline = pipeline
        self._telemetry = telemetry
        self._now = now

    async def __call__(self, space_id: UUID) -> Pass:
        result = await self._pipeline.relay_step(space_id)
        due = result.due
        if self._telemetry is not None and due is not None:
            now = self._now()
            self._telemetry.outbox_lag(space_id, due.outbox_lag_s(now))
            self._telemetry.intake_backlog(space_id, due.intake_pending, due.intake_lag_s(now))
        return Pass(result.found, result.next_in_s)


class Remembered:
    """A loop step that reads its space's database again only when its marker moved (06, 6.3). The marker
    is what the step compares with the state it stored, computed without the database: a configuration's
    fingerprint, or the local day once the nightly hour passed (None before it). The stored state stays
    the guard across processes and restarts; this process only stops asking every few minutes for an
    answer it already had, which kept a server connection busy for every quiet space."""

    def __init__(
        self, step: Callable[[UUID], Awaitable[int]], marker: Callable[[UUID], object | None]
    ) -> None:
        self._step = step
        self._marker = marker
        self._seen: dict[UUID, object] = {}

    async def __call__(self, space_id: UUID) -> int:
        try:
            mark = self._marker(space_id)
        except Exception:  # a space the configuration no longer has: the step decides
            mark = None
        if mark is not None and self._seen.get(space_id) == mark:
            return 0
        found = await self._step(space_id)
        if mark is not None:
            self._seen[space_id] = mark
        return found


def night_day(now: datetime, timezone: str, hour: int) -> int | None:
    """The local day once `hour` passed in `timezone`, the marker of a once-a-day step; None before."""
    local = now.astimezone(ZoneInfo(timezone))
    return local.date().toordinal() if local.hour >= hour else None


class ServingSpaces(Protocol):
    def serving_spaces(self) -> list[UUID]:
        """Spaces whose database serves, as this process last read the directory."""
        ...


def next_interval(spec: LoopSpec, current: float, found: int, due_in_s: float | None = None) -> float:
    interval = spec.base_s if found else min(spec.cap_s, current * 2)
    if due_in_s is not None and not found:
        interval = min(interval, max(spec.base_s, due_in_s))
    return interval


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class SpaceLoopSupervisor:
    def __init__(
        self,
        spaces: ServingSpaces,
        loops: Sequence[LoopSpec],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        refresh_s: float = REFRESH_S,
        telemetry: LoopTelemetry | None = None,
        sleep: Sleep = wait_or_stop,
        jitter: Callable[[], float] | None = None,
        start_spread_s: float = 0.0,
    ) -> None:
        self._spaces = spaces
        self._loops = list(loops)
        self._slots = asyncio.Semaphore(concurrency)
        self._refresh_s = refresh_s
        self._telemetry = telemetry
        self._sleep = sleep
        # A factor around 1 applied to idle waits, so quiet spaces do not come back in step.
        self._jitter = jitter
        self._start_spread_s = start_spread_s
        self._running: dict[UUID, tuple[asyncio.Event, list[asyncio.Task[None]]]] = {}
        self._alarms: dict[UUID, asyncio.Event] = {}

    @property
    def running(self) -> list[UUID]:
        return sorted(self._running)

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await self.reconcile()
                await self._sleep(stop, self._refresh_s)
        finally:
            for space_id in list(self._running):
                await self._stop(space_id)

    def wake(self, space_id: UUID) -> None:
        """The space's waking loops pass now instead of at the end of their wait."""
        alarm = self._alarms.get(space_id)
        if alarm is not None:
            alarm.set()

    async def listen(
        self, doorbell: Doorbell, stop: asyncio.Event, every_s: float = DOORBELL_EVERY_S
    ) -> None:
        """Wakes the spaces the doorbell names, one call to the store for the whole cell per tick."""
        failing = False
        while not stop.is_set():
            try:
                for space_id in await doorbell.due(datetime.now(UTC)):
                    self.wake(space_id)
                failing = False
            except Exception:
                # The relays keep their own schedule; the doorbell only makes them sooner.
                if not failing:
                    log.warning("doorbell unavailable", exc_info=True)
                failing = True
            await wait_or_stop(stop, every_s)

    async def reconcile(self) -> None:
        """Starts the loops of spaces that began to serve and stops those of spaces that stopped
        (a restore in maintenance, a deletion)."""
        wanted = set(self._spaces.serving_spaces())
        for space_id in sorted(wanted - set(self._running)):
            quit_ = asyncio.Event()
            alarm = self._alarms[space_id] = asyncio.Event()
            first = random.uniform(0, self._start_spread_s)  # noqa: S311 (timing, not a secret)
            tasks = [
                asyncio.create_task(
                    self.space_loop(
                        space_id, spec, quit_, alarm=alarm if spec.wakes else None, first_wait_s=first
                    ),
                    name=f"{spec.name}:{space_id}",
                )
                for spec in self._loops
            ]
            self._running[space_id] = (quit_, tasks)
        for space_id in sorted(set(self._running) - wanted):
            await self._stop(space_id)

    async def space_loop(
        self,
        space_id: UUID,
        spec: LoopSpec,
        stop: asyncio.Event,
        *,
        alarm: asyncio.Event | None = None,
        first_wait_s: float = 0.0,
    ) -> None:
        interval = spec.base_s
        if first_wait_s > 0:
            await self._sleep(stop, first_wait_s)
        while not stop.is_set():
            async with self._slots:
                try:
                    result = await spec.step(space_id)
                    passed = result if isinstance(result, Pass) else Pass(result)
                    found, due_in = passed.found, passed.due_in_s
                except Unavailable:
                    found, due_in = 0, None  # the database is between states; the directory will say which
                except Exception:
                    # The database or the broker is down; the rows wait and the next pass retries (11).
                    where = {"loop": spec.name, "space_id": str(space_id)}
                    log.exception("space loop step failed", extra=where)
                    found, due_in = 0, None
            interval = next_interval(spec, interval, found, due_in)
            wait = interval * self._jitter() if self._jitter is not None and not found else interval
            if self._telemetry is not None:
                self._telemetry.loop_interval(space_id, spec.name, wait)
            if alarm is None:
                await self._sleep(stop, wait)
                continue
            await self._sleep(alarm, wait)
            if alarm.is_set() and not stop.is_set():
                alarm.clear()
                interval = spec.base_s

    async def _stop(self, space_id: UUID) -> None:
        quit_, tasks = self._running.pop(space_id)
        quit_.set()
        alarm = self._alarms.pop(space_id, None)
        if alarm is not None:
            alarm.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._telemetry is not None:
            self._telemetry.forget_space(space_id)
