"""Turn records, replay, scenarios, data issues and the notification feed (7.7).

Owner: the turns module. `router` is the write side on `niadra-ingest`; `feed_router` the pull
copy of the webhooks on `niadra-read`; `admin_router` the viewer, replay and scenarios on `niadra-admin`.
Turn and scenario ids are system ids and may go in the path; a conversation id never does (7.0).
"""

from datetime import date
from typing import Annotated, Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from niadra.adapters.inbound.http.auth import require_governance, require_source
from niadra.adapters.inbound.http.bodies import Large, decoded_body
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.inbound.http.features import (
    Cursor,
    IdempotencyKey,
    Limit,
    gated,
    source_or_person,
)
from niadra.contracts.turns import (
    MAX_BODY_BYTES,
    MAX_DECODED_BYTES,
    MAX_TURNS_PER_REQUEST,
    ChangePage,
    DataIssue,
    DataIssuePage,
    FailedIntake,
    FailedIntakePage,
    FailedIntakeRetried,
    FailedIntakeRetry,
    NotificationPage,
    PromoteRequest,
    PromoteResponse,
    ReplayCase,
    ReplayCaseRequest,
    Scenario,
    ScenarioCreate,
    ScenarioFromReport,
    ScenarioPage,
    ScenarioRun,
    ScenarioRunCreate,
    ScenarioUpdate,
    TurnId,
    TurnIndexPage,
    TurnSearchRequest,
    TurnSearchResponse,
    TurnsRequest,
    TurnsResponse,
    TurnView,
)
from niadra.domain.common.errors import InvalidInput, TurnTooLarge
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Feature, Scope

router = APIRouter(prefix="/v1/turns", tags=["turns"])
feed_router = APIRouter(prefix="/v1/notifications", tags=["notifications"])
admin_router = APIRouter(prefix="/v1", tags=["turns"])

Tracker = Annotated[SourcePrincipal, gated(require_source(Scope.TRACK), Feature.TURNS)]
AnyKey = Annotated[SourcePrincipal, gated(require_source(), Feature.NOTIFICATIONS)]
Replay = Annotated[
    Principal, gated(source_or_person(Scope.REPLAY, Role.INTEGRATION, Role.SECURITY), Feature.TURNS)
]
Integration = Annotated[Principal, gated(require_governance(Role.INTEGRATION), Feature.TURNS, Feature.STATE)]
Operator = Annotated[Principal, Depends(require_governance(Role.INTEGRATION))]
"""The kept batches' routes: a batch of events is kept aside whatever features the space has turned on."""
Turns = Annotated[Any, Depends(service("turns"))]
ReplayRuns = Annotated[Any, Depends(service("replay"))]
Issues = Annotated[Any, Depends(service("data_issues"))]
Ingest = Annotated[Any, Depends(service("ingest"))]
Feed = Annotated[Any, Depends(service("notifications"))]
MAX_LISTED = 50


# Parsed turn by turn, so one malformed turn is an entry of the 207 and never a 422 for the request; the
# body is declared by hand, with an `$id` that scopes the schema's own `#/$defs/...` references.
_TURNS_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$id": "urn:niadra:v1:turns-request", **TurnsRequest.model_json_schema()}
            }
        },
    }
}


