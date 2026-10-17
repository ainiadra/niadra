"""Governance routes of context-use measurement, review by sample and the configuration assistant
(7.1); niadra-admin."""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from niadra.adapters.inbound.http.auth import require_aggregates, require_governance, require_person
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.configure import AssistRequest, AssistRunOut
from niadra.contracts.measure import (
    ContextUseDetail,
    ContextUseReport,
    ConversationRef,
    MeasuredConversationPage,
)
from niadra.contracts.review import (
    AgreementReport,
    ReviewCases,
    ReviewQueue,
    VerdictsRequest,
    VerdictsResponse,
)
from niadra.domain.common.space import PersonPrincipal, Principal
from niadra.domain.control.records import Role

router = APIRouter(prefix="/v1", tags=["governance"])

Analyst = Annotated[Principal, Depends(require_governance(Role.ANALYSIS, Role.VENDOR))]
# The report sums conversations and names none: an analyst key with `analytics` reads it too (19.4).
AnalystOrKey = Annotated[Principal, Depends(require_aggregates(Role.ANALYSIS, Role.VENDOR))]
Reviewer = Annotated[Principal, Depends(require_governance(Role.REVIEW))]
ReviewerPerson = Annotated[PersonPrincipal, Depends(require_person(Role.REVIEW))]
Integrator = Annotated[Principal, Depends(require_governance(Role.INTEGRATION))]
Measure = Annotated[Any, Depends(service("measure"))]
Review = Annotated[Any, Depends(service("review"))]
Assist = Annotated[Any, Depends(service("configure"))]
AdminAudit = Annotated[Any, Depends(service("admin_audit"))]


@router.get("/context-use", response_model=ContextUseReport)
async def context_use(
    caller: AnalystOrKey,
    measure: Measure,
    since: date | None = None,
    until: date | None = None,
    group_by: Annotated[list[str] | None, Query()] = None,
    source_id: UUID | None = None,
    channel: str | None = None,
    view: str | None = None,
    experiment_group: str | None = None,
) -> ContextUseReport:
    report: ContextUseReport = await measure.context_use(
        caller,
        since=since,
        until=until,
        group_by=group_by or ["source_id"],
        source_id=source_id,
        channel=channel,
        view=view,
        experiment_group=experiment_group,
    )
    return report


@router.get("/context-use/conversations", response_model=MeasuredConversationPage)
async def measured_conversations(
    caller: Analyst,
    measure: Measure,
    since: date,
    until: date,
    source_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MeasuredConversationPage:
    """The measured conversations of a period, per agent, newest first; the ids come in the body, and a
    conversation opens with `POST /v1/context-use/conversation`. Declared before the path route below,
    which would otherwise take `conversations` for an id."""
    page: MeasuredConversationPage = await measure.measured_conversations(
        caller, source_id=source_id, since=since, until=until, cursor=cursor, limit=limit
    )
    return page


@router.get("/context-use/{conversation_id}", response_model=ContextUseDetail)
async def context_use_detail(conversation_id: str, caller: Analyst, measure: Measure) -> ContextUseDetail:
    detail: ContextUseDetail = await measure.conversation_detail(caller, conversation_id)
    return detail


@router.post("/context-use/conversation", response_model=ContextUseDetail)
async def context_use_detail_by_body(
    body: ConversationRef, caller: Analyst, measure: Measure
) -> ContextUseDetail:
    """The same as `GET /v1/context-use/{conversation_id}`, with the conversation id in the body (7.0)."""
    detail: ContextUseDetail = await measure.conversation_detail(caller, body.conversation_id)
    return detail


@router.get("/review/queue", response_model=ReviewQueue)
async def review_queue(
    caller: Reviewer, review: Review, admin_audit: AdminAudit, limit: Annotated[int, Query(ge=1, le=50)] = 10
) -> ReviewQueue:
    queue: ReviewQueue = await review.queue(caller, limit)
    # The queue shows extracted memory to whoever reviews it.
    await admin_audit.record(caller, action="review.queue.read", target=f"samples:{len(queue.items)}")
    return queue


@router.post("/review/{sample_id}/verdicts", response_model=VerdictsResponse)
async def review_verdicts(
    sample_id: UUID, body: VerdictsRequest, caller: ReviewerPerson, review: Review, admin_audit: AdminAudit
) -> VerdictsResponse:
    response: VerdictsResponse = await review.record_verdicts(caller, sample_id, body)
    await admin_audit.record(caller, action="review.verdicts", target=f"sample:{sample_id}")
    return response


@router.get("/review/agreement", response_model=AgreementReport)
async def review_agreement(
    caller: Reviewer,
    review: Review,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
    source_id: UUID | None = None,
) -> AgreementReport:
    report: AgreementReport = await review.agreement(caller, days=days, source_id=source_id)
    return report


@router.get("/review/cases", response_model=ReviewCases)
async def review_cases(
    caller: Reviewer, review: Review, days: Annotated[int, Query(ge=1, le=365)] = 90
) -> ReviewCases:
    """Each verdict of the period as a test run against today's memory and measurement."""
    cases: ReviewCases = await review.cases(caller, days=days)
    return cases


@router.post("/assist", response_model=AssistRunOut, status_code=202)
async def request_assist(
    body: AssistRequest, caller: Integrator, assist: Assist, admin_audit: AdminAudit
) -> AssistRunOut:
    run: AssistRunOut = await assist.request(caller, body)
    await admin_audit.record(caller, action="assist.request", target=f"assist_run:{run.run_id}")
    return run


@router.get("/assist/{run_id}", response_model=AssistRunOut)
async def get_assist(run_id: UUID, caller: Integrator, assist: Assist) -> AssistRunOut:
    run: AssistRunOut = await assist.get(caller, run_id)
    return run
