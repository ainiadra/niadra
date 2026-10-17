"""Replay (24.3.5, 24.6.5): scenarios, the case a runner gets, the verdict of a run and the change log.

Niadra orchestrates and the company's CI executes. A scenario keeps its turns in a copy no bucket rule
expires (`turns/scenario/`), with a kept row the retention sweep deletes once the recording document's
`scenario_days` passed since the scenario last ran; unset, the turns stay until an erasure.
A case is a turn with the build it ran on, served only to a run that shares its pins; the runner runs
it inside the company's boundary and reports each execution. The verdict is decided here, per assertion,
against the scenario's last accepted run (`domain.turns.replay`). Every route is an operator's, off the hot
path: a few statements each, and no model.
"""

import base64
import hashlib
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import orjson

from niadra.app.admin_audit import AdminAudit
from niadra.app.data_issues import type_rules
from niadra.app.turns import LoadedTurn, TurnService
from niadra.config.models import SpaceConfig
from niadra.contracts.turns import (
    MAX_SCENARIO_TURNS,
    Change,
    ChangePage,
    ReplayAssertion,
    ReplayCase,
    ReplayCaseRequest,
    ReplayHistoryEntry,
    ReplayInput,
    Scenario,
    ScenarioCreate,
    ScenarioFromReport,
    ScenarioPage,
    ScenarioRun,
    ScenarioRunCreate,
    ScenarioUpdate,
    TurnBuild,
    TurnRecord,
)
from niadra.domain.common.errors import InvalidInput, NotFound, NotReplayable, PinMismatch, ReplayExpired
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.records import EventRecord
from niadra.domain.turns.daily import build_hash, started
from niadra.domain.turns.frames import frame_of
from niadra.domain.turns.issues import DATA_ASSERTIONS, DefectKey, data_defects, merged_refs
from niadra.domain.turns.records import KeptTurn, ScenarioChange, ScenarioRow, ScenarioRunRow
from niadra.domain.turns.replay import (
    Execution,
    Outcome,
    argument_errors,
    blockers,
    changes,
    config_changes,
    newest_first,
    pin_mismatches,
    pins_of,
    scenario_stats,
    suggest,
    worst,
)
from niadra.domain.turns.storage import KeptBy, Tier, turn_key
from niadra.domain.vocabulary import EventKind, Feature, ReceiptKind, ReceiptSurface, Speaker, Visibility
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import Clock, ConfigProvider, ObjectStore
from niadra.ports.uow import UnitOfWorkFactory

HISTORY_WINDOW = timedelta(days=7)
HISTORY_ENTRIES = 50
BASELINE_RUNS = 50
"""The earlier runs a report looks through for each scenario's baseline."""
CHANGE_BUILDS = 2000
CHANGE_SECTIONS = 2000
_IDENTITY = frozenset({"space_id", "tenant_id", "region", "environment"})
MAX_DATA_ISSUES = 20
"""Issues one run may open or count: the first by class, type, field and tool."""
_ROLES = {Speaker.CUSTOMER: "customer", Speaker.AI_AGENT: "agent", Speaker.HUMAN_AGENT: "human"}
_ACCEPTED = frozenset({"pass", "flaky"})


