"""Dynamic batching per model: requests that arrive within a few milliseconds run as one call (06 4.5).

One worker task per model drains an asyncio queue, waits up to `max_wait_s` for more requests (or until
`max_items` are gathered), runs the model once in the thread pool and hands each caller its slice.
The event loop never runs model code, so health checks and metrics answer while a batch computes.
"""

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field

from niadra_models.metrics import Metrics


@dataclass(slots=True)
class _Request[T, R]:
    items: Sequence[T]
    done: asyncio.Future[list[R]] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class MicroBatcher[T, R]:
    def __init__(
        self,
        name: str,
        run: Callable[[list[T]], Sequence[R]],
        executor: Executor,
        metrics: Metrics,
        max_wait_s: float = 0.005,
        max_items: int = 64,
    ) -> None:
        self.name = name
        self._run = run
        self._executor = executor
        self._metrics = metrics
        self._max_wait_s = max_wait_s
        self._max_items = max_items
        self._queue: asyncio.Queue[_Request[T, R]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def submit(self, items: Sequence[T]) -> list[R]:
        if not items:
            return []
        # Started on first use so the worker lives on the loop that serves the requests.
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._serve(), name=f"batcher:{self.name}")
        request: _Request[T, R] = _Request(items)
        await self._queue.put(request)
        return await request.done

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _serve(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = await self._gather()
            flat = [item for request in batch for item in request.items]
            self._metrics.batch(self.name, len(flat))
            try:
                results = await loop.run_in_executor(self._executor, self._run, flat)
            except Exception as error:
                for request in batch:
                    if not request.done.done():
                        request.done.set_exception(error)
                continue
            offset = 0
            for request in batch:
                size = len(request.items)
                # A caller that disconnected cancelled its future; the others still get their results.
                if not request.done.done():
                    request.done.set_result(list(results[offset : offset + size]))
                offset += size

    async def _gather(self) -> list[_Request[T, R]]:
        loop = asyncio.get_running_loop()
        batch = [await self._queue.get()]
        count = len(batch[0].items)
        deadline = loop.time() + self._max_wait_s
        while count < self._max_items:
            remaining = deadline - loop.time()
            try:
                if remaining > 0:
                    request = await asyncio.wait_for(self._queue.get(), remaining)
                else:
                    request = self._queue.get_nowait()
            except (TimeoutError, asyncio.QueueEmpty):
                break
            batch.append(request)
            count += len(request.items)
        return batch
