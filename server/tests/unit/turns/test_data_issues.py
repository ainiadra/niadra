"""Data issues (24.3.5, 24.6.6): one open issue per class, type, field and source, counted and with up to 50
references; acknowledged, it gives way to the next; announced once, with names and ids only; listed and
acknowledged by an operator, never by an agent's key."""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import CheckConstraint

from niadra.adapters.inbound.http import turns
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.memory.fakes import StaticConfig
from niadra.adapters.outbound.postgres.schema.turns import DataIssue
from niadra.app.data_issues import DataIssuesService
from niadra.config.models import FeatureSettings, WebhookEndpoint
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal
from niadra.domain.ingest.intake import FailedIntake
from niadra.domain.tasks import TaskName
from niadra.domain.turns.issues import DATA_ISSUE_KINDS, fingerprint, merged_refs, new_issue
from niadra.domain.vocabulary import Feature, Scope
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.audit.fakes import RecordingAdminAudit

LISTENER = WebhookEndpoint(
    endpoint_id="data",
    url="https://data.example/hooks",
    source_id=WHATSAPP_SOURCE,
    event_types=["data_issue.opened"],
    secret_refs=["spaces/x/webhook/data"],
)


@dataclass
class World:
    cell: Cell
    audit: RecordingAdminAudit
    issues: DataIssuesService

    async def open(self, kind: Any, **names: Any) -> str:
        async with self.cell.uow(SPACE_ID) as uow:
            return str(await self.issues.open(uow, SPACE_ID, kind, **names))


def _world(*features: Feature, listening: bool = False) -> World:
    cell = Cell()
    cell.config.put(
        sample_config().model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset(features)),
                "webhook_endpoints": [LISTENER] if listening else [],
            }
        )
    )
    audit = RecordingAdminAudit()
    return World(cell, audit, DataIssuesService(cell.uow, cell.clock, cell.config, audit, cell.cache))  # type: ignore[arg-type]


def test_the_kinds_the_table_takes_are_the_kinds_the_port_takes() -> None:
    [check] = [c for c in DataIssue.__table__.constraints if isinstance(c, CheckConstraint)]
    assert set(re.findall(r"'([a-z_]+)'", str(check.sqltext))) == DATA_ISSUE_KINDS
    assert {"type_undeclared", "drift"} <= DATA_ISSUE_KINDS


def test_an_issue_is_its_class_type_field_and_source_and_keeps_50_references_that_are_references() -> None:
    assert fingerprint("drift", "lawsuit", None, "erp") == fingerprint("drift", "lawsuit", None, "erp")
    assert fingerprint("drift", "lawsuit", None, "erp") != fingerprint("drift", "lawsuit", "phase", "erp")
    assert fingerprint("drift", "a", "b", None) != fingerprint("drift", "a", None, "b")
    refs = [f"item_variant:store:{i}" for i in range(60)]
    assert merged_refs(refs[:10], refs) == tuple(refs[:50])
    assert merged_refs((), ["not a ref", "item_variant:store:1", "item_variant:store:1"]) == (
        "item_variant:store:1",
    )
    with pytest.raises(ValueError, match="unknown data issue kind"):
        new_issue(uuid7(), "typo", Cell().clock.now(), object_type=None, field=None, source=None, refs=())
    with pytest.raises(ValueError, match="1 to 256"):
        new_issue(uuid7(), "drift", Cell().clock.now(), object_type="", field=None, source=None, refs=())


