"""Replay against the in-memory cell (24.3.5, the replay spec): scenarios keep their turns and suggest
assertions, a case is served only to a run that shares its pins, and a run's executions become a verdict
against the scenario's last accepted run."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from niadra.app.replay import ReplayService
from niadra.contracts.turns import (
    ReplayCaseRequest,
    ScenarioCreate,
    ScenarioFromReport,
    ScenarioRunCreate,
    ScenarioUpdate,
    TurnBuild,
)
from niadra.domain.common.errors import InvalidInput, NotFound, NotReplayable, PinMismatch, ReplayExpired
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord
from niadra.domain.turns.storage import HMAC_PURPOSE, KeptBy, Tier, turn_key
from niadra.domain.vocabulary import EventKind, ReceiptSurface, Speaker, Visibility
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE
from tests.unit.turns.conftest import World
from tests.unit.turns.records import QUOTE_RESULT, turn

T1, T2, T3 = (f"0192f5a0-7c3e-7a10-9d2b-5e61a4c0d00{i}" for i in (1, 2, 3))
BUILD = {"pins": {"prompts": {"core": "v16"}, "model": "model-2026-08"}}


@pytest.fixture
def replay(world: World) -> ReplayService:
    return ReplayService(
        world.cell.uow,
        world.service,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
    )


def _build(**pins: Any) -> TurnBuild:
    return TurnBuild.model_validate({"pins": {**BUILD["pins"], **pins}})


async def _conversation(world: World) -> bytes:
    return await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")


async def _message(world: World, speaker: Speaker, text: str, at: datetime, **fields: Any) -> None:
    record = EventRecord(
        event_id=uuid7(),
        kind=fields.pop("kind", EventKind.MESSAGE),
        source_id=WHATSAPP_SOURCE,
        channel="whatsapp",
        speaker=speaker,
        occurred_at=at,
        received_at=at,
        handle_ids=(),
        text=text,
        conversation_id="c-8812",
        **fields,
    )
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.ingest.insert_events([NewEvent(SPACE_ID, record, str(record.event_id))])


def _results(scenario_id: str, turn_id: str, outcomes: str, **extra: Any) -> list[dict[str, Any]]:
    """One execution per letter: p passes the assertion `a`, f fails it, e is an infrastructure error."""
    return [
        {
            "scenario_id": scenario_id,
            "turn_id": turn_id,
            "run": run,
            "status": "infrastructure_error" if o == "e" else "completed",
            "assertions": []
            if o == "e"
            else [{"id": "a", "kind": "tool_called", "outcome": "pass" if o == "p" else "fail"}],
            **extra,
        }
        for run, o in enumerate(outcomes, start=1)
    ]


async def test_a_scenario_keeps_its_turns_beyond_their_tier_and_suggests_assertions(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1), turn(T2)])

    made = await replay.create(world.caller, ScenarioCreate(name="quote", turn_ids=[T1, T2]), "k1")
    again = await replay.create(world.caller, ScenarioCreate(name="other", turn_ids=[T1]), "k1")

    assert again == made, "the same idempotency key is the same scenario"
    assert (made.origin, made.status, made.version, made.turn_ids) == ("manual", "active", 1, [T1, T2])
    ids = {a.id for a in made.assertions}
    assert {"t1.tool_called.quote", "t1.claims_match_state", "t2.tool_called.quote"} <= ids
    assert all(a.suggested for a in made.assertions)
    conversation = await _conversation(world)
    assert turn_key(Tier.SCENARIO, SPACE_ID, conversation, T1) in world.turn_objects()
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.turns.kept([T1])
    assert row.kept_by == KeptBy.SCENARIO
    assert row.kept_until == world.cell.clock.now() + timedelta(days=180)

    # the short tier goes; the scenario's copy is read instead
    for key in [k for k in world.turn_objects() if k.startswith("turns/short/")]:
        del world.cell.objects.objects[key]
    view = await world.service.read(world.caller, T1)
    assert view.record.blobs["b:2"].content == QUOTE_RESULT


async def test_a_scenario_refuses_turns_that_cannot_be_run_again_with_every_reason(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1, mode="hash_only"), turn(T2)])
    with pytest.raises(NotReplayable) as refused:
        await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T1, T2]), "k1")
    assert refused.value.members == {
        "reasons": [{"turn_id": T1, "reason": "content_mode", "value": "hash_only"}]
    }
    with pytest.raises(NotFound):
        await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T3]), "k2")


async def test_given_assertions_are_checked(world: World, replay: ReplayService) -> None:
    await world.service.record(world.caller, [turn(T1)])
    bad = [
        [{"id": "a", "kind": "tool_called", "args": {}}],
        [{"id": "a", "kind": "handoff_when"}, {"id": "a", "kind": "claims_traced"}],
        [{"id": "a", "kind": "claims_traced", "turn_id": T2}],
    ]
    for assertions in bad:
        with pytest.raises(InvalidInput):
            await replay.create(
                world.caller,
                ScenarioCreate.model_validate({"name": "x", "turn_ids": [T1], "assertions": assertions}),
                "k" + str(len(assertions)),
            )


async def test_a_bug_report_becomes_a_scenario_of_the_conversations_turns(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1), turn(T2)])
    report = ScenarioFromReport(conversation_id="c-8812", description="the agent promised to call back")

    made = await replay.from_report(world.caller, report, "r1")

    assert (made.origin, made.turn_ids) == ("report", [T1, T2])
    report_ids = [a.id for a in made.assertions if a.turn_id is None]
    assert report_ids == ["report.no_promise_without_action"]
    with pytest.raises(NotFound):
        await replay.from_report(world.caller, ScenarioFromReport(conversation_id="c-unknown"), "r2")


async def test_a_case_carries_the_record_the_history_and_the_turns_assertions(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1), turn(T2)])
    started = datetime.fromisoformat("2026-09-29T14:02:11+00:00")
    await _message(world, Speaker.CUSTOMER, "quero cotar um plano", started - timedelta(minutes=3))
    await _message(world, Speaker.AI_AGENT, "Para quantas pessoas?", started - timedelta(minutes=2))
    await _message(
        world,
        Speaker.HUMAN_AGENT,
        "nota interna",
        started - timedelta(minutes=1),
        visibility=Visibility.INTERNAL,
    )
    await _message(world, Speaker.CUSTOMER, "para a familia", started - timedelta(seconds=5))
    await _message(world, Speaker.AI_AGENT, "depois do turno", started + timedelta(seconds=9))
    made = await replay.create(world.caller, ScenarioCreate(name="quote", turn_ids=[T1, T2]), "k1")

    case = await replay.case(
        world.caller,
        ReplayCaseRequest(
            turn_id=T1, scenario_id=made.scenario_id, build=_build(prompts={"core": "v17"}), vary=["prompts"]
        ),
    )

    assert case.record.blobs["b:2"].content == QUOTE_RESULT
    assert [(h.role, h.text) for h in case.history] == [
        ("customer", "quero cotar um plano"),
        ("agent", "Para quantas pessoas?"),
        ("customer", "para a familia"),
    ]
    assert (case.input.kind, case.input.text) == ("message", "para a familia")
    assert case.required_pins == ["model", "prompts"]
    assert {a.turn_id for a in case.assertions} == {T1}
    assert case.expires_at == world.cell.clock.now() + timedelta(days=180)
    assert world.audit.records[-1]["surface"] == ReceiptSurface.TURN


async def test_replay_with_a_different_pin_stops_with_pin_mismatch(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1)])
    other_model = _build(model="model-2026-09")

    with pytest.raises(PinMismatch) as stopped:
        await replay.case(world.caller, ReplayCaseRequest(turn_id=T1, build=other_model))

    assert stopped.value.members == {
        "pins": [{"name": "model", "recorded": "model-2026-08", "running": "model-2026-09"}]
    }
    case = await replay.case(world.caller, ReplayCaseRequest(turn_id=T1, build=other_model, vary=["model"]))
    assert case.scenario_id is None
    assert case.expires_at == world.cell.clock.now() + timedelta(days=30), "the space's `kept_days`"


async def test_a_case_refuses_a_turn_that_cannot_be_replayed_and_expires_with_its_scenario(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1, mode="hash_only"), turn(T2)])
    with pytest.raises(NotReplayable):
        await replay.case(world.caller, ReplayCaseRequest(turn_id=T1, build=_build()))
    made = await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T2]), "k1")
    for key in [k for k in world.turn_objects() if T2 in k]:
        del world.cell.objects.objects[key]
    with pytest.raises(ReplayExpired):
        await replay.case(
            world.caller, ReplayCaseRequest(turn_id=T2, scenario_id=made.scenario_id, build=_build())
        )
    with pytest.raises(NotFound):
        await replay.case(
            world.caller, ReplayCaseRequest(turn_id=T1, scenario_id=made.scenario_id, build=_build())
        )


async def test_a_run_gets_its_verdict_against_the_last_accepted_run(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1)])
    made = await replay.create(
        world.caller,
        ScenarioCreate.model_validate(
            {
                "name": "x",
                "turn_ids": [T1],
                "assertions": [{"id": "a", "kind": "tool_called", "args": {"tool": "quote"}}],
            }
        ),
        "k1",
    )
    sid = made.scenario_id

    def report(outcomes: str) -> ScenarioRunCreate:
        return ScenarioRunCreate.model_validate(
            {"scenario_ids": [sid], "build": BUILD, "runs": 5, "results": _results(sid, T1, outcomes)}
        )

    first = await replay.report(world.caller, report("ppppe"), "run-1")
    assert (first.status, first.verdict) == ("done", "pass")
    [scenario] = first.summary.scenarios
    assert (scenario.completed, scenario.infrastructure_errors, scenario.baseline_run_id) == (4, 1, None)
    assert await replay.report(world.caller, report("fffff"), "run-1") == first, "idempotent"

    world.cell.clock.advance(minutes=1)
    second = await replay.report(world.caller, report("pffff"), "run-2")
    assert second.verdict == "regression"
    [stats] = second.summary.scenarios[0].assertions
    assert (stats.baseline_passed, stats.baseline_failed, stats.regression) == (4, 0, True)
    assert second.summary.scenarios[0].baseline_run_id == first.run_id

    world.cell.clock.advance(minutes=1)
    third = await replay.report(world.caller, report("ppppp"), "run-3")
    assert third.summary.scenarios[0].baseline_run_id == first.run_id, "a regression is never a baseline"
    assert await replay.read_run(world.caller, second.run_id) == second


async def test_a_run_on_another_build_stops_with_pin_mismatch_and_records_nothing(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1)])
    made = await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T1]), "k1")
    body = {
        "scenario_ids": [made.scenario_id],
        "build": {"pins": {**BUILD["pins"], "prompts": {"core": "v17"}}},
        "results": _results(made.scenario_id, T1, "pp"),
    }
    with pytest.raises(PinMismatch):
        await replay.report(world.caller, ScenarioRunCreate.model_validate(body), "run-1")
    with pytest.raises(NotFound):
        await replay.read_run(world.caller, "00000000-0000-0000-0000-000000000000")
    varied = await replay.report(
        world.caller, ScenarioRunCreate.model_validate({**body, "vary": ["prompts"]}), "run-1"
    )
    assert varied.verdict == "pass"
    stray = {**body, "vary": ["prompts"], "results": _results(str(uuid7()), T1, "p")}
    with pytest.raises(InvalidInput):
        await replay.report(world.caller, ScenarioRunCreate.model_validate(stray), "run-2")


async def test_a_run_renews_its_scenarios_turns(world: World, replay: ReplayService) -> None:
    await world.service.record(world.caller, [turn(T1)])
    made = await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T1]), "k1")
    world.cell.clock.advance(days=100)
    body = {
        "scenario_ids": [made.scenario_id],
        "build": BUILD,
        "results": _results(made.scenario_id, T1, "p"),
    }
    await replay.report(world.caller, ScenarioRunCreate.model_validate(body), "run-1")
    async with world.cell.uow(SPACE_ID) as uow:
        [row] = await uow.turns.kept([T1])
    assert row.kept_until == world.cell.clock.now() + timedelta(days=180)


async def test_scenarios_are_listed_changed_and_retired(world: World, replay: ReplayService) -> None:
    await world.service.record(world.caller, [turn(T1)])
    made = [
        await replay.create(world.caller, ScenarioCreate(name=f"s{i}", turn_ids=[T1]), f"k{i}")
        for i in range(3)
    ]

    page = await replay.scenarios(world.caller, ids=None, status="active", cursor=None, limit=2)
    rest = await replay.scenarios(world.caller, ids=None, status="active", cursor=page.next_cursor, limit=2)
    assert len(page.items) + len(rest.items) == 3
    assert rest.next_cursor is None
    named = await replay.scenarios(
        world.caller, ids=[made[0].scenario_id], status="active", cursor=None, limit=50
    )
    assert [s.name for s in named.items] == ["s0"]

    changed = await replay.update(
        world.caller, made[0].scenario_id, ScenarioUpdate(status="retired", name="old")
    )
    assert (changed.status, changed.name, changed.version) == ("retired", "old", 2)
    retired = await replay.scenarios(world.caller, ids=None, status="retired", cursor=None, limit=50)
    assert [s.scenario_id for s in retired.items] == [made[0].scenario_id]
    assert (await replay.get(world.caller, made[0].scenario_id)).name == "old"
    with pytest.raises(NotFound):
        await replay.get(world.caller, "not-a-uuid")


async def test_the_change_log_comes_from_the_builds_seen(world: World, replay: ReplayService) -> None:
    await world.service.record(world.caller, [turn(T1)])
    world.cell.clock.advance(hours=1)
    later = turn(T2)
    later["build"]["pins"]["prompts"] = {"core": "v17"}
    await world.service.record(world.caller, [later])

    page = await replay.changes(world.caller, agent=None, cursor=None, limit=50)

    assert [(c.kind, c.name, c.before, c.after, c.agent) for c in page.items] == [
        ("prompt", "core", "v16", "v17", "closing")
    ]
    assert (await replay.changes(world.caller, agent="router", cursor=None, limit=50)).items == []


async def test_a_run_checks_the_pins_in_the_record_before_its_build_is_added_up(
    world: World, replay: ReplayService
) -> None:
    await world.service.record(world.caller, [turn(T1)])
    made = await replay.create(world.caller, ScenarioCreate(name="x", turn_ids=[T1]), "k1")
    world.cell.uow.store.module("turns")["builds"].clear()
    body = {
        "scenario_ids": [made.scenario_id],
        "build": _build(model="other").model_dump(),
        "results": _results(made.scenario_id, T1, "p"),
    }
    with pytest.raises(PinMismatch):
        await replay.report(world.caller, ScenarioRunCreate.model_validate(body), "run-1")
    same = {**body, "build": BUILD}
    assert (
        await replay.report(world.caller, ScenarioRunCreate.model_validate(same), "run-1")
    ).verdict == "pass"
