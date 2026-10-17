"""Typed state, the agent's working state and the SDK profile (7.7).

Owner: the objects module. Each route is gated by the space's features. `push_router` is on `niadra-ingest`,
`router` on `niadra-read` (no SQL on the hot path) and `admin_router` on `niadra-admin`. A state read leaves a
receipt of kind `read` and surface `state` (5.13) through the read process's receipt path, naming the objects
it served.
"""

import hashlib
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.inbound.http.features import IdempotencyKey, Limit, gated
from niadra.app.serve import BlockAsk
from niadra.contracts.state import (
    AgentState,
    AgentStateMetaPage,
    AgentStateReadRequest,
    AgentStateWrite,
    AgentStateWriteResult,
    ContentRelease,
    ContentReleaseResult,
    ObjectCoverage,
    ObjectPushRequest,
    ObjectPushResponse,
    ObjectSnapshotResponse,
    RefreshRelease,
    RefreshReleased,
    RefreshRequestPage,
    SdkProfile,
    StateReadRequest,
    StateReadResponse,
    StateVerifyRequest,
    StateVerifyResponse,
    StateView,
    StateViewRequest,
    TypeFingerprintRequest,
    TypeFingerprintResponse,
)
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Feature, ReceiptKind, ReceiptSurface, Scope

push_router = APIRouter(prefix="/v1", tags=["state"])
router = APIRouter(prefix="/v1", tags=["state"])
admin_router = APIRouter(prefix="/v1", tags=["state"])

