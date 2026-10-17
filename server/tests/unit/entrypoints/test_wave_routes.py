"""The agent core wave's routes (7.7): off, they answer as if they did not exist; on, the caller is still
authenticated and scoped first."""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter
from fastapi.routing import APIRoute

from niadra.adapters.inbound.http import coordinate, signals, state, turns
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.bodies import LargeBodies
from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, StaticConfig
from niadra.adapters.memory.notifications import InMemoryFeedCache
from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.app.notifications import NotificationsService
from niadra.app.state import StateService
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.vocabulary import Environment, Feature, Scope
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, principal, sample_config

WAVE: tuple[APIRouter, ...] = (
    turns.router,
    turns.feed_router,
    turns.admin_router,
    state.push_router,
    state.router,
    state.admin_router,
    signals.router,
    signals.admin_router,
    coordinate.router,
    coordinate.admin_router,
)
PUBLIC = {"/.well-known/niadra-contact-keys.json"}
# A batch of events is kept aside whatever the space turned on, so its routes answer in every space.
EVERY_SPACE = {"/v1/intake/failed", "/v1/intake/failed/retry"}
SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
KEYS = {
    "nia_sk_all": principal(WHATSAPP_SOURCE, scopes=frozenset(Scope)),
    "nia_sk_track": principal(WHATSAPP_SOURCE, scopes=frozenset({Scope.TRACK})),
}
ADMIN = PersonPrincipal(SPACE, uuid7(), frozenset({"admin"}))


class FakeEdge:
    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token not in KEYS:
            raise Unauthenticated("unknown key")
        return KEYS[token]

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "person":
            raise Unauthenticated("unknown token")
        return ADMIN

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Container:
    edge: FakeEdge
    config: StaticConfig
    state: StateService
    notifications: NotificationsService
    large_bodies: LargeBodies = field(default_factory=lambda: LargeBodies(2, 1024 * 1024))


class Arriving(StaticConfig):
    """A copy whose next read finds `later`, the snapshot that reached the cell after this process's timed
    read; with no `later`, the rate limit leaves the copy as it is."""

    def __init__(self, later: SpaceConfig | None) -> None:
        super().__init__(sample_config())
        self._later = later
        self.reads = 0

    async def refresh_on_miss(self) -> bool:
        self.reads += 1
        if self._later is None:
            return False
        self.put(self._later)
        return True


def _app(*features: Feature, config: StaticConfig | None = None) -> httpx.AsyncClient:
    config = config or StaticConfig(
        sample_config().model_copy(update={"features": FeatureSettings(enabled=frozenset(features))})
    )
    state = StateService(
        InMemoryUnitOfWorkFactory(), InMemoryCache(), LocalKeyVault(b"k" * 32), FixedClock(), config
    )
    feed = NotificationsService(InMemoryUnitOfWorkFactory(), InMemoryFeedCache())
    app = create_app("niadra-test", Container(FakeEdge(), config, state, feed), WAVE)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell")


def _routes() -> list[tuple[str, str]]:
    return [
        (method, route.path)
        for router in WAVE
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods)
    ]


def _url(path: str) -> str:
    for name in ("profile_id", "project_id"):
        path = path.replace("{" + name + "}", str(uuid4()))
    while "{" in path:
        start = path.index("{")
        path = path[:start] + "x1" + path[path.index("}", start) + 1 :]
    return path


def _headers(path: str) -> dict[str, str]:
    # Governance routes take a person, the rest a source key with every scope.
    governance = path.startswith(("/v1/turns/{", "/v1/turns/search", "/v1/replay", "/v1/scenario"))
    governance |= path.startswith(("/v1/changes", "/v1/data-issues", "/v1/objects/coverage", "/v1/content"))
    governance |= path.startswith(("/v1/profiles", "/v1/review-requests", "/v1/legal-holds", "/v1/measure"))
    governance |= path.startswith(("/v1/coordination/reports", "/v1/coordination/shadow"))
    token = "person" if governance else "nia_sk_all"
    return {"authorization": f"Bearer {token}", "idempotency-key": "k1"}


