"""Per-space retrieval weights, learned at night from what agents did with their slots (11, 3.4).

The fusion of the per-turn slots weighs each retrieval channel (`domain.serve.slots.DEFAULT_WEIGHTS`).
Once a day, every space compares, per view, each channel's use rate over the last seven
complete days with the rate of all channels together. A channel whose slots the agents use clearly more
(the lower bound of its Wilson interval above the pooled rate) goes up one step; one used clearly less,
or contradicted clearly more, goes down one step. Nothing else moves it:

- a step is at most 10% a week, compounded over the nightly passes, and a weight never leaves
  [half, double] its default;
- a channel with fewer than `min_slots` measured slots in the window, or a view with fewer than two
  such channels, keeps its weight; a window without any slot verdict changes nothing;
- a channel whose default is 0 (an experiment switched off, as `linked`) is never judged nor moved, and
  its old verdicts never weigh on the others;
- the control group vetoes: when an experiment holds customers out of memory, and the customers with
  memory are asked for what memory had more often than the held-out ones, or the gap between the two
  groups narrowed since the weights last moved, nothing moves.

No gradient and no access counts: the signal is the agent's verdict on what it got, and the control
group guards the outcome.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.measure.aggregate import Rate, wilson
from niadra.domain.measure.model import DailyRow
from niadra.domain.measure.slots import SlotCounters, SlotDailyRow
from niadra.domain.serve.slots import DEFAULT_WEIGHTS

WEIGHT_RULES = "w1"
"""Bump when a rule below changes; every run records the rules it followed."""
MEMORY_GROUP = "memory"
CONTROL_GROUP = "control"


class WeightOutcome(StrEnum):
    APPLIED = "applied"
    """At least one weight moved; the version went up."""
    UNCHANGED = "unchanged"
    """Verdicts, but no channel clearly apart from the rest."""
    NO_VERDICTS = "no_verdicts"
    VETOED = "vetoed"
    """The control group says the customers with memory did worse, or the gap narrowed."""


class GuardState(StrEnum):
    NO_EXPERIMENT = "no_experiment"
    """No customer held out of memory in the window: nothing to compare with, the band still holds."""
    TOO_SMALL = "too_small"
    """An experiment runs, but either group asked too few questions to tell."""
    HOLDS = "holds"
    WORSE = "worse"
    NARROWED = "narrowed"


@dataclass(frozen=True, slots=True)
class WeightRules:
    window_days: int = 7
    min_slots: int = 30
    weekly_step: float = 0.10
    floor: float = 0.5
    """Times the channel's default weight."""
    ceiling: float = 2.0
    min_questions: int = 30
    """Questions each experiment group must have asked in the window for the guard to judge."""
    tolerance: float = 0.02
    """How far the gap between the groups may narrow, in repetition rate, before it vetoes."""

    @property
    def up(self) -> float:
        # Seven nightly steps compound to at most `weekly_step` (11, 3.4).
        return float((1 + self.weekly_step) ** (1 / 7))

    @property
    def down(self) -> float:
        return float((1 - self.weekly_step) ** (1 / 7))

    def band(self, channel: str, defaults: Mapping[str, float] = DEFAULT_WEIGHTS) -> tuple[float, float]:
        default = defaults[channel]
        return default * self.floor, default * self.ceiling


RULES = WeightRules()


@dataclass(frozen=True, slots=True)
class Guard:
    state: GuardState
    lift: float | None = None
    """Control repetition rate minus memory repetition rate: how much memory spares the customer."""
    memory: Rate | None = None
    control: Rate | None = None

    @property
    def vetoes(self) -> bool:
        return self.state in (GuardState.TOO_SMALL, GuardState.WORSE, GuardState.NARROWED)

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "lift": _round(self.lift),
            "memory": _rate(self.memory),
            "control": _rate(self.control),
        }


@dataclass(frozen=True, slots=True)
class RetrievalWeights:
    """One nightly run: the weights in force after it (every view it has learned for), and why."""

    run_id: UUID
    day: date
    computed_at: datetime
    version: int
    outcome: WeightOutcome
    weights: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    """View -> channel -> weight; a view or a channel missing here fuses with the default."""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    """Per view and channel, the counters of the window and each move; the guard and its baseline."""
    rules: str = WEIGHT_RULES

    def for_view(self, view: str, defaults: Mapping[str, float] = DEFAULT_WEIGHTS) -> dict[str, float]:
        # A weight learned while an experiment ran never turns it back on once its default is 0 again.
        learned = {k: v for k, v in self.weights.get(view, {}).items() if defaults.get(k, 0.0) > 0}
        return {**defaults, **learned}

    @property
    def baseline_lift(self) -> float | None:
        """The gap between the groups when the weights last moved; the guard compares with it."""
        value = self.evidence.get("baseline_lift")
        return float(value) if value is not None else None


def weights_for(
    current: RetrievalWeights | None, view: str, defaults: Mapping[str, float] = DEFAULT_WEIGHTS
) -> dict[str, float] | None:
    """What the read path hands the fusion: None, the defaults, until the space has learned something."""
    if current is None or not current.weights.get(view):
        return None
    return current.for_view(view, defaults)


