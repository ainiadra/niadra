"""Per-space loops back off while a space is idle and stay quick while it is busy (6)."""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.app.space_loops import (
    LoopSpec,
    Pass,
    RelayStep,
    Remembered,
    SpaceLoopSupervisor,
    next_interval,
    night_day,
)
from niadra.domain.common.errors import Unavailable
from niadra.domain.pipeline.due import PipelineDue

BUSY, IDLE = UUID(int=0xA), UUID(int=0xB)


class FakeTime:
    """A clock the loops sleep on: each sleep records its interval and advances virtual time."""

    def __init__(self, passes: int) -> None:
        self.now = 0.0
        self.waits: dict[UUID, list[float]] = defaultdict(list)
        self.passes = passes
        self.current: UUID | None = None

    async def sleep(self, stop: asyncio.Event, seconds: float) -> None:
        assert self.current is not None
        self.waits[self.current].append(seconds)
        self.now += seconds
        if len(self.waits[self.current]) >= self.passes:
            stop.set()
        await asyncio.sleep(0)


class Spaces:
    def __init__(self, *spaces: UUID) -> None:
        self.spaces = list(spaces)

    def serving_spaces(self) -> list[UUID]:
        return list(self.spaces)


def test_the_interval_resets_on_work_and_doubles_up_to_the_cap() -> None:
    spec = LoopSpec("relay", 0.5, 15.0, lambda _: asyncio.sleep(0, 0))
    assert next_interval(spec, 8.0, found=3) == 0.5
    assert next_interval(spec, 0.5, found=0) == 1.0
    assert next_interval(spec, 12.0, found=0) == 15.0


async def test_an_idle_space_is_polled_at_the_cap_and_a_busy_one_at_the_base() -> None:
    time = FakeTime(passes=8)
    polls: dict[UUID, list[float]] = defaultdict(list)

    async def relay(space_id: UUID) -> int:
        time.current = space_id
        polls[space_id].append(time.now)
        return 3 if space_id == BUSY else 0

    spec = LoopSpec("relay", 0.5, 15.0, relay)
    supervisor = SpaceLoopSupervisor(Spaces(BUSY, IDLE), [spec], sleep=time.sleep)
    for space in (BUSY, IDLE):
        await supervisor.space_loop(space, spec, asyncio.Event())
    assert time.waits[BUSY] == [0.5] * 8
    assert time.waits[IDLE] == [1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0, 15.0]
    # 600 polls a second for 300 idle databases is what this avoids: the idle one settles at the cap.
    assert time.waits[IDLE][-1] == spec.cap_s


async def test_a_failing_or_unavailable_step_counts_as_idle_and_the_loop_goes_on() -> None:
    time = FakeTime(passes=3)
    calls = 0

    async def step(space_id: UUID) -> int:
        nonlocal calls
        time.current = space_id
        calls += 1
        if calls == 1:
            raise Unavailable("being restored", retry_after_s=5)
        raise RuntimeError("database down")

    spec = LoopSpec("scheduler", 1.0, 30.0, step)
    await SpaceLoopSupervisor(Spaces(), [spec], sleep=time.sleep).space_loop(IDLE, spec, asyncio.Event())
    assert time.waits[IDLE] == [2.0, 4.0, 8.0]


async def test_loops_start_for_serving_spaces_and_stop_when_a_space_stops_serving() -> None:
    started: list[UUID] = []
    forgotten: list[UUID] = []

    async def step(space_id: UUID) -> int:
        started.append(space_id)
        return 0

    class Telemetry:
        def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None:
            return None

        def outbox_lag(self, space_id: UUID, seconds: float) -> None:
            return None

        def forget_space(self, space_id: UUID) -> None:
            forgotten.append(space_id)

    spaces = Spaces(BUSY, IDLE)
    loops = [LoopSpec("relay", 60, 60, step), LoopSpec("scheduler", 60, 60, step)]
    supervisor = SpaceLoopSupervisor(spaces, loops, telemetry=Telemetry())
    await supervisor.reconcile()
    await asyncio.sleep(0.01)
    assert supervisor.running == [BUSY, IDLE]
    assert sorted(started) == sorted([BUSY, BUSY, IDLE, IDLE])
    spaces.spaces = [BUSY]  # IDLE went into maintenance
    await supervisor.reconcile()
    assert supervisor.running == [BUSY]
    assert forgotten == [IDLE]
    stop = asyncio.Event()
    stop.set()
    await supervisor.run(stop)
    assert supervisor.running == []