class ReplayService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        turns: TurnService,
        objects: ObjectStore,
        clock: Clock,
        config: ConfigProvider,
        audit: AdminAudit,
        issues: DataIssues | None = None,
    ) -> None:
        self._uow = uow
        self._turns = turns
        self._objects = objects
        self._clock = clock
        self._config = config
        self._audit = audit
        self._issues = issues

    # Scenarios

    async def create(self, principal: Principal, request: ScenarioCreate, idempotency_key: str) -> Scenario:
        assertions = list(request.assertions) or None
        return await self._make(
            principal, request.name, "manual", request.turn_ids, assertions, None, idempotency_key
        )

    async def from_report(
        self, principal: Principal, request: ScenarioFromReport, idempotency_key: str
    ) -> Scenario:
        """The report's turns, or the newest of its conversation's kept and short ones; its description is
        read for the suggestions and never kept."""
        turn_ids = list(request.turn_ids)
        if request.conversation_id is not None:
            turn_ids = await self._turns.conversation_turns(
                principal, request.conversation_id, MAX_SCENARIO_TURNS
            )
            if not turn_ids:
                raise NotFound("no recorded turn of the conversation is still kept")
        name = request.name or f"report {self._clock.now():%Y-%m-%d %H:%M}"
        return await self._make(
            principal, name, "report", turn_ids, None, request.description, idempotency_key
        )

    async def _make(
        self,
        principal: Principal,
        name: str,
        origin: str,
        turn_ids: Sequence[str],
        assertions: list[ReplayAssertion] | None,
        description: str | None,
        idempotency_key: str,
    ) -> Scenario:
        space_id = principal.space.space_id
        scenario_id = _derived_id(space_id, "scenario", idempotency_key)
        async with self._uow(space_id) as uow:
            existing = await uow.turns.scenarios(ids=[scenario_id])
        if existing:
            return _scenario(existing[0])
        turn_ids = list(dict.fromkeys(turn_ids))
        loaded = [await self._turns.load(principal, turn_id) for turn_id in turn_ids]
        frames = [frame_of(turn.envelope["record"]) for turn in loaded]
        required = self._config.space(space_id).recording.required_pins
        refused = [
            {"turn_id": turn.turn_id, "reason": reason, "value": value}
            for turn, frame in zip(loaded, frames, strict=True)
            for reason, value in blockers(frame, required)
        ]
        if refused:
            raise NotReplayable(refused)
        documents = (
            _checked(assertions, turn_ids)
            if assertions is not None
            else [
                ReplayAssertion.model_validate({**asdict(s), "suggested": True})
                for s in suggest(frames, description)
            ]
        )
        now = self._clock.now()
        kept = []
        for turn, frame in zip(loaded, frames, strict=True):
            copy = turn_key(Tier.SCENARIO, space_id, turn.conversation, turn.turn_id)
            if turn.key != copy:
                await self._objects.copy(turn.key, copy)
            kept.append(_scenario_row(turn, frame, now, _scenario_until(self._config, space_id, now)))
        row = ScenarioRow(
            scenario_id=scenario_id,
            name=name,
            origin=origin,
            turn_ids=tuple(turn_ids),
            assertions=tuple(a.model_dump(mode="json", exclude_none=True) for a in documents),
            status="active",
            version=1,
            created_at=now,
            created_by=_actor(principal),
            updated_at=now,
        )
        async with self._uow(space_id) as uow:
            await uow.turns.keep_for_scenario(kept)
            stored = await uow.turns.add_scenario(row)
        return _scenario(stored)

    async def scenarios(
        self,
        principal: Principal,
        *,
        ids: Sequence[str] | None,
        status: str,
        cursor: str | None,
        limit: int,
    ) -> ScenarioPage:
        space_id = principal.space.space_id
        if ids is not None:
            async with self._uow(space_id) as uow:
                rows = await uow.turns.scenarios(ids=[_uuid(i) for i in ids])
            return ScenarioPage(items=[_scenario(r) for r in rows])
        async with self._uow(space_id) as uow:
            rows = await uow.turns.scenarios(status=status, before=_scenario_cursor(cursor), limit=limit + 1)
        page = rows[:limit]
        more = len(rows) > limit and page
        return ScenarioPage(
            items=[_scenario(r) for r in page],
            next_cursor=_encode(f"{page[-1].created_at.isoformat()}|{page[-1].scenario_id}")
            if more
            else None,
        )

    async def get(self, principal: Principal, scenario_id: str) -> Scenario:
        return _scenario(await self._scenario(principal.space.space_id, scenario_id))

    async def update(self, principal: Principal, scenario_id: str, request: ScenarioUpdate) -> Scenario:
        space_id = principal.space.space_id
        current = await self._scenario(space_id, scenario_id)
        assertions = (
            tuple(
                a.model_dump(mode="json", exclude_none=True)
                for a in _checked(list(request.assertions), current.turn_ids)
            )
            if request.assertions is not None
            else None
        )
        change = ScenarioChange(name=request.name, assertions=assertions, status=request.status)
        async with self._uow(space_id) as uow:
            row = await uow.turns.change_scenario(current.scenario_id, change, self._clock.now())
        if row is None:
            raise NotFound("scenario not found")
        return _scenario(row)

    async def _scenario(self, space_id: UUID, scenario_id: str) -> ScenarioRow:
        async with self._uow(space_id) as uow:
            rows = await uow.turns.scenarios(ids=[_uuid(scenario_id)])
        if not rows:
            raise NotFound("scenario not found")
        return rows[0]

    # Cases

    async def case(self, principal: Principal, request: ReplayCaseRequest) -> ReplayCase:
        """A turn with everything a runner needs, once the running build shares its pins (the replay spec,
        4.2); in `stored` mode with the kept values, otherwise with the pointers and digests only."""
        space_id = principal.space.space_id
        required = self._config.space(space_id).recording.required_pins
        assertions: list[dict[str, Any]] = []
        if request.scenario_id is not None:
            scenario = await self._scenario(space_id, request.scenario_id)
            if request.turn_id not in scenario.turn_ids:
                raise NotFound("the turn is not one of the scenario's")
            try:
                loaded = await self._turns.load(principal, request.turn_id)
            except NotFound:
                raise ReplayExpired("the turn left storage") from None
            assertions = [a for a in scenario.assertions if a.get("turn_id") in (None, request.turn_id)]
        else:
            loaded = await self._turns.load(principal, request.turn_id)
        frame = frame_of(loaded.envelope["record"])
        refused = blockers(frame, required)
        if refused:
            raise NotReplayable([{"reason": reason, "value": value} for reason, value in refused])
        _check_pins(pins_of(frame), request.build, required, request.vary)
        record = TurnRecord.model_validate(loaded.envelope["record"])
        history = await self._history(space_id, record)
        await self._audit.record_read(
            principal,
            kind=ReceiptKind.QUALITY,
            surface=ReceiptSurface.TURN,
            item_ids=(request.turn_id,),
            conversation_id=record.conversation_id,
            task_id=record.task_id,
        )
        opening = next((h.text for h in reversed(history) if h.role == "customer"), None)
        return ReplayCase(
            case_id=str(uuid7()),
            turn_id=request.turn_id,
            scenario_id=request.scenario_id,
            mode=request.mode,
            record=record,
            required_pins=sorted(required),
            vary=sorted(set(request.vary)),
            input=ReplayInput(kind=record.kind, text=opening if record.kind == "message" else None),
            history=history,
            assertions=[ReplayAssertion.model_validate(a) for a in assertions],
            expires_at=_expires(loaded, self._config.space(space_id).recording.kept_days),
        )

    async def _history(self, space_id: UUID, record: TurnRecord) -> list[ReplayHistoryEntry]:
        """The conversation's public messages before the turn, masked as the memory keeps them."""
        if record.conversation_id is None:
            return []
        async with self._uow(space_id) as uow:
            events = await uow.ingest.events_between(
                conversation_id=record.conversation_id,
                since=record.started_at - HISTORY_WINDOW,
                until=record.started_at,
            )
        return [
            ReplayHistoryEntry.model_validate(
                {"role": _ROLES[e.speaker], "text": e.text, "at": e.occurred_at}
            )
            for e in events
            if _spoken(e)
        ][-HISTORY_ENTRIES:]

    # Runs

    async def report(
        self, principal: Principal, request: ScenarioRunCreate, idempotency_key: str
    ) -> ScenarioRun:
        """The verdict of N executions, decided at once: per scenario against its last accepted run, or
        against the recording when there is none (the replay spec, 8)."""
        space_id = principal.space.space_id
        run_id = _derived_id(space_id, "run", idempotency_key)
        wanted = [_uuid(s) for s in dict.fromkeys(request.scenario_ids)]
        strays = {r.scenario_id for r in request.results} - set(request.scenario_ids)
        if strays:
            raise InvalidInput("a result names a scenario the run does not list")
        now = self._clock.now()
        required = self._config.space(space_id).recording.required_pins
        async with self._uow(space_id) as uow:
            done = await uow.turns.run(run_id)
            if done is not None:
                return _run(done)
            scenarios = await uow.turns.scenarios(ids=wanted)
            if len(scenarios) != len(wanted):
                raise NotFound("a scenario of the run does not exist")
            turn_ids = sorted({t for s in scenarios for t in s.turn_ids})
            kept = await uow.turns.kept(turn_ids)
            pins = await uow.turns.build_pins(
                sorted({k.build_hash for k in kept if k.build_hash is not None})
            )
            earlier = await uow.turns.runs_of(wanted, now, BASELINE_RUNS * len(wanted))
        for row in kept:
            recorded = pins.get(row.build_hash) if row.build_hash is not None else None
            if recorded is None:
                # The build is added up after the scenario took the turn: its pins are in the record.
                recorded = pins_of(
                    frame_of((await self._turns.load(principal, row.turn_id)).envelope["record"])
                )
            _check_pins(recorded, request.build, required, request.vary)
        verdicts = []
        for scenario in sorted(scenarios, key=lambda s: str(s.scenario_id)):
            sid = str(scenario.scenario_id)
            executions = [
                Execution(r.status, tuple(Outcome(a.id, a.outcome) for a in r.assertions), r.paraphrase)
                for r in request.results
                if r.scenario_id == sid
            ]
            baseline_run, baseline = _baseline(earlier, sid)
            stats = scenario_stats(executions, baseline)
            verdicts.append({"scenario_id": sid, "baseline_run_id": baseline_run, **_plain(asdict(stats))})
        defects = await self._defects(principal, request)
        async with self._uow(space_id) as uow:
            await uow.turns.renew_scenario_turns(turn_ids, _scenario_until(self._config, space_id, now))
            stored = await uow.turns.add_run(
                ScenarioRunRow(
                    run_id=run_id,
                    scenario_ids=tuple(wanted),
                    build=request.build.model_dump(mode="json", by_alias=True, exclude_none=True),
                    mode=request.mode,
                    vary=tuple(sorted(set(request.vary))),
                    status="done",
                    verdict=worst(v["verdict"] for v in verdicts),
                    summary={"scenarios": verdicts},
                    created_at=now,
                    decided_at=now,
                )
            )
            if self._issues is not None:
                for (kind, object_type, field, tool), refs in defects:
                    await self._issues.open(
                        uow, space_id, kind, object_type=object_type, field=field, source=tool, refs=refs
                    )
        return _run(stored)

    async def _defects(
        self, principal: Principal, request: ScenarioRunCreate
    ) -> list[tuple[DefectKey, tuple[str, ...]]]:
        """A failure attributed to data (24.6.6): the turns where an assertion that reads what the tools
        showed failed, and what was wrong with what their tools showed, per class, type, field and tool."""
        if self._issues is None:
            return []
        rules = type_rules(self._config.space(principal.space.space_id))
        failed = sorted(
            {
                r.turn_id
                for r in request.results
                if r.status == "completed"
                and any(a.outcome == "fail" and a.kind in DATA_ASSERTIONS for a in r.assertions)
            }
        )
        if not rules or not failed:
            return []
        found: dict[DefectKey, tuple[str, ...]] = {}
        for turn_id in failed:
            try:
                loaded = await self._turns.load(principal, turn_id)
            except NotFound:
                continue
            for key, refs in data_defects(frame_of(loaded.envelope["record"]), rules).items():
                found[key] = merged_refs(found.get(key, ()), refs)
        return sorted(found.items())[:MAX_DATA_ISSUES]

    async def read_run(self, principal: Principal, run_id: str) -> ScenarioRun:
        async with self._uow(principal.space.space_id) as uow:
            row = await uow.turns.run(_uuid(run_id))
        if row is None:
            raise NotFound("run not found")
        return _run(row)

    # The change log

    async def record_config(self, config: SpaceConfig, version: int, at: datetime) -> None:
        """`ports.turns.ConfigHistory`: the digest of each section of an applied configuration that changed,
        once the space records turns; one statement. Section names and digests, never a value."""
        if Feature.TURNS not in config.features.enabled:
            return
        document = config.model_dump(mode="json")
        sections = {
            name: hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).digest()
            for name, value in document.items()
            if name not in _IDENTITY
        }
        async with self._uow(config.space_id) as uow:
            await uow.turns.record_config(sections, version, at)

    async def changes(
        self, principal: Principal, *, agent: str | None, cursor: str | None, limit: int
    ) -> ChangePage:
        async with self._uow(principal.space.space_id) as uow:
            builds = await uow.turns.builds(CHANGE_BUILDS)
            history = await uow.turns.config_history(CHANGE_SECTIONS)
        # A change of the space's configuration is every agent's, so a filter by agent keeps it.
        found = newest_first(
            [
                *(
                    c
                    for c in changes((str(b.source_id), b.agent, b.at, b.pins) for b in builds)
                    if agent is None or c.agent == agent
                ),
                *config_changes(history),
            ]
        )
        start = _offset(cursor)
        page = found[start : start + limit]
        return ChangePage(
            items=[Change.model_validate(asdict(c)) for c in page],
            next_cursor=_encode(str(start + limit)) if start + limit < len(found) else None,
        )


