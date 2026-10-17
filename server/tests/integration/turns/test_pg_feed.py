"""The notification feed on PostgreSQL and the cache store (7.5, 24.6): appends that race commit in their
numbers' order, a poll with nothing new is no statement once warm, and a page the warm copy holds is none
either; the table answers the rest in one statement."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis
import pytest
from redis.asyncio import Redis

from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.notifications import RedisFeedCache
from niadra.app.notifications import NotificationsService
from niadra.config.models import FeatureSettings
from niadra.domain.common.ids import uuid7
from niadra.domain.notify.feed import NewFeedEvent
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.turns.test_pg_turns import counting

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
REDIS = os.environ.get("NIADRA_TEST_REDIS", "")


@pytest.fixture(params=["fakeredis", "redis"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Redis]:
    if request.param == "redis" and not REDIS:
        pytest.skip("needs NIADRA_TEST_REDIS")
    client: Redis = fakeredis.FakeAsyncRedis() if request.param == "fakeredis" else connect(REDIS)
    yield client
    await client.aclose()


def _event(i: int, at: datetime = NOW, **data: str) -> NewFeedEvent:
    event_id = uuid7()
    body = {
        "id": str(event_id),
        "type": "data_issue.opened",
        "created_at": at.isoformat(),
        "tenant": str(uuid4()),
        "data": {"issue_id": f"i-{i}", **data},
    }
    return NewFeedEvent(event_id, "data_issue.opened", at, body)


async def test_appends_that_race_are_numbered_in_commit_order_and_once(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    first, second = _event(1), _event(2)
    holding = asyncio.Event()

    async def slow() -> int | None:
        async with pg_uow(space) as uow:
            seq = await uow.feed.append(first)
            holding.set()
            await asyncio.sleep(0.3)  # the lock is held to the commit
            return seq

    async def fast() -> int | None:
        await holding.wait()
        async with pg_uow(space) as uow:
            return await uow.feed.append(second)

    assert await asyncio.gather(slow(), fast()) == [1, 2]
    async with pg_uow(space) as uow:
        assert await uow.feed.append(first) is None, "a redelivered task appends nothing"
        events, head = await uow.feed.since(0, 10)
        assert ([e.body["data"]["issue_id"] for e in events], head) == (["i-1", "i-2"], 2)
        assert await uow.feed.since(2, 10) == ([], 2)


async def test_a_warm_poll_is_no_statement_and_a_cold_one_is_one(
    pg_uow: CloningUnitOfWork, store: Redis
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    await store.delete(f"nf:{{{space}}}", f"nfh:{{{space}}}")
    config = sample_config(space).model_copy(
        update={"features": FeatureSettings(enabled=frozenset({Feature.NOTIFICATIONS}))}
    )
    feed = NotificationsService(pg_uow, RedisFeedCache(store))  # type: ignore[arg-type]
    caller = principal(WHATSAPP_SOURCE, config)
    async with pg_uow(space) as uow:  # the space's engine checks the database's stamp once, on first use
        await uow.feed.since(0, 1)

    with counting() as cold:
        empty = await feed.page(caller, None, 100)
    assert (empty.items, empty.next_cursor, len(cold)) == ([], "0", 2), "the space pin and one read"
    with counting() as warm:
        again = await feed.page(caller, "0", 100)
    assert (again.items, warm) == ([], []), "an idle poll reads nothing from the database"

    for i in range(3):
        event = _event(i)
        payload = {
            "event_id": str(event.event_id),
            "type": event.type,
            "at": NOW.isoformat(),
            "body": event.body,
        }
        await feed.append(Task(TaskName.APPEND_NOTIFICATION, space, payload))
    with counting() as warm:
        page = await feed.page(caller, "0", 2)
        rest = await feed.page(caller, page.next_cursor, 100)
        idle = await feed.page(caller, rest.next_cursor, 100)
    assert [i.data["issue_id"] for i in page.items + rest.items] == ["i-0", "i-1", "i-2"]
    assert (idle.items, idle.next_cursor, warm) == ([], "3", [])


async def test_the_feed_is_purged_after_its_week_and_erased_with_its_subject(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    handle = uuid4()
    async with pg_uow(space) as uow:
        await uow.feed.append(_event(1, NOW - timedelta(days=8)))
        await uow.feed.append(_event(2, NOW, handle=str(handle)))
        await uow.feed.append(_event(3, NOW))
        assert await uow.feed.purge_before(NOW - timedelta(days=7)) == 1
        scope = ErasureScope(uuid7(), TargetKind.HANDLE, (handle,), None, None, NOW)
        erased = await uow.feed.erase_for(scope, limit=10)
        events, head = await uow.feed.since(0, 10)
    assert [r.table for r in erased] == ["notification_feed"]
    assert ([e.seq for e in events], head) == ([3], 3)
