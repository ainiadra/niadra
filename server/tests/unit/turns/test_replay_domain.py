"""Replay's pure rules (24.3.5, the replay spec): what blocks a replay, the pin check, the assertions
suggested by rule and their arguments, the regression statistics against the published vectors, and the
change log."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.domain.turns.frames import frame_of
from niadra.domain.turns.replay import (
    ARGUMENTS,
    Execution,
    Outcome,
    argument_errors,
    blockers,
    changes,
    fisher_one_sided,
    pin_mismatches,
    pins_of,
    scenario_stats,
    suggest,
    worst,
)
from tests.spec import cases
from tests.unit.turns.records import turn

REQUIRED = frozenset({"prompts", "model"})


def test_only_a_gold_complete_turn_with_values_and_its_required_pins_can_be_replayed() -> None:
    assert blockers(frame_of(turn()), REQUIRED) == []
    record = turn(mode="hash_only", fidelity="bronze", completeness="unknown")
    record["build"] = {"pins": {"prompts": {"core": "v16"}}}
    assert blockers(frame_of(record), REQUIRED) == [
        ("content_mode", "hash_only"),
        ("fidelity", "bronze"),
        ("completeness", "unknown"),
        ("missing_pin", "model"),
    ]
    assert blockers(frame_of(turn(mode="pointer")), REQUIRED | {"assembler"}) == [
        ("missing_pin", "assembler")
    ]


def test_a_required_pin_must_match_and_an_optional_one_only_when_both_builds_carry_it() -> None:
    recorded = pins_of(frame_of(turn()))
    same = {**recorded, "corpus_digest": None}
    assert pin_mismatches(recorded, same, REQUIRED, ()) == []
    new_prompt = {**recorded, "prompts": {"core": "v17"}}
    assert pin_mismatches(recorded, new_prompt, REQUIRED, ()) == [
        ("prompts", {"core": "v16"}, {"core": "v17"})
    ]
    assert pin_mismatches(recorded, new_prompt, REQUIRED, ("prompts",)) == []
    no_model = {**recorded, "model": None}
    assert pin_mismatches(recorded, no_model, REQUIRED, ()) == [("model", "model-2026-08", None)]
    # tool schemas are not required here: compared only because both builds carry them
    other_tool = {**recorded, "tool_schemas": {"quote": "sha256:" + "2" * 64}}
    assert [m[0] for m in pin_mismatches(recorded, other_tool, REQUIRED, ())] == ["tool_schemas"]
    assert pin_mismatches(recorded, {**recorded, "tool_schemas": {}}, REQUIRED, ()) == []


def test_assertions_are_suggested_from_what_each_turn_did_and_from_the_report_words() -> None:
    handoff = turn("t2", output={"handoff_id": "h-1"}, effects=[{"key": "farewell:c-8812", "state": "done"}])
    suggested = suggest([frame_of(turn()), frame_of(handoff)], "O agente negou o plano e prometeu ligar")
    by_id = {s.id: s for s in suggested}
    assert {"t1.tool_called.quote", "t1.claims_traced", "t1.claims_match_state", "t1.budget"} <= set(by_id)
    assert by_id["t1.tool_called.quote"].args == {"tool": "quote"}
    assert by_id["t1.tool_called.quote"].turn_id == "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d001"
    assert by_id["t1.budget"].args == {"max_tool_calls": 2}
    assert by_id["t1.no_denial_with_results"].kind == "no_denial_with_results"
    assert {"t2.handoff_when", "t2.effect_once"} <= set(by_id)
    # the report adds what no turn suggested, for every turn
    assert by_id["report.no_promise_without_action"].turn_id is None
    assert (
        "report.no_denial_with_results" not in by_id or by_id["report.no_denial_with_results"].turn_id is None
    )
    assert len(by_id) == len(suggested), "ids are unique"
    for s in suggested:
        assert argument_errors(s.kind, s.args) == [], s.id


@pytest.mark.parametrize(
    ("kind", "args", "wrong"),
    [
        ("tool_called", {}, "needs `tool`"),
        ("tool_called", {"tool": "quote", "min": 0}, "out of range"),
        ("expected_in_topk", {"ref": "item:store:1", "k": "3"}, "wrong type"),
        ("budget", {}, "at least one limit"),
        ("budget", {"max_cost_usd": True}, "wrong type"),
        ("lexicon", {"lang": "pt"}, "at least one limit"),
        ("lexicon", {"must_include": ["", "x"]}, "non-empty strings"),
        ("no_denial_with_results", {"lang": "fr"}, "one of pt, en, es"),
        ("hard_respected", {"strict": True}, "not an argument"),
    ],
)
def test_an_assertion_takes_only_its_kinds_arguments(kind: str, args: dict[str, Any], wrong: str) -> None:
    errors = argument_errors(kind, args)
    assert any(wrong in e for e in errors), errors


def test_every_kind_of_the_spec_has_its_arguments() -> None:
    assert len(ARGUMENTS) == 13


FIELDS = {"description", "executions", "baseline"}


@pytest.mark.parametrize("case", cases("regression-stats", FIELDS), ids=lambda c: c["id"])
def test_regression_statistics_keep_flaky_results_and_infrastructure_errors_apart(
    case: dict[str, Any],
) -> None:
    executions = []
    for execution in case["executions"]:
        assert set(execution) == {"status", "paraphrase", "outcomes"}, case["id"]
        outcomes = tuple(Outcome(k, v) for k, v in execution["outcomes"].items())
        executions.append(Execution(execution["status"], outcomes, execution["paraphrase"]))
    baseline = case["baseline"]
    stats = scenario_stats(executions, {k: tuple(v) for k, v in baseline.items()} if baseline else None)
    assert asdict(stats) == {**case["expect"], "assertions": tuple(case["expect"]["assertions"])}


def test_the_fisher_tail_is_the_hypergeometric_one() -> None:
    assert fisher_one_sided(5, 0, 0, 5) == pytest.approx(1 / 252)
    assert fisher_one_sided(5, 0, 5, 0) == 1.0
    assert fisher_one_sided(0, 0, 0, 0) == 1.0


def test_the_run_takes_its_worst_scenario() -> None:
    assert worst(["pass", "flaky"]) == "flaky"
    assert worst(["infrastructure_error", "regression", "pin_mismatch"]) == "regression"
    assert worst([]) == "pass"


def test_the_change_log_names_what_moved_between_an_agents_builds() -> None:
    at = datetime(2026, 9, 1, tzinfo=UTC)
    first = {"prompts": {"core": "v15"}, "model": "m-1", "tool_schemas": {"quote": "a"}}
    second = {**first, "prompts": {"core": "v16", "rules": "v1"}, "corpus_digest": "sha256:" + "0" * 64}
    third = {**second, "model": "m-2", "niadra": {"compiler": "compiler-1.16"}}
    found = changes(
        [
            ("s", "closing", at, first),
            ("s", "closing", at + timedelta(days=2), third),
            ("s", "closing", at + timedelta(days=1), second),
            ("s", "router", at, {"model": "m-9"}),
        ]
    )
    assert [(c.kind, c.name, c.before, c.after) for c in found] == [
        ("model", None, "m-1", "m-2"),
        ("compiler", None, None, "compiler-1.16"),
        ("prompt", "rules", None, "v1"),
        ("prompt", "core", "v15", "v16"),
        ("data", None, None, "sha256:" + "0" * 64),
    ]
    assert {c.agent for c in found} == {"closing"}