def _scenario_until(config: ConfigProvider, space_id: UUID, now: datetime) -> datetime | None:
    """Until when a scenario's turns stay after it last ran (the replay spec, 3.1): a run renews them to at
    least this, so a shorter setting takes effect as they come due. None while no retention is set."""
    days = config.space(space_id).recording.scenario_days
    return now + timedelta(days=days) if days is not None else None


def _check_pins(
    recorded: dict[str, Any], build: TurnBuild, required: frozenset[str], vary: Sequence[str]
) -> None:
    running = build.pins.model_dump(mode="json", by_alias=True)
    found = pin_mismatches(recorded, running, required, set(vary))
    if found:
        raise PinMismatch([{"name": n, "recorded": r, "running": g} for n, r, g in found])


def _checked(assertions: list[ReplayAssertion], turn_ids: Sequence[str]) -> list[ReplayAssertion]:
    """The ids are unique, each assertion's arguments are what its kind takes, and a scoped one names a turn
    of the scenario."""
    ids = [a.id for a in assertions]
    if len(ids) != len(set(ids)):
        raise InvalidInput("an assertion id repeats")
    for assertion in assertions:
        errors = argument_errors(assertion.kind, assertion.args)
        if assertion.turn_id is not None and assertion.turn_id not in turn_ids:
            errors.append("`turn_id` is not a turn of the scenario")
        if errors:
            raise InvalidInput(f"assertion {assertion.id}: " + "; ".join(errors))
    return assertions


