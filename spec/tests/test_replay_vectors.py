"""The regression statistics of the replay spec, section 8, run against the reference implementation below,
which follows the spec's rules one by one: every field of every case must come out exactly."""

from __future__ import annotations

import json
from math import comb
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "vectors" / "regression-stats.v0.json").read_text())["cases"]
ORDER = ("pass", "flaky", "infrastructure_error", "pin_mismatch", "regression")


def fisher(base_pass: int, base_fail: int, passed: int, failed: int) -> float:
    total, failures, drawn = base_pass + base_fail + passed + failed, base_fail + failed, passed + failed
    if failures == 0 or drawn == 0:
        return 1.0
    tail = sum(
        comb(failures, x) * comb(total - failures, drawn - x) for x in range(failed, min(failures, drawn) + 1)
    )
    return tail / comb(total, drawn)


def rate(passed: int, failed: int) -> float:
    return passed / (passed + failed) if passed + failed else 1.0


def assertion(aid: str, outcomes: list[str], baseline: list[int] | None) -> dict[str, Any]:
    passed, failed = outcomes.count("pass"), outcomes.count("fail")
    base_pass, base_fail = baseline if baseline is not None else (passed + failed, 0)
    drop = round(rate(base_pass, base_fail) - rate(passed, failed), 6)
    p_value = round(fisher(base_pass, base_fail, passed, failed), 6)
    regression = failed >= 2 and drop >= 0.2 and p_value < 0.05
    return {
        "id": aid,
        "passed": passed,
        "failed": failed,
        "not_checked": outcomes.count("not_checked"),
        "baseline_passed": base_pass,
        "baseline_failed": base_fail,
        "drop": drop,
        "p_value": p_value,
        "regression": regression,
        "flaky": not regression and failed >= 1,
    }


def verdict(executions: list[dict[str, Any]], baseline: dict[str, list[int]] | None) -> dict[str, Any]:
    completed = [e for e in executions if e["status"] == "completed"]
    outcomes: dict[str, list[str]] = {}
    for execution in completed:
        for aid, outcome in execution["outcomes"].items():
            outcomes.setdefault(aid, []).append(outcome)
    assertions = [assertion(aid, o, (baseline or {}).get(aid)) for aid, o in sorted(outcomes.items())]
    mismatches = sum(e["status"] == "pin_mismatch" for e in executions)
    if mismatches:
        scenario = "pin_mismatch"
    elif not completed:
        scenario = "infrastructure_error"
    elif any(a["regression"] for a in assertions):
        scenario = "regression"
    elif any(a["flaky"] for a in assertions):
        scenario = "flaky"
    else:
        scenario = "pass"
    return {
        "verdict": scenario,
        "completed": len(completed),
        "infrastructure_errors": sum(e["status"] == "infrastructure_error" for e in executions),
        "pin_mismatches": mismatches,
        "needs_paraphrase": any(a["passed"] and a["failed"] for a in assertions),
        "assertions": assertions,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_regression_stats(case: dict[str, Any]) -> None:
    for execution in case["executions"]:
        assert set(execution) == {"status", "paraphrase", "outcomes"}
        assert execution["status"] in ("completed", "pin_mismatch", "infrastructure_error")
        assert set(execution["outcomes"].values()) <= {"pass", "fail", "not_checked"}
    assert verdict(case["executions"], case["baseline"]) == case["expect"]


def test_the_vectors_reach_every_verdict_and_both_baselines() -> None:
    assert {case["expect"]["verdict"] for case in CASES} == set(ORDER)
    assert any(case["baseline"] is None for case in CASES)
    assert any(case["baseline"] is not None for case in CASES)
    assert any(
        a["regression"] and case["expect"]["verdict"] == "pin_mismatch"
        for case in CASES
        for a in case["expect"]["assertions"]
    )
