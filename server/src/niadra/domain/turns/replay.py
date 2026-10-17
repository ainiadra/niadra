"""Replay (24.3.5, 24.6.5): what makes a recorded turn runnable again, the pins a run must share with the
recording, the assertions suggested by rule, and the statistics that turn N executions into a verdict.

Niadra orchestrates and the company's CI executes: the runner runs the agent inside the company's boundary
and reports each execution's assertion outcomes. The verdict stays here, per assertion: a one-sided Fisher
exact test against the baseline, a regression only with a significant drop above `MIN_DROP` and at least
`MIN_FAILURES` failures, flaky results listed apart without blocking, and infrastructure errors counted apart.
"""

import itertools
import math
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PINS = ("prompts", "corpus_digest", "model", "assembler", "tool_schemas")
ALPHA = 0.05
MIN_DROP = 0.2
MIN_FAILURES = 2
P_DECIMALS = 6
# Worst last: the run's verdict is the worst of its scenarios'.
VERDICT_ORDER = ("pass", "flaky", "infrastructure_error", "pin_mismatch", "regression")


# What makes a turn replayable


def blockers(frame: Mapping[str, Any], required_pins: Collection[str]) -> list[tuple[str, str]]:
    """Why a recorded turn cannot be run again, as (reason, value); empty when it can (Turn Record 5.2, 7)."""
    found: list[tuple[str, str]] = []
    if frame.get("content_mode") == "hash_only":
        found.append(("content_mode", "hash_only"))
    fidelity = frame.get("fidelity", "gold")
    if fidelity != "gold":
        found.append(("fidelity", fidelity))
    completeness = frame.get("completeness", "complete")
    if completeness != "complete":
        found.append(("completeness", completeness))
    pins = pins_of(frame)
    found += [("missing_pin", pin) for pin in PINS if pin in required_pins and _absent(pins.get(pin))]
    return found


def replayable(frame: Mapping[str, Any], required_pins: Collection[str]) -> bool:
    return not blockers(frame, required_pins)


def pins_of(frame: Mapping[str, Any]) -> dict[str, Any]:
    return dict((frame.get("build") or {}).get("pins") or {})


def pin_mismatches(
    recorded: Mapping[str, Any], running: Mapping[str, Any], required: Collection[str], vary: Collection[str]
) -> list[tuple[str, Any, Any]]:
    """The pins a run does not share with the recording, as (name, recorded, running): every required pin,
    and every other pin both builds carry, except those the run varies on purpose."""
    found: list[tuple[str, Any, Any]] = []
    for pin in PINS:
        if pin in vary:
            continue
        before, after = recorded.get(pin), running.get(pin)
        if pin not in required and (_absent(before) or _absent(after)):
            continue
        if _absent(before) or _absent(after) or before != after:
            found.append((pin, before, after))
    return found


def _absent(value: Any) -> bool:
    return value in (None, {}, [], "")


# Assertions: what each kind takes, and the ones suggested by rule

_TEXT = (str,)
_INT = (int,)
_NUMBER = (int, float)
_BOOL = (bool,)
_TEXTS = (list,)
# kind -> (argument -> accepted types, the required arguments, whether at least one argument is needed)
ARGUMENTS: dict[str, tuple[dict[str, tuple[type, ...]], frozenset[str], bool]] = {
    "tool_called": ({"tool": _TEXT, "min": _INT}, frozenset({"tool"}), False),
    "tool_not_called": ({"tool": _TEXT}, frozenset({"tool"}), False),
    "hard_respected": ({}, frozenset(), False),
    "no_denial_with_results": ({"lang": _TEXT}, frozenset(), False),
    "claims_traced": ({}, frozenset(), False),
    "claims_match_state": ({}, frozenset(), False),
    "no_promise_without_action": ({"lang": _TEXT}, frozenset(), False),
    "expected_in_topk": ({"ref": _TEXT, "k": _INT, "list_id": _TEXT}, frozenset({"ref", "k"}), False),
    "handoff_when": ({"expected": _BOOL}, frozenset(), False),
    "effect_once": ({"key": _TEXT}, frozenset(), False),
    "budget": (
        {
            "max_tool_calls": _INT,
            "max_model_calls": _INT,
            "max_tokens": _INT,
            "max_cost_usd": _NUMBER,
            "max_latency_ms": _INT,
        },
        frozenset(),
        True,
    ),
    "lexicon": ({"must_include": _TEXTS, "must_not_include": _TEXTS, "lang": _TEXT}, frozenset(), True),
    "tools_offered_match": ({"tools": _TEXTS}, frozenset({"tools"}), False),
}
LANGS = frozenset({"pt", "en", "es"})


