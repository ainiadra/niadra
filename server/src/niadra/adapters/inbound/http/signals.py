"""Subject signals, the constraints block, the reviewable profile, review requests, legal holds and the
agent features' measurement (7.7).

Owner: the signals and measurement module. Every route is gated by the space's features. `router` is on
`niadra-read`; `admin_router` on `niadra-admin`. Profile ids are pseudonymous and may go in the path (7.0).
"""

import dataclasses
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from niadra.adapters.inbound.http.auth import require_aggregates, require_governance, require_source
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.inbound.http.features import (
    Cursor,
    IdempotencyKey,
    Limit,
    gated,
    source_or_person,
)
from niadra.app.serve import BlockAsk
from niadra.contracts.signals import (
    AttributionReport,
    BiUpload,
    BiUploadRequest,
    ConstraintsBlock,
    ConstraintsRequest,
    CounterfactualRun,
    CounterfactualRunCreate,
    CounterfactualRunPage,
    ExperimentReport,
    Inference,
    InferenceCorrection,
    InferencePage,
    InterleavingReport,
    LegalHold,
    LegalHoldCreate,
    LegalHoldRelease,
    OutcomePage,
    PowerRequest,
    PowerResult,
    ReconcileRequest,
    ReconcileResult,
    ReviewRequest,
    ReviewRequestCreate,
    ReviewRequestPage,
    ReviewResolution,
    UnmetDemand,
    UnmetDemandPage,
)
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Feature, Scope

router = APIRouter(prefix="/v1", tags=["signals"])
admin_router = APIRouter(prefix="/v1", tags=["signals"])

