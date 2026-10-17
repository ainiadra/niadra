"""The Console's relationship graph: a customer's relations, the space's overview, what a reader may see of
them, and the route's checks and receipt."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.graph import router
from niadra.adapters.memory.coordinate import InMemoryCoordinateRepository
from niadra.adapters.memory.ingest import InMemoryEventRepository
from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.adapters.memory.sessions import InMemorySessionRepository
from niadra.app.graph import GraphService
from niadra.config.models import PolicyCondition, PolicyRule
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.graph import Graph
from niadra.domain.common.errors import NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.coordinate.destination import SUBJECT_HMAC, subject_canonical
from niadra.domain.graph import OBJECTS_PER_CUSTOMER
from niadra.domain.identity.model import SuggestionRecord
from niadra.domain.ingest.events import NewEvent
from niadra.domain.memory.facts import normalize_value
from niadra.domain.objects.state import SYSTEM_PREFIX
from niadra.domain.records import AgentAction, BusinessObject, EventRecord, Fact, ObjectKey, SessionRecord
from niadra.domain.state.observed import Observation
from niadra.domain.vocabulary import (
    ActionStatus,
    Audience,
    Environment,
    EventKind,
    FactStatus,
    HandleType,
    Sensitivity,
    Speaker,
    SubjectKind,
)
from niadra.ports.repos.coordinate import HandoffRow
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.identity.conftest import Env, handle, make_env

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)
SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
PHONE = handle(HandleType.PHONE_E164, "+5511900030001")
EMAIL = handle(HandleType.EMAIL, "marina.teixeira@demo.niadra.com")
CRM = handle(HandleType.SYSTEM_ID, "CLI-20501", "crm")
OTHER_PHONE = handle(HandleType.PHONE_E164, "+5511900030002")
COMPANY = handle(HandleType.SYSTEM_ID, "EMP-HOTEIS", "erp", SubjectKind.ACCOUNT)
COLLEAGUE = handle(HandleType.PHONE_E164, "+5511900030003")
ORDER_TYPE = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "billing-agent"},
    "sources": {"billing_agent": {"kind": "push", "precedence": 1}},
    "states": ["placed", "shipped", "delivered"],
}
RAW = ("900030001", "marina.teixeira", "CLI-20501", "EMP-HOTEIS")


def reader(*roles: str) -> PersonPrincipal:
    return PersonPrincipal(SPACE, UUID(int=7), frozenset(roles))


@dataclass
class World:
    env: Env
    graphs: GraphService
    marina: UUID
    other: UUID
    company: UUID
    colleague: UUID
    order: UUID

    @property
    def cell(self) -> Cell:
        return self.env.cell


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def profile_of(env: Env, h: Handle) -> UUID:
    return (await env.profile(h)).profile_id


@pytest.fixture
async def world(cell: Cell) -> World:
    config = cell.config.space(SPACE_ID)
    cell.config.put(
        config.model_copy(update={"object_types": ObjectTypeRegistry.model_validate({"types": [ORDER_TYPE]})})
    )
    env = make_env(cell)
    await env.attach(PHONE, EMAIL, CRM, at=T0)
    await env.attach(OTHER_PHONE, at=T0)
    await env.attach(COLLEAGUE, at=T0)
    await env.attach(COMPANY, kind=SubjectKind.ACCOUNT, at=T0)
    await env.service.create_link(SPACE_ID, PHONE, COMPANY, role="compras")
    await env.service.create_link(SPACE_ID, COLLEAGUE, COMPANY, role="financeiro")
    store = cell.uow.store
    marina_phone = await env.find(PHONE)
    other_phone = await env.find(OTHER_PHONE)
    await env.repo.add_suggestion(
        SuggestionRecord(uuid7(), marina_phone.handle_id, other_phone.handle_id, 0.82, at(1))
    )

    objects = InMemoryObjectRepository(store)
    order = BusinessObject(
        uuid7(),
        ObjectKey("order", "erp", "PED-90039"),
        {"state": "shipped", "total": "199.90"},
        at(5),
        BILLING_SOURCE,
        origin_handle_id=marina_phone.handle_id,
        fields={"state": {f"{SYSTEM_PREFIX}{BILLING_SOURCE}": Observation("shipped", at(5), at(5), at(5))}},
    )
    await objects.add_object(order)
    system_event = EventRecord(
        uuid7(), EventKind.SYSTEM_EVENT, BILLING_SOURCE, "erp", Speaker.SYSTEM, at(5), at(5), ()
    )
    await InMemoryEventRepository(store, SPACE_ID).insert_events([NewEvent(SPACE_ID, system_event, "ev-1")])
    await objects.add_object_event(order.object_id, system_event.event_id, at(5))
    for minutes in (6, 7):
        await objects.add_action(
            AgentAction(
                uuid7(),
                uuid7(),
                VOICE_SOURCE,
                "update_address",
                ActionStatus.DECLARED,
                at(minutes),
                order.object_id,
            )
        )
    marina = await profile_of(env, PHONE)
    sessions = InMemorySessionRepository(store)
    for minutes in (10, 20):
        await sessions.create_session(
            SPACE_ID,
            SessionRecord(
                uuid7(), "conversation", WHATSAPP_SOURCE, "whatsapp", at(minutes), at(minutes + 3),
                f"c{minutes}", profile_id=marina,
            ),
        )  # fmt: skip
    canonical, _ = subject_canonical(HandleType.PHONE_E164, marina_phone.value)
    digest = await cell.keys.hmac(SPACE_ID, SUBJECT_HMAC, canonical.encode())
    await InMemoryCoordinateRepository(store).add_handoff(
        HandoffRow(
            uuid7(), digest, None, WHATSAPP_SOURCE, "whatsapp-bot", "queue:fechamento", b"", b"", 1, "closed",
            "venda_fechada", at(30), None,
        )
    )  # fmt: skip
    return World(
        env,
        GraphService(cell.uow, cell.keys, cell.clock, cell.config),
        marina,
        await profile_of(env, OTHER_PHONE),
        await profile_of(env, COMPANY),
        await profile_of(env, COLLEAGUE),
        order.object_id,
    )


def by_ref(graph: Graph) -> dict[str | None, Any]:
    return {n.ref: n for n in graph.nodes if n.ref is not None}


def edges(graph: Graph, kind: str) -> list[tuple[str, str, dict[str, Any]]]:
    labels = {n.id: n.ref or n.label for n in graph.nodes}
    return [(labels[e.from_], labels[e.to], e.meta) for e in graph.edges if e.kind == kind]


async def test_a_customer_graph_shows_identity_companies_objects_and_who_touched_them(world: World) -> None:
    graph = await world.graphs.around(reader("security"), world.marina)

    nodes = by_ref(graph)
    center = nodes[str(world.marina)]
    assert graph.center == str(world.marina)
    assert center.kind == "person"
    assert center.label == "+55 11 90003-0001"
    assert (center.meta["display_name"], center.meta["display_contact"]) == (None, "+55 11 90003-0001")
    assert center.meta["pseudonym"].startswith("p_")
    assert center.meta["hop"] == 0
    assert center.id == "n1"
    assert center.meta["objects_total"] == 1
    handles = [n for n in graph.nodes if n.kind == "handle"]
    assert sorted(n.meta["channel"] for n in handles) == ["crm", "email", "phone"]
    assert {(n.meta["type"], n.label) for n in handles} >= {("email", "m***@demo.niadra.com")}
    identifies = edges(graph, "identifies")
    assert len(identifies) == 3
    assert {m["strength"] for _, _, m in identifies} == {"weak", "strong"}
    assert edges(graph, "member_of") == [
        (
            str(world.marina),
            str(world.company),
            {"role": "compras", "since": edges(graph, "member_of")[0][2]["since"]},
        )
    ]
    assert nodes[str(world.company)].kind == "account"
    [order] = [n for n in graph.nodes if n.kind == "object"]
    assert order.label == "order PED-90039"
    assert order.ref == str(world.order)
    assert order.meta["state"] == "shipped"
    assert order.meta["state_label"] is None
    assert edges(graph, "owns") == [(str(world.marina), str(world.order), {"role": "owner"})]
    [(agent, target, acted)] = edges(graph, "acted_on")
    assert (agent, target) == (str(VOICE_SOURCE), str(world.order))
    assert (acted["count"], acted["operations"]) == (2, ["update_address"])
    assert nodes[str(VOICE_SOURCE)].kind == "agent"
    [(system, reported_on, reported)] = edges(graph, "reported")
    assert (system, reported_on, reported["count"]) == (str(BILLING_SOURCE), str(world.order), 1)
    assert nodes[str(BILLING_SOURCE)].kind == "system"
    [(talker, talked_to, talked)] = edges(graph, "talked_with")
    assert (talker, talked_to) == (str(WHATSAPP_SOURCE), str(world.marina))
    assert (talked["count"], talked["channels"]) == (2, ["whatsapp"])
    [(handed_by, queue, handed)] = edges(graph, "handed_off")
    assert (handed_by, queue) == (str(WHATSAPP_SOURCE), "fechamento")
    assert (handed["outcome"], handed["status"]) == ("venda_fechada", "closed")
    assert handed["about"] == center.id
    [(first, second, suggested)] = edges(graph, "suggested_same")
    assert {first, second} == {str(world.marina), str(world.other)}
    assert suggested == {"score": 0.82}
    assert (graph.truncated, graph.withheld) == (False, 0)
    assert [n.id for n in graph.nodes] == [f"n{i}" for i in range(1, len(graph.nodes) + 1)]


async def test_no_identifier_leaves_in_full_and_a_contact_only_for_the_roles_that_see_contacts(
    world: World,
) -> None:
    for role in ("admin", "security", "integration"):
        graph = await world.graphs.around(reader(role), world.marina, depth=2)
        body = graph.model_dump_json()
        for raw in RAW:
            assert raw not in body, (role, raw)
        contact = by_ref(graph)[str(world.marina)].meta["display_contact"]
        assert contact == ("+55 ** *****-0001" if role == "integration" else "+55 11 90003-0001")
        assert ("90003-0001" in body) is (role != "integration")


async def test_customers_go_by_their_names_a_company_s_to_every_role_a_person_s_to_who_may_see_it(
    world: World,
) -> None:
    store = world.cell.uow.store
    company = await world.env.find(COMPANY)
    await InMemoryObjectRepository(store).add_object(
        BusinessObject(
            uuid7(), ObjectKey("account", "erp", "EMP-HOTEIS"), {"nome_fantasia": "Horizonte Hotéis"}, at(1),
            BILLING_SOURCE, origin_handle_id=company.handle_id,
        )
    )  # fmt: skip
    phone = await world.env.find(PHONE)
    name = Fact(
        uuid7(), phone.handle_id, "preferred_name", "Marina", "contact", Sensitivity.NORMAL, Speaker.CUSTOMER,
        0.9, at(2), at(2), FactStatus.ACTIVE, at(2),
    )  # fmt: skip
    await InMemoryMemoryRepository(store).insert_fact(name, normalize_value(name.value))

    security = by_ref(await world.graphs.around(reader("security"), world.marina, depth=2))
    integration = by_ref(await world.graphs.around(reader("integration"), world.marina))
    overview = by_ref(await world.graphs.overview(reader("integration")))

    assert security[str(world.marina)].label == "Marina"
    assert security[str(world.marina)].meta["display_name"] == "Marina"
    assert integration[str(world.marina)].label == "+55 ** *****-0001"
    assert integration[str(world.marina)].meta["display_name"] is None
    for nodes in (security, integration, overview):
        assert nodes[str(world.company)].label == "Horizonte Hotéis"
    colleague = security[str(world.colleague)]
    assert (colleague.label, colleague.meta["display_name"]) == ("+55 11 90003-0003", None)
    assert colleague.meta["pseudonym"].startswith("p_")


async def test_depth_two_reaches_the_company_s_other_people(world: World) -> None:
    near = await world.graphs.around(reader("security"), world.marina)
    far = await world.graphs.around(reader("security"), world.marina, depth=2)

    assert str(world.colleague) not in by_ref(near)
    nodes = by_ref(far)
    assert nodes[str(world.colleague)].meta["hop"] == 2
    assert nodes[str(world.company)].meta["hop"] == 1
    assert (str(world.colleague), str(world.company)) in [(a, b) for a, b, _ in edges(far, "member_of")]
    company_handles = [n for n in far.nodes if n.kind == "handle" and n.meta["channel"] == "erp"]
    assert [n.meta["hop"] for n in company_handles] == [2]


async def test_a_cut_keeps_the_center_its_identifiers_then_its_ties_and_says_so(world: World) -> None:
    tiny = await world.graphs.around(reader("security"), world.marina, limit=3)
    small = await world.graphs.around(reader("security"), world.marina, limit=6)

    assert tiny.truncated
    assert tiny.nodes[0].ref == str(world.marina)
    assert [n.kind for n in tiny.nodes[1:]] == ["handle", "handle"]
    assert [n.kind for n in small.nodes] == ["person", "person", "account", "handle", "handle", "handle"]
    assert len(edges(small, "identifies")) == 3
    for graph in (tiny, small):
        ids = {n.id for n in graph.nodes}
        assert all(e.from_ in ids and e.to in ids for e in graph.edges)


async def busy(world: World, orders: int) -> None:
    """`orders` more orders of the center, newer than the first one."""
    objects = InMemoryObjectRepository(world.cell.uow.store)
    phone = await world.env.find(PHONE)
    for n in range(orders):
        await objects.add_object(
            BusinessObject(
                uuid7(), ObjectKey("order", "erp", f"PED-{n:05d}"), {}, at(100 + n), BILLING_SOURCE,
                origin_handle_id=phone.handle_id,
            )
        )  # fmt: skip


@pytest.mark.parametrize("depth", [1, 2])
async def test_a_customer_with_more_objects_than_the_limit_keeps_who_they_are(
    world: World, depth: int
) -> None:
    await busy(world, 400)

    graph = await world.graphs.around(reader("security"), world.marina, depth=depth)

    nodes = by_ref(graph)
    center = nodes[str(world.marina)]
    assert center.meta["objects_total"] == 401
    assert graph.truncated
    assert len(graph.nodes) <= 150
    assert len([n for n in graph.nodes if n.kind == "handle" and n.meta["hop"] == 1]) == 3
    assert len(edges(graph, "identifies")) >= 3
    assert str(world.company) in nodes
    assert str(world.other) in nodes
    owned = edges(graph, "owns")
    assert len([o for a, o, _ in owned if a == str(world.marina)]) == OBJECTS_PER_CUSTOMER
    shown = {n.label for n in graph.nodes if n.kind == "object"}
    assert "order PED-00399" in shown
    assert "order PED-90039" not in shown  # the oldest is past the latest 25
    if depth == 2:
        assert str(world.colleague) in nodes
        assert nodes[str(world.company)].meta["objects_total"] == 0


async def test_an_object_the_policy_denies_is_withheld_with_its_actions(world: World) -> None:
    config = world.cell.config.space(SPACE_ID)
    deny = PolicyRule(effect="deny", when=PolicyCondition(audience=Audience.ANALYST, category="order"))
    world.cell.config.put(
        config.model_copy(
            update={"policy": config.policy.model_copy(update={"rules": [*config.policy.rules, deny]})}
        )
    )

    analyst = await world.graphs.around(reader("integration"), world.marina)
    security = await world.graphs.around(reader("security"), world.marina)

    assert [n for n in analyst.nodes if n.kind == "object"] == []
    assert edges(analyst, "acted_on") == edges(analyst, "reported") == edges(analyst, "owns") == []
    assert analyst.withheld == 1
    assert [n.ref for n in security.nodes if n.kind == "object"] == [str(world.order)]


async def test_a_masked_object_shows_no_state(world: World) -> None:
    config = world.cell.config.space(SPACE_ID)
    mask = PolicyRule(effect="mask", when=PolicyCondition(audience=Audience.ANALYST, category="order"))
    world.cell.config.put(
        config.model_copy(
            update={"policy": config.policy.model_copy(update={"rules": [*config.policy.rules, mask]})}
        )
    )

    graph = await world.graphs.around(reader("integration"), world.marina)

    [order] = [n for n in graph.nodes if n.kind == "object"]
    assert (order.meta["masked"], order.meta["state"], order.label) == (True, None, "order PED-90039")


async def test_an_identifier_a_claim_joined_is_withheld_from_roles_that_may_not_see_it(world: World) -> None:
    claimed = handle(HandleType.EMAIL, "marina.pessoal@demo.niadra.com")
    await world.env.attach(claimed, at=T0)
    record = await world.env.find(claimed)
    await world.env.repo.set_membership([record.handle_id], world.marina, 1)
    await world.env.repo.set_provisional([record.handle_id], True)

    analyst = await world.graphs.around(reader("integration"), world.marina)
    security = await world.graphs.around(reader("security"), world.marina)

    assert analyst.withheld == 1
    assert len([n for n in analyst.nodes if n.kind == "handle"]) == 3
    claimed_nodes = [n for n in security.nodes if n.kind == "handle" and n.meta["provisional"]]
    assert [n.label for n in claimed_nodes] == ["m***@demo.niadra.com"]
    assert security.withheld == 0


async def test_an_erased_or_merged_away_customer_is_not_found_nor_shown(world: World) -> None:
    await world.env.repo.retire_profile(world.other, datetime.now(UTC))

    graph = await world.graphs.around(reader("security"), world.marina)

    assert str(world.other) not in by_ref(graph)
    assert edges(graph, "suggested_same") == []
    with pytest.raises(NotFound):
        await world.graphs.around(reader("security"), world.other)


async def test_the_overview_ranks_the_most_connected_and_ties_them(world: World) -> None:
    graph = await world.graphs.overview(reader("security"))

    nodes = by_ref(graph)
    assert graph.center is None
    marina = nodes[str(world.marina)]
    assert (marina.meta["handles"], marina.meta["objects"], marina.meta["interactions"]) == (3, 1, 2)
    assert marina.meta["score"] == 3 + 1 + 1 + 2
    # Its two people; an organization is tied to nothing but the people linked to it.
    assert nodes[str(world.company)].meta["links"] == 2
    profiles = [n for n in graph.nodes if n.kind in ("person", "account")]
    assert profiles[0].ref in (str(world.marina), str(world.company))
    assert {n.kind for n in graph.nodes} == {"person", "account", "agent", "system"}
    assert (str(world.marina), str(world.company)) in [(a, b) for a, b, _ in edges(graph, "member_of")]
    assert (str(WHATSAPP_SOURCE), str(world.marina)) in [(a, b) for a, b, _ in edges(graph, "talked_with")]
    assert (str(VOICE_SOURCE), str(world.marina)) in [(a, b) for a, b, _ in edges(graph, "acted_on")]
    assert (str(BILLING_SOURCE), str(world.marina)) in [(a, b) for a, b, _ in edges(graph, "reported")]
    assert [n for n in graph.nodes if n.kind in ("handle", "object")] == []
    assert all(n.meta["hop"] == 0 for n in graph.nodes)
    body = graph.model_dump_json()
    assert not any(raw in body for raw in RAW)


async def test_the_overview_limit_cuts_the_least_connected(world: World) -> None:
    graph = await world.graphs.overview(reader("security"), limit=4)

    assert graph.truncated
    assert len(graph.nodes) == 4
    kept = {n.ref for n in graph.nodes}
    assert str(world.marina) in kept
    assert str(world.other) not in kept


# The route.


@dataclass
class FakeEdge:
    roles: frozenset[str] = frozenset({"integration"})

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        return reader(*self.roles)


@dataclass
class RecordingAudit:
    receipts: list[tuple[str, UUID | None, str | None]]

    async def record(
        self, principal: Any, *, action: str, profile_id: UUID | None = None, target: str | None = None
    ) -> None:
        self.receipts.append((action, profile_id, target))


@dataclass
class Container:
    edge: FakeEdge
    graph: GraphService
    admin_audit: RecordingAudit


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit([])


@pytest.fixture
async def http(world: World, audit: RecordingAudit) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("admin", Container(FakeEdge(), world.graphs, audit), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
        client.headers["authorization"] = "Bearer person-jwt"
        yield client


async def test_the_route_answers_the_contract_and_leaves_one_receipt(
    world: World, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    response = await http.get("/v1/graph", params={"center": str(world.marina), "depth": 2})
    overview = await http.get("/v1/graph", params={"overview": 1, "limit": 10})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"center", "nodes", "edges", "truncated", "withheld", "generated_at"}
    assert set(body["edges"][0]) == {"id", "from", "to", "kind", "meta"}
    assert set(body["nodes"][0]) == {"id", "kind", "label", "ref", "meta"}
    assert overview.status_code == 200
    assert overview.json()["center"] is None
    assert audit.receipts == [
        ("graph.read", world.marina, f"graph:{len(body['nodes'])}"),
        ("graph.read", None, f"graph:{len(overview.json()['nodes'])}"),
    ]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"overview": 1, "center": "0192f5a0-0000-7000-8000-000000000001"},
        {"center": "0192f5a0-0000-7000-8000-000000000001", "depth": 3},
        {"overview": 1, "limit": 301},
        {"center": "not-an-id"},
    ],
)
async def test_the_route_refuses_what_it_cannot_answer(
    http: httpx.AsyncClient, audit: RecordingAudit, params: dict[str, Any]
) -> None:
    response = await http.get("/v1/graph", params=params)

    assert response.status_code == 422
    assert audit.receipts == []


async def test_an_unknown_center_is_not_found_and_leaves_no_receipt(
    http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    response = await http.get("/v1/graph", params={"center": str(uuid7())})

    assert response.status_code == 404
    assert audit.receipts == []