async def test_every_route_of_a_feature_the_space_did_not_turn_on_answers_not_found() -> None:
    async with _app() as client:
        for method, path in _routes():
            if path in PUBLIC | EVERY_SPACE:
                continue
            response = await client.request(method, _url(path), headers=_headers(path), json={})
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["code"] == "not_found"


async def test_a_space_nobody_configured_opens_every_features_routes() -> None:
    # Zero touch: the routes a space never turned on used to answer 404 until a person approved
    # a `features` diff; a space with no declaration at all now answers on every one of them.
    bare = SpaceConfig(space_id=SPACE_ID, tenant_id=TENANT_ID, region="us-east-2", environment="sandbox")
    fresh = sample_config().model_copy(update={"features": bare.features})
    async with _app(config=StaticConfig(fresh)) as client:
        for method, path in _routes():
            if path in PUBLIC:
                continue
            response = await client.request(method, _url(path), headers=_headers(path), json={})
            assert response.json().get("detail") != "not enabled for this space", (method, path)
        profile = await client.get("/v1/sdk/profile", headers=_headers("/v1/sdk/profile"))
    assert profile.json()["features"] == sorted(Feature)


async def test_the_caller_is_authenticated_and_scoped_before_the_feature_is_looked_at() -> None:
    async with _app() as client:
        anonymous = await client.post("/v1/turns", json={})
        unscoped = await client.post(
            "/v1/coordination/check", headers={"authorization": "Bearer nia_sk_track"}, json={}
        )
    assert anonymous.status_code == 401
    assert unscoped.status_code == 403
    assert unscoped.json()["code"] == "scope_missing"


async def test_one_feature_opens_only_its_own_routes() -> None:
    async with _app(Feature.NOTIFICATIONS) as client:
        feed = await client.get("/v1/notifications", headers=_headers("/v1/notifications"))
        profile = await client.get("/v1/sdk/profile", headers=_headers("/v1/sdk/profile"))
        turn = await client.get("/v1/turns/t1", headers=_headers("/v1/turns/{turn_id}"))
    assert (feed.status_code, feed.json()["items"]) == (200, [])
    assert profile.status_code == 200  # the discovery route answers once any feature is on
    assert profile.json()["features"] == ["notifications"]
    assert turn.status_code == 404


async def test_a_feature_this_process_has_not_read_yet_reads_the_copy_before_answering_not_found() -> None:
    enabled = FeatureSettings(enabled=frozenset({Feature.NOTIFICATIONS}))
    on = sample_config().model_copy(update={"features": enabled})
    config = Arriving(on)
    async with _app(config=config) as client:
        feed = await client.get("/v1/notifications", headers=_headers("/v1/notifications"))
        turn = await client.get("/v1/turns/t1", headers=_headers("/v1/turns/{turn_id}"))
    assert feed.status_code == 200
    assert (turn.status_code, turn.json()["detail"]) == (404, "not enabled for this space")
    assert config.reads == 2


async def test_a_rate_limited_read_answers_not_found_from_the_copy_it_has() -> None:
    config = Arriving(None)
    async with _app(config=config) as client:
        feed = await client.get("/v1/notifications", headers=_headers("/v1/notifications"))
    assert feed.status_code == 404
    assert config.reads == 1


async def test_the_profile_tells_a_source_how_it_records_turns_once_the_space_records_them() -> None:
    async with _app(Feature.NOTIFICATIONS) as client:
        off = await client.get("/v1/sdk/profile", headers=_headers("/v1/sdk/profile"))
    async with _app(Feature.TURNS) as client:
        on = await client.get("/v1/sdk/profile", headers=_headers("/v1/sdk/profile"))
    assert off.json()["recording"] is None
    assert on.json()["recording"] == {"content_mode": "stored", "required_pins": ["model", "prompts"]}
