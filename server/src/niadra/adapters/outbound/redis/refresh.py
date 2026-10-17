"""The refresh requests waiting for the database, on the state store (24.3.5), where nothing is evicted.

Per space, under one hash tag: a limiter per object and reason (`rl:`) that says whether the last request of
its period was admitted, the queue of admitted requests (`rq:`) a per-space loop writes in one statement, and
per object with a request outstanding (`ro:`) the reservation its refresh settles; the answers pushes brought
for the consolidator (`ra:`), and the mark of a resolver worker that took requests lately (`rw:`).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated


def _tag(space_id: UUID) -> str:
    return f"{{{space_id}}}"


class RedisRefreshWants:
    def __init__(self, redis: Redis, *, store: str = "state") -> None:
        self._redis = redis
        self._store = store

    async def claim(self, space_id: UUID, key: str, ttl_s: int) -> str | None:
        name = f"rl:{_tag(space_id)}:{key}"
        async with translated(self._store, "refresh_claim"):
            if await self._redis.set(name, b"pending", ex=ttl_s, nx=True):
                return None
            found = await self._redis.get(name)
        return _text(found) if found is not None else None

    async def settle_claim(self, space_id: UUID, key: str, outcome: str, ttl_s: int) -> None:
        async with translated(self._store, "refresh_settle_claim"):
            await self._redis.set(f"rl:{_tag(space_id)}:{key}", outcome.encode(), ex=ttl_s)

    async def enqueue(
        self, space_id: UUID, requests: Sequence[bytes], pending: Mapping[str, bytes], ttl_s: int
    ) -> None:
        if not requests:
            return
        async with translated(self._store, "refresh_enqueue"), self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(f"rq:{_tag(space_id)}", *requests)
            for object_id, reservation in pending.items():
                pipe.set(f"ro:{_tag(space_id)}:{object_id}", reservation, ex=ttl_s)
            await pipe.execute()

    async def take(self, space_id: UUID, limit: int) -> list[bytes]:
        async with translated(self._store, "refresh_take"):
            found: Any = await self._redis.lpop(f"rq:{_tag(space_id)}", limit)
        return [item if isinstance(item, bytes) else str(item).encode() for item in found or ()]

    async def requeue(self, space_id: UUID, requests: Sequence[bytes]) -> None:
        if requests:
            async with translated(self._store, "refresh_requeue"):
                await self._redis.lpush(f"rq:{_tag(space_id)}", *reversed(requests))

    async def outstanding(self, space_id: UUID, object_ids: Sequence[str]) -> dict[str, bytes]:
        if not object_ids:
            return {}
        async with (
            translated(self._store, "refresh_outstanding"),
            self._redis.pipeline(transaction=False) as pipe,
        ):
            for object_id in object_ids:
                pipe.getdel(f"ro:{_tag(space_id)}:{object_id}")
            found = await pipe.execute()
        return {o: bytes(r) for o, r in zip(object_ids, found, strict=True) if r is not None}

    async def answered(self, space_id: UUID, answers: Sequence[bytes], ttl_s: int) -> None:
        if not answers:
            return
        name = f"ra:{_tag(space_id)}"
        async with (
            translated(self._store, "refresh_answered"),
            self._redis.pipeline(transaction=True) as pipe,
        ):
            pipe.rpush(name, *answers)
            pipe.expire(name, ttl_s)
            await pipe.execute()

    async def take_answered(self, space_id: UUID, limit: int) -> list[bytes]:
        async with translated(self._store, "refresh_take_answered"):
            found: Any = await self._redis.lpop(f"ra:{_tag(space_id)}", limit)
        return [item if isinstance(item, bytes) else str(item).encode() for item in found or ()]

    async def requeue_answered(self, space_id: UUID, answers: Sequence[bytes]) -> None:
        if answers:
            async with translated(self._store, "refresh_requeue_answered"):
                await self._redis.lpush(f"ra:{_tag(space_id)}", *reversed(answers))

    async def worker_seen(self, space_id: UUID, ttl_s: int) -> None:
        async with translated(self._store, "refresh_worker_seen"):
            await self._redis.set(f"rw:{_tag(space_id)}", b"1", ex=ttl_s)

    async def has_worker(self, space_id: UUID) -> bool:
        async with translated(self._store, "refresh_has_worker"):
            return bool(await self._redis.exists(f"rw:{_tag(space_id)}"))


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
