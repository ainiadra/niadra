"""The notification feed (7.5; 24.1.1, pull first): every event a webhook carries, once its transaction
committed, numbered in commit order, read from a warm copy with no statement, and from the table when the
copy cannot answer."""

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from niadra.adapters.inbound.http import turns
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.memory.fakes import StaticConfig
from niadra.adapters.memory.notifications import InMemoryFeedCache
from niadra.app.notifications import NotificationsService
from niadra.app.notify import enqueue_event, subscribes
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.domain.common.errors import InvalidInput, Unauthenticated
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.notify.feed import FeedEvent, contiguous, decode_seq
from niadra.domain.policy.rules import ItemAttrs
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature, Scope
from niadra.ports.infra import CacheUnavailable
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config


def _config(*features: Feature) -> SpaceConfig:
    return sample_config().model_copy(update={"features": FeatureSettings(enabled=frozenset(features))})


def _outbox(cell: Cell) -> list[Task]:
    return list(cell.uow.store.module("pipeline").get("outbox", []))


async def _announce(cell: Cell, config: SpaceConfig, event_type: str, **extra: Any) -> None:
    async with cell.uow(SPACE_ID) as uow:
        await enqueue_event(uow, config, event_type, None, {"issue_id": "i-1"}, **extra)


def test_numbers_follow_without_a_gap_and_a_cursor_is_a_number() -> None:
    events = [FeedEvent(s, {"id": str(s)}) for s in (4, 2, 3, 6)]
    assert [e.seq for e in contiguous(1, events)] == [2, 3, 4], "6 waits for 5"
    assert contiguous(2, [FeedEvent(4, {})]) == []
    assert (decode_seq(None), decode_seq("42")) == (0, 42)
    for bad in ("-1", "x", "9" * 19):
        with pytest.raises(InvalidInput):
            decode_seq(bad)


async def test_an_event_joins_the_feed_only_where_it_is_on_and_only_when_every_source_may_read_it() -> None:
    cell = Cell()
    off, on = _config(), _config(Feature.NOTIFICATIONS)
    assert not subscribes(off, ("data_issue.opened",))
    assert subscribes(on, ("data_issue.opened",)), "the feed takes what no endpoint subscribes to"

    await _announce(cell, off, "data_issue.opened")
    await _announce(cell, on, "data_issue.opened")
    await _announce(cell, on, "trigger.fired", endpoint_ids=["crm"])
    await _announce(cell, on, "fact.added", item=ItemAttrs(kind="fact", category="preference"))
    await _announce(cell, on, "trait.added", purposes=frozenset({"billing"}))

    [task] = [t for t in _outbox(cell) if t.name == TaskName.APPEND_NOTIFICATION]
    body = task.payload["body"]
    assert (task.payload["type"], body["type"], body["id"]) == (
        "data_issue.opened",
        "data_issue.opened",
        task.payload["event_id"],
    )
    assert body["data"] == {"issue_id": "i-1"}
    assert set(body) == {"id", "type", "created_at", "tenant", "subject", "data", "links"}, "the webhook body"


class DownCache(InMemoryFeedCache):
    down = False

    async def since(self, space_id: Any, after: int, limit: int) -> list[FeedEvent] | None:
        if self.down:
            raise CacheUnavailable("cache since failed")
        return await super().since(space_id, after, limit)


@dataclass
class Feed:
    cell: Cell
    cache: DownCache
    service: NotificationsService

    async def announce(self, n: int) -> None:
        config = _config(Feature.NOTIFICATIONS)
        for i in range(n):
            async with self.cell.uow(SPACE_ID) as uow:
                await enqueue_event(uow, config, "data_issue.opened", None, {"issue_id": f"i-{i}"})
        for task in [t for t in _outbox(self.cell) if t.name == TaskName.APPEND_NOTIFICATION]:
            await self.service.append(task)
            await self.service.append(task)  # a redelivered task appends nothing more
        self.cell.uow.store.module("pipeline")["outbox"] = []

    async def page(self, cursor: str | None, limit: int = 100) -> Any:
        return await self.service.page(
            principal(WHATSAPP_SOURCE, _config(Feature.NOTIFICATIONS)), cursor, limit
        )


@pytest.fixture
def feed() -> Feed:
    cell = Cell()
    cache = DownCache()
    return Feed(cell, cache, NotificationsService(cell.uow, cache))


async def test_polls_read_in_commit_order_from_the_warm_copy_and_the_table_when_it_cannot_answer(
    feed: Feed,
) -> None:
    await feed.announce(3)
    first = await feed.page(None, limit=2)
    rest = await feed.page(first.next_cursor)
    idle = await feed.page(rest.next_cursor)
    assert [i.data["issue_id"] for i in first.items + rest.items] == ["i-0", "i-1", "i-2"]
    assert (first.next_cursor, rest.next_cursor, idle.next_cursor, idle.items) == ("2", "3", "3", [])

    feed.cache.events.clear()
    feed.cache.heads.clear()
    cold = await feed.page("1")
    assert [i.data["issue_id"] for i in cold.items] == ["i-1", "i-2"], "the table answers an evicted copy"
    assert feed.cache.heads[SPACE_ID] == 3, "and warms it"
    assert [e.seq for e in await feed.cache.since(SPACE_ID, 1, 10) or []] == [2, 3]

    feed.cache.down = True
    assert [i.data["issue_id"] for i in (await feed.page("2")).items] == ["i-2"], "a cache outage is a read"


async def test_the_feed_route_takes_any_key_of_a_space_that_turned_the_feed_on(feed: Feed) -> None:
    await feed.announce(1)

    class Edge:
        async def authenticate_key(self, token: str) -> SourcePrincipal:
            if token != "nia_sk_track":
                raise Unauthenticated("unknown key")
            return principal(WHATSAPP_SOURCE, config.space(SPACE_ID), scopes=frozenset({Scope.TRACK}))

        async def check_rate(self, principal: Any, path: str) -> None:
            return None

    @dataclass
    class Container:
        edge: Edge
        config: StaticConfig
        notifications: NotificationsService

    headers = {"authorization": "Bearer nia_sk_track"}
    for features, expected in (((Feature.NOTIFICATIONS,), 200), ((Feature.TURNS,), 404)):
        config = StaticConfig(_config(*features))
        app = create_app("niadra-test", Container(Edge(), config, feed.service), (turns.feed_router,))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://cell"
        ) as client:
            answer = await client.get("/v1/notifications", headers=headers)
            bad = await client.get("/v1/notifications", params={"cursor": "x"}, headers=headers)
            anonymous = await client.get("/v1/notifications")
        assert answer.status_code == expected
        assert anonymous.status_code == 401
        if expected == 200:
            assert [i["type"] for i in answer.json()["items"]] == ["data_issue.opened"]
            assert answer.json()["next_cursor"] == "1"
            assert bad.status_code == 422
