from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.insights import TOOLS, router
from niadra.app.insights import InsightsService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import principal
from tests.unit.insights.conftest import Space, person

TOKENS: dict[str, Principal] = {
    "analyst-token": person("analysis"),
    "security-token": person("security"),
    "nia_sk_analytics": principal(scopes=frozenset({Scope.ANALYTICS})),
    "nia_sk_agent": principal(),
}


class FakeEdge:
    def __init__(self) -> None:
        self.rated: list[str] = []

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        found = TOKENS.get(token)
        if not isinstance(found, SourcePrincipal):
            raise Unauthenticated("unknown key")
        return found

    async def authenticate_person(self, token: str) -> Principal:
        if token not in TOKENS:
            raise Unauthenticated("invalid person token")
        return TOKENS[token]

    async def check_rate(self, p: SourcePrincipal, route: str) -> None:
        self.rated.append(route)


@dataclass
class Container:
    insights: InsightsService
    edge: FakeEdge


@pytest.fixture
async def client(space: Space) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("niadra-insights", Container(space.service, FakeEdge()), [router])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://insights.test"
    ) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _window(space: Space) -> dict[str, str]:
    since, until = space.window()
    return {"since": since.isoformat(), "until": until.isoformat()}


async def test_rest_aggregate_and_auth(client: httpx.AsyncClient, space: Space) -> None:
    body = {"entity": "episodes", "group_by": ["outcome"], "metrics": ["count", "profiles"], **_window(space)}
    assert (await client.post("/v1/insights/aggregate", json=body)).status_code == 401
    assert (
        await client.post("/v1/insights/aggregate", json=body, headers=_auth("nia_sk_agent"))
    ).status_code == 403
    response = await client.post("/v1/insights/aggregate", json=body, headers=_auth("analyst-token"))
    assert response.status_code == 200, response.text
    assert [(r["group"]["outcome"], r["count"], r["profiles"]) for r in response.json()["rows"]] == [
        ("resolved", 2, 2),
        ("unresolved", 2, 2),
    ]
    keyed = await client.post("/v1/insights/aggregate", json=body, headers=_auth("nia_sk_analytics"))
    assert keyed.status_code == 200


async def test_rest_profiles_context_and_reveal(client: httpx.AsyncClient, space: Space) -> None:
    found = await client.post(
        "/v1/insights/profiles", json={"entity": "episodes", **_window(space)}, headers=_auth("analyst-token")
    )
    assert [i["pseudonym"] for i in found.json()["items"]] == ["p_ana", "p_bia", "p_caio"]
    context = await client.post(
        "/v1/insights/context", json={"pseudonym": "p_ana"}, headers=_auth("analyst-token")
    )
    assert context.json()["facts"][0]["value"] == "Ana, e-mail [email]"
    denied = await client.post(
        "/v1/insights/reveal",
        json={"pseudonym": "p_ana", "reason": "fraud case 4411"},
        headers=_auth("analyst-token"),
    )
    assert denied.status_code == 403
    revealed = await client.post(
        "/v1/insights/reveal",
        json={"pseudonym": "p_ana", "reason": "fraud case 4411"},
        headers=_auth("security-token"),
    )
    assert revealed.json()["handles"][0]["type"] == "phone_e164"


async def _rpc(
    client: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/mcp/insights",
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}},
        headers=_auth("analyst-token"),
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["id"] == 7
    return body


async def test_mcp_lists_the_eight_tools_and_calls_them(client: httpx.AsyncClient, space: Space) -> None:
    init = await _rpc(client, "initialize")
    assert init["result"]["serverInfo"]["name"] == "niadra-insights"
    tools = (await _rpc(client, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == list(TOOLS)
    assert len(tools) == 8
    assert all(t["inputSchema"]["type"] == "object" for t in tools)

    call = await _rpc(
        client,
        "tools/call",
        {
            "name": "aggregate",
            "arguments": {"entity": "episodes", "group_by": ["normalized_category"], **_window(space)},
        },
    )
    result = call["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["rows"][0]["group"] == {"normalized_category": "billing"}
    assert result["content"][0]["text"].startswith("<data>")


async def test_mcp_errors(client: httpx.AsyncClient, space: Space) -> None:
    bad = await _rpc(client, "tools/call", {"name": "aggregate", "arguments": {"entity": "episodes"}})
    assert bad["result"]["isError"] is True
    assert "invalid_input" in bad["result"]["content"][0]["text"]
    denied = await _rpc(
        client,
        "tools/call",
        {"name": "request_reveal", "arguments": {"pseudonym": "p_ana", "reason": "curious about her"}},
    )
    assert denied["result"]["isError"] is True
    assert denied["result"]["content"][0]["text"].startswith("forbidden")
    assert (await _rpc(client, "nope"))["error"]["code"] == -32601
    assert (await _rpc(client, "tools/call", {"name": "drop_table"}))["error"]["code"] == -32602
    notification = await client.post(
        "/mcp/insights",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=_auth("analyst-token"),
    )
    assert notification.status_code == 202


async def test_mcp_overview_resource(client: httpx.AsyncClient, space: Space) -> None:
    resources = (await _rpc(client, "resources/list"))["result"]["resources"]
    assert [r["uri"] for r in resources] == ["niadra://tenant/overview"]
    read = await _rpc(client, "resources/read", {"uri": "niadra://tenant/overview"})
    assert '"day":null' in read["result"]["contents"][0]["text"]


async def test_rest_knowledge_gaps_and_their_review(client: httpx.AsyncClient, space: Space) -> None:
    assert (await client.get("/v1/insights/gaps")).status_code == 401
    assert (await client.get("/v1/insights/gaps", headers=_auth("nia_sk_agent"))).status_code == 403
    assert (await client.get("/v1/insights/gaps", headers=_auth("security-token"))).status_code == 403
    assert (await client.get("/v1/insights/gaps?days=91", headers=_auth("analyst-token"))).status_code == 422
    found = await client.get("/v1/insights/gaps?days=7", headers=_auth("analyst-token"))
    assert found.status_code == 200, found.text
    body = found.json()
    assert (body["gaps"], body["recurring"], body["proposals"], body["withheld_groups"]) == ([], [], [], 0)
    review = {"key": "no_record:refund:billing", "decision": "accepted", "note": "connect the ERP refunds"}
    answered = await client.post("/v1/insights/gaps/review", json=review, headers=_auth("nia_sk_analytics"))
    assert answered.status_code == 200, answered.text
    assert answered.json()["decision"] == "accepted"
    wrong = {**review, "decision": "maybe"}
    assert (
        await client.post("/v1/insights/gaps/review", json=wrong, headers=_auth("analyst-token"))
    ).status_code == 422