Pusher = Annotated[SourcePrincipal, gated(require_source(Scope.STATE_PUSH), Feature.STATE)]
Reader = Annotated[SourcePrincipal, gated(require_source(Scope.CONTEXT), Feature.STATE)]
Working = Annotated[SourcePrincipal, gated(require_source(Scope.AGENT_STATE), Feature.AGENT_STATE)]
AnyKey = Annotated[SourcePrincipal, gated(require_source())]
Integration = Annotated[Principal, gated(require_governance(Role.INTEGRATION), Feature.STATE)]
Security = Annotated[Principal, gated(require_governance(Role.SECURITY), Feature.STATE)]
StateReader = Annotated[
    Principal,
    gated(
        require_governance(Role.SECURITY, Role.REVIEW, Role.ANALYSIS, Role.INTEGRATION), Feature.AGENT_STATE
    ),
]
State = Annotated[Any, Depends(service("state"))]
Shared = Annotated[Any, Depends(service("shared"))]
Objects = Annotated[Any, Depends(service("objects"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]
Refresh = Annotated[Any, Depends(service("refresh"))]
AgentStates = Annotated[Any, Depends(service("agent_state"))]
Serve = Annotated[Any, Depends(service("serve"))]
TypeDrift = Annotated[Any, Depends(service("type_drift"))]


_NDJSON_BODY = {
    "requestBody": {"required": True, "content": {"application/x-ndjson": {"schema": {"type": "string"}}}}
}


@push_router.post("/objects/push", response_model=ObjectPushResponse)
async def push_objects(body: ObjectPushRequest, caller: Pusher, shared: Shared) -> ObjectPushResponse:
    """State from a system of record: a shared object's items are decided against the working set's hot
    copy with no database statement and written within a second; a customer's, kept in one statement."""
    response: ObjectPushResponse = await shared.push(caller, body)
    return response


@push_router.post(
    "/objects/snapshot", response_model=ObjectSnapshotResponse, status_code=202, openapi_extra=_NDJSON_BODY
)
async def snapshot_objects(
    request: Request,
    caller: Pusher,
    shared: Shared,
    type_: Annotated[str, Query(alias="type", pattern=r"^[a-z][a-z0-9_]{0,39}$")],
) -> ObjectSnapshotResponse:
    """Reconciliation of a shared type from its system of record: NDJSON, one push item per line, applied as
    pushes of provenance `snapshot` by the same version rule."""
    response: ObjectSnapshotResponse = await shared.take_snapshot(caller, type_, request.stream())
    return response


@push_router.post(
    "/types/fingerprint",
    response_model=TypeFingerprintResponse,
    responses={
        404: {"description": "The space declares no type of this name."},
        422: {"description": "`no_fingerprint`: the declared type keeps no fingerprint of the schema."},
    },
)
async def type_fingerprint(
    body: TypeFingerprintRequest, caller: Pusher, drift: TypeDrift
) -> TypeFingerprintResponse:
    """A deriving tool's check of a declared type: a fingerprint other than the declaration's is drift, and a
    type that alerts opens a data issue of kind `drift`, or counts the open one, and announces `type.drift`.
    Only the fingerprint and the counts of what changed travel: never a name, a value or a definition."""
    response: TypeFingerprintResponse = await drift.check(caller, body)
    return response


@router.post("/state/view", response_model=StateView)
async def state_view(body: StateViewRequest, caller: Reader, serve: Serve) -> StateView:
    """The subject's `now` view for the read's purpose: their objects, the shared objects they showed
    interest in and what changed since they saw them, and the same as lines for the turn block."""
    ask = BlockAsk(None, None, "chat", purpose=body.purpose)
    view: StateView = await serve.read_block(caller, "state", body.subject, ask)
    return view


@router.post("/state/read", response_model=StateReadResponse)
async def state_read(body: StateReadRequest, caller: Reader, state: State, serve: Serve) -> StateReadResponse:
    """Objects by reference, each for the read's purpose: values with their logical value, stamps, source and
    freshness computed now, what may not be claimed, and the refusal a `decide` read of a type asks for."""
    guard = await serve.guarded(caller.space.space_id)
    answer = await state.read(caller, body, guard)
    await serve.emit_receipt(
        caller,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.STATE,
        item_ids=[str(object_id) for object_id in answer.object_ids],
    )
    response: StateReadResponse = answer.response
    return response


@router.post("/state/verify", response_model=StateVerifyResponse)
async def state_verify(
    body: StateVerifyRequest, caller: Reader, state: State, serve: Serve
) -> StateVerifyResponse:
    """A verdict per object and field for a claim, without waiting for the source: whether it may be claimed
    now, whether a value given matches the one held, and what may not be said with data this old."""
    guard = await serve.guarded(caller.space.space_id)
    answer = await state.verify(caller, body, guard)
    await serve.emit_receipt(
        caller,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.STATE,
        item_ids=[str(object_id) for object_id in answer.object_ids],
    )
    verdicts: StateVerifyResponse = answer.verdicts
    return verdicts


@router.get("/state/refresh-requests", response_model=RefreshRequestPage)
async def refresh_requests(caller: Pusher, refresh: Refresh, limit: Limit = 50) -> RefreshRequestPage:
    """The company's resolver worker takes what to fetch again: the higher priorities and the older first,
    each leased to it for 60 seconds; a request it does not answer is offered again, three times at most."""
    page: RefreshRequestPage = await refresh.lease(caller, limit)
    return page


@router.post("/state/refresh-requests/{request_id}/release", response_model=RefreshReleased)
async def release_refresh_request(
    request_id: Annotated[str, Path(max_length=64)], body: RefreshRelease, caller: Pusher, refresh: Refresh
) -> RefreshReleased:
    """A leased request the worker cannot answer, the object not at the source or the fetch failed: it leaves
    now, its paid call counted. A request it can answer it answers by pushing the object with the request's
    `request_id`."""
    released: RefreshReleased = await refresh.release(caller, request_id, body.outcome)
    return released


@router.post("/agent-state/read", response_model=AgentState)
async def read_agent_state(
    body: AgentStateReadRequest, caller: Working, agent_state: AgentStates, serve: Serve
) -> AgentState:
    """The working state of one scope and agent: version 0 and an empty body when it was never written."""
    state: AgentState = await agent_state.read(caller, body)
    await serve.emit_receipt(caller, kind=ReceiptKind.READ, surface=ReceiptSurface.AGENT_STATE)
    return state


@router.put(
    "/agent-state",
    response_model=AgentStateWriteResult,
    responses={412: {"description": "`agent_state_conflict`: the version moved since `if_version`."}},
)
async def write_agent_state(
    body: AgentStateWrite, caller: Working, agent_state: AgentStates
) -> AgentStateWriteResult:
    """A write by the agent's code: over the cap it answers `stored: false`, never an error."""
    result: AgentStateWriteResult = await agent_state.write(caller, body)
    return result


@router.get(
    "/sdk/profile",
    response_model=SdkProfile,
    responses={304: {"description": "The profile behind `If-None-Match` is still current."}},
)
async def sdk_profile(
    caller: AnyKey, state: State, response: Response, if_none_match: Annotated[str | None, Header()] = None
) -> Any:
    """What the SDK keeps in its local cache, from the configuration alone: no database, no receipt."""
    profile: SdkProfile = state.profile(caller)
    etag = hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:32]
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Cache-Control"] = f"private, max-age={profile.valid_for_s}"
    return profile


@admin_router.get("/profiles/{profile_id}/agent-state", response_model=AgentStateMetaPage)
async def profile_agent_states(
    profile_id: UUID, caller: StateReader, agent_state: AgentStates, audit: AdminAudit
) -> AgentStateMetaPage:
    """The customer's working states, as the Console shows them: size, version and dates, never the
    content."""
    page: AgentStateMetaPage = await agent_state.profile_states(caller, profile_id)
    await audit.record(caller, action="profile.agent_state.read", profile_id=profile_id)
    return page


@admin_router.get("/objects/coverage", response_model=ObjectCoverage)
async def object_coverage(caller: Integration, state: State) -> ObjectCoverage:
    """Each declared type's fields, the share of its objects that carry each and how old it is: the Console's
    Types screen. Stamps only, never a value."""
    found: ObjectCoverage = await state.coverage(caller)
    return found


@admin_router.post("/content/{ref:path}/release", response_model=ContentReleaseResult)
async def release_content(
    ref: str,
    body: ContentRelease,
    caller: Security,
    idempotency_key: IdempotencyKey,  # noqa: ARG001 (a second release of the same content finds it clean)
    objects: Objects,
    state: State,
    audit: AdminAudit,
) -> ContentReleaseResult:
    """A person clears content the scan flagged or holds, with a reason and a receipt; the object's reads
    serve it from then on."""
    released = await objects.release_content(caller, ref, body.reason)
    if released is None:
        return ContentReleaseResult(released=False)
    await state.publish(caller.space.space_id, [released])
    await audit.record(caller, action="content.released", target=ref)
    return ContentReleaseResult(released=True)
