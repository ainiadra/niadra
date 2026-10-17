"""Speculative work for the read that answers a turn (11, 3.5).

A voice adapter sends the customer's partial transcript while they still speak (`POST /v1/context/prefetch`).
The read process prepares that turn in the background: the customer's retrieval entry, the question's
encoding and the slots. The read that follows with the same question takes the prepared slots when every
input they came from is unchanged, and otherwise computes them as always, on a warm cache.

Two bounds keep the speculation from costing the reads it serves:

- `PrefetchQueue`: per conversation one job in flight and at most one waiting, the newest (a newer partial
  replaces the waiting one); at most `concurrency` jobs at once in the process and `max_threads`
  conversations tracked; each job within `deadline_s`.
- `PreparedTurns`: the newest prepared turn per conversation, reader and view, least recently used first
  out, each for `ttl_s`.

Nothing here writes a receipt, pins a pack or moves a watermark: only a real read does.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

PREFETCH_CONCURRENCY = 4
"""Jobs at once per process. Each opens one short transaction of the space's small pool, like a read."""
PREFETCH_THREADS = 1024
PREFETCH_DEADLINE_S = 2.0
"""A partial transcript arrives a second or more before the turn ends; a job still running after that
prepares a turn already read."""
PREPARED_TURNS = 512
"""Prepared turns hold the retrieval entry they were computed on; few, and short-lived, so they never keep
much memory alive past the retrieval cache's own bound."""
PREPARED_TTL_S = 60.0


@dataclass
class _Thread:
    waiting: Any = None


class PrefetchQueue:
    """Runs `run(job)` in the background, bounded per conversation (`key`) and per process."""

    def __init__(
        self,
        run: Callable[[Any], Awaitable[None]],
        *,
        concurrency: int = PREFETCH_CONCURRENCY,
        max_threads: int = PREFETCH_THREADS,
        deadline_s: float = PREFETCH_DEADLINE_S,
    ) -> None:
        self._run = run
        self._slots = asyncio.Semaphore(concurrency)
        self._max_threads = max_threads
        self._deadline_s = deadline_s
        self._threads: dict[Hashable, _Thread] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        return len(self._threads)

    def submit(self, key: Hashable, job: Any) -> bool:
        """False when the process tracks as many conversations as it may; the job is then dropped."""
        thread = self._threads.get(key)
        if thread is not None:
            thread.waiting = job  # the newest partial wins; an older one still waiting is dropped
            return True
        if len(self._threads) >= self._max_threads:
            return False
        self._threads[key] = _Thread()
        task = asyncio.create_task(self._drive(key, job), name="prefetch")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self) -> None:
        """Waits for every job queued so far (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _drive(self, key: Hashable, job: Any) -> None:
        try:
            while job is not None:
                try:
                    # The deadline counts the wait for a slot too: a job that waited that long is stale.
                    await asyncio.wait_for(self._one(job), self._deadline_s)
                except TimeoutError:
                    log.info("prefetch past its deadline")
                except Exception:
                    # A prefetch never fails anything (L10); the read computes what the job did not.
                    log.warning("prefetch failed", exc_info=True)
                thread = self._threads[key]
                job, thread.waiting = thread.waiting, None
        finally:
            self._threads.pop(key, None)

    async def _one(self, job: Any) -> None:
        async with self._slots:
            await self._run(job)


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    query: str
    inputs: tuple[Any, ...]
    """Everything the slots were computed from besides the configuration; the read compares its own."""
    config: object
    """The configuration document the slots were computed under, compared by identity."""
    slots: Any
    reusable: bool
    """False when the slots depend on the moment they were computed (time words, counts over a window):
    the read then keeps only the analysis and the warm cache, and computes the slots at its own moment."""
    at: float


class PreparedTurns:
    """The newest prepared turn per key, least recently used first out, each valid for `ttl_s`."""

    def __init__(self, max_entries: int = PREPARED_TURNS, ttl_s: float = PREPARED_TTL_S) -> None:
        self._entries: OrderedDict[Hashable, PreparedTurn] = OrderedDict()
        self._max = max_entries
        self._ttl_s = ttl_s

    def __len__(self) -> int:
        return len(self._entries)

    def put(self, key: Hashable, turn: PreparedTurn) -> None:
        self._entries.pop(key, None)
        self._entries[key] = turn
        while self._entries and (
            len(self._entries) > self._max or turn.at - next(iter(self._entries.values())).at > self._ttl_s
        ):
            self._entries.popitem(last=False)

    def get(self, key: Hashable, query: str) -> PreparedTurn | None:
        """The turn prepared for exactly `query`, while fresh."""
        turn = self._entries.get(key)
        if turn is None or turn.query != query:
            return None
        if time.monotonic() - turn.at > self._ttl_s:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return turn
