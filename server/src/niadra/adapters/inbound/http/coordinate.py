"""Ownership and effects: the coordination decision, declarations, claims, effects, handoffs, suppressions
and the contact token's public keys (7.7, 24.3.4).

Owner: the coordination module. `router` is on `niadra-read`: the check, the claims and the effects
read and write the state store and the confirmed queue, never a table; a handoff writes its sealed package.
`admin_router` is on `niadra-admin`, with the shadow run and its reports; the public keys have no caller, so
no gate.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.inbound.http.features import Cursor, IdempotencyKey, Limit, gated
from niadra.contracts.coordinate import (
    CheckBatchRequest,
    CheckBatchResponse,
    CheckRequest,
    CheckResult,
    ClaimRelease,
    ClaimRequest,
    ContactKeys,
    CoordinationOverview,
    CoordinationReportPage,
    DeclareRequest,
    DeclareResult,
    Effect,
    EffectReserve,
    EffectSettle,
    Handoff,
    HandoffCreate,
    HandoffOutcome,
    OwnershipClaim,
    ShadowRequest,
    ShadowRun,
    SuppressionPage,
    SuppressionSalt,
)
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Feature, Scope

router = APIRouter(tags=["coordination"])
admin_router = APIRouter(prefix="/v1/coordination", tags=["coordination"])

Coordinator = Annotated[SourcePrincipal, gated(require_source(Scope.COORDINATE), Feature.COORDINATION)]
Oversight = Annotated[
    Principal, gated(require_governance(Role.ANALYSIS, Role.SECURITY), Feature.COORDINATION)
]
Coordinate = Annotated[Any, Depends(service("coordinate"))]


@router.post("/v1/coordination/check", response_model=CheckResult)
async def check(body: CheckRequest, caller: Coordinator, coordinate: Coordinate) -> CheckResult:
    # A dependency that is down never turns into an error here: the answer is the purpose's declared
    # default (L23), `defer` with `unavailable` or `allow` with `unchecked`.
    result: CheckResult = await coordinate.check(caller, body)
    return result


@router.post("/v1/coordination/check/batch", response_model=CheckBatchResponse)
async def check_batch(
    body: CheckBatchRequest, caller: Coordinator, coordinate: Coordinate
) -> CheckBatchResponse:
    result: CheckBatchResponse = await coordinate.check_batch(caller, body)
    return result


@router.post(
    "/v1/coordination/declare",
    response_model=DeclareResult,
    responses={503: {"description": "`coordination_unavailable`: nothing new was recorded, send it again."}},
)
async def declare(
    body: DeclareRequest, caller: Coordinator, idempotency_key: IdempotencyKey, coordinate: Coordinate
) -> DeclareResult:
    result: DeclareResult = await coordinate.declare(caller, body, idempotency_key)
    return result


@router.post(
    "/v1/coordination/claims",
    response_model=OwnershipClaim,
    status_code=201,
    responses={409: {"description": "`lease_held`, with the holder and until when."}},
)
async def claim(
    body: ClaimRequest, caller: Coordinator, idempotency_key: IdempotencyKey, coordinate: Coordinate
) -> OwnershipClaim:
    result: OwnershipClaim = await coordinate.claim(caller, body, idempotency_key)
    return result


@router.post("/v1/coordination/claims/{claim_id}/release", response_model=OwnershipClaim)
async def release_claim(
    claim_id: str,
    body: ClaimRelease,
    caller: Coordinator,
    idempotency_key: IdempotencyKey,
    coordinate: Coordinate,
) -> OwnershipClaim:
    result: OwnershipClaim = await coordinate.release(caller, claim_id, body.epoch, idempotency_key)
    return result


@router.post(
    "/v1/coordination/effects",
    response_model=Effect,
    status_code=201,
    responses={
        200: {"description": "The key was done or ambiguous already: nothing was reserved, do not act."},
        409: {"description": "`effect_in_flight`: another attempt holds the key."},
    },
)
async def reserve_effect(
    body: EffectReserve,
    caller: Coordinator,
    idempotency_key: IdempotencyKey,
    coordinate: Coordinate,
    response: Response,
) -> Effect:
    result: Effect = await coordinate.reserve_effect(caller, body, idempotency_key)
    if result.state != "reserved":
        response.status_code = 200
    return result


@router.post("/v1/coordination/effects/{effect_id}/settle", response_model=Effect)
async def settle_effect(
    effect_id: str,
    body: EffectSettle,
    caller: Coordinator,
    idempotency_key: IdempotencyKey,
    coordinate: Coordinate,
) -> Effect:
    # The path names the key's keyed hash, never the key, which may carry a conversation id (7.0).
    result: Effect = await coordinate.settle_effect(caller, effect_id, body, idempotency_key)
    return result


@router.post("/v1/handoffs", response_model=Handoff, status_code=201)
async def create_handoff(
    body: HandoffCreate, caller: Coordinator, idempotency_key: IdempotencyKey, coordinate: Coordinate
) -> Handoff:
    # The package is compiled at the receiving side's level and kept sealed: one statement besides the read.
    result: Handoff = await coordinate.create_handoff(caller, body, idempotency_key)
    return result


@router.get("/v1/handoffs/{handoff_id}", response_model=Handoff)
async def read_handoff(handoff_id: str, caller: Coordinator, coordinate: Coordinate) -> Handoff:
    result: Handoff = await coordinate.read_handoff(caller, handoff_id)
    return result


@router.post(
    "/v1/handoffs/{handoff_id}/accept",
    response_model=Handoff,
    responses={409: {"description": "The handoff was closed or expired."}},
)
async def accept_handoff(
    handoff_id: str, caller: Coordinator, idempotency_key: IdempotencyKey, coordinate: Coordinate
) -> Handoff:
    result: Handoff = await coordinate.accept_handoff(caller, handoff_id, idempotency_key)
    return result


@router.post(
    "/v1/handoffs/{handoff_id}/outcome",
    response_model=Handoff,
    responses={409: {"description": "The handoff was closed with another outcome, or expired."}},
)
async def handoff_outcome(
    handoff_id: str,
    body: HandoffOutcome,
    caller: Coordinator,
    idempotency_key: IdempotencyKey,
    coordinate: Coordinate,
) -> Handoff:
    result: Handoff = await coordinate.handoff_outcome(caller, handoff_id, body, idempotency_key)
    return result


@router.get("/v1/suppressions", response_model=SuppressionPage)
async def suppressions(
    caller: Coordinator, coordinate: Coordinate, cursor: Cursor = None, limit: Limit = 200
) -> SuppressionPage:
    # Read once a minute per reader, from the durable record: one statement per page.
    result: SuppressionPage = await coordinate.suppressions(caller, cursor, limit)
    return result


@router.get("/v1/suppressions/salt", response_model=SuppressionSalt)
async def suppression_salt(caller: Coordinator, coordinate: Coordinate) -> SuppressionSalt:
    result: SuppressionSalt = await coordinate.salt(caller)
    return result


@router.get(
    "/.well-known/niadra-contact-keys.json",
    response_model=ContactKeys,
    responses={404: {"description": "No such space, or it has no coordination."}},
)
async def contact_keys(
    space: Annotated[UUID, Query()], coordinate: Coordinate, response: Response
) -> ContactKeys:
    # One space's keys: a gateway knows its space, and no caller may list the cell's spaces. Public and read
    # from the configuration alone; a gateway refreshes its copy hourly at least (contact-token spec, 5.3).
    result: ContactKeys = await coordinate.contact_keys(space)
    response.headers["cache-control"] = "public, max-age=300"
    return result


@admin_router.get("/overview", response_model=CoordinationOverview)
async def overview(
    caller: Oversight, coordinate: Coordinate, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> CoordinationOverview:
    """Ownership that holds now, effects, conflicts, budgets and contacts of the last `days`, in counts."""
    result: CoordinationOverview = await coordinate.overview(caller, days)
    return result


@admin_router.get("/reports", response_model=CoordinationReportPage)
async def reports(
    caller: Oversight, coordinate: Coordinate, cursor: Cursor = None, limit: Limit = 50
) -> CoordinationReportPage:
    result: CoordinationReportPage = await coordinate.reports(caller, cursor, limit)
    return result


@admin_router.post("/shadow", response_model=ShadowRun, status_code=202)
async def shadow(body: ShadowRequest, caller: Oversight, coordinate: Coordinate) -> ShadowRun:
    # The run reads weeks of events in a background task; the report is listed when it is done.
    result: ShadowRun = await coordinate.request_shadow(caller, body)
    return result