async def test_the_same_problem_is_counted_in_its_open_issue_and_the_next_opens_after_acknowledgement() -> (
    None
):
    world = _world(Feature.TURNS, listening=True)
    first = await world.open(
        "null_field",
        object_type="item_variant",
        field="price",
        source="catalog",
        refs=["item_variant:store:1"],
    )
    world.cell.clock.advance(minutes=5)
    again = await world.open(
        "null_field",
        object_type="item_variant",
        field="price",
        source="catalog",
        refs=["item_variant:store:2", "item_variant:store:1"],
    )
    other = await world.open("null_field", object_type="item_variant", field="size", source="catalog")
    assert again == first
    assert other != first
    announced = [t for t in _outbox(world) if t.name == TaskName.NOTIFY_DATA_ISSUE]
    assert [t.payload["issue_id"] for t in announced] == [first, other], "each new issue once"

    page = await world.issues.list(_person(world), status="open", kind="null_field", cursor=None, limit=1)
    rest = await world.issues.list(_person(world), status="open", kind=None, cursor=page.next_cursor, limit=5)
    assert [i.issue_id for i in page.items + rest.items] == [other, first]
    counted = rest.items[0]
    assert (counted.occurrences, counted.refs) == (2, ["item_variant:store:1", "item_variant:store:2"])
    assert counted.last_seen_at > counted.opened_at

    closed = await world.issues.acknowledge(_person(world), first)
    assert closed.acknowledged_at is not None
    assert world.audit.records[-1] == {
        "action": "data_issue.acknowledged",
        "profile_id": None,
        "target": f"data_issue:{first}",
    }
    reopened = await world.open("null_field", object_type="item_variant", field="price", source="catalog")
    assert reopened != first
    done = await world.issues.list(_person(world), status="acknowledged", kind=None, cursor=None, limit=5)
    assert [i.issue_id for i in done.items] == [first]


async def test_nothing_is_announced_without_a_listener_and_the_announcement_carries_no_value() -> None:
    quiet = _world(Feature.TURNS)
    await quiet.open("drift", object_type="lawsuit", source="erp")
    assert not [t for t in _outbox(quiet) if t.name == TaskName.NOTIFY_DATA_ISSUE]

    world = _world(Feature.TURNS, listening=True)
    issue_id = await world.open(
        "type_undeclared", object_type="gift_card", source="lookup_card", refs=["gift_card:store:7"]
    )
    [task] = [t for t in _outbox(world) if t.name == TaskName.NOTIFY_DATA_ISSUE]
    await world.issues.notify_opened(task)
    async with world.cell.uow(SPACE_ID) as uow:
        [delivery] = await uow.notify.list_deliveries()
    assert delivery.event_type == "data_issue.opened"
    assert delivery.body["data"] == {
        "issue_id": issue_id,
        "kind": "type_undeclared",
        "object_type": "gift_card",
        "field": None,
        "source": "lookup_card",
        "refs": ["gift_card:store:7"],
        "occurrences": 1,
        "opened_at": world.cell.clock.now().isoformat(),
    }


def _outbox(world: World) -> list[Any]:
    return list(world.cell.uow.store.module("pipeline").get("outbox", []))


def _person(world: World, *roles: str) -> PersonPrincipal:
    space = principal(WHATSAPP_SOURCE, world.cell.config.space(SPACE_ID)).space
    return PersonPrincipal(space, uuid7(), frozenset(roles or {"integration"}))


class Edge:
    def __init__(self, world: World) -> None:
        config = world.cell.config.space(SPACE_ID)
        self._keys = {
            "nia_sk_track": principal(WHATSAPP_SOURCE, config, scopes=frozenset({Scope.TRACK})),
            "nia_sk_admin": principal(WHATSAPP_SOURCE, config, scopes=frozenset({Scope.ADMIN})),
        }
        self._people = {"integration": _person(world), "analyst": _person(world, "analysis")}

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token not in self._keys:
            raise Unauthenticated("unknown key")
        return self._keys[token]

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token not in self._people:
            raise Unauthenticated("unknown token")
        return self._people[token]

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Kept:
    """A module's batches set aside: what the routes list and retry."""

    kind: str
    rows: list[FailedIntake] = field(default_factory=list)
    retried: list[Any] = field(default_factory=list)
    unlisted: int = 0
    """Kept batches past what a listing shows."""

    async def failed(self, space_id: UUID) -> list[FailedIntake]:
        return list(self.rows)

    async def kept(self, space_id: UUID) -> int:
        return len(self.rows) + self.unlisted

    async def retry(self, space_id: UUID, intake_ids: list[UUID] | None) -> int:
        self.retried.append(intake_ids)
        back = [r for r in self.rows if intake_ids is None or r.intake_id in intake_ids]
        self.rows = [r for r in self.rows if r not in back]
        return len(back)