def window_counters(rows: Iterable[SlotDailyRow]) -> dict[str, dict[str, SlotCounters]]:
    """Per view and retrieval channel, the slot counters of the window."""
    found: dict[str, dict[str, SlotCounters]] = {}
    for row in rows:
        found.setdefault(row.key.view, {}).setdefault(row.key.slot_channel, SlotCounters()).add(row.counters)
    return found


def control_guard(rows: Iterable[DailyRow], baseline: float | None, rules: WeightRules = RULES) -> Guard:
    """The control group's say (19.7): repetition, the rate held-out deliveries also measure, in the
    customers with memory against those without, over the window."""
    asked = {MEMORY_GROUP: 0, CONTROL_GROUP: 0}
    repeated = dict(asked)
    for row in rows:
        group = row.key.experiment_group.rpartition(":")[2] if ":" in row.key.experiment_group else ""
        if group in asked:
            asked[group] += row.counters.questions
            repeated[group] += row.counters.repeated
    if not asked[CONTROL_GROUP] and not asked[MEMORY_GROUP]:
        return Guard(GuardState.NO_EXPERIMENT)
    memory = wilson(repeated[MEMORY_GROUP], asked[MEMORY_GROUP])
    control = wilson(repeated[CONTROL_GROUP], asked[CONTROL_GROUP])
    if memory is None or control is None or min(asked.values()) < rules.min_questions:
        return Guard(GuardState.TOO_SMALL, memory=memory, control=control)
    lift = control.value - memory.value
    if lift < 0:
        state = GuardState.WORSE
    elif baseline is not None and lift < baseline - rules.tolerance:
        state = GuardState.NARROWED
    else:
        state = GuardState.HOLDS
    return Guard(state, lift, memory, control)


def learn(
    current: RetrievalWeights | None,
    counters: Mapping[str, Mapping[str, SlotCounters]],
    guard: Guard,
    *,
    run_id: UUID,
    day: date,
    now: datetime,
    rules: WeightRules = RULES,
    defaults: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> RetrievalWeights:
    """One nightly run. Same inputs, same weights: the steps are fixed and ties do not exist. `defaults`
    are the fusion's default weights."""
    weights = {view: dict(w) for view, w in (current.weights if current else {}).items()}
    version = current.version if current else 0
    baseline = current.baseline_lift if current else None
    evidence: dict[str, Any] = {"window_days": rules.window_days, "guard": guard.to_json(), "views": {}}
    total = sum(c.slots for channels in counters.values() for c in channels.values())

    def run(outcome: WeightOutcome, moved: bool = False) -> RetrievalWeights:
        lift = guard.lift if moved and guard.lift is not None else baseline
        evidence["baseline_lift"] = _round(lift)
        return RetrievalWeights(
            run_id, day, now, version + int(moved), outcome, weights, evidence, WEIGHT_RULES
        )

    if total == 0:
        return run(WeightOutcome.NO_VERDICTS)
    for view, channels in sorted(counters.items()):
        evidence["views"][view] = {
            name: c.as_dict() for name, c in sorted(channels.items()) if defaults.get(name, 0.0) > 0
        }
    if guard.vetoes:
        return run(WeightOutcome.VETOED)
    moved = False
    for view, channels in sorted(counters.items()):
        moves = _moves(weights.get(view, {}), channels, rules, defaults)
        for name, (before, after) in moves.items():
            weights.setdefault(view, {})[name] = after
            evidence["views"][view][name]["from"] = _round(before)
            evidence["views"][view][name]["to"] = _round(after)
            moved = True
    if not moved:
        return run(WeightOutcome.UNCHANGED)
    return run(WeightOutcome.APPLIED, moved=True)


def _moves(
    learned: Mapping[str, float],
    channels: Mapping[str, SlotCounters],
    rules: WeightRules,
    defaults: Mapping[str, float],
) -> dict[str, tuple[float, float]]:
    judged = {
        name: c
        for name, c in sorted(channels.items())
        if defaults.get(name, 0.0) > 0 and c.slots >= rules.min_slots
    }
    if len(judged) < 2:
        return {}
    slots = sum(c.slots for c in judged.values())
    pooled_use = sum(c.used for c in judged.values()) / slots
    pooled_contradiction = sum(c.contradicted for c in judged.values()) / slots
    moves = {}
    for name, c in judged.items():
        use, contradiction = wilson(c.used, c.slots), wilson(c.contradicted, c.slots)
        assert use is not None
        assert contradiction is not None
        before = learned.get(name, defaults[name])
        if contradiction.low > pooled_contradiction or use.high < pooled_use:
            factor = rules.down
        elif use.low > pooled_use:
            factor = rules.up
        else:
            continue
        low, high = rules.band(name, defaults)
        after = step_within(before, factor, low, high)
        if after != before:
            moves[name] = (before, after)
    return moves


def step_within(before: float, factor: float, low: float, high: float) -> float:
    """One nightly step of a learned number, kept in [low, high]. Rounded toward the number it came from,
    so seven rounded steps never pass the weekly bound; the learned view budgets step the same way."""
    stepped = before * factor
    stepped = math.floor(stepped * 1e6) / 1e6 if factor > 1 else math.ceil(stepped * 1e6) / 1e6
    return min(high, max(low, stepped))


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _rate(rate: Rate | None) -> dict[str, float | int] | None:
    if rate is None:
        return None
    return {"value": _round(rate.value) or 0.0, "n": rate.n}
