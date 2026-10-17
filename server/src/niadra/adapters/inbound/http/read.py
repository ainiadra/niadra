"""Read routes of `niadra-read` (7.1): context, history navigation and subject tokens (`router`); and the
policy simulator of `niadra-admin` (`policy_router`), which the proxy sends with the other governance routes.

Personal data travels only in bodies (7.0): reads by handle are POSTs, and the GET variants take
the pseudonymous profile id.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.observability.metrics import ReadSummary
from niadra.contracts.context import (
    ContextRequest,
    ContextResponse,
    OpenedItem,
    OpenItemRequest,
    PrefetchRequest,
    PrefetchResponse,
    SearchRequest,
    SearchResponse,
    TimelineRequest,
    TimelineResponse,
    ToolDefinition,
)
from niadra.contracts.policy import SimulateRequest, SimulateResponse
from niadra.contracts.serve import SubjectTokenRequest, SubjectTokenResponse
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.serve.tools import agent_memory_tools
from niadra.domain.vocabulary import Scope, Verification

router = APIRouter(tags=["read"])
policy_router = APIRouter(tags=["governance"])

Reader = Annotated[SourcePrincipal, Depends(require_source(Scope.CONTEXT))]
Navigator = Annotated[SourcePrincipal, Depends(require_source(Scope.SEARCH))]
Governor = Annotated[Principal, Depends(require_governance(Role.SECURITY))]
Serve = Annotated[Any, Depends(service("serve"))]
Policy = Annotated[Any, Depends(service("policy"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]


@router.post("/v1/context", response_model=ContextResponse, response_model_exclude_none=True)
async def context(body: ContextRequest, caller: Reader, serve: Serve, request: Request) -> Response:
    # The answer is rendered while the queue confirms its receipt, and sent only after (5.11).
    answer = await serve.context_answer(caller, body)
    content = rendered(answer.response)
    await answer.delivered()
    measured(request, answer)
    return Response(content, media_type="application/json")


@router.post(
    "/v1/context/prefetch",
    response_model=PrefetchResponse,
    status_code=202,
    summary="Prepare the read of a turn the customer is still speaking",
)
async def prefetch(body: PrefetchRequest, caller: Reader, serve: Serve) -> PrefetchResponse:
    """Send the partial transcript while the customer speaks; the server opens the customer's
    memory and computes the turn's slots in the background, so the `POST /v1/context` with the same
    `query` answers from that work. Answers 202 at once and never changes what a read returns. No
    receipt: only the read that uses the work leaves one. Counted against the key's rate limit on its
    own, so prefetches never cost a read its place."""
    queued: bool = serve.prefetch(caller, body)
    return PrefetchResponse(queued=queued)


@router.get(
    "/v1/context",
    response_model=ContextResponse,
    response_model_exclude_none=True,
    responses={304: {"description": "The pack behind `If-None-Match` is still current."}},
)
async def context_by_profile(
    caller: Reader,
    serve: Serve,
    request: Request,
    response: Response,
    profile_id: UUID,
    view: str = "chat",
    verification: Verification = Verification.V0,
    conversation_id: str | None = None,
    task_id: str | None = None,
    format: Literal["text", "json"] = "text",
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    answer = await serve.context_answer_for_profile(
        caller,
        profile_id,
        view=view,
        verification=verification,
        conversation_id=conversation_id,
        task_id=task_id,
        known_etag=if_none_match.strip('"') if if_none_match else None,
        format=format,
    )
    result: ContextResponse = await answer.delivered()
    measured(request, answer)
    if result.not_modified:
        return Response(status_code=304, headers={"ETag": f'"{result.etag}"'})
    if result.etag:
        response.headers["ETag"] = f'"{result.etag}"'
    return result


@router.post("/v1/history/search", response_model=SearchResponse, response_model_exclude_none=True)
async def search(body: SearchRequest, caller: Navigator, serve: Serve) -> SearchResponse:
    result: SearchResponse = await serve.search(caller, body)
    return result


@router.post("/v1/history/timeline", response_model=TimelineResponse, response_model_exclude_none=True)
async def timeline(body: TimelineRequest, caller: Navigator, serve: Serve) -> TimelineResponse:
    result: TimelineResponse = await serve.timeline(caller, body)
    return result


@router.get("/v1/history/timeline", response_model=TimelineResponse, response_model_exclude_none=True)
async def timeline_by_profile(
    caller: Navigator,
    serve: Serve,
    profile_id: UUID,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    verification: Verification = Verification.V0,
    conversation_id: str | None = None,
) -> TimelineResponse:
    result: TimelineResponse = await serve.timeline_for_profile(
        caller,
        profile_id,
        cursor=cursor,
        limit=limit,
        verification=verification,
        conversation_id=conversation_id,
    )
    return result


@router.get("/v1/history/items/{item_id}", response_model=OpenedItem, response_model_exclude_none=True)
async def open_item(
    item_id: str,
    caller: Navigator,
    serve: Serve,
    profile_id: UUID | None = None,
    verification: Verification = Verification.V0,
    conversation_id: str | None = None,
) -> OpenedItem:
    result: OpenedItem = await serve.open_item(
        caller, item_id, verification=verification, conversation_id=conversation_id, profile_id=profile_id
    )
    return result


@router.post("/v1/history/open", response_model=OpenedItem, response_model_exclude_none=True)
async def open_item_by_body(body: OpenItemRequest, caller: Navigator, serve: Serve) -> OpenedItem:
    """The same as the GET, with the conversation id and the customer in the body, never in the URL."""
    result: OpenedItem = await serve.open_item(
        caller,
        body.item_id,
        verification=body.verification,
        conversation_id=body.conversation_id,
        profile_id=body.profile_id,
        subject=body.subject,
        about=body.about,
    )
    return result


@router.get("/v1/history/tools", response_model=list[ToolDefinition])
async def tools(caller: Navigator, serve: Serve, agent_memory: bool = False) -> list[ToolDefinition]:
    """With `agent_memory=true`, also `search_agent_memory` and, for a key that may write, `remember`."""
    result: list[ToolDefinition] = serve.tools_definitions(caller)
    if agent_memory:
        extra = agent_memory_tools(caller.allows(Scope.AGENT_MEMORY_WRITE))
        result = [*result, *(ToolDefinition(function=tool.as_function()) for tool in extra)]
    return result


@router.post("/v1/subject-tokens", response_model=SubjectTokenResponse, status_code=201)
async def subject_token(body: SubjectTokenRequest, caller: Reader, serve: Serve) -> SubjectTokenResponse:
    result: SubjectTokenResponse = serve.mint_subject_token(caller, body)
    return result


@policy_router.post("/v1/policy/simulate", response_model=SimulateResponse)
async def simulate(
    body: SimulateRequest, caller: Governor, policy: Policy, admin_audit: AdminAudit
) -> SimulateResponse:
    result: SimulateResponse = await policy.simulate(caller, body)
    # The simulator shows a profile's data as some reader would get it: a Console read of the profile (5.22).
    target = f"source:{body.source_id}" if body.source_id else f"audience:{result.audience.value}"
    await admin_audit.record(caller, action="policy.simulate", profile_id=body.profile_id, target=target)
    return result


def measured(request: Request, answer: Any) -> None:
    """The read's stages, path, degradation and warmth, for the request's metrics, Server-Timing and log
    line."""
    response: ContextResponse = answer.response
    request.state.read = ReadSummary(response.timing, response.path.value, response.degraded, answer.warm)


def rendered(model: BaseModel) -> bytes:
    """The bytes FastAPI writes for `model` under `response_model_exclude_none`: pydantic's JSON dump of the
    same model, by alias, without the fields that are None."""
    return model.model_dump_json(by_alias=True, exclude_none=True).encode()
