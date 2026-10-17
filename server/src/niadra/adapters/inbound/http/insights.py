"""Insights over HTTP (5.21; 06, 4.6): `/v1/insights/*` and the analytical MCP at `/mcp/insights`.

Both surfaces run the same tool table, so REST, MCP and function calling never drift apart. Callers are
people with the analysis role or keys with the `analytics` scope; the service applies the gates.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from niadra.adapters.inbound.http.auth import authenticate
from niadra.adapters.inbound.http.deps import service
from niadra.app.insights import InsightsService
from niadra.contracts.common import Model
from niadra.contracts.insights import (
    AggregateRequest,
    AggregateResponse,
    AggregateRowOut,
    CustomerContextRequest,
    CustomerContextResponse,
    DescribeSchemaRequest,
    FindProfilesRequest,
    FindProfilesResponse,
    GapOut,
    GapReviewOut,
    GapReviewRequest,
    GapSource,
    GapsResponse,
    LineageRequest,
    OverviewResponse,
    ProposalOut,
    RecurringOut,
    RevealRequest,
    RevealResponse,
    SampleEpisodesRequest,
    SampleEpisodesResponse,
    SearchSemanticRequest,
    SearchSemanticResponse,
)
from niadra.domain.common.errors import NiadraError
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.insights.gaps import GapsReport

MCP_PROTOCOL = "2025-06-18"
OVERVIEW_URI = "niadra://tenant/overview"


async def analyst(request: Request) -> Principal:
    principal = await authenticate(request)
    if isinstance(principal, SourcePrincipal):
        await request.app.state.container.edge.check_rate(principal, request.url.path)
    return principal


Caller = Annotated[Principal, Depends(analyst)]
Service = Annotated[InsightsService, Depends(service("insights"))]
Runner = Callable[[InsightsService, Principal, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    description: str
    request: type[Model]
    run: Runner


async def _describe(s: InsightsService, p: Principal, _: DescribeSchemaRequest) -> dict[str, Any]:
    return await s.describe_schema(p)


async def _aggregate(s: InsightsService, p: Principal, r: AggregateRequest) -> AggregateResponse:
    result = await s.aggregate(p, r.entity, r.metrics, r.group_by, r.filters, r.since, r.until)
    return AggregateResponse(
        rows=[
            AggregateRowOut(
                group=dict(zip(result.group_by, row.group, strict=True)),
                count=row.count,
                profiles=row.profiles,
                avg_sentiment=row.avg_sentiment,
            )
            for row in result.rows
        ],
        suppressed_groups=result.suppressed_groups,
        min_group_size=result.min_group_size,
    )


async def _find(s: InsightsService, p: Principal, r: FindProfilesRequest) -> FindProfilesResponse:
    page = await s.find_profiles(
        p, r.entity, r.filters, r.since, r.until, min_matches=r.min_matches, cursor=r.cursor, limit=r.limit
    )
    return FindProfilesResponse.model_validate(page, from_attributes=True)


async def _search(s: InsightsService, p: Principal, r: SearchSemanticRequest) -> SearchSemanticResponse:
    found = await s.search_semantic(p, r.query, r.since, r.until, r.limit)
    return SearchSemanticResponse.model_validate(found, from_attributes=True)


async def _sample(s: InsightsService, p: Principal, r: SampleEpisodesRequest) -> SampleEpisodesResponse:
    samples = await s.sample_episodes(p, r.filters, r.since, r.until, r.limit)
    return SampleEpisodesResponse.model_validate(samples, from_attributes=True)


async def _context(s: InsightsService, p: Principal, r: CustomerContextRequest) -> CustomerContextResponse:
    view = await s.get_customer_context(p, r.pseudonym)
    return CustomerContextResponse.model_validate(view, from_attributes=True)


async def _lineage(s: InsightsService, p: Principal, r: LineageRequest) -> dict[str, Any]:
    return await s.explain_lineage(p, r.pseudonym, r.item_id)


async def _reveal(s: InsightsService, p: Principal, r: RevealRequest) -> RevealResponse:
    return RevealResponse.model_validate(await s.request_reveal(p, r.pseudonym, r.reason))


TOOLS: dict[str, Tool] = {
    "describe_schema": Tool(
        "What can be asked in this space: entities, dimensions, metrics, known values and limits.",
        DescribeSchemaRequest,
        _describe,
    ),
    "aggregate": Tool(
        "Counts, distinct profiles and average sentiment of an entity, grouped and filtered over a window."
        " Groups smaller than the minimum size are withheld.",
        AggregateRequest,
        _aggregate,
    ),
    "find_profiles": Tool(
        "Pseudonyms of the profiles matching filters, with the reason of each match; paginated.",
        FindProfilesRequest,
        _find,
    ),
    "search_semantic": Tool(
        "Profiles whose conversation summaries mean something close to the query, across the whole space.",
        SearchSemanticRequest,
        _search,
    ),
    "sample_episodes": Tool(
        "A random sample of redacted conversation summaries matching filters, to read examples.",
        SampleEpisodesRequest,
        _sample,
    ),
    "get_customer_context": Tool(
        "One customer by pseudonym: derived facts, summaries, open items and traits, redacted.",
        CustomerContextRequest,
        _context,
    ),
    "explain_lineage": Tool(
        "Where one item of a customer's memory came from: events, source, dates.",
        LineageRequest,
        _lineage,
    ),
    "request_reveal": Tool(
        "Exchanges a pseudonym for the real identifiers. Needs an elevated role or scope and a reason;"
        " the reveal is recorded.",
        RevealRequest,
        _reveal,
    ),
}

router = APIRouter()


@router.get("/v1/insights/schema")
async def describe_schema(caller: Caller, svc: Service) -> dict[str, Any]:
    return await _describe(svc, caller, DescribeSchemaRequest())


@router.post("/v1/insights/aggregate", response_model=AggregateResponse)
async def aggregate(body: AggregateRequest, caller: Caller, svc: Service) -> AggregateResponse:
    return await _aggregate(svc, caller, body)


@router.post("/v1/insights/profiles", response_model=FindProfilesResponse)
async def find_profiles(body: FindProfilesRequest, caller: Caller, svc: Service) -> FindProfilesResponse:
    return await _find(svc, caller, body)


@router.post("/v1/insights/search", response_model=SearchSemanticResponse)
async def search_semantic(
    body: SearchSemanticRequest, caller: Caller, svc: Service
) -> SearchSemanticResponse:
    return await _search(svc, caller, body)


@router.post("/v1/insights/episodes/sample", response_model=SampleEpisodesResponse)
async def sample_episodes(
    body: SampleEpisodesRequest, caller: Caller, svc: Service
) -> SampleEpisodesResponse:
    return await _sample(svc, caller, body)


@router.post("/v1/insights/context", response_model=CustomerContextResponse)
async def customer_context(
    body: CustomerContextRequest, caller: Caller, svc: Service
) -> CustomerContextResponse:
    return await _context(svc, caller, body)


@router.post("/v1/insights/lineage")
async def explain_lineage(body: LineageRequest, caller: Caller, svc: Service) -> dict[str, Any]:
    return await _lineage(svc, caller, body)


@router.post("/v1/insights/reveal", response_model=RevealResponse)
async def request_reveal(body: RevealRequest, caller: Caller, svc: Service) -> RevealResponse:
    return await _reveal(svc, caller, body)


@router.get("/v1/insights/overview", response_model=OverviewResponse)
async def overview(caller: Caller, svc: Service) -> OverviewResponse:
    return OverviewResponse.model_validate(await svc.overview(caller), from_attributes=True)


@router.get("/v1/insights/gaps", response_model=GapsResponse)
async def knowledge_gaps(
    caller: Caller, svc: Service, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> GapsResponse:
    """What customers asked that memory had no record of, what agents had to ask them for, which source
    could record it, the questions memory answers every time and the standing answers proposed (13, 6.5).
    People with the analysis or admin role, or keys with the `analytics` scope."""
    return gaps_out(await svc.gaps(caller, days))


@router.post("/v1/insights/gaps/review", response_model=GapReviewOut)
async def review_gap(body: GapReviewRequest, caller: Caller, svc: Service) -> GapReviewOut:
    """The team's word on a gap or a proposal: accepted or dismissed, with a note. A proposal takes effect
    only through the configuration the team approves."""
    decision, by, at, note = await svc.review_gap(caller, body.key, body.decision, body.note)
    return GapReviewOut(decision=decision, decided_by=by, decided_at=at, note=note)


def gaps_out(report: GapsReport) -> GapsResponse:
    def review(key: str) -> GapReviewOut | None:
        found = report.reviews.get(key)
        if found is None:
            return None
        decision, by, at, note = found
        return GapReviewOut(decision=decision, decided_by=by, decided_at=at, note=note)

    return GapsResponse(
        since=report.since,
        until=report.until,
        gaps=[
            GapOut(
                key=g.key,
                kind=g.kind.value,
                subject=g.subject,
                topic=g.topic,
                customers=g.customers,
                turns=g.turns,
                could_record=[
                    GapSource(source_id=h.source_id, name=h.name, via=h.via) for h in g.could_record
                ],
                answered_share=g.answered_share,
                review=review(g.key),
            )
            for g in report.gaps
        ],
        recurring=[
            RecurringOut(
                key=r.key,
                subject=r.subject,
                topic=r.topic,
                customers=r.customers,
                turns=r.turns,
                answered_share=r.answered_share,
                review=review(r.key),
            )
            for r in report.recurring
        ],
        proposals=[
            ProposalOut(
                key=p.key, kind=p.kind, subject=p.subject, topic=p.topic, text=p.text, review=review(p.key)
            )
            for p in report.proposals
        ],
        withheld_groups=report.withheld_groups,
    )


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@router.post("/mcp/insights")
async def mcp(request: Request, caller: Caller, svc: Service) -> Response:
    """JSON-RPC 2.0 over HTTP, one message per request; tool failures come back as `isError` results."""
    try:
        message = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return _rpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "invalid request")
    if "id" not in message:
        return Response(status_code=202)
    try:
        result = await _dispatch(str(message["method"]), message.get("params") or {}, svc, caller)
    except JsonRpcError as exc:
        return _rpc_error(message["id"], exc.code, exc.message)
    return JSONResponse({"jsonrpc": "2.0", "id": message["id"], "result": jsonable_encoder(result)})


async def _dispatch(method: str, params: dict[str, Any], svc: InsightsService, caller: Principal) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": MCP_PROTOCOL,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "niadra-insights", "version": "1"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": name,
                    "description": tool.description,
                    "inputSchema": tool.request.model_json_schema(),
                }
                for name, tool in TOOLS.items()
            ]
        }
    if method == "tools/call":
        return await _call_tool(params, svc, caller)
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": OVERVIEW_URI,
                    "name": "overview",
                    "mimeType": "application/json",
                    "description": "Yesterday's volumes by intent, outcome, category, channel and more.",
                }
            ]
        }
    if method == "resources/read":
        if params.get("uri") != OVERVIEW_URI:
            raise JsonRpcError(-32602, "unknown resource")
        body = OverviewResponse.model_validate(
            await svc.overview(caller), from_attributes=True
        ).model_dump_json()
        return {"contents": [{"uri": OVERVIEW_URI, "mimeType": "application/json", "text": body}]}
    raise JsonRpcError(-32601, "method not found")


async def _call_tool(params: dict[str, Any], svc: InsightsService, caller: Principal) -> dict[str, Any]:
    tool = TOOLS.get(str(params.get("name")))
    if tool is None:
        raise JsonRpcError(-32602, "unknown tool")
    try:
        arguments = tool.request.model_validate(params.get("arguments") or {})
        data = jsonable_encoder(await tool.run(svc, caller, arguments))
    except ValidationError as exc:
        fields = "; ".join(".".join(str(p) for p in e["loc"]) + f": {e['type']}" for e in exc.errors())
        return _tool_error("invalid_input", fields)
    except NiadraError as exc:
        return _tool_error(exc.code, exc.detail)
    # Memory content is customer text: the marker keeps the analyst's LLM from taking it as instructions.
    text = "<data>" + json.dumps(data, ensure_ascii=False) + "</data>"
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}


def _tool_error(code: str, detail: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"{code}: {detail}"}], "isError": True}


def _rpc_error(message_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})
