"""The turn counters of the context budget on the state store: one hash per conversation and
per case, `<source>:<turn>` to that turn's counts, so a turn group applied again writes the same fields."""

from collections.abc import Mapping, Sequence
from uuid import UUID

from redis.asyncio import Redis

from niadra.adapters.outbound.redis.errors import translated


def _key(space_id: UUID, key: str) -> str:
    return f"tb:{space_id}:{key}"


class RedisTurnCounters:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, space_id: UUID, counts: Mapping[str, Mapping[str, str]], ttl_s: int) -> None:
        async with translated("state", "budget_add"), self._redis.pipeline(transaction=False) as pipe:
            for key, fields in counts.items():
                if fields:
                    pipe.hset(_key(space_id, key), items=[x for pair in fields.items() for x in pair])
                    pipe.expire(_key(space_id, key), ttl_s)
            await pipe.execute()

    async def read(self, space_id: UUID, keys: Sequence[str]) -> list[dict[str, str]]:
        async with translated("state", "budget_read"), self._redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.hgetall(_key(space_id, key))
            found = await pipe.execute()
        return [{_text(k): _text(v) for k, v in row.items()} for row in found]

    async def forget(self, space_id: UUID, keys: Sequence[str]) -> None:
        if keys:
            async with translated("state", "budget_forget"):
                await self._redis.delete(*(_key(space_id, k) for k in keys))


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
