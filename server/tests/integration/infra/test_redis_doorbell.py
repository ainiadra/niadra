"""The doorbell on a real Redis-compatible store (06, 6.3): the earliest ring wins, and taking the due
spaces takes each one once."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.doorbell import KEY, RedisDoorbell

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")

pytestmark = pytest.mark.skipif(not REDIS, reason="needs NIADRA_TEST_REDIS")


async def test_the_earliest_ring_wins_and_a_due_space_is_taken_once() -> None:
    redis = connect(REDIS)
    await redis.delete(KEY)
    doorbell = RedisDoorbell(redis)
    now = datetime.now(UTC)
    soon, later, far = uuid4(), uuid4(), uuid4()
    try:
        await doorbell.ring(soon, now + timedelta(seconds=30))
        await doorbell.ring(soon, now - timedelta(seconds=1))  # earlier: moves it forward
        await doorbell.ring(later, now + timedelta(seconds=5))
        await doorbell.ring(later, now + timedelta(seconds=60))  # later: ignored
        await doorbell.ring(far, now + timedelta(hours=1))
        assert await doorbell.due(now) == [soon]
        assert await doorbell.due(now) == []
        assert await doorbell.due(now + timedelta(seconds=6)) == [later]
        assert await doorbell.due(now + timedelta(hours=2)) == [far]
    finally:
        await redis.delete(KEY)
        await redis.aclose()