class Telemetry:
    def __init__(self) -> None:
        self.lags: list[tuple[UUID, float]] = []
        self.backlog: list[tuple[UUID, int, float]] = []

    def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None:
        return None

    def outbox_lag(self, space_id: UUID, seconds: float) -> None:
        self.lags.append((space_id, seconds))

    def intake_backlog(self, space_id: UUID, pending: int, oldest_s: float) -> None:
        self.backlog.append((space_id, pending, oldest_s))

    def forget_space(self, space_id: UUID) -> None:
        return None


async def test_the_relay_reports_what_its_probe_read_and_when_the_space_is_due() -> None:
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)

    class Result:
        found = 0
        due = PipelineDue(now - timedelta(seconds=4), None, None, 3, now - timedelta(seconds=9))
        next_in_s = 7.5

    class Pipeline:
        async def relay_step(self, space_id: UUID) -> Result:
            return Result()

    telemetry = Telemetry()
    assert await RelayStep(Pipeline(), telemetry, now=lambda: now)(BUSY) == Pass(0, 7.5)
    assert telemetry.lags == [(BUSY, 4.0)]
    assert telemetry.backlog == [(BUSY, 3, 9.0)]


def test_a_known_due_time_shortens_an_idle_wait_but_never_below_the_base() -> None:
    spec = LoopSpec("relay", 0.5, 120.0, lambda _: asyncio.sleep(0, 0))
    assert next_interval(spec, 64.0, found=0, due_in_s=None) == 120.0
    assert next_interval(spec, 64.0, found=0, due_in_s=7.0) == 7.0
    assert next_interval(spec, 64.0, found=0, due_in_s=0.0) == 0.5
    assert next_interval(spec, 64.0, found=2, due_in_s=7.0) == 0.5


async def test_a_quiet_relay_sleeps_to_its_cap_and_the_doorbell_wakes_it_at_once() -> None:
    passes: list[float] = []
    loop = asyncio.get_running_loop()

    async def relay(space_id: UUID) -> Pass:
        passes.append(loop.time())
        return Pass(0, None)

    class Doorbell:
        def __init__(self) -> None:
            self.rung: set[UUID] = set()

        async def ring(self, space_id: UUID, at: datetime) -> None:
            self.rung.add(space_id)

        async def due(self, now: datetime) -> list[UUID]:
            rung, self.rung = sorted(self.rung), set()
            return rung

    spec = LoopSpec("relay", 30.0, 60.0, relay, wakes=True)
    supervisor = SpaceLoopSupervisor(Spaces(IDLE), [spec])
    doorbell, stop = Doorbell(), asyncio.Event()
    listener = asyncio.create_task(supervisor.listen(doorbell, stop, every_s=0.01))
    await supervisor.reconcile()
    await asyncio.sleep(0.1)
    assert len(passes) == 1  # then a wait of 60 s
    await doorbell.ring(IDLE, datetime.now(UTC))
    await asyncio.sleep(0.1)
    assert len(passes) == 2  # woken at once
    stop.set()
    await supervisor.run(stop)
    await listener


async def test_a_failing_doorbell_leaves_the_relays_on_their_own_schedule() -> None:
    class Down:
        async def due(self, now: datetime) -> list[UUID]:
            raise ConnectionError("state store down")

    supervisor = SpaceLoopSupervisor(Spaces(), [])
    stop = asyncio.Event()
    listener = asyncio.create_task(supervisor.listen(Down(), stop, every_s=0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    stop.set()
    await listener


async def test_a_remembered_step_asks_again_only_when_its_marker_moves() -> None:
    calls: list[UUID] = []
    marks = {BUSY: "rules-1", IDLE: None}

    async def watch(space_id: UUID) -> int:
        calls.append(space_id)
        return 0

    step = Remembered(watch, marks.__getitem__)
    for _ in range(3):
        assert await step(BUSY) == 0
        assert await step(IDLE) == 0
    assert calls == [BUSY, IDLE, IDLE, IDLE]  # no marker (before the night hour): every pass asks
    marks[BUSY] = "rules-2"
    await step(BUSY)
    assert calls[-1] == BUSY


async def test_a_step_that_fails_is_not_remembered() -> None:
    calls = 0

    async def watch(space_id: UUID) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Unavailable("restoring", retry_after_s=5)
        return 1

    step = Remembered(watch, lambda _: 7)
    with pytest.raises(Unavailable):
        await step(IDLE)
    assert await step(IDLE) == 1
    assert await step(IDLE) == 0
    assert calls == 2


def test_the_night_marker_is_the_local_day_after_the_hour() -> None:
    before = datetime(2026, 9, 28, 5, 59, tzinfo=UTC)  # 02:59 in São Paulo
    after = datetime(2026, 9, 28, 6, 1, tzinfo=UTC)
    assert night_day(before, "America/Sao_Paulo", 3) is None
    assert night_day(after, "America/Sao_Paulo", 3) == datetime(2026, 9, 28).toordinal()