@router.post(
    "",
    response_model=TurnsResponse,
    responses={
        207: {"model": TurnsResponse, "description": "Some turns were rejected; see `errors`."},
        413: {"description": "The body is over 4 MB compressed or 16 MB decompressed: `turn_too_large`."},
    },
    openapi_extra=_TURNS_BODY,
)
async def record_turns(request: Request, caller: Tracker, turns: Turns, large: Large) -> JSONResponse:
    """Up to 50 turn records, the body in zstd, gzip or plain JSON. The same turn sent twice is one turn."""
    async with decoded_body(request, large, MAX_BODY_BYTES, MAX_DECODED_BYTES, TurnTooLarge) as body:
        try:
            parsed = orjson.loads(body)
        except orjson.JSONDecodeError:
            raise InvalidInput("the body is not JSON") from None
        items = parsed.get("turns") if isinstance(parsed, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_TURNS_PER_REQUEST:
            raise InvalidInput(f"`turns` must be a list of 1 to {MAX_TURNS_PER_REQUEST} turn records")
        result: TurnsResponse = await turns.record(caller, items)
    return JSONResponse(result.model_dump(mode="json"), status_code=207 if result.errors else 200)


@router.post("/promote", response_model=PromoteResponse)
async def promote_turns(
    body: PromoteRequest, caller: Tracker, turns: Turns, _idempotency_key: IdempotencyKey
) -> PromoteResponse:
    """Promoting is idempotent by itself: a turn promoted again counts as `already_kept`."""
    result: PromoteResponse = await turns.promote(caller, body)
    return result


@feed_router.get("", response_model=NotificationPage)
async def notifications(
    caller: AnyKey, feed: Feed, cursor: Cursor = None, limit: Limit = 100
) -> NotificationPage:
    """The events the webhooks carry, oldest first, from `cursor` (the start of the feed without one); a
    poll with nothing new answers the same cursor. Events stay 7 days."""
    result: NotificationPage = await feed.page(caller, cursor, limit)
    return result


@admin_router.get("/turns/index/{day}", response_model=TurnIndexPage)
async def turn_index(
    day: date,
    caller: Replay,
    turns: Turns,
    cursor: Annotated[TurnId | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> TurnIndexPage:
    """The rows the kept tier took on a UTC day with their digests, which make the `turns.root` of the day's
    `audit.root` event: the company anchors them in its own audit chain."""
    result: TurnIndexPage = await turns.index(caller, day, cursor, limit)
    return result


@admin_router.get("/turns/{turn_id}", response_model=TurnView)
async def read_turn(turn_id: TurnId, caller: Replay, turns: Turns) -> TurnView:
    """One turn from either tier, by its id: 404 once its tier let it go."""
    result: TurnView = await turns.read(caller, turn_id)
    return result


@admin_router.post("/turns/search", response_model=TurnSearchResponse)
async def search_turns(body: TurnSearchRequest, caller: Replay, turns: Turns) -> TurnSearchResponse:
    """The kept tier, newest first, by conversation, agent, time and flags."""
    result: TurnSearchResponse = await turns.search(caller, body)
    return result


@admin_router.post(
    "/replay/cases",
    response_model=ReplayCase,
    responses={
        410: {"description": "`replay_expired`: the scenario's turn left storage."},
        422: {"description": "`pin_mismatch` with `pins`, or `not_replayable` with `reasons`."},
    },
)
async def replay_case(body: ReplayCaseRequest, caller: Replay, replay: ReplayRuns) -> ReplayCase:
    """One turn with what a runner needs to run it again, once the running build shares its pins."""
    result: ReplayCase = await replay.case(caller, body)
    return result


@admin_router.post("/scenarios", response_model=Scenario, status_code=201)
async def create_scenario(
    body: ScenarioCreate, caller: Replay, replay: ReplayRuns, idempotency_key: IdempotencyKey
) -> Scenario:
    """Keeps the turns as a scenario; without assertions, the ones suggested by rule."""
    result: Scenario = await replay.create(caller, body, idempotency_key)
    return result


@admin_router.post("/scenarios/from-report", response_model=Scenario, status_code=201)
async def scenario_from_report(
    body: ScenarioFromReport, caller: Replay, replay: ReplayRuns, idempotency_key: IdempotencyKey
) -> Scenario:
    result: Scenario = await replay.from_report(caller, body, idempotency_key)
    return result


@admin_router.get("/scenarios", response_model=ScenarioPage)
async def list_scenarios(
    caller: Replay,
    replay: ReplayRuns,
    ids: Annotated[str | None, Query(max_length=2000, description="Up to 50 ids, comma-separated.")] = None,
    status: Literal["active", "retired"] = "active",
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ScenarioPage:
    """A status's scenarios, newest first; or the ones named by `ids`, whatever their status."""
    wanted = [i for i in ids.split(",") if i] if ids is not None else None
    if wanted is not None and len(wanted) > MAX_LISTED:
        raise InvalidInput(f"`ids` names at most {MAX_LISTED} scenarios")
    result: ScenarioPage = await replay.scenarios(
        caller, ids=wanted, status=status, cursor=cursor, limit=limit
    )
    return result


@admin_router.get("/scenarios/{scenario_id}", response_model=Scenario)
async def read_scenario(scenario_id: str, caller: Replay, replay: ReplayRuns) -> Scenario:
    result: Scenario = await replay.get(caller, scenario_id)
    return result


@admin_router.patch("/scenarios/{scenario_id}", response_model=Scenario)
async def update_scenario(
    scenario_id: str, body: ScenarioUpdate, caller: Replay, replay: ReplayRuns
) -> Scenario:
    result: Scenario = await replay.update(caller, scenario_id, body)
    return result


@admin_router.post(
    "/scenario-runs",
    response_model=ScenarioRun,
    status_code=201,
    responses={422: {"description": "`pin_mismatch` with `pins`: nothing was recorded."}},
)
async def create_scenario_run(
    body: ScenarioRunCreate, caller: Replay, replay: ReplayRuns, idempotency_key: IdempotencyKey
) -> ScenarioRun:
    """The executions the company's CI ran; the answer carries the verdict already decided."""
    result: ScenarioRun = await replay.report(caller, body, idempotency_key)
    return result


@admin_router.get("/scenario-runs/{run_id}", response_model=ScenarioRun)
async def read_scenario_run(run_id: str, caller: Replay, replay: ReplayRuns) -> ScenarioRun:
    result: ScenarioRun = await replay.read_run(caller, run_id)
    return result


@admin_router.get("/changes", response_model=ChangePage)
async def changes(
    caller: Replay,
    replay: ReplayRuns,
    agent: Annotated[str | None, Query(max_length=256)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ChangePage:
    """What changed between consecutive builds of each agent, newest first."""
    result: ChangePage = await replay.changes(caller, agent=agent, cursor=cursor, limit=limit)
    return result


@admin_router.get("/data-issues", response_model=DataIssuePage)
async def data_issues(
    caller: Integration,
    issues: Issues,
    status: Literal["open", "acknowledged", "all"] = "open",
    kind: Annotated[str | None, Query(max_length=64)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> DataIssuePage:
    """Newest opened first: data problems, drift, divergence and rule conflicts, with no personal data."""
    result: DataIssuePage = await issues.list(caller, status=status, kind=kind, cursor=cursor, limit=limit)
    return result


@admin_router.post("/data-issues/{issue_id}/ack", response_model=DataIssue)
async def acknowledge_data_issue(issue_id: str, caller: Integration, issues: Issues) -> DataIssue:
    """Closes the issue; the next occurrence of the same problem opens a new one."""
    result: DataIssue = await issues.acknowledge(caller, issue_id)
    return result


@admin_router.get("/intake/failed", response_model=FailedIntakePage)
async def failed_intake(caller: Operator, ingest: Ingest, turns: Turns) -> FailedIntakePage:
    """The batches that failed every attempt to be taken in (`row_failed`): kept aside, sealed, never
    deleted, oldest first. Kind, source and times only, never their content."""
    space_id = caller.space.space_id
    kept = [*await ingest.failed(space_id), *await turns.failed(space_id)]
    items = [
        FailedIntake(
            intake_id=f.intake_id,
            kind=f.kind,
            source_id=f.source_id,
            received_at=f.received_at,
            failed_at=f.failed_at,
            attempts=f.attempts,
        )
        for f in sorted(kept, key=lambda f: f.received_at)
    ]
    return FailedIntakePage(items=items, kept=await ingest.kept(space_id) + await turns.kept(space_id))


@admin_router.post("/intake/failed/retry", response_model=FailedIntakeRetried)
async def retry_failed_intake(
    body: FailedIntakeRetry, caller: Operator, ingest: Ingest, turns: Turns
) -> FailedIntakeRetried:
    """Puts kept batches back in the space's order for a worker to store, every one when no ids are given.
    A batch that fails again is kept aside again, with a new `row_failed` occurrence."""
    space_id = caller.space.space_id
    ids = body.intake_ids
    retried = await ingest.retry(space_id, ids) + await turns.retry(space_id, ids)
    return FailedIntakeRetried(retried=retried)
