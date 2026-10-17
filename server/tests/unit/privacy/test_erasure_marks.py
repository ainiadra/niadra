"""The erasure marks every fence shares: when each mark was erased, exact to the microsecond, the newer time
winning, and the one rule that drops only what predates the erasure."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis

from niadra.adapters.memory.erasure import InMemoryErasureMarks
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.domain.privacy.fences import erased_before

AT = datetime(2026, 9, 29, 14, 2, 20, 123456, tzinfo=UTC)


async def test_the_state_store_keeps_each_marks_erasure_time_and_the_newer_one_wins() -> None:
    redis = fakeredis.FakeAsyncRedis()
    marks = RedisErasureMarks(redis)
    space, other = uuid4(), uuid4()
    await marks.fence(space, [b"customer", b"conversation"], AT, 3600)
    assert await marks.fenced(space, [b"customer", b"stranger", b"customer"]) == {b"customer": AT}
    assert await marks.fenced(other, [b"customer"]) == {}
    await marks.fence(space, [b"customer"], AT - timedelta(minutes=1), 3600)
    assert (await marks.fenced(space, [b"customer"]))[b"customer"] == AT
    later = AT + timedelta(hours=2)
    await marks.fence(space, [b"customer"], later, 3600)
    assert (await marks.fenced(space, [b"customer"]))[b"customer"] == later
    assert 0 < await redis.pttl(f"erased:{space}:{b'customer'.hex()}") <= 3_600_000


async def test_the_memory_marks_behave_the_same() -> None:
    marks = InMemoryErasureMarks()
    space = uuid4()
    await marks.fence(space, [b"customer"], AT, 3600)
    await marks.fence(space, [b"customer"], AT - timedelta(minutes=1), 3600)
    assert await marks.fenced(space, [b"customer", b"stranger"]) == {b"customer": AT}


def test_only_what_was_made_at_or_before_the_erasure_is_dropped() -> None:
    fenced = {b"customer": AT}
    assert erased_before(AT - timedelta(microseconds=1), b"customer", fenced)
    assert erased_before(AT, b"customer", fenced)
    assert not erased_before(AT + timedelta(microseconds=1), b"customer", fenced)
    assert not erased_before(AT, b"stranger", fenced)
    assert not erased_before(AT, None, fenced)
