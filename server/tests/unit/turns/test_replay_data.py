"""What a replay run adds to the space (24.6.5, 24.6.6): a failure attributed to data opens a data issue for
its owner, the configuration's changed sections join the change log, and a scenario keeps its turns as long
as the recording document says."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from niadra.app.data_issues import DataIssuesService, type_rules
from niadra.app.replay import ReplayService
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.recording import RecordingSettings, escalate
from niadra.contracts.turns import ScenarioCreate, ScenarioRunCreate
from niadra.domain.control.records import Role
from niadra.domain.turns.issues import IssueQuery, TypeRules, data_defects
from niadra.domain.turns.replay import ConfigSeen, config_changes
from niadra.testing.cell import SPACE_ID
from tests.unit.turns.conftest import World, turned_on
from tests.unit.turns.records import turn

T1, T2 = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d001", "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d002"
BUILD = {"pins": {"prompts": {"core": "v16"}, "model": "model-2026-08"}}
QUOTE_TYPE = {
    "type": "health_quote",
    "ownership": "subject",
    "mirror_of": {"system": "quoting"},
    "fields": {
        "price_full": {"type": "money", "freshness": {"class": "price"}},
        "plan": {"type": "string"},
        "network": {"type": "string", "freshness": {"class": "none"}},
    },
    "states": ["open", "accepted"],
    "sources": {"quoting": {"kind": "pull", "precedence": 1}},
    "lifecycle": {
        "outcome": {
            "final": ["accepted"],
            "vocabularies": [{"source": "quoting", "map": {"ACC": "accepted"}}],
        }
    },
}


def _observed(fields: dict[str, Any], seen_at: str = "2026-09-29T14:02:10Z") -> dict[str, Any]:
    """A quote turn whose tool showed these fields, seen at the source at `seen_at`; it ran at 14:02:11."""
    record = turn(T1)
    record["calls"][0]["observations"] = [
        {
            "ref": "health_quote:operator:q-77",
            "fields": fields,
            "provenance": {"source": "live", "source_observed_at": seen_at},
        },
        {"ref": "gift_card:store:7", "fields": {"balance": None}},
    ]
    return record


def test_what_a_tool_showed_is_wrong_when_null_stale_or_out_of_the_types_vocabulary() -> None:
    rules = {"health_quote": TypeRules({"price_full": 60_000, "plan": None}, frozenset({"open", "ACC"}))}
    found = data_defects(
        _observed({"price_full": 511.06, "plan": None, "state": "LOST", "extra": None}), rules
    )
    old = data_defects(_observed({"price_full": 511.06}, seen_at="2026-09-29T14:00:00Z"), rules)
    fresh = data_defects(_observed({"price_full": 511.06, "state": "ACC"}), rules)
    assert found == {
        ("null_field", "health_quote", "plan", "quote"): ("health_quote:operator:q-77",),
        ("out_of_vocabulary", "health_quote", "state", "quote"): ("health_quote:operator:q-77",),
    }, "an undeclared field or type is not this module's issue"
    assert old == {("stale_source", "health_quote", "price_full", "quote"): ("health_quote:operator:q-77",)}
    assert fresh == {}


def test_a_types_rules_are_its_fields_ages_and_every_state_a_vocabulary_names() -> None:
    registry = ObjectTypeRegistry.model_validate({"types": [QUOTE_TYPE]})
    [rules] = type_rules(_config_with(registry)).values()
    assert rules.fields == {"price_full": 60_000, "plan": None, "network": None}
    assert rules.states == {"open", "accepted", "ACC"}


def test_the_config_kind_pairs_each_sections_digests_and_starts_at_its_first() -> None:
    t0 = datetime.fromisoformat(turn()["started_at"])
    seen = [
        ConfigSeen("recording", 3, t0, b"\x01"),
        ConfigSeen("recording", 7, t0 + timedelta(hours=1), b"\x02"),
        ConfigSeen("policy", 7, t0 + timedelta(hours=1), b"\x09"),
    ]
    [change] = config_changes(seen)
    assert (change.kind, change.agent, change.name) == ("config", None, "recording")
    assert (change.before, change.after) == ("sha256:01", "sha256:02")


def test_keeping_scenarios_longer_needs_security() -> None:
    dated = RecordingSettings(scenario_days=180)
    assert escalate(dated, RecordingSettings(scenario_days=365)) == {Role.SECURITY}
    assert escalate(dated, RecordingSettings()) == {Role.SECURITY}, "unset keeps them forever"
    assert escalate(dated, RecordingSettings(scenario_days=30)) == frozenset()
    assert escalate(RecordingSettings(), RecordingSettings(scenario_days=30)) == frozenset()
    with pytest.raises(ValueError, match="scenario_days"):
        RecordingSettings(scenario_days=3)


def _config_with(registry: ObjectTypeRegistry, **recording: Any) -> Any:
    config = turned_on()
    return config.model_copy(
        update={
            "object_types": registry,
            "recording": RecordingSettings(**{"sample_rate": 0, "kept_days": 30, **recording}),
        }
    )


def _replay(world: World, **recording: Any) -> tuple[ReplayService, DataIssuesService]:
    world.cell.config.put(
        _config_with(ObjectTypeRegistry.model_validate({"types": [QUOTE_TYPE]}), **recording)
    )
    issues = DataIssuesService(
        world.cell.uow, world.cell.clock, world.cell.config, world.audit, world.cell.cache
    )  # type: ignore[arg-type]
    replay = ReplayService(
        world.cell.uow,
        world.service,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        issues=issues,
    )
    return replay, issues


def _run(scenario_id: str, turn_id: str, outcome: str, kind: str = "claims_match_state") -> ScenarioRunCreate:
    return ScenarioRunCreate.model_validate(
        {
            "scenario_ids": [scenario_id],
            "build": BUILD,
            "results": [
                {
                    "scenario_id": scenario_id,
                    "turn_id": turn_id,
                    "run": run,
                    "status": "completed",
                    "assertions": [{"id": "a", "kind": kind, "outcome": outcome}],
                }
                for run in (1, 2)
            ],
        }
    )


async def test_a_failure_that_read_null_data_opens_a_data_issue_and_a_pass_or_another_kind_does_not(
    world: World,
) -> None:
    replay, _ = _replay(world, scenario_days=30)
    null_plan = _observed({"price_full": 511.06, "plan": None})
    null_plan["turn_id"] = T2
    await world.service.record(world.caller, [turn(T1), null_plan])
    scenario = await replay.create(world.caller, ScenarioCreate(name="quote", turn_ids=[T1, T2]), "s1")

    await replay.report(world.caller, _run(scenario.scenario_id, T2, "pass"), "r1")
    await replay.report(world.caller, _run(scenario.scenario_id, T2, "fail", kind="tool_called"), "r2")
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.turns.issues(IssueQuery()) == []

    await replay.report(world.caller, _run(scenario.scenario_id, T2, "fail"), "r3")
    await replay.report(world.caller, _run(scenario.scenario_id, T2, "fail"), "r3")  # the same report again
    async with world.cell.uow(SPACE_ID) as uow:
        [issue] = await uow.turns.issues(IssueQuery())
        [kept] = await uow.turns.kept([T2])
    assert (issue.kind, issue.object_type, issue.field, issue.source) == (
        "null_field",
        "health_quote",
        "plan",
        "quote",
    )
    assert (issue.refs, issue.occurrences) == (("health_quote:operator:q-77",), 1)
    assert kept.kept_until == world.cell.clock.now() + timedelta(days=30), "the recording's scenario_days"


async def test_a_changed_section_of_the_configuration_joins_the_change_log_by_its_digest(
    world: World,
) -> None:
    replay, _ = _replay(world)
    config = world.cell.config.space(SPACE_ID)
    await replay.record_config(config, 1, world.cell.clock.now())
    world.cell.clock.advance(hours=1)
    changed = config.model_copy(update={"recording": RecordingSettings(kept_days=60)})
    await replay.record_config(changed, 2, world.cell.clock.now())
    await replay.record_config(changed, 3, world.cell.clock.now())
    off = changed.model_copy(update={"features": type(changed.features)(enabled=frozenset())})
    await replay.record_config(off.model_copy(update={"policy": config.policy}), 4, world.cell.clock.now())

    log = await replay.changes(world.caller, agent="closing", cursor=None, limit=50)
    [change] = [c for c in log.items if c.kind == "config"]
    assert (change.name, change.agent, change.at) == ("recording", None, world.cell.clock.now())
    assert change.before != change.after
    assert str(change.after).startswith("sha256:")
    async with world.cell.uow(SPACE_ID) as uow:
        history = await uow.turns.config_history(100)
    assert {h.version for h in history} == {1, 2}, "unchanged sections and a space off record nothing"
