"""The workers publish generations and T0 packs under the cell's cache epoch, the one the read process looks
them up under: a worker asks the cell for it before it consumes, and again with the read process's period.
Until 28/09/2026 only the read process asked, so the workers published under 0 while every read asked for
the cell's 1, and no read ever found a generation or a T0 pack."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from niadra.domain.serve.cache_keys import CacheEpoch
from niadra.entrypoints import roles


class _Runtime:
    epoch_at_start: int | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, stop: asyncio.Event) -> None:
        _Runtime.epoch_at_start = EPOCH.value
        stop.set()


EPOCH = CacheEpoch()


async def test_a_worker_consumes_only_once_it_knows_the_cells_cache_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(roles, "WorkerRuntime", _Runtime)
    monkeypatch.setattr(roles, "registry", lambda c, maps: {})
    monkeypatch.setattr(roles, "CACHE_EPOCH_EVERY_S", 0.01)
    answers = iter([ConnectionError("cell database restarting"), 3])

    async def refresh() -> int:
        answer = next(answers, 3)
        if isinstance(answer, Exception):
            raise answer
        EPOCH.value = answer
        return answer

    c = SimpleNamespace(
        serve=SimpleNamespace(refresh_cache_epoch=refresh), pipeline=None, config=SimpleNamespace(run=None)
    )
    infra = SimpleNamespace(bus=None, registry=SimpleNamespace(run=None), state=None)
    consume, epoch_loop, *_ = roles.worker_loops(c, infra, ["realtime"])  # type: ignore[arg-type]
    stop = asyncio.Event()
    await asyncio.wait_for(consume(stop), 2)
    assert _Runtime.epoch_at_start == 3
    stop.clear()
    EPOCH.value = 0
    loop = asyncio.create_task(epoch_loop(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await loop
    assert EPOCH.value == 3