def argument_errors(kind: str, args: Mapping[str, Any]) -> list[str]:
    """What is wrong with an assertion's arguments, by name; empty when they are what the kind takes."""
    accepted, required, needs_one = ARGUMENTS[kind]
    errors = [f"`{name}` is not an argument of {kind}" for name in sorted(set(args) - set(accepted))]
    errors += [f"{kind} needs `{name}`" for name in sorted(required - set(args))]
    if needs_one and not set(args) & set(accepted) - {"lang"}:
        errors.append(f"{kind} needs at least one limit")
    for name, value in args.items():
        types = accepted.get(name)
        if types is not None:
            errors += _argument_value(name, value, types)
    return errors


def _argument_value(name: str, value: Any, types: tuple[type, ...]) -> list[str]:
    # `given` keeps its `Any`: an isinstance check against a tuple of types would narrow it to `object`.
    given: Any = value
    if (isinstance(value, bool) and bool not in types) or not isinstance(value, types):
        return [f"`{name}` has the wrong type"]
    if types is _TEXTS and not all(isinstance(v, str) and v for v in given):
        return [f"`{name}` must list non-empty strings"]
    if types in (_INT, _NUMBER) and given < (1 if name in ("min", "k") else 0):
        return [f"`{name}` is out of range"]
    if name == "lang" and given not in LANGS:
        return ["`lang` is one of pt, en, es"]
    return []


@dataclass(frozen=True, slots=True)
class Suggested:
    id: str
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None


def suggest(frames: Sequence[Mapping[str, Any]], description: str | None = None) -> list[Suggested]:
    """The assertions a scenario starts with, by rule: from what each recorded turn did (the tools it called
    with success, the constraints it applied, its claims, effects and handoff, how many calls it made), and
    from the words of a bug report. The rules only read the frames; the recorded values never matter."""
    out: list[Suggested] = []
    for index, frame in enumerate(frames, start=1):
        turn_id = frame["turn_id"]
        prefix = f"t{index}."
        calls = [c for c in frame.get("calls") or () if c.get("kind") == "tool"]
        tools = sorted({c["name"] for c in calls if c.get("name") and c.get("status", "ok") == "ok"})
        out += [
            Suggested(prefix + "tool_called." + _slug(tool), "tool_called", {"tool": tool}, turn_id)
            for tool in tools
        ]
        if any(c.get("applied") for c in calls):
            out.append(Suggested(prefix + "hard_respected", "hard_respected", {}, turn_id))
        if any(c.get("observations") for c in calls):
            out.append(Suggested(prefix + "no_denial_with_results", "no_denial_with_results", {}, turn_id))
        if frame.get("claims"):
            out.append(Suggested(prefix + "claims_traced", "claims_traced", {}, turn_id))
            out.append(Suggested(prefix + "claims_match_state", "claims_match_state", {}, turn_id))
        if frame.get("effects"):
            out.append(Suggested(prefix + "effect_once", "effect_once", {}, turn_id))
        if (frame.get("output") or {}).get("handoff_id"):
            out.append(Suggested(prefix + "handoff_when", "handoff_when", {"expected": True}, turn_id))
        if calls:
            out.append(Suggested(prefix + "budget", "budget", {"max_tool_calls": 2 * len(calls)}, turn_id))
    have = {(s.kind, s.turn_id) for s in out}
    for kind, words in REPORT_WORDS.items():
        if description and words.search(description) and (kind, None) not in have:
            args = {"expected": True} if kind == "handoff_when" else {}
            out.append(Suggested("report." + kind, kind, args))
    return _unique(out)


