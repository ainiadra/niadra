"""Redis-compatible adapters (Valkey in production): the hot layer and the key-value cache."""

from redis.asyncio import Redis


def connect(url: str) -> Redis:
    # Bytes in, bytes out: sealed envelopes and packs are binary.
    return Redis.from_url(url, decode_responses=False, health_check_interval=30, socket_timeout=1.0)