def _baseline(
    earlier: Sequence[ScenarioRunRow], scenario_id: str
) -> tuple[str | None, dict[str, tuple[int, int]] | None]:
    """The latest earlier run whose verdict for the scenario was accepted, and its counts per assertion."""
    for run in earlier:
        for verdict in run.summary.get("scenarios", ()):
            if verdict["scenario_id"] == scenario_id and verdict["verdict"] in _ACCEPTED:
                counts = {a["id"]: (a["passed"], a["failed"]) for a in verdict["assertions"]}
                return str(run.run_id), counts
    return None, None


def _scenario_row(turn: LoadedTurn, frame: dict[str, Any], now: datetime, until: datetime | None) -> KeptTurn:
    pins = pins_of(frame)
    return KeptTurn(
        turn_id=turn.turn_id,
        conversation=turn.conversation,
        source_id=UUID(turn.envelope["source_id"]),
        agent=frame["agent"]["name"],
        kind=frame.get("kind", "message"),
        started_at=started(frame),
        fidelity=frame.get("fidelity", "gold"),
        completeness=frame.get("completeness", "complete"),
        content_mode=frame["content_mode"],
        replayable=True,
        build_hash=build_hash(pins),
        flags=tuple(sorted(frame.get("flags") or ())),
        kept_by=KeptBy.SCENARIO,
        kept_at=now,
        kept_until=until,
    )


