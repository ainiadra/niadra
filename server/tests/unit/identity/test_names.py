"""The Console names customers by name and contact, never by pseudonym: on the list, the customer's page and
the search, as each role may see them."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.auth import SOURCE_KEY_PREFIX
from niadra.adapters.inbound.http.identity import router
from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.config.models import PolicyCondition, PolicyRule
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.records import BusinessObject, Fact, ObjectKey, ProfileAttr
from niadra.domain.vocabulary import (
    Audience,
    FactStatus,
    HandleType,
    Scope,
    Sensitivity,
    Speaker,
    SubjectKind,
)
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.identity.conftest import Env, handle
from tests.unit.identity.test_routes import Container, FakeEdge, RecordingAudit

T = HandleType
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
PHONE = handle(T.PHONE_E164, "+55 11 98117-2201")
EMAIL = handle(T.EMAIL, "marina.lima@acme.com.br")
BARE = handle(T.PHONE_E164, "+5511900000003")
COMPANY = handle(T.SYSTEM_ID, "ACC-2201", "crm", SubjectKind.ACCOUNT)


@pytest.fixture
def edge() -> FakeEdge:
    return FakeEdge(roles=frozenset({"security"}))


@pytest.fixture
async def http(env: Env, edge: FakeEdge) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("admin", Container(edge, env.service, RecordingAudit()), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
        client.headers["authorization"] = "Bearer person-jwt"
        yield client


async def said(env: Env, h: Any, predicate: str, value: str, category: str = "contact") -> Fact:
    found = await env.find(h)
    fact = Fact(
        uuid7(), found.handle_id, predicate, value, category, Sensitivity.NORMAL, Speaker.CUSTOMER, 0.9,
        T0, T0, FactStatus.ACTIVE, T0,
    )  # fmt: skip
    await InMemoryMemoryRepository(env.cell.uow.store).insert_fact(fact, normalize_value(value))
    return fact


async def customers(env: Env) -> dict[str, str]:
    """Marina, named in her memory; a company named by its CRM account; a person nobody named."""
    ids = {
        "marina": (await env.attach(PHONE, EMAIL)).profile_id,
        "company": (await env.attach(COMPANY, kind=SubjectKind.ACCOUNT)).profile_id,
        "bare": (await env.attach(BARE)).profile_id,
    }
    await said(env, PHONE, "preferred_name", "Marina")
    company = await env.find(COMPANY)
    await InMemoryObjectRepository(env.cell.uow.store).add_object(
        BusinessObject(
            uuid7(), ObjectKey("account", "crm", "ACC-2201"),
            {"razao_social": "Horizonte Hotéis Ltda.", "nome_fantasia": "Horizonte Hotéis"},
            T0, BILLING_SOURCE, company.handle_id,
        )
    )  # fmt: skip
    return {k: str(v) for k, v in ids.items()}


async def listed(http: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    response = await http.get("/v1/profiles")
    assert response.status_code == 200
    return {row["profile_id"]: row for row in response.json()["items"]}


def shown(row: dict[str, Any]) -> tuple[str | None, str | None]:
    return row["display_name"], row["display_contact"]


async def test_a_role_that_sees_personal_data_reads_names_and_contacts_in_full(
    env: Env, http: httpx.AsyncClient
) -> None:
    ids = await customers(env)

    rows = await listed(http)

    assert shown(rows[ids["marina"]]) == ("Marina", "+55 11 98117-2201")
    assert shown(rows[ids["company"]]) == ("Horizonte Hotéis", None)
    assert shown(rows[ids["bare"]]) == (None, "+55 11 90000-0003")
    assert all("98117" not in h["value"] for h in rows[ids["marina"]]["handles"])  # the handles stay masked
    detail = (await http.get(f"/v1/profiles/{ids['marina']}")).json()
    assert shown(detail) == ("Marina", "+55 11 98117-2201")
    assert detail["pseudonym"].startswith("p_")


async def test_a_role_that_does_not_see_personal_data_reads_company_names_and_masked_contacts(
    env: Env, http: httpx.AsyncClient, edge: FakeEdge
) -> None:
    ids = await customers(env)
    edge.roles = frozenset({"integration"})

    rows = await listed(http)

    assert shown(rows[ids["marina"]]) == (None, "+55 ** *****-2201")
    assert shown(rows[ids["company"]]) == ("Horizonte Hotéis", None)
    assert shown((await http.get(f"/v1/profiles/{ids['company']}")).json()) == ("Horizonte Hotéis", None)
    assert "Marina" not in (await http.get(f"/v1/profiles/{ids['marina']}")).text


async def test_a_source_key_reads_names_through_its_own_policy_and_contacts_masked(
    env: Env, http: httpx.AsyncClient, edge: FakeEdge
) -> None:
    ids = await customers(env)
    edge.key_scopes = frozenset({Scope.ADMIN})
    http.headers["authorization"] = f"Bearer {SOURCE_KEY_PREFIX}admin"

    rows = await listed(http)

    assert shown(rows[ids["marina"]]) == ("Marina", "+55 ** *****-2201")


async def test_a_name_the_policy_masks_for_the_role_is_not_shown_and_the_contact_stands_in(
    cell: Cell, env: Env, http: httpx.AsyncClient
) -> None:
    ids = await customers(env)
    config = sample_config()
    masked = PolicyRule(effect="mask", when=PolicyCondition(audience=Audience.HUMAN, category="contact"))
    cell.config.put(
        config.model_copy(
            update={"policy": config.policy.model_copy(update={"rules": [*config.policy.rules, masked]})}
        )
    )

    rows = await listed(http)

    assert shown(rows[ids["marina"]]) == (None, "+55 11 98117-2201")
    assert shown(rows[ids["company"]]) == ("Horizonte Hotéis", None)
    found = await http.post("/v1/profiles/search", json={"query": "Marina"})
    assert found.json() == {"items": []}


async def test_a_profile_slot_names_the_person_before_a_fact(env: Env, http: httpx.AsyncClient) -> None:
    ids = await customers(env)
    phone = await env.find(PHONE)
    await InMemoryMemoryRepository(env.cell.uow.store).put_attr(
        ProfileAttr(phone.handle_id, "name", "Marina Souza Lima", WHATSAPP_SOURCE, T0)
    )

    assert shown((await listed(http))[ids["marina"]])[0] == "Marina Souza Lima"


async def test_the_search_finds_a_customer_by_a_name_the_role_may_see(
    env: Env, http: httpx.AsyncClient, edge: FakeEdge
) -> None:
    ids = await customers(env)
    await said(env, BARE, "name", "José da Silva")

    by_name = await http.post("/v1/profiles/search", json={"query": "  jose  DA silva "})
    nickname = await http.post("/v1/profiles/search", json={"query": "marina"})
    nobody = await http.post("/v1/profiles/search", json={"query": "Ninguém Aqui"})

    [match] = by_name.json()["items"]
    assert (match["profile_id"], match["display_name"]) == (ids["bare"], "José da Silva")
    assert match["display_contact"] == "+55 11 90000-0003"
    assert [m["profile_id"] for m in nickname.json()["items"]] == [ids["marina"]]
    assert nobody.json() == {"items": []}
    edge.roles = frozenset({"integration"})
    refused = await http.post("/v1/profiles/search", json={"query": "José da Silva"})
    assert refused.status_code == 422
    assert "José" not in refused.text


async def test_a_name_said_about_something_else_finds_nobody(env: Env, http: httpx.AsyncClient) -> None:
    await customers(env)
    await said(env, BARE, "product_or_service_used", "Plano Ouro", "relationship")

    assert (await http.post("/v1/profiles/search", json={"query": "Plano Ouro"})).json() == {"items": []}


async def test_the_search_answer_names_a_customer_found_by_an_identifier(
    env: Env, http: httpx.AsyncClient
) -> None:
    ids = await customers(env)

    [match] = (await http.post("/v1/profiles/search", json={"query": "marina.lima@acme.com.br"})).json()[
        "items"
    ]

    assert (match["profile_id"], match["display_name"], match["display_contact"]) == (
        ids["marina"],
        "Marina",
        "+55 11 98117-2201",
    )
