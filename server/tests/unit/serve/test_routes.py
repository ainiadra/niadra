"""The read routes and the MCP server over the FastAPI app (7.1, 5.17)."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http import mcp, read
from niadra.adapters.inbound.http.app import create_app
from niadra.app.policy import PolicyService
from niadra.contracts.context import ContextResponse, LiveTurn, VerificationResult
from niadra.contracts.events import BatchRequest, BatchResponse, EventItem, FeedbackRequest, IdentifyItem
from niadra.domain.common.errors import RateLimited, Unauthenticated
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.vocabulary import AssertionMethod, DeliveryPath, EventKind, Scope, Verification
from niadra.testing.cell import BILLING_SOURCE, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.compile.factories import PROFILE, uid
from tests.unit.serve.harness import Harness

SUBJECT = {"type": "phone_e164", "value": "+55 11 99999-0001"}


class FakeEdge:
    def __init__(self, keys: dict[str, SourcePrincipal]) -> None:
        self.keys = keys
        self.rated: list[str] = []

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token not in self.keys:
            raise Unauthenticated("unknown key")
        return self.keys[token]

    async def check_rate(self, principal: SourcePrincipal, path: str) -> None:
        self.rated.append(path)


class FakeIngest:
    def __init__(self) -> None:
        self.batches: list[BatchRequest] = []
        self.feedback: list[FeedbackRequest] = []

    async def record_feedback(self, caller: SourcePrincipal, request: FeedbackRequest) -> BatchResponse:
        self.feedback.append(request)
        return BatchResponse(accepted=1, duplicates=0, errors=[])

    async def ingest_batch(self, caller: SourcePrincipal, request: BatchRequest) -> BatchResponse:
        self.batches.append(request)
        return BatchResponse(accepted=len(request.items), duplicates=0, errors=[])


@pytest.fixture
async def app(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, Harness, SimpleNamespace]]:
    h = Harness(cell)
    h.world.proven = Verification.V2
    await h.compile()
    keys = {
        "nia_sk_bot": principal(),
        "nia_sk_track": principal(scopes=frozenset({Scope.TRACK})),
        "nia_sk_admin": principal(scopes=frozenset(Scope)),
        "nia_sk_billing": principal(BILLING_SOURCE),
    }
    container = SimpleNamespace(
        edge=FakeEdge(keys),
        serve=h.serve,
        policy=PolicyService(h.uow, cell.config, live=h.serve),
        ingest=FakeIngest(),
        clock=cell.clock,
        admin_audit=RecordingAdminAudit(),
        config=cell.config,
    )
    asgi = create_app("read", container, [read.router, read.policy_router, mcp.router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://cell") as client:
        yield client, h, container


def auth(key: str = "nia_sk_bot", **extra: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}", **extra}


async def test_post_context_and_the_etag_round_trip(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    read = {"subject": SUBJECT, "verification": "V1", "conversation_id": "wa-now"}
    response = await client.post("/v1/context", json=read, headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "t0"
    assert "[Cliente] Marina" in body["text"]
    again = await client.post("/v1/context", json=read | {"known_etag": body["etag"]}, headers=auth())
    assert again.json()["not_modified"] is True
    assert "text" not in again.json()
    by_id = await client.get("/v1/context", params={"profile_id": str(PROFILE)}, headers=auth())
    assert by_id.status_code == 200
    # A 200 carries the ETag in the header too, so an HTTP cache or client can use it as is.
    assert by_id.headers["etag"] == f'"{by_id.json()["etag"]}"'
    unchanged = await client.get(
        "/v1/context",
        params={"profile_id": str(PROFILE)},
        headers=auth(**{"if-none-match": by_id.headers["etag"]}),
    )
    assert unchanged.status_code == 304
    assert unchanged.headers["etag"] == by_id.headers["etag"]


async def test_context_errors_are_problems(app: tuple[httpx.AsyncClient, Harness, SimpleNamespace]) -> None:
    client, _, _ = app
    assert (await client.post("/v1/context", json={"subject": SUBJECT})).status_code == 401
    missing_scope = await client.post("/v1/context", json={"subject": SUBJECT}, headers=auth("nia_sk_track"))
    assert missing_scope.status_code == 403
    both = await client.post(
        "/v1/context",
        json={"subject": SUBJECT, "object": {"type": "a", "namespace": "b", "id": "c"}},
        headers=auth(),
    )
    assert both.status_code == 422
    about = await client.post(
        "/v1/context",
        json={"subject": SUBJECT, "about": {"type": "system_id", "value": "x", "scope": "crm"}},
        headers=auth(),
    )
    assert about.status_code == 200
    assert about.json()["about_unlinked"] is True


async def test_history_routes(app: tuple[httpx.AsyncClient, Harness, SimpleNamespace]) -> None:
    client, _, _ = app
    found = await client.post(
        "/v1/history/search",
        json={"subject": SUBJECT, "query": "visita", "verification": "V2", "conversation_id": "c1"},
        headers=auth(),
    )
    assert found.status_code == 200
    assert found.json()["recurrence"]["occurrences"] == 3
    page = await client.post(
        "/v1/history/timeline",
        json={"subject": SUBJECT, "limit": 2, "verification": "V2", "conversation_id": "c1"},
        headers=auth(),
    )
    assert page.status_code == 200
    assert page.json()["next_cursor"]
    by_profile = await client.get(
        "/v1/history/timeline",
        params={
            "profile_id": str(PROFILE),
            "verification": "V2",
            "conversation_id": "c1",
            "cursor": page.json()["next_cursor"],
        },
        headers=auth(),
    )
    assert by_profile.status_code == 200
    assert by_profile.json()["items"]
    item = found.json()["items"][0]["id"]
    opened = await client.get(
        f"/v1/history/items/{item}", params={"verification": "V2", "conversation_id": "c1"}, headers=auth()
    )
    assert opened.status_code == 200
    assert opened.json()["kind"] == "episode"
    assert (await client.get(f"/v1/history/items/episode:{uid(0x999)}", headers=auth())).status_code == 404
    # Every read addressed by profile has a POST twin with the conversation id in the body.
    in_body = {"profile_id": str(PROFILE), "verification": "V2", "conversation_id": "c1"}
    timeline = await client.post("/v1/history/timeline", json=in_body | {"limit": 2}, headers=auth())
    assert timeline.json()["items"] == page.json()["items"]
    opened_by_body = await client.post("/v1/history/open", json=in_body | {"item_id": item}, headers=auth())
    assert opened_by_body.json() == opened.json()
    context = await client.post("/v1/context", json=in_body, headers=auth())
    assert context.status_code == 200
    assert context.json()["text"]
    both = await client.post("/v1/context", json=in_body | {"subject": SUBJECT}, headers=auth())
    assert both.status_code == 422
    tools = await client.get("/v1/history/tools", headers=auth())
    assert [t["function"]["name"] for t in tools.json()] == [
        "search_customer_history",
        "get_customer_timeline",
        "open_history_item",
    ]


async def test_subject_tokens_and_the_simulator(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    minted = await client.post(
        "/v1/subject-tokens",
        json={"subject": SUBJECT, "conversation_id": "c1", "verification": "V2"},
        headers=auth(),
    )
    assert minted.status_code == 201
    assert minted.json()["token"].startswith("nst1.")
    too_high = await client.post(
        "/v1/subject-tokens", json={"subject": SUBJECT, "verification": "V4"}, headers=auth()
    )
    assert too_high.status_code == 422
    assert too_high.json()["code"] == "verification_not_allowed"
    simulated = await client.post(
        "/v1/policy/simulate",
        json={"profile_id": str(PROFILE), "verification": "V1"},
        headers=auth("nia_sk_admin"),
    )
    assert simulated.status_code == 200
    # The simulated reader holds none of the purposes billing and health are released to: no level would
    # release them, so they are the policy's, never the level's.
    assert simulated.json()["withheld"] == {"policy": 4}
    # The `live` block of the same read, through the same policy (no recent turn in this world).
    assert simulated.json()["live"] == {"turns": [], "delivered": 0, "withheld": {}, "complete": True}
    # A simulation shows a profile's data: it leaves an admin receipt, ids only (5.22).
    assert container.admin_audit.records == [
        {"action": "policy.simulate", "profile_id": PROFILE, "target": "audience:customer_agent"}
    ]
    assert (
        await client.post("/v1/policy/simulate", json={"profile_id": str(PROFILE)}, headers=auth())
    ).status_code == 403
    assert len(container.admin_audit.records) == 1


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    token: str = "",
    key: str = "nia_sk_bot",
) -> dict[str, Any]:
    headers = auth(key, **({"niadra-subject-token": token} if token else {}))
    response = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}, headers=headers
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


async def token_for(client: httpx.AsyncClient, key: str = "nia_sk_bot") -> str:
    response = await client.post(
        "/v1/subject-tokens",
        json={"subject": SUBJECT, "conversation_id": "c1", "verification": "V2"},
        headers=auth(key),
    )
    token: str = response.json()["token"]
    return token


async def test_mcp_handshake_and_listings(app: tuple[httpx.AsyncClient, Harness, SimpleNamespace]) -> None:
    client, _, _ = app
    init = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        headers=auth(),
    )
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert init.headers["mcp-session-id"]
    note = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth()
    )
    assert note.status_code == 202
    names = [t["name"] for t in (await rpc(client, "tools/list"))["result"]["tools"]]
    # A customer-facing source never gets resolve_identity by default.
    assert names == [
        "get_customer_context",
        "track_event",
        "record_action",
        "search_customer_history",
        "get_customer_timeline",
        "open_history_item",
        "correct_customer_memory",
    ]
    internal = [t["name"] for t in (await rpc(client, "tools/list", key="nia_sk_billing"))["result"]["tools"]]
    assert "resolve_identity" in internal
    assert (await rpc(client, "resources/list"))["result"]["resources"][0]["uri"] == "niadra://context"
    assert (await rpc(client, "prompts/list"))["result"]["prompts"][0]["name"] == "customer_context"
    assert (await rpc(client, "nope"))["error"]["code"] == -32601
    assert (await client.get("/mcp", headers=auth())).status_code == 405


async def test_mcp_tools_are_bound_to_the_token_subject(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    token = await token_for(client)
    context = await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}}, token=token)
    assert "[Cliente] Marina" in context["result"]["content"][0]["text"]
    assert context["result"]["structuredContent"]["verification"]["effective"] == "V2"
    searched = await rpc(
        client,
        "tools/call",
        {"name": "search_customer_history", "arguments": {"query": "visita"}},
        token=token,
    )
    assert "recurrence: technical_visit 3x" in searched["result"]["content"][0]["text"]
    first_id = searched["result"]["structuredContent"]["items"][0]["id"]
    opened = await rpc(
        client, "tools/call", {"name": "open_history_item", "arguments": {"id": first_id}}, token=token
    )
    assert opened["result"]["isError"] is False
    page = await rpc(
        client, "tools/call", {"name": "get_customer_timeline", "arguments": {"limit": 1}}, token=token
    )
    assert page["result"]["structuredContent"]["next_cursor"]
    read = await rpc(client, "resources/read", {"uri": "niadra://context"}, token=token)
    assert "Marina" in read["result"]["contents"][0]["text"]
    assert "/mcp/get_customer_context" in container.edge.rated


async def test_mcp_context_takes_object_and_about_like_context(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, h, _ = app
    token = await token_for(client)
    tools = (await rpc(client, "tools/list"))["result"]["tools"]
    schema = next(t for t in tools if t["name"] == "get_customer_context")["inputSchema"]["properties"]
    assert {"object", "about"} <= set(schema)
    invoice = {"type": "invoice", "namespace": "erp", "id": "0823"}
    focused = await rpc(
        client, "tools/call", {"name": "get_customer_context", "arguments": {"object": invoice}}, token=token
    )
    assert focused["result"]["isError"] is False
    assert h.receipts.sent[-1].object_id == uid(0x700)
    unknown = {**invoice, "id": "9999"}
    refused = await rpc(
        client, "tools/call", {"name": "get_customer_context", "arguments": {"object": unknown}}, token=token
    )
    assert refused["result"]["content"][0]["text"] == "error: not_found"
    about = {"type": "email_domain", "value": "acme.com.br"}
    no_link = await rpc(
        client, "tools/call", {"name": "get_customer_context", "arguments": {"about": about}}, token=token
    )
    # Without an active link the read fails soft and never says whether the organization exists (5.11).
    assert no_link["result"]["isError"] is False
    assert no_link["result"]["structuredContent"]["about_unlinked"] is True
    assert no_link["result"]["content"][0]["text"].endswith(mcp.ABOUT_UNLINKED)
    malformed = await rpc(
        client, "tools/call", {"name": "get_customer_context", "arguments": {"about": {"x": 1}}}, token=token
    )
    assert malformed["result"]["content"][0]["text"] == "error: invalid_input"


async def test_a_token_bound_organization_cannot_be_switched_by_the_model(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    acme = {"type": "email_domain", "value": "acme.com.br"}
    minted = await client.post(
        "/v1/subject-tokens",
        json={"subject": SUBJECT, "about": acme, "conversation_id": "c1", "verification": "V2"},
        headers=auth(),
    )
    token = minted.json()["token"]
    bound = await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}}, token=token)
    # The token's organization is used without the model naming it; the read still checks the link.
    assert bound["result"]["structuredContent"]["about_unlinked"] is True
    other = {"type": "email_domain", "value": "globex.com"}
    switched = await rpc(
        client, "tools/call", {"name": "get_customer_context", "arguments": {"about": other}}, token=token
    )
    assert switched["error"]["data"]["code"] == "forbidden"


async def test_mcp_writes_go_to_ingest_with_the_bound_subject(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    token = await token_for(client)
    tracked = await rpc(
        client, "tools/call", {"name": "track_event", "arguments": {"text": "cliente ligou"}}, token=token
    )
    assert tracked["result"]["isError"] is False
    acted = await rpc(
        client,
        "tools/call",
        {
            "name": "record_action",
            "arguments": {
                "operation": "credit",
                "result": "R$ 40",
                "object": {"type": "invoice", "namespace": "erp", "id": "0823"},
            },
        },
        token=token,
    )
    assert acted["result"]["isError"] is False
    event, action = (b.items[0] for b in container.ingest.batches)
    assert isinstance(event, EventItem)
    assert event.kind is EventKind.MESSAGE
    assert event.conversation_id == "c1"
    assert event.handles[0].value == SUBJECT["value"]
    assert isinstance(action, EventItem)
    assert action.action is not None
    assert action.action.operation == "credit"
    billing_token = await token_for(client, "nia_sk_billing")
    linked = await rpc(
        client,
        "tools/call",
        {
            "name": "resolve_identity",
            "arguments": {"handles": [{"type": "email", "value": "marina@example.com"}]},
        },
        token=billing_token,
        key="nia_sk_billing",
    )
    assert linked["result"]["isError"] is False
    identify = container.ingest.batches[-1].items[0]
    assert isinstance(identify, IdentifyItem)
    # What a model passes on is what the customer typed: a declaration, never an explicit identify.
    assert identify.method is AssertionMethod.DECLARED


async def test_mcp_refuses_missing_foreign_and_unscoped_calls(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    no_token = await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}})
    assert no_token["error"]["data"]["code"] == "unauthenticated"
    foreign = await token_for(client, "nia_sk_billing")
    stolen = await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}}, token=foreign)
    assert stolen["error"]["data"]["code"] == "forbidden"
    token = await token_for(client)
    unscoped = await rpc(
        client, "tools/call", {"name": "resolve_identity", "arguments": {"handles": []}}, token=token
    )
    assert unscoped["result"]["isError"] is True
    unknown = await rpc(client, "tools/call", {"name": "delete_everything", "arguments": {}}, token=token)
    assert unknown["error"]["code"] == -32602
    bad = await rpc(
        client, "tools/call", {"name": "search_customer_history", "arguments": {"query": ""}}, token=token
    )
    assert bad["result"]["isError"] is True


async def test_a_read_the_policy_refuses_leaves_a_denied_receipt(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    # "the billing agent tried and was refused" is on the chain, never what it asked for.
    client, h, _ = app
    no_search = await client.post(
        "/v1/history/search", json={"subject": SUBJECT, "query": "fatura"}, headers=auth("nia_sk_track")
    )
    assert (no_search.status_code, no_search.json()["code"]) == (403, "scope_missing")
    no_customer = await client.post(
        "/v1/context", json={"subject": SUBJECT, "verification": "no_customer"}, headers=auth()
    )
    assert no_customer.status_code == 422
    denied = [r for r in h.receipts.sent if r.kind.value == "denied"]
    assert [(r.surface.value, r.detail["reason"]) for r in denied] == [
        ("search", "scope_missing"),
        ("pack", "verification_not_allowed"),
    ]
    assert all(r.profile_id is None and r.item_ids == () for r in denied)
    assert "fatura" not in str(denied)


async def test_an_unknown_key_never_reaches_the_chain(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, h, _ = app
    refused = await client.post("/v1/context", json={"subject": SUBJECT}, headers=auth("nia_sk_nobody"))
    assert refused.status_code == 401
    assert h.receipts.sent == []


async def test_an_mcp_call_with_another_sources_token_is_refused_on_the_chain(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, h, _ = app
    foreign = await token_for(client, "nia_sk_billing")
    stolen = await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}}, token=foreign)
    assert stolen["error"]["data"]["code"] == "forbidden"
    [denied] = [r for r in h.receipts.sent if r.kind.value == "denied"]
    assert (denied.surface.value, denied.detail["reason"], denied.source_id) == (
        "pack",
        "forbidden",
        principal().source_id,
    )


async def test_an_mcp_call_without_a_required_argument_tells_the_model_what_is_missing(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    token = await token_for(client)
    missing = await rpc(
        client, "tools/call", {"name": "search_customer_history", "arguments": {}}, token=token
    )
    assert missing["result"]["isError"] is True
    assert missing["result"]["content"][0]["text"] == "error: invalid_input (missing argument: query)"
    opened = await rpc(client, "tools/call", {"name": "open_history_item", "arguments": {}}, token=token)
    assert opened["result"]["content"][0]["text"] == "error: invalid_input (missing argument: id)"


async def test_each_mcp_request_counts_once_against_the_rate_limit(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    token = await token_for(client)
    container.edge.rated.clear()
    await rpc(client, "tools/call", {"name": "get_customer_context", "arguments": {}}, token=token)
    assert container.edge.rated == ["/mcp/get_customer_context"]
    container.edge.rated.clear()
    await rpc(client, "tools/list", token=token)
    assert container.edge.rated == ["/mcp"]


async def test_an_mcp_request_over_the_limit_is_an_http_429_with_retry_after(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    token = await token_for(client)

    async def over(principal: SourcePrincipal, path: str) -> None:
        raise RateLimited(retry_after_s=1, detail="source key over its rate limit")

    container.edge.check_rate = over
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_customer_context"}},
        headers=auth("nia_sk_bot", **{"niadra-subject-token": token}),
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json()["code"] == "rate_limited"


async def test_context_as_data_carries_the_same_lines_by_section(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    read = {"subject": SUBJECT, "verification": "V1", "conversation_id": "wa-json", "format": "json"}
    body = (await client.post("/v1/context", json=read, headers=auth())).json()
    pack = body["pack"]
    assert pack["spec"] == "context-pack.v1"
    assert pack["stamp"]["etag"] == body["etag"]
    assert pack["verification"] == body["verification"]["effective"]
    customer = next(s for s in pack["sections"] if s["name"] == "customer")
    assert (customer["label"], customer["layer"]) == ("Cliente", "stable")
    assert customer["lines"][0].startswith("Marina")
    # Every line of the data form is in the text, under its label: the two never disagree.
    for section in pack["sections"]:
        for line in section["lines"]:
            assert f"[{section['label']}] {line}" in body["text"]
    assert pack["preamble"] in body["text"]
    assert "customer" not in pack["variables"]
    plain = (await client.post("/v1/context", json=read | {"format": "text"}, headers=auth())).json()
    assert "pack" not in plain
    by_id = await client.get(
        "/v1/context", params={"profile_id": str(PROFILE), "format": "json"}, headers=auth()
    )
    assert by_id.json()["pack"]["stamp"]["etag"] == by_id.json()["etag"]


async def test_explain_is_refused_outside_the_typed_pack(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    read = {"subject": SUBJECT, "conversation_id": "wa-explain", "query": "E o protocolo?", "explain": True}
    refused = await client.post("/v1/context", json=read, headers=auth())
    assert refused.status_code == 422
    typed = await client.post("/v1/context", json=read | {"format": "json"}, headers=auth())
    assert typed.status_code == 200


async def test_the_kit_offers_agent_memory_tools_on_request(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    names = lambda r: [t["function"]["name"] for t in r.json()]  # noqa: E731
    reader = principal(scopes=frozenset({Scope.SEARCH, Scope.CONTEXT}))
    client_keys = client._transport.app.state.container.edge.keys  # type: ignore[attr-defined]
    client_keys["nia_sk_reader"] = reader
    with_memory = await client.get("/v1/history/tools", params={"agent_memory": "true"}, headers=auth())
    assert names(with_memory)[-2:] == ["search_agent_memory", "remember"]
    read_only = await client.get(
        "/v1/history/tools", params={"agent_memory": "true"}, headers=auth("nia_sk_reader")
    )
    assert names(read_only)[-1] == "search_agent_memory"
    assert "remember" not in names(read_only)


async def test_a_customers_correction_needs_a_fact_this_agent_may_read(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, container = app
    token = await token_for(client)
    searched = await rpc(
        client,
        "tools/call",
        {
            "name": "search_customer_history",
            "arguments": {"query": "fatura", "filters": {"item_kinds": ["fact"]}},
        },
        token=token,
    )
    [found] = searched["result"]["structuredContent"]["items"]
    fact_id = found["id"]
    assert found["kind"] == "fact"

    fixed = await rpc(
        client,
        "tools/call",
        {
            "name": "correct_customer_memory",
            "arguments": {"fact_id": fact_id, "action": "correct", "value": "fatura de setembro"},
        },
        token=token,
    )
    assert fixed["result"]["isError"] is False
    [request] = container.ingest.feedback
    assert (request.action, request.fact_id, request.value) == (
        "correct_fact",
        UUID(fact_id),
        "fatura de setembro",
    )
    assert (request.subject.value, request.conversation_id) == (SUBJECT["value"], "c1")

    # A fact the policy withholds at this level, or another customer's, is not there for the model.
    unseen = await rpc(
        client,
        "tools/call",
        {
            "name": "correct_customer_memory",
            "arguments": {"fact_id": str(uid(999)), "action": "retract"},
        },
        token=token,
    )
    no_value = await rpc(
        client,
        "tools/call",
        {"name": "correct_customer_memory", "arguments": {"fact_id": fact_id, "action": "correct"}},
        token=token,
    )
    assert unseen["result"]["isError"] is True
    assert no_value["result"]["isError"] is True
    assert len(container.ingest.feedback) == 1


async def test_prefetch_answers_202_at_once_and_counts_against_its_own_rate(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, h, container = app
    body = {"subject": SUBJECT, "conversation_id": "voz-1", "query": "qual o protocolo"}
    response = await client.post("/v1/context/prefetch", json=body, headers=auth())
    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert container.edge.rated[-1] == "/v1/context/prefetch"
    await h.serve.prefetches.drain()
    assert h.receipts.sent == []


async def test_prefetch_refuses_what_the_read_refuses(
    app: tuple[httpx.AsyncClient, Harness, SimpleNamespace],
) -> None:
    client, _, _ = app
    body = {"subject": SUBJECT, "query": "qual o protocolo"}
    assert (await client.post("/v1/context/prefetch", json=body)).status_code == 401
    missing = await client.post("/v1/context/prefetch", json=body, headers=auth("nia_sk_track"))
    assert missing.status_code == 403
    for wrong in (
        {"query": "qual o protocolo"},
        body | {"query": ""},
        body | {"query": "x" * 2001},
        body | {"object": {"type": "a", "namespace": "b", "id": "c"}},
        body | {"conversation_id": "a", "task_id": "b"},
        body | {"profile_id": str(PROFILE)},
    ):
        response = await client.post("/v1/context/prefetch", json=wrong, headers=auth())
        assert response.status_code == 422, wrong


def test_the_mcp_context_text_puts_the_turn_block_in_the_sdks_order() -> None:
    live = LiveTurn(
        at="2026-09-25T10:00:00Z",  # type: ignore[arg-type]
        channel="voice",
        kind=EventKind.MESSAGE,
        speaker="customer",
        text="caiu de novo",
        source_id="s",
    )
    response = ContextResponse(
        text="<pack/>",
        version="g1",
        etag="e",
        verification=VerificationResult(requested=Verification.V1, effective=Verification.V1),
        path=DeliveryPath.T0,
        live=[live],
        slots="<slots/>",
        delta="<delta/>",
    )
    text = mcp._context_text(response)
    assert (
        text.index("<pack/>") < text.index("caiu de novo") < text.index("<slots/>") < text.index("<delta/>")
    )
