"""Store errors cross the port as `CacheUnavailable`, counted by store and operation.

The redis client raises its own hierarchy (`ConnectionError`, `TimeoutError`, `OutOfMemoryError`...),
none of which is Python's `ConnectionError`; the read path's fallbacks catch the port's error only.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from prometheus_client import Counter
from redis.exceptions import RedisError

from niadra.ports.infra import CacheUnavailable

CACHE_FAILURES = Counter(
    "niadra_cache_failures_total", "Cache and hot-layer commands that failed", ["store", "operation"]
)


@asynccontextmanager
async def translated(store: str, operation: str) -> AsyncIterator[None]:
    try:
        yield
    except RedisError as exc:
        CACHE_FAILURES.labels(store, operation).inc()
        raise CacheUnavailable(f"{store} {operation} failed: {type(exc).__name__}") from exc
