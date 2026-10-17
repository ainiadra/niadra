"""Governance routes of `niadra-admin` for traits, triggers, webhook deliveries and the history of the
configuration assistant (7.1, 22.4).

The rules themselves are configuration and arrive in the signed snapshot (4.7); what the cell exposes
is their execution: retraction, dry-runs, firings, deliveries, the dead-letter list and past assistant
runs (requesting a run lives with the measurement routes).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from niadra.adapters.inbound.http.auth import require_governance
from niadra.adapters.inbound.http.deps import service
from niadra.app.admin_audit import AdminAudit
from niadra.contracts.common import Page
from niadra.contracts.configure import AssistRunOut
from niadra.contracts.notify import (
    DeadTaskOut,
    DeadTasks,
    DeliveryPage,
    DeliveryView,
    ReplayRequest,
    ReplayResult,
)
from niadra.contracts.traits import OptOutResponse, TraitDryRunResponse, TraitRuleDraft, TraitView
from niadra.contracts.triggers import FiringPage, TriggerDryRunResponse, TriggerRuleDraft
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.space import Principal
from niadra.domain.control.records import Role
from niadra.domain.notify.webhooks import DeliveryStatus
from niadra.domain.tasks import TaskName
from niadra.domain.traits.rules import TraitRule
from niadra.domain.triggers.conditions import TriggerRule

router = APIRouter(tags=["operate"])

# Roles of 5.22: opting out is a privacy decision (security); retiring a pattern belongs to whoever
# curates the rules (integration) as well.
Privacy = Annotated[Principal, Depends(require_governance(Role.SECURITY))]
Integration = Annotated[Principal, Depends(require_governance(Role.INTEGRATION))]
Curator = Annotated[Principal, Depends(require_governance(Role.SECURITY, Role.INTEGRATION))]
Observer = Annotated[Principal, Depends(require_governance(Role.INTEGRATION, Role.ANALYSIS))]
Traits = Annotated[Any, Depends(service("traits"))]
Triggers = Annotated[Any, Depends(service("triggers"))]
Notify = Annotated[Any, Depends(service("notify"))]
Assist = Annotated[Any, Depends(service("configure"))]
Pipeline = Annotated[Any, Depends(service("pipeline"))]
# Retractions, opt-outs, dry-runs and redeliveries leave an `admin` receipt once they succeeded (5.22).
Actions = Annotated[AdminAudit, Depends(service("admin_audit"))]
Cursor = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=200)]


@router.post("/v1/traits/{trait_id}/retract", response_model=TraitView)
async def retract_trait(trait_id: UUID, caller: Curator, traits: Traits, actions: Actions) -> TraitView:
    view: TraitView = await traits.retract(caller.space.space_id, trait_id)
    await actions.record(
        caller, action="traits.retract", profile_id=UUID(view.profile_id), target=f"trait:{trait_id}"
    )
    return view


@router.post("/v1/traits/rules/dry-run", response_model=TraitDryRunResponse)
async def dry_run_trait_rule(
    body: TraitRuleDraft, caller: Observer, traits: Traits, actions: Actions
) -> TraitDryRunResponse:
    rule = TraitRule(body.rule_id, body.catalog, body.version, body.params)
    result: TraitDryRunResponse = await traits.dry_run(caller.space.space_id, rule)
    # Changes nothing, but reads 30 days of profiles: an action on personal data all the same (5.22).
    await actions.record(caller, action="traits.rule_dry_run", target=f"trait_rule:{body.rule_id}")
    return result


@router.post("/v1/profiles/{profile_id}/traits/opt-out", response_model=OptOutResponse)
async def opt_out_of_traits(
    profile_id: UUID, caller: Privacy, traits: Traits, actions: Actions
) -> OptOutResponse:
    result: OptOutResponse = await traits.opt_out(caller.space.space_id, profile_id)
    await actions.record(caller, action="traits.opt_out", profile_id=profile_id)
    return result


@router.post("/v1/triggers/dry-run", response_model=TriggerDryRunResponse)
async def dry_run_trigger_rule(
    body: TriggerRuleDraft, caller: Integration, triggers: Triggers, actions: Actions
) -> TriggerDryRunResponse:
    rule = TriggerRule(
        rule_id=body.rule_id,
        condition=body.condition,
        version=body.version,
        params=body.params,
        endpoint_id=body.endpoint_id,
        window_hours=body.window_hours,
        late_policy=body.late_policy,
    )
    result: TriggerDryRunResponse = await triggers.dry_run(caller.space.space_id, rule)
    await actions.record(caller, action="triggers.rule_dry_run", target=f"trigger_rule:{body.rule_id}")
    return result


@router.get("/v1/triggers/firings", response_model=FiringPage)
async def list_firings(
    caller: Observer,
    triggers: Triggers,
    cursor: Cursor = None,
    limit: Limit = 50,
    rule_id: Annotated[str | None, Query(max_length=64)] = None,
    subject_id: UUID | None = None,
) -> FiringPage:
    page: FiringPage = await triggers.list_firings(
        caller.space.space_id, rule_id=rule_id, subject_id=subject_id, cursor=cursor, limit=limit
    )
    return page


@router.get("/v1/webhooks/deliveries", response_model=DeliveryPage)
async def list_deliveries(
    caller: Integration,
    notify: Notify,
    cursor: Cursor = None,
    limit: Limit = 50,
    status: DeliveryStatus | None = None,
    endpoint_id: Annotated[str | None, Query(max_length=128)] = None,
) -> DeliveryPage:
    page: DeliveryPage = await notify.list_deliveries(
        caller.space.space_id, status=status, endpoint_id=endpoint_id, cursor=cursor, limit=limit
    )
    return page


@router.post("/v1/webhooks/deliveries/{delivery_id}/redeliver", response_model=DeliveryView, status_code=202)
async def redeliver(delivery_id: UUID, caller: Integration, notify: Notify, actions: Actions) -> DeliveryView:
    view: DeliveryView = await notify.redeliver(caller.space.space_id, delivery_id)
    await actions.record(caller, action="notify.redeliver", target=f"delivery:{delivery_id}")
    return view


@router.post("/v1/webhooks/endpoints/{endpoint_id}/test", response_model=DeliveryView, status_code=202)
async def test_endpoint(
    endpoint_id: Annotated[str, Path(max_length=128)], caller: Integration, notify: Notify, actions: Actions
) -> DeliveryView:
    """Sends a signed delivery marked as a test to a webhook endpoint or a SIEM stream, once; follow it in
    the deliveries."""
    view: DeliveryView = await notify.send_test(caller.space.space_id, endpoint_id)
    await actions.record(caller, action="notify.test", target=f"endpoint:{endpoint_id}")
    return view


@router.get("/v1/assist", response_model=Page[AssistRunOut])
async def list_assist_runs(
    caller: Integration, assist: Assist, cursor: Cursor = None, limit: Limit = 50
) -> Page[AssistRunOut]:
    page: Page[AssistRunOut] = await assist.runs(caller, cursor=cursor, limit=limit)
    return page


@router.get("/v1/operations/dead-tasks", response_model=DeadTasks)
async def dead_tasks(caller: Privacy, pipeline: Pipeline) -> DeadTasks:
    """Work of this space that exhausted its attempts (an erasure among them marks its request failed)."""
    tasks = await pipeline.dead_tasks(caller.space.space_id)
    return DeadTasks(
        items=[DeadTaskOut(task_id=t.id, name=t.name.value, profile_id=t.profile_id) for t in tasks]
    )


@router.post("/v1/operations/dead-tasks/replay", response_model=ReplayResult)
async def replay_dead_tasks(
    body: ReplayRequest, caller: Privacy, pipeline: Pipeline, admin_audit: Actions
) -> ReplayResult:
    """Sends this space's buried tasks back with fresh attempts: a failed erasure resumes where it stopped,
    since each step is idempotent. Leaves an `admin` receipt."""
    try:
        name = TaskName(body.task) if body.task else None
    except ValueError:
        raise InvalidInput("unknown task name") from None
    replayed = await pipeline.requeue_dead(caller.space.space_id, name)
    await admin_audit.record(caller, action="operations.replay_dead_tasks", target=f"task:{body.task or '*'}")
    return ReplayResult(replayed=replayed)