# The words of a bug report that point at an assertion, in the three languages of the claim contract.
REPORT_WORDS: dict[str, re.Pattern[str]] = {
    "no_denial_with_results": re.compile(
        r"\b(negou|nega|não tem|nao tem|indispon[ií]vel|esgotad|denied|not available|unavailable|"
        r"out of stock|neg[oó]|no hay|no disponible|agotad)",
        re.IGNORECASE,
    ),
    "claims_match_state": re.compile(
        r"\b(pre[çc]o|valor|prazo|price|deadline|amount|precio|plazo|monto)", re.IGNORECASE
    ),
    "no_promise_without_action": re.compile(
        r"\b(prometeu|prometid|promised|promise|prometi[oó]|promesa)", re.IGNORECASE
    ),
    "effect_once": re.compile(r"\b(duplicad|duas vezes|twice|duplicate|dos veces)", re.IGNORECASE),
    "handoff_when": re.compile(
        r"\b(humano|atendente|transfer|human|handoff|operador|persona real)", re.IGNORECASE
    ),
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")[:40] or "tool"


def _unique(items: Iterable[Suggested]) -> list[Suggested]:
    seen: Counter[str] = Counter()
    out: list[Suggested] = []
    for item in items:
        seen[item.id] += 1
        if seen[item.id] > 1:
            item = Suggested(f"{item.id}.{seen[item.id]}"[:64], item.kind, item.args, item.turn_id)
        out.append(item)
    return out


# Regression statistics


@dataclass(frozen=True, slots=True)
class Outcome:
    """One assertion's outcome in one execution: `pass`, `fail` or `not_checked`."""

    assertion_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class Execution:
    """One execution of one case: `completed`, `pin_mismatch` or `infrastructure_error`."""

    status: str
    outcomes: tuple[Outcome, ...] = ()
    paraphrase: bool = False


@dataclass(frozen=True, slots=True)
class AssertionVerdict:
    id: str
    passed: int
    failed: int
    not_checked: int
    baseline_passed: int
    baseline_failed: int
    drop: float
    p_value: float
    regression: bool
    flaky: bool


@dataclass(frozen=True, slots=True)
class ScenarioStats:
    verdict: str
    completed: int
    infrastructure_errors: int
    pin_mismatches: int
    needs_paraphrase: bool
    assertions: tuple[AssertionVerdict, ...]


def fisher_one_sided(base_pass: int, base_fail: int, pass_: int, fail: int) -> float:
    """P(at least `fail` failures in the run) under the hypergeometric law of the 2x2 table: the chance of a
    drop at least this large if the run failed no more often than the baseline."""
    total, failures, drawn = base_pass + base_fail + pass_ + fail, base_fail + fail, pass_ + fail
    if failures == 0 or drawn == 0:
        return 1.0
    ways = math.comb(total, drawn)
    tail = sum(
        math.comb(failures, x) * math.comb(total - failures, drawn - x)
        for x in range(fail, min(failures, drawn) + 1)
    )
    return tail / ways


def assertion_verdict(
    assertion_id: str, outcomes: Sequence[str], baseline: tuple[int, int] | None
) -> AssertionVerdict:
    """`baseline` is (passed, failed) of the same assertion in the baseline run; None compares against the
    recording, which passed it as many times as the run checked it."""
    passed, failed = outcomes.count("pass"), outcomes.count("fail")
    base_pass, base_fail = baseline if baseline is not None else (passed + failed, 0)
    rate = passed / (passed + failed) if passed + failed else 1.0
    base_rate = base_pass / (base_pass + base_fail) if base_pass + base_fail else 1.0
    drop = round(base_rate - rate, P_DECIMALS)
    p_value = round(fisher_one_sided(base_pass, base_fail, passed, failed), P_DECIMALS)
    regression = failed >= MIN_FAILURES and drop >= MIN_DROP and p_value < ALPHA
    return AssertionVerdict(
        id=assertion_id,
        passed=passed,
        failed=failed,
        not_checked=outcomes.count("not_checked"),
        baseline_passed=base_pass,
        baseline_failed=base_fail,
        drop=drop,
        p_value=p_value,
        regression=regression,
        flaky=not regression and failed >= 1,
    )


def scenario_stats(
    executions: Sequence[Execution], baseline: Mapping[str, tuple[int, int]] | None
) -> ScenarioStats:
    """The scenario's verdict over its executions. A pin mismatch invalidates the run; with no completed
    execution it is an infrastructure error; otherwise a regression in any assertion is the scenario's,
    then a flaky one. Only completed executions count in an assertion's outcomes."""
    completed = [e for e in executions if e.status == "completed"]
    by_assertion: dict[str, list[str]] = {}
    for execution in completed:
        for outcome in execution.outcomes:
            by_assertion.setdefault(outcome.assertion_id, []).append(outcome.outcome)
    verdicts = tuple(
        assertion_verdict(aid, outcomes, (baseline or {}).get(aid))
        for aid, outcomes in sorted(by_assertion.items())
    )
    mismatches = sum(1 for e in executions if e.status == "pin_mismatch")
    errors = sum(1 for e in executions if e.status == "infrastructure_error")
    if mismatches:
        verdict = "pin_mismatch"
    elif not completed:
        verdict = "infrastructure_error"
    elif any(v.regression for v in verdicts):
        verdict = "regression"
    elif any(v.flaky for v in verdicts):
        verdict = "flaky"
    else:
        verdict = "pass"
    return ScenarioStats(
        verdict=verdict,
        completed=len(completed),
        infrastructure_errors=errors,
        pin_mismatches=mismatches,
        needs_paraphrase=any(v.passed and v.failed for v in verdicts),
        assertions=verdicts,
    )


def worst(verdicts: Iterable[str]) -> str:
    return max(verdicts, key=VERDICT_ORDER.index, default="pass")


# The change log


@dataclass(frozen=True, slots=True)
class PinChange:
    kind: str
    at: datetime
    agent: str | None
    """None for a change of the space's configuration, which every agent of the space reads."""
    name: str | None
    before: str | None
    after: str | None


def changes(builds: Iterable[tuple[str, str, datetime, Mapping[str, Any]]]) -> list[PinChange]:
    """What changed between consecutive builds of each agent, newest first. `builds` are (source, agent,
    first seen, pins); two sources' agents of one name are apart, as their builds are."""
    by_agent: dict[tuple[str, str], list[tuple[datetime, Mapping[str, Any]]]] = {}
    for source, agent, at, pins in builds:
        by_agent.setdefault((source, agent), []).append((at, pins))
    out: list[PinChange] = []
    for (_, agent), seen in by_agent.items():
        seen.sort(key=lambda item: item[0])
        for (_, before), (at, after) in itertools.pairwise(seen):
            out += _diff(agent, at, before, after)
    return newest_first(out)


@dataclass(frozen=True, slots=True)
class ConfigSeen:
    """A section of the space's configuration as one applied snapshot left it: its name and digest."""

    section: str
    version: int
    at: datetime
    digest: bytes


def config_changes(history: Iterable[ConfigSeen]) -> list[PinChange]:
    """The sections that changed between two applied snapshots, as the `config` kind of the change log. A
    section's first recorded digest is where its history starts, not a change."""
    by_section: dict[str, list[ConfigSeen]] = {}
    for seen in history:
        by_section.setdefault(seen.section, []).append(seen)
    out = [
        PinChange("config", after.at, None, section, _digest(before.digest), _digest(after.digest))
        for section, seen in by_section.items()
        for before, after in itertools.pairwise(sorted(seen, key=lambda s: s.version))
    ]
    return newest_first(out)


def newest_first(found: Iterable[PinChange]) -> list[PinChange]:
    return sorted(found, key=lambda c: (c.at, c.agent or "", c.kind, c.name or ""), reverse=True)


def _digest(value: bytes) -> str:
    return f"sha256:{value.hex()}"


def _diff(agent: str, at: datetime, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[PinChange]:
    found: list[PinChange] = []
    for kind, pin, name in (
        ("model", "model", None),
        ("code", "assembler", "assembler"),
        ("data", "corpus_digest", None),
    ):
        if before.get(pin) != after.get(pin):
            found.append(PinChange(kind, at, agent, name, before.get(pin), after.get(pin)))
    for kind, pin in (("prompt", "prompts"), ("code", "tool_schemas")):
        old, new = before.get(pin) or {}, after.get(pin) or {}
        found += [
            PinChange(kind, at, agent, key, old.get(key), new.get(key))
            for key in sorted(old.keys() | new.keys())
            if old.get(key) != new.get(key)
        ]
    old_compiler = (before.get("niadra") or {}).get("compiler")
    new_compiler = (after.get("niadra") or {}).get("compiler")
    if old_compiler != new_compiler:
        found.append(PinChange("compiler", at, agent, None, old_compiler, new_compiler))
    return found
