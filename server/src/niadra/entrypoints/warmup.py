"""What a new HTTP pod does before it takes traffic: a bounded warm-up, after which `/readyz` answers and
the Service sends it requests (`/healthz`, the liveness probe, answers from the start).

A `read` pod that took traffic as soon as its server listened paid, on its first requests, for the space
databases' pools, the Valkey connections, the configuration snapshot, the privacy feed and the question
encoder's first call. On the benchmark cell of 10/10/2026 a replica that started in the middle of a burst
took the history search's p95 from 70 to 330 ms. The warm-up opens all of that first. It never holds a pod
back for long: past `WARMUP_TIMEOUT_S` the pod is ready anyway and says which step it was in, and a step
that fails is logged and skipped, since the request it would have served retries the same way.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from niadra.entrypoints.cell import CellContainer, Infra
    from niadra.ports.infra import QueryEncoder

log = logging.getLogger("niadra.warmup")

WARMUP_TIMEOUT_S = 20.0
"""Well inside the liveness probe's 30 s before its first check: a slow warm-up delays readiness only."""
WARM_SPACES = 64
"""Space databases whose pool the warm-up opens, at most: the ones a cell serves today, with room."""
WARM_PARALLEL = 4
"""Pools opened at once: a new pod must not take every PgBouncer slot while the warm ones serve."""

Step = tuple[str, Callable[[], Awaitable[object]]]


async def warm_up(
    steps: Sequence[Step], ready: asyncio.Event, *, timeout_s: float = WARMUP_TIMEOUT_S
) -> None:
    """Runs `steps` in order within `timeout_s`, then sets `ready`, whatever happened."""
    started = time.monotonic()
    current = ""
    try:
        async with asyncio.timeout(timeout_s):
            for current, step in steps:
                try:
                    await step()
                except Exception:
                    log.warning("warm-up step %s failed; the pod goes on", current, exc_info=True)
            current = ""
    except TimeoutError:
        log.warning(
            "warm-up past its bound in step %s; the pod is ready anyway",
            current,
            extra={"duration_ms": round((time.monotonic() - started) * 1000)},
        )
    finally:
        ready.set()
    if not current:
        log.info("warmed up", extra={"duration_ms": round((time.monotonic() - started) * 1000)})


async def open_space_pools[K](
    spaces: Sequence[K],
    open_one: Callable[[K], Awaitable[object]],
    *,
    limit: int = WARM_SPACES,
    parallel: int = WARM_PARALLEL,
) -> int:
    """Opens the pool of each of the first `limit` `spaces`, `parallel` at a time; a space whose database
    does not answer is skipped. Returns how many opened."""
    gate = asyncio.Semaphore(parallel)
    opened = 0

    async def one(space: K) -> None:
        nonlocal opened
        async with gate:
            with contextlib.suppress(Exception):
                await open_one(space)
                opened += 1

    await asyncio.gather(*(one(space) for space in list(spaces)[:limit]))
    return opened


QUESTION = "aquecimento da leitura"
"""What the warm-up encodes: no customer's text, only the encoder's first call."""


def http_steps(role: str, c: "CellContainer", infra: "Infra", encoder: "QueryEncoder | None") -> list[Step]:
    """What a pod of `role` opens before it is ready. Every HTTP deployable: the configuration snapshot, the
    space directory and both Valkey stores. `read` also: the cache epoch, the privacy feed, a pool on each
    space database it serves and the question encoder."""
    from sqlalchemy import text

    async def space_pool(space_id: "UUID") -> None:
        async with infra.registry.lease(space_id, "app") as engine, engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def space_pools() -> None:
        await open_space_pools(infra.registry.serving_spaces(), space_pool)

    async def valkey() -> None:
        await infra.cache.client.ping()
        await infra.state_redis.ping()

    steps: list[Step] = [
        ("config", c.config.refresh),
        ("space directory", infra.registry.refresh),
        ("valkey", valkey),
    ]
    if role == "read":
        steps += [
            ("cache epoch", c.serve.refresh_cache_epoch),
            ("privacy feed", c.serve.refresh_privacy),
            ("space pools", space_pools),
        ]
        if encoder is not None:
            steps.append(("question encoder", lambda: encoder.embed_query(QUESTION)))
    return steps