Reader = Annotated[SourcePrincipal, gated(require_source(Scope.CONTEXT), Feature.SIGNALS)]
Serve = Annotated[Any, Depends(service("serve"))]
Outcomes = Annotated[Any, Depends(service("outcomes"))]
InferencesApp = Annotated[Any, Depends(service("inferences"))]
ReviewsApp = Annotated[Any, Depends(service("review_requests"))]
HoldsApp = Annotated[Any, Depends(service("legal_holds"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]
Inferences = Annotated[Principal, gated(require_governance(Role.SECURITY), Feature.SIGNALS)]
Reviews = Annotated[
    Principal, gated(require_governance(Role.SECURITY), Feature.SIGNALS, Feature.COORDINATION)
]
Holds = Annotated[Principal, gated(require_governance(Role.SECURITY), Feature.LEGAL_HOLDS)]
Analysis = Annotated[Principal, gated(require_governance(Role.ANALYSIS), Feature.MEASUREMENT)]
# Reports that sum the space and name no conversation: an analyst key with `analytics` reads them too (19.4).
Aggregates = Annotated[Principal, gated(require_aggregates(Role.ANALYSIS), Feature.MEASUREMENT)]
# The runner of the company's CI posts counterfactual runs with the key it runs replay cases with.
Runner = Annotated[Principal, gated(source_or_person(Scope.REPLAY, Role.ANALYSIS), Feature.MEASUREMENT)]
ExperimentsApp = Annotated[Any, Depends(service("experiments"))]
SignalsApp = Annotated[Any, Depends(service("signals"))]


@router.post("/constraints", response_model=ConstraintsBlock, response_model_exclude_none=True)
async def constraints(
    body: ConstraintsRequest, caller: Reader, serve: Serve, signals: SignalsApp
) -> ConstraintsBlock:
    """The constraints block of one subject, the one `include: ["constraints"]` adds to a context read;
    `for` asks for a beneficiary's. With `tool`, it comes rendered through the tool's binding for this
    source, in advisory mode; 422 when the space binds no such tool for it."""
    ask = BlockAsk(body.conversation_id, None, "chat", body.for_)
    block: ConstraintsBlock = await serve.read_block(caller, "constraints", body.subject, ask)
    if body.tool is not None:
        block = signals.for_tool(caller, block, body.tool)
    return block


@admin_router.get("/profiles/{profile_id}/inferences", response_model=InferencePage)
async def list_inferences(
    profile_id: UUID,
    caller: Inferences,
    inferences: InferencesApp,
    audit: AdminAudit,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> InferencePage:
    """Every inference about the subject, with its origin, evidence, confidence, use and date."""
    page: InferencePage = await inferences.page(caller, profile_id, cursor=cursor, limit=limit)
    await audit.record(caller, action="profile.inferences.read", profile_id=profile_id)
    return page


@admin_router.post("/profiles/{profile_id}/inferences/{key}/correct", response_model=Inference)
async def correct_inference(
    profile_id: UUID,
    key: str,
    body: InferenceCorrection,
    caller: Inferences,
    inferences: InferencesApp,
    audit: AdminAudit,
) -> Inference:
    """The subject's value becomes a stated preference or attribute; the inference is deleted."""
    corrected: Inference = await inferences.correct(caller, profile_id, key, body)
    await audit.record(
        caller, action="profile.inference.corrected", profile_id=profile_id, target=f"inference:{key}"
    )
    return corrected


@admin_router.delete("/profiles/{profile_id}/inferences/{key}", status_code=204)
async def delete_inference(
    profile_id: UUID, key: str, caller: Inferences, inferences: InferencesApp, audit: AdminAudit
) -> None:
    """A tombstone: the same evidence never infers it again."""
    await inferences.delete(caller, profile_id, key)
    await audit.record(
        caller, action="profile.inference.deleted", profile_id=profile_id, target=f"inference:{key}"
    )


@admin_router.post("/profiles/{profile_id}/review-requests", response_model=ReviewRequest, status_code=201)
async def create_review_request(
    profile_id: UUID,
    body: ReviewRequestCreate,
    caller: Reviews,
    reviews: ReviewsApp,
    audit: AdminAudit,
    idempotency_key: IdempotencyKey,
) -> ReviewRequest:
    """Sent to the controller's data protection officer with the explanation built from receipts."""
    created: ReviewRequest = await reviews.create(caller, profile_id, body, idempotency_key=idempotency_key)
    await audit.record(
        caller,
        action="review_request.created",
        profile_id=profile_id,
        target=f"review_request:{created.request_id}",
    )
    return created


@admin_router.get("/review-requests", response_model=ReviewRequestPage)
async def list_review_requests(
    caller: Reviews, reviews: ReviewsApp, cursor: Cursor = None, limit: Limit = 50
) -> ReviewRequestPage:
    page: ReviewRequestPage = await reviews.page(caller, cursor=cursor, limit=limit)
    return page


@admin_router.post("/review-requests/{request_id}/resolve", response_model=ReviewRequest)
async def resolve_review_request(
    request_id: str, body: ReviewResolution, caller: Reviews, reviews: ReviewsApp, audit: AdminAudit
) -> ReviewRequest:
    """The controller's decision, recorded once."""
    resolved: ReviewRequest = await reviews.resolve(caller, request_id, body, by=_actor(caller))
    await audit.record(caller, action="review_request.resolved", target=f"review_request:{request_id}")
    return resolved


@admin_router.post("/legal-holds", response_model=LegalHold, status_code=201)
async def create_legal_hold(
    body: LegalHoldCreate, caller: Holds, holds: HoldsApp, audit: AdminAudit, idempotency_key: IdempotencyKey
) -> LegalHold:
    """Keeps a handle's, an object's or a conversation's data from purging until the release."""
    hold: LegalHold = await holds.create(caller, body, idempotency_key=idempotency_key, by=_actor(caller))
    await audit.record(caller, action="legal_hold.created", target=f"legal_hold:{hold.hold_id}")
    return hold


@admin_router.post("/legal-holds/{hold_id}/release", response_model=LegalHold)
async def release_legal_hold(
    hold_id: str, body: LegalHoldRelease, caller: Holds, holds: HoldsApp, audit: AdminAudit
) -> LegalHold:
    """What erasures left waiting under the hold goes once it is released."""
    released: LegalHold = await holds.release(caller, hold_id, body, by=_actor(caller))
    await audit.record(caller, action="legal_hold.released", target=f"legal_hold:{hold_id}")
    return released


def _actor(principal: Principal) -> str:
    if isinstance(principal, SourcePrincipal):
        return f"key:{principal.key_id}"
    return f"user:{principal.user_id}"


@admin_router.get("/measure/outcomes", response_model=OutcomePage, response_model_exclude_none=True)
async def outcomes(
    caller: Analysis, service: Outcomes, cursor: Cursor = None, limit: Limit = 50
) -> OutcomePage:
    """Outcomes attributed to what agents did, newest first: each link with its method, band and finality."""
    page: OutcomePage = await service.outcomes(caller, cursor, limit)
    return page


@admin_router.get("/measure/attribution", response_model=AttributionReport)
async def attribution(caller: Aggregates, service: Outcomes, since: date, until: date) -> AttributionReport:
    """Outcomes and value by day, agent, method and band, without a person: deterministic and probable
    apart."""
    report: AttributionReport = await service.attribution(caller, since, until)
    return report


@admin_router.post("/measure/reconcile/uploads", response_model=BiUpload, status_code=201)
async def reconcile_upload(body: BiUploadRequest, caller: Analysis, service: Outcomes) -> BiUpload:
    """Where to upload the company's BI file for a reconciliation: a short-lived link for those bytes only."""
    upload: BiUpload = await service.bi_upload(caller, body)
    return upload


@admin_router.post("/measure/reconcile", response_model=ReconcileResult)
async def reconcile(body: ReconcileRequest, caller: Analysis, service: Outcomes) -> ReconcileResult:
    """The period's attributed lines against the company's BI file, line by line, with the deviation kept."""
    result: ReconcileResult = await service.reconcile(caller, body)
    return result


@admin_router.get("/measure/interleaving", response_model=InterleavingReport)
async def interleaving(caller: Aggregates, service: Outcomes, since: date, until: date) -> InterleavingReport:
    """Interleaved lists of a tool's two rankings: which one engagement credited, with the sign test."""
    report: InterleavingReport = await service.interleaving(caller, since, until)
    return report


@admin_router.get("/measure/experiments", response_model=ExperimentReport, response_model_exclude_none=True)
async def experiments(caller: Aggregates, service: ExperimentsApp) -> ExperimentReport:
    """Each experiment on an element or an agent, arm by arm against `control`: CUPED-adjusted, with the
    mixture sequential test, whose p-value and interval hold however often the report is read."""
    report: ExperimentReport = await service.report(caller)
    return report


@admin_router.post("/measure/power", response_model=PowerResult, response_model_exclude_none=True)
async def power(body: PowerRequest, caller: Aggregates, service: ExperimentsApp) -> PowerResult:
    """How many days of the space's traffic a relative difference needs, looked at once or every day."""
    result: PowerResult = await service.power(caller, body)
    return result


@admin_router.post("/measure/counterfactual-runs", response_model=CounterfactualRun, status_code=201)
async def counterfactual_run(
    body: CounterfactualRunCreate, caller: Runner, service: ExperimentsApp
) -> CounterfactualRun:
    """What a runner in the company's CI measured: whether an element changes what a tool returns, against the
    tool's own noise. Positions and overlaps only (the tool counterfactual spec)."""
    run: CounterfactualRun = await service.record_run(caller, body)
    return run


@admin_router.get("/measure/counterfactual-runs", response_model=CounterfactualRunPage)
async def counterfactual_runs(
    caller: Runner, service: ExperimentsApp, cursor: Cursor = None, limit: Limit = 50
) -> CounterfactualRunPage:
    page: CounterfactualRunPage = await service.runs(caller, cursor, limit)
    return page


@admin_router.get("/measure/counterfactual-runs/{run_id}", response_model=CounterfactualRun)
async def counterfactual_run_read(run_id: UUID, caller: Runner, service: ExperimentsApp) -> CounterfactualRun:
    run: CounterfactualRun = await service.run(caller, run_id)
    return run


@admin_router.get("/measure/unmet-demand", response_model=UnmetDemandPage)
async def unmet_demand(
    caller: Aggregates,
    signals: SignalsApp,
    since: date,
    until: date,
    tool: Annotated[str | None, Query(max_length=64)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> UnmetDemandPage:
    """What people asked the tools for and did not get, by week and combination of what was asked, where at
    least the space's minimum of distinct people asked it: never per person."""
    if until < since or (until - since).days > 400:
        raise InvalidInput("the period runs forward and spans at most 400 days")
    found = await signals.unmet_demand(caller.space.space_id, since, until, tool=tool)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = found[start : start + limit]
    minimum = signals.min_people(caller.space.space_id)
    return UnmetDemandPage(
        since=since,
        until=until,
        min_people=minimum,
        items=[UnmetDemand.model_validate(dataclasses.asdict(w)) for w in page],
        next_cursor=str(start + limit) if start + limit < len(found) else None,
    )
