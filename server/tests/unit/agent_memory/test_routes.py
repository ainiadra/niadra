"""The agent memory routes over the FastAPI app, and the two tools on the MCP server (10, C.6)."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from niadra.adapters.inbound.http import agent_memory, mcp
from niadra.adapters.inbound.http.app import create_app
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import Principal
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.agent_memory.test_service import CREDIT, World


class FakeEdge:
    def __init__(self, tokens: dict[str, Principal]) -> None:
        self.tokens = tokens

    async def authenticate_key(self, token: str) -> Principal:
        if token not in self.tokens:
            raise Unauthenticated("unknown key")
        return self.tokens[token]

    authenticate_person = authenticate_key

    async def check_rate(self, p: Principal, route: str) -> None:
        return None


@pytest.fixture
async def client(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, World]]:
    world = World(cell)
    tokens: dict[str, Principal] = {
        "nia_sk_writer": world.agent(),  # type: ignore[dict-item]
        "nia_sk_reader": world.agent(VOICE_SOURCE, Scope.CONTEXT),  # type: ignore[dict-item]
        "nia_sk_track": principal(WHATSAPP_SOURCE, world.config, scopes=frozenset({Scope.TRACK})),
        "integrator": world.person("integration"),
        "admin": world.person("admin"),
    }
    serve = SimpleNamespace(history_label=lambda caller: "Histórico")
    container = SimpleNamespace(
        edge=FakeEdge(tokens), agent_memory=world.service, config=cell.config, serve=serve, clock=cell.clock
    )
    app = create_app("admin", container, [agent_memory.router, mcp.router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as c:
        yield c, world


def auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_personal_data_is_a_422_with_its_own_code(client: tuple[httpx.AsyncClient, World]) -> None:
    c, _ = client
    body = {"kind": "pitfall", "title": "Call back", "body": "Ring +55 11 99999-0001 first"}
    refused = await c.post("/v1/agent-memory/notes", json=body, headers=auth("nia_sk_writer"))
    assert refused.status_code == 422
    assert refused.json()["code"] == "personal_data_in_agent_memory"
    assert "99999" not in refused.text


async def test_write_read_and_the_block_etag(client: tuple[httpx.AsyncClient, World]) -> None:
    c, _ = client
    created = await c.post(
        "/v1/agent-memory/notes", json=CREDIT.model_dump(mode="json"), headers=auth("nia_sk_writer")
    )
    assert created.status_code == 201
    note = created.json()["note"]
    assert "proposal_id" not in created.json()
    block = await c.get("/v1/agent-memory/block", headers=auth("nia_sk_writer"))
    assert block.json()["notes"] == [note["note_id"]]
    again = await c.get(
        "/v1/agent-memory/block", headers={**auth("nia_sk_writer"), "if-none-match": block.headers["etag"]}
    )
    assert again.status_code == 304
    # Another vendor's agent reads nothing of it, and cannot write without the scope.
    assert (await c.get("/v1/agent-memory/block", headers=auth("nia_sk_reader"))).json()["notes"] == []
    denied = await c.post(
        "/v1/agent-memory/notes", json=CREDIT.model_dump(mode="json"), headers=auth("nia_sk_reader")
    )
    assert (denied.status_code, denied.json()["code"]) == (403, "scope_missing")
    assert (await c.get("/v1/agent-memory/block", headers=auth("nia_sk_track"))).status_code == 403
    versions = await c.get(f"/v1/agent-memory/notes/{note['note_id']}/versions", headers=auth("integrator"))
    assert [v["version"] for v in versions.json()] == [1]


async def test_only_admins_erase_and_approve(client: tuple[httpx.AsyncClient, World]) -> None:
    c, _ = client
    source = str(WHATSAPP_SOURCE)
    assert (
        await c.delete(f"/v1/agent-memory/notes?source_id={source}", headers=auth("integrator"))
    ).status_code == 403
    erased = await c.delete(f"/v1/agent-memory/notes?source_id={source}", headers=auth("admin"))
    assert erased.json() == {"source_id": source, "deleted": 0, "receipt_id": None}


async def _rpc(c: httpx.AsyncClient, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    answer: dict[str, Any] = (await c.post("/mcp", json=body, headers=auth(token))).json()
    return answer


async def test_the_mcp_server_offers_the_agent_tools_when_the_space_turns_them_on(
    client: tuple[httpx.AsyncClient, World],
) -> None:
    c, _ = client
    listed = await _rpc(c, "nia_sk_writer", "tools/list", {})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert names[-2:] == ["search_agent_memory", "remember"]
    reader = [t["name"] for t in (await _rpc(c, "nia_sk_reader", "tools/list", {}))["result"]["tools"]]
    assert "remember" not in reader
    saved = await _rpc(
        c, "nia_sk_writer", "tools/call", {"name": "remember", "arguments": CREDIT.model_dump(mode="json")}
    )
    assert saved["result"]["isError"] is False
    refused = await _rpc(
        c,
        "nia_sk_writer",
        "tools/call",
        {"name": "remember", "arguments": {"kind": "pitfall", "title": "x", "body": "mail joe@example.com"}},
    )
    assert refused["result"]["content"][0]["text"] == "error: personal_data_in_agent_memory"
    found = await _rpc(
        c, "nia_sk_writer", "tools/call", {"name": "search_agent_memory", "arguments": {"query": "credit"}}
    )
    assert "post_credit" in found["result"]["content"][0]["text"]
