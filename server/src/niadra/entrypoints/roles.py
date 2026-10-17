"""What a cell deployable runs once `serve` picked its role: the HTTP deployables with their loops and the
task consumers here, the cell's single-instance loops in `loops`. The control plane and the setup hook never
import this.
"""

import asyncio
import contextlib
import gc
import logging
import os
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any

import uvloop

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.workers.runtime import (
    POOLS,
    WorkerRuntime,
    registry,
    stop_on_signals,
)
from niadra.adapters.observability import logging as json_logging
from niadra.adapters.observability import metrics
from niadra.app.lease_claims import LeaseClaims
from niadra.app.prewarm import TICK_S as PREWARM_TICK_S
from niadra.entrypoints.cell import CellContainer, Infra, build_container, open_infra
from niadra.entrypoints.http_server import serve_http
from niadra.entrypoints.serve import routers, worker_modules
from niadra.entrypoints.settings import Settings
from niadra.entrypoints.warmup import http_steps, warm_up

log = logging.getLogger("niadra.serve")

READ_REFRESH_EVERY_S = 2.0
CACHE_EPOCH_EVERY_S = 5.0
"""How often the processes that read or write the epoch-keyed cache (read, workers) ask the cell for it."""
RECEIPT_HEARTBEAT_EVERY_S = 5.0
WORKER_PREFETCH = int(os.environ.get("NIADRA_WORKER_PREFETCH", "16"))
# No space holds more than this share of a pool's slots, so one tenant's burst never stalls the rest.
WORKER_SPACE_CAP = int(os.environ.get("NIADRA_WORKER_SPACE_CAP", str(WORKER_PREFETCH * 3 // 4)))

Loop = Callable[[asyncio.Event], Coroutine[Any, Any, None]]


async def every(stop: asyncio.Event, seconds: float, step: Callable[[], Awaitable[Any]], name: str) -> None:
    """Runs `step` until `stop`; a failing step is logged and retried on the next tick (11)."""
    while not stop.is_set():
        try:
            await step()
        except Exception:
            log.exception("background step failed", extra={"loop": name})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), seconds)


def http_loops(role: str, c: CellContainer, infra: Infra) -> list[Loop]:
    # Every HTTP deployable may record governance actions; an idle producer still moves the watermark.
    loops: list[Loop] = [
        c.config.run,
        infra.registry.run,
        lambda stop: every(stop, RECEIPT_HEARTBEAT_EVERY_S, c.admin_audit.heartbeat, "admin_heartbeat"),
    ]
    if role == "read":
        loops += [
            lambda stop: every(stop, CACHE_EPOCH_EVERY_S, c.serve.refresh_cache_epoch, "cache_epoch"),
            lambda stop: every(stop, READ_REFRESH_EVERY_S, c.serve.refresh_privacy, "privacy_feed"),
            lambda stop: every(stop, 1.0, c.serve.flush_receipts, "receipts"),
            lambda stop: every(stop, RECEIPT_HEARTBEAT_EVERY_S, c.serve.heartbeat, "receipt_heartbeat"),
            # Watched customers' voice context follows their new generations (13, 6.4).
            lambda stop: every(stop, PREWARM_TICK_S, c.serve.prewarm.tick, "prewarm"),
        ]
    return loops


async def epoch_known(c: CellContainer, stop: asyncio.Event) -> None:
    """Returns once the process knows the cell's cache epoch, or `stop` is set.

    What a worker or a loop publishes (generations, T0 packs, the typed objects' copies) is keyed by the epoch
    the read process looks it up under: a process that published under another epoch would leave every
    context and history read to the database."""
    while not stop.is_set():
        try:
            await c.serve.refresh_cache_epoch()
            return
        except Exception:
            log.warning("cache epoch unavailable; the process waits for it before publishing")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), CACHE_EPOCH_EVERY_S)


def worker_loops(c: CellContainer, infra: Infra, pools: Sequence[str]) -> list[Loop]:
    handlers = registry(c, [m.handlers for m in worker_modules()])
    queues = [queue.value for pool in pools for queue in POOLS[pool]]
    runtime = WorkerRuntime(
        infra.bus,
        c.pipeline,
        handlers,
        queues=queues,
        prefetch=WORKER_PREFETCH,
        per_space=WORKER_SPACE_CAP,
        databases=infra.registry,
        claims=LeaseClaims(infra.state),
    )

    async def consume(stop: asyncio.Event) -> None:
        await epoch_known(c, stop)
        if not stop.is_set():
            await runtime.run(stop)

    return [
        consume,
        lambda stop: every(stop, CACHE_EPOCH_EVERY_S, c.serve.refresh_cache_epoch, "cache_epoch"),
        c.config.run,
        infra.registry.run,
    ]


async def run_role(role: str, pools: Sequence[str]) -> None:
    settings = Settings.from_env()
    json_logging.configure(settings.log_level)
    stop = asyncio.Event()
    stop_on_signals(stop)
    async with open_infra(settings, role) as infra, contextlib.AsyncExitStack() as stack:
        if role == "read":
            metrics.count_statements()
        container = build_container(infra)
        # The HTTP deployables answer /readyz only once warmed up (`entrypoints.warmup`); workers and the
        # loops take no traffic from a Service and are ready at once.
        http = role not in ("workers", "loops")
        ready = asyncio.Event() if http else None
        app = create_app(
            f"niadra-{role}", container, routers(role), settings.allowed_origins, compress=True, ready=ready
        )
        if role == "workers":
            loops = worker_loops(container, infra, pools)
        elif role == "loops":
            from niadra.entrypoints.loops import loops_role

            loops = loops_role(container, infra, stack)
        else:
            loops = http_loops(role, container, infra)
        # What is built so far lives as long as the process: frozen out of the collector's reach, a full
        # collection no longer walks it, a pause every read in flight would otherwise wait for.
        gc.collect()
        gc.freeze()
        jobs = [asyncio.create_task(loop(stop)) for loop in loops]
        if ready is not None:
            question = infra.models if settings.semantic_channel == "models" else None
            jobs.append(asyncio.create_task(warm_up(http_steps(role, container, infra, question), ready)))
        try:
            await serve_http(app, stop)
        finally:
            stop.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            with contextlib.suppress(Exception):
                await container.ingest.close()
            with contextlib.suppress(Exception):
                await container.admin_audit.heartbeat(final=True)
            if role == "read":
                with contextlib.suppress(Exception):
                    await container.serve.flush_receipts()
                    await container.serve.heartbeat(final=True)


def main(role: str, pools: Sequence[str]) -> None:
    # uvloop: every read is a handful of round trips to Valkey, the broker and the proxy, and the loop's own
    # cost per round trip is what is left of a warm read.
    asyncio.run(run_role(role, pools), loop_factory=uvloop.new_event_loop)