def _expires(turn: LoadedTurn, kept_days: int | None) -> datetime | None:
    """When the turn leaves storage: its kept row's date, or the recording retention counted from when it
    arrived; None while no retention is set."""
    if turn.kept is not None and turn.kept.kept_until is not None:
        return turn.kept.kept_until
    if kept_days is None:
        return None
    return datetime.fromisoformat(turn.envelope["received_at"]) + timedelta(days=kept_days)


def _spoken(event: EventRecord) -> bool:
    return (
        event.kind == EventKind.MESSAGE
        and event.visibility == Visibility.PUBLIC
        and event.speaker in _ROLES
        and bool(event.text)
    )


def _scenario(row: ScenarioRow) -> Scenario:
    return Scenario.model_validate(
        {
            "scenario_id": str(row.scenario_id),
            "name": row.name,
            "origin": row.origin,
            "turn_ids": list(row.turn_ids),
            "assertions": list(row.assertions),
            "status": row.status,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _run(row: ScenarioRunRow) -> ScenarioRun:
    return ScenarioRun.model_validate(
        {
            "run_id": str(row.run_id),
            "status": row.status,
            "verdict": row.verdict,
            "mode": row.mode,
            "vary": list(row.vary),
            "build": row.build,
            "summary": row.summary,
            "created_at": row.created_at,
        }
    )


def _plain(stats: dict[str, Any]) -> dict[str, Any]:
    return {**stats, "assertions": [dict(a) for a in stats["assertions"]]}


def _derived_id(space_id: UUID, kind: str, key: str) -> UUID:
    """The same `Idempotency-Key` names the same scenario or run: a retried request finds what it made."""
    return uuid5(space_id, f"{kind}|{key}")


def _actor(principal: Principal) -> str:
    if isinstance(principal, SourcePrincipal):
        return f"source:{principal.source_id}"
    return f"user:{principal.user_id}"


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFound("not found") from None


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _scenario_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        at, scenario_id = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at), UUID(scenario_id)
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput("the cursor is not one this list returned") from None


def _offset(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(value.encode()).decode()))
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput("the cursor is not one this list returned") from None
