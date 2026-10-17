"""Usage aggregates for governance: `GET /v1/usage` (5.15, 7.4)."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from niadra.adapters.inbound.http.auth import require_governance
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.metering import (
    BudgetReport,
    ContextLatencyReport,
    PromptCacheReport,
    UsageItem,
    UsageResponse,
)
from niadra.domain.common.space import Principal
from niadra.domain.control.records import Role
from niadra.domain.metering import Granularity

router = APIRouter(prefix="/v1", tags=["usage"])


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    since: datetime,
    until: datetime,
    principal: Annotated[Principal, Depends(require_governance(Role.ANALYSIS))],
    metering: Annotated[Any, Depends(service("metering"))],
    granularity: Granularity = Granularity.HOUR,
    metric: Annotated[list[str] | None, Query(max_length=20)] = None,
    source_id: UUID | None = None,
    group_by: Literal["source"] | None = None,
) -> UsageResponse:
    rows = await metering.usage(
        principal.space.space_id,
        since,
        until,
        granularity,
        metric,
        source_id=source_id,
        per_source=group_by == "source",
    )
    items = [
        UsageItem(
            start=r.hour,
            metric=r.metric,
            quantity=r.quantity,
            cost_micros=r.cost_micros,
            source_id=str(r.source_id) if r.source_id else None,
        )
        for r in rows
    ]
    return UsageResponse(
        since=since,
        until=until,
        granularity=granularity,
        source_id=str(source_id) if source_id else None,
        group_by=group_by,
        items=items,
    )


@router.get("/usage/budget", response_model=BudgetReport)
async def llm_budget(
    principal: Annotated[Principal, Depends(require_governance(Role.ANALYSIS))],
    metering: Annotated[Any, Depends(service("metering"))],
) -> BudgetReport:
    """Today's AI spend against the space's daily ceiling per purpose, the contract's or the default."""
    report: BudgetReport = await metering.budget(principal.space.space_id)
    return report


@router.get("/usage/context-latency", response_model=ContextLatencyReport)
async def context_latency(
    principal: Annotated[Principal, Depends(require_governance(Role.ANALYSIS))],
    latency: Annotated[Any, Depends(service("latency"))],
) -> ContextLatencyReport:
    """The server time of the space's context reads over 24 hours and 7 days, without the network: median,
    p95, the share under the published target and against the objective, the reads and the share served
    degraded."""
    report: ContextLatencyReport = await latency.report(principal.space.space_id)
    return report


@router.get("/usage/prompt-cache", response_model=PromptCacheReport)
async def prompt_cache(
    principal: Annotated[Principal, Depends(require_governance(Role.ANALYSIS, Role.VENDOR))],
    metering: Annotated[Any, Depends(service("metering"))],
    since: date | None = None,
    until: date | None = None,
    group_by: Annotated[list[str] | None, Query()] = None,
    source_id: UUID | None = None,
) -> PromptCacheReport:
    """The agents' prompt cache: tokens, cache hits and the estimated saving, from the usage the SDK read
    from each model call (15.3)."""
    report: PromptCacheReport = await metering.prompt_cache(
        principal, since=since, until=until, group_by=group_by or ["source_id"], source_id=source_id
    )
    return report
