import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from niadra_models.batching import MicroBatcher
from niadra_models.metrics import Metrics


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.threads: set[str] = set()

    def __call__(self, items: list[str]) -> list[str]:
        self.calls.append(items)
        self.threads.add(threading.current_thread().name)
        return [item.upper() for item in items]


@pytest.fixture
def executor() -> Any:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-models") as pool:
        yield pool


async def test_concurrent_requests_are_grouped_into_one_call(executor: ThreadPoolExecutor) -> None:
    run = Recorder()
    batcher = MicroBatcher("m", run, executor, Metrics(), max_wait_s=0.02, max_items=64)
    try:
        results = await asyncio.gather(*(batcher.submit([f"a{i}", f"b{i}"]) for i in range(10)))
    finally:
        await batcher.close()
    assert results == [[f"A{i}", f"B{i}"] for i in range(10)]
    assert len(run.calls) == 1
    assert len(run.calls[0]) == 20


async def test_model_code_runs_off_the_event_loop(executor: ThreadPoolExecutor) -> None:
    run = Recorder()
    batcher = MicroBatcher("m", run, executor, Metrics())
    try:
        await batcher.submit(["x"])
    finally:
        await batcher.close()
    assert all(name.startswith("test-models") for name in run.threads)


async def test_batch_is_cut_at_max_items(executor: ThreadPoolExecutor) -> None:
    run = Recorder()
    batcher = MicroBatcher("m", run, executor, Metrics(), max_wait_s=0.05, max_items=4)
    try:
        await asyncio.gather(*(batcher.submit([str(i)]) for i in range(10)))
    finally:
        await batcher.close()
    assert [len(c) for c in run.calls] == [4, 4, 2]


async def test_requests_further_apart_than_the_wait_run_separately(executor: ThreadPoolExecutor) -> None:
    run = Recorder()
    batcher = MicroBatcher("m", run, executor, Metrics(), max_wait_s=0.001)
    try:
        await batcher.submit(["a"])
        await batcher.submit(["b"])
    finally:
        await batcher.close()
    assert run.calls == [["a"], ["b"]]


async def test_a_model_error_reaches_every_caller_and_the_worker_survives(
    executor: ThreadPoolExecutor,
) -> None:
    def explode(items: list[str]) -> list[str]:
        if "bad" in items:
            raise RuntimeError("model failed")
        return items

    batcher = MicroBatcher("m", explode, executor, Metrics(), max_wait_s=0.02)
    try:
        outcomes = await asyncio.gather(
            batcher.submit(["ok"]), batcher.submit(["bad"]), return_exceptions=True
        )
        assert all(isinstance(o, RuntimeError) for o in outcomes)
        assert await batcher.submit(["again"]) == ["again"]
    finally:
        await batcher.close()


async def test_a_cancelled_caller_does_not_break_the_batch(executor: ThreadPoolExecutor) -> None:
    release = threading.Event()

    def slow(items: list[str]) -> list[str]:
        release.wait(1)
        return items

    batcher = MicroBatcher("m", slow, executor, Metrics(), max_wait_s=0.01)
    try:
        leaving = asyncio.create_task(batcher.submit(["leaves"]))
        staying = asyncio.create_task(batcher.submit(["stays"]))
        await asyncio.sleep(0.03)
        leaving.cancel()
        release.set()
        assert await staying == ["stays"]
    finally:
        await batcher.close()


async def test_empty_requests_skip_the_queue(executor: ThreadPoolExecutor) -> None:
    run = Recorder()
    batcher = MicroBatcher("m", run, executor, Metrics())
    assert await batcher.submit([]) == []
    assert run.calls == []


async def test_batch_sizes_are_measured(executor: ThreadPoolExecutor) -> None:
    metrics = Metrics()
    batcher = MicroBatcher("embed", Recorder(), executor, metrics, max_wait_s=0.02)
    try:
        await asyncio.gather(batcher.submit(["a"]), batcher.submit(["b", "c"]))
    finally:
        await batcher.close()
    text = metrics.render()
    assert 'niadra_models_batch_items_count{model="embed"} 1' in text
    assert 'niadra_models_batch_items_sum{model="embed"} 3' in text