@dataclass
class Container:
    edge: Edge
    config: StaticConfig
    data_issues: DataIssuesService
    ingest: Kept = field(default_factory=lambda: Kept("events"))
    turns: Kept = field(default_factory=lambda: Kept("turns"))


def _client(world: World, container: Container | None = None) -> httpx.AsyncClient:
    container = container or Container(Edge(world), world.cell.config, world.issues)
    app = create_app("niadra-test", container, (turns.admin_router,))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell")


def _as(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_the_routes_take_an_operator_or_an_admin_key_once_the_space_turned_them_on() -> None:
    world = _world(Feature.STATE)
    issue_id = await world.open("drift", object_type="lawsuit", source="erp")
    async with _client(world) as client:
        listed = await client.get("/v1/data-issues", headers=_as("integration"))
        by_key = await client.get("/v1/data-issues", headers=_as("nia_sk_admin"))
        agent = await client.get("/v1/data-issues", headers=_as("nia_sk_track"))
        analyst = await client.post(f"/v1/data-issues/{issue_id}/ack", headers=_as("analyst"))
        bad_kind = await client.get("/v1/data-issues", params={"kind": "typo"}, headers=_as("integration"))
        bad_cursor = await client.get("/v1/data-issues", params={"cursor": "x"}, headers=_as("integration"))
        missing = await client.post(f"/v1/data-issues/{uuid7()}/ack", headers=_as("integration"))
        not_an_id = await client.post("/v1/data-issues/c-8812/ack", headers=_as("integration"))
        acked = await client.post(f"/v1/data-issues/{issue_id}/ack", headers=_as("integration"))
    assert [i["issue_id"] for i in listed.json()["items"]] == [issue_id]
    assert by_key.status_code == 200
    assert (agent.status_code, agent.json()["code"]) == (403, "scope_missing")
    assert analyst.status_code == 403
    assert (bad_kind.status_code, bad_cursor.status_code) == (422, 422)
    assert (missing.status_code, not_an_id.status_code) == (404, 404)
    assert acked.json()["acknowledged_at"] is not None

    off = _world(Feature.NOTIFICATIONS)
    async with _client(off) as client:
        hidden = await client.get("/v1/data-issues", headers=_as("integration"))
    assert hidden.status_code == 404


async def test_the_kept_batches_are_listed_oldest_first_and_retried_by_an_operator() -> None:
    world = _world(Feature.STATE)
    now = world.cell.clock.now()
    container = Container(Edge(world), world.cell.config, world.issues)
    late = FailedIntake(uuid7(), "events", uuid4(), now, now, 5)
    early = FailedIntake(uuid7(), "turns", uuid4(), now - timedelta(minutes=5), now, 5)
    container.ingest.rows, container.turns.rows = [late], [early]
    container.ingest.unlisted = 700
    async with _client(world, container) as client:
        listed = await client.get("/v1/intake/failed", headers=_as("integration"))
        agent = await client.get("/v1/intake/failed", headers=_as("nia_sk_track"))
        one = await client.post(
            "/v1/intake/failed/retry", json={"intake_ids": [str(late.intake_id)]}, headers=_as("integration")
        )
        rest = await client.post("/v1/intake/failed/retry", json={}, headers=_as("integration"))
        after = await client.get("/v1/intake/failed", headers=_as("integration"))
    body = listed.json()
    assert [(i["intake_id"], i["kind"]) for i in body["items"]] == [
        (str(early.intake_id), "turns"),
        (str(late.intake_id), "events"),
    ]
    assert "body_ct" not in body["items"][0]
    assert body["kept"] == 702
    assert (agent.status_code, agent.json()["code"]) == (403, "scope_missing")
    assert (one.json(), rest.json()) == ({"retried": 1}, {"retried": 1})
    assert container.ingest.retried == [[late.intake_id], None]
    assert after.json()["items"] == []

    # A batch of events is kept whatever the space turned on, so its routes answer in every space.
    plain = _world(Feature.NOTIFICATIONS)
    async with _client(plain, Container(Edge(plain), plain.cell.config, plain.issues)) as client:
        listed = await client.get("/v1/intake/failed", headers=_as("integration"))
    assert (listed.status_code, listed.json()["kept"]) == (200, 0)
