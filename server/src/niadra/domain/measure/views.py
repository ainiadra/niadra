"""The view each agent and model learned to use (13, 6.3), from what it did with what it got.

Measurement already knows, per delivery, which items of the pinned skeleton the agent used and which it
needed and did not get (a held-back item it contradicted, a trimmed one it asked for). Per reader, that
is per source, target model and view, it counts every day and per learned section (facts, conversations,
other agents' actions, and the per-turn slots) the deliveries that carried it, those where the agent used
it, and those where the agent missed it. Once a night, beside the retrieval weights (`weights.py`, whose
step, band rounding and control group this reuses), each reader's sections move by the same rules:

- a section carried in at least `min_deliveries` deliveries of the window, never missed, whose use rate
  is clearly below the reader's pooled rate (the upper bound of its Wilson interval under it) goes down
  one step; one missed at least `min_misses` times, or used clearly more than the pool, goes up one;
- a step compounds to at most 10% a week, and a section stays within half and one and a half times the
  view's ceiling; what a smaller ceiling leaves out waits in the pack's reserve for a slot to bring;
- the floors never move: the customer line, withheld items, guards and overdue promises are other
  sections, and a learned ceiling never goes under a section's floor;
- two control groups veto. The space's experiment (the customers held out of memory, `weights.control_
  guard`) stops every move when memory does worse. And a stable fifth of the customers (`arm`) keeps the
  view's own budgets: once a reader's view differs from the default, it moves only while its customers
  with the learned view use what they get at least as often as those with the default view, and are
  asked again no more often. Too few deliveries to tell, and nothing moves.

Every run is kept with its version and its evidence; the read path compiles each reader's skeleton with
its learned ceilings (a variant of its own), the pinned body of a conversation never changes under it,
and slots take the learned slot budget from the next turn. Counters only: no line and no customer.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.compile.model import Variant
from niadra.domain.compile.views import LEARNED_SECTIONS, SLOTS, learned_ceilings
from niadra.domain.measure.aggregate import Rate, wilson
from niadra.domain.measure.model import ContextUseRow
from niadra.domain.measure.weights import Guard, WeightOutcome, WeightRules, step_within
from niadra.domain.serve.holdout import draw

VIEW_RULES = "v1"
"""Bump when a rule below changes; every run records the rules it followed."""
LEARNED_VIEWS = frozenset({"chat", "voice"})
"""The views whose skeleton is learned: the ones that pin a skeleton."""
SECTIONS = (*LEARNED_SECTIONS, SLOTS)
DELIVERY = ""
"""The section name of a reader's per-delivery counters: what the arms are compared on."""
LEARNED_ARM = "learned"
DEFAULT_ARM = "default"
CONTROL_FRACTION = 0.2
"""Customers who keep the view's own budgets, whatever their agents learned."""
_HELD_OUT = frozenset({"control", "shadow"})


def arm(space_id: UUID, profile_id: UUID) -> str:
    """The customer's arm of the learned views: stable per space and profile, on every channel."""
    return DEFAULT_ARM if draw(f"views:{space_id}", profile_id) < CONTROL_FRACTION else LEARNED_ARM


def reader_key(source_id: UUID, target: str, view: str) -> str:
    return f"{source_id}|{target}|{view}"


def target_of(variant_key: str | None) -> str:
    """The target model a pack was compiled for, from its variant key: `provider/model` or `neutral`."""
    if not variant_key:
        return "neutral"
    try:
        return Variant.parse(variant_key).target
    except (ValueError, KeyError):
        return "neutral"


def learned_variant(variant_key: str | None) -> bool:
    """Whether a pack's variant is a skeleton of a learned view."""
    if not variant_key:
        return False
    try:
        variant = Variant.parse(variant_key)
    except (ValueError, KeyError):
        return False
    return variant.slotted and variant.view in LEARNED_VIEWS


@dataclass(slots=True)
class ViewUseCounters:
    """For a section: deliveries that carried it, those where the agent used it, those where it missed
    it. For `DELIVERY`: measured deliveries, those with any use, and the agent's questions and repeats."""

    deliveries: int = 0
    used: int = 0
    missed: int = 0
    questions: int = 0
    repeated: int = 0

    def add(self, other: "ViewUseCounters", sign: int = 1) -> None:
        for name in VIEW_USE_FIELDS:
            setattr(self, name, getattr(self, name) + sign * getattr(other, name))

    def negated(self) -> "ViewUseCounters":
        negated = ViewUseCounters()
        negated.add(self, sign=-1)
        return negated

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in VIEW_USE_FIELDS}


VIEW_USE_FIELDS = tuple(ViewUseCounters.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class ViewUseKey:
    day: date
    source_id: UUID
    target: str
    view: str
    arm: str
    section: str


@dataclass(frozen=True, slots=True)
class ViewUseRow:
    key: ViewUseKey
    counters: ViewUseCounters = field(default_factory=ViewUseCounters)


def view_use_counters(row: ContextUseRow) -> dict[str, ViewUseCounters]:
    """What one measured row adds per learned section and for the delivery; a row not measured, or of a
    view that learns nothing, adds nothing, so measuring again subtracts exactly what was added."""
    if row.not_measured_reason is not None or row.view not in LEARNED_VIEWS:
        return {}
    if row.experiment_group.rpartition(":")[2] in _HELD_OUT:
        return {}  # a held-out customer's agent saw nothing: it says nothing about what it uses
    carried: dict[str, bool] = {}
    missed: set[str] = set()
    for item in row.items:
        kind = str(item.get("kind") or "")
        if kind not in LEARNED_SECTIONS:
            continue
        if item.get("delivered", True):
            carried[kind] = carried.get(kind, False) or bool(item.get("used"))
        elif item.get("selection_miss") or item.get("trimmed"):
            missed.add(kind)
    slotted = [i for turn in row.slots for i in turn.get("items") or ()]
    if slotted:
        carried[SLOTS] = any(bool(i.get("used")) for i in slotted)
    counted = {kind: ViewUseCounters(deliveries=1, used=int(used)) for kind, used in carried.items()}
    for kind in missed:
        counted.setdefault(kind, ViewUseCounters()).missed = 1
    counted[DELIVERY] = ViewUseCounters(
        deliveries=1, used=int(row.used > 0), questions=row.questions, repeated=row.repeated
    )
    return counted


class ArmState(StrEnum):
    SAME = "same"
    """The reader's view is still the default: nothing to compare yet."""
    TOO_SMALL = "too_small"
    HOLDS = "holds"
    WORSE = "worse"


@dataclass(frozen=True, slots=True)
class ArmGuard:
    state: ArmState
    learned_use: Rate | None = None
    default_use: Rate | None = None
    learned_repeats: Rate | None = None
    default_repeats: Rate | None = None

    @property
    def vetoes(self) -> bool:
        return self.state in (ArmState.TOO_SMALL, ArmState.WORSE)

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            **{
                name: _rate(getattr(self, name))
                for name in ("learned_use", "default_use", "learned_repeats", "default_repeats")
            },
        }


@dataclass(frozen=True, slots=True)
class ViewRules:
    min_deliveries: int = 30
    min_misses: int = 3
    steps: WeightRules = field(default_factory=lambda: WeightRules(floor=0.5, ceiling=1.5))
    """The weights' step, band, window and experiment guard, with a band of half to one and a half."""

    @property
    def window_days(self) -> int:
        return self.steps.window_days


VIEWS_RULES = ViewRules()


@dataclass(frozen=True, slots=True)
class ViewBudgets:
    """One nightly run: every reader's learned factors after it (times the view's own budget) and why."""

    run_id: UUID
    day: date
    computed_at: datetime
    version: int
    outcome: WeightOutcome
    factors: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    """`reader_key` -> section -> factor; a reader or a section missing here keeps the view's budget."""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    rules: str = VIEW_RULES

    def factors_for(self, source_id: UUID, target: str, view: str) -> dict[str, float]:
        return dict(self.factors.get(reader_key(source_id, target, view), {}))

    def ceilings_for(self, source_id: UUID, target: str, view: str) -> tuple[tuple[str, int], ...]:
        """The section ceilings the reader's skeleton compiles with; empty while it learned nothing."""
        return learned_ceilings(view, self.factors_for(source_id, target, view))

    def slot_factor(self, source_id: UUID, target: str, view: str) -> float | None:
        return self.factors_for(source_id, target, view).get(SLOTS)

    @property
    def baseline_lift(self) -> float | None:
        value = self.evidence.get("baseline_lift")
        return float(value) if value is not None else None

    def all_ceilings(self) -> set[tuple[tuple[str, int], ...]]:
        """Every set of ceilings some reader compiles with today, and the view's own (empty)."""
        found: set[tuple[tuple[str, int], ...]] = {()}
        for key, factors in self.factors.items():
            view = key.rpartition("|")[2]
            found.add(learned_ceilings(view, factors))
        return found


type ReaderCounters = Mapping[str, Mapping[str, ViewUseCounters]]
"""Arm -> section -> the window's counters, for one reader."""


def window_counters(rows: Iterable[ViewUseRow]) -> dict[str, dict[str, dict[str, ViewUseCounters]]]:
    """Per reader, arm and section, the counters of the window."""
    found: dict[str, dict[str, dict[str, ViewUseCounters]]] = {}
    for row in rows:
        key = reader_key(row.key.source_id, row.key.target, row.key.view)
        found.setdefault(key, {}).setdefault(row.key.arm, {}).setdefault(
            row.key.section, ViewUseCounters()
        ).add(row.counters)
    return found


def arm_guard(counters: ReaderCounters, learned: bool, rules: ViewRules = VIEWS_RULES) -> ArmGuard:
    """The reader's own control group: its customers with the learned view against those with the view's
    own, on deliveries used and repetition, once the learned view differs from the default."""
    if not learned:
        return ArmGuard(ArmState.SAME)
    mine = counters.get(LEARNED_ARM, {}).get(DELIVERY, ViewUseCounters())
    theirs = counters.get(DEFAULT_ARM, {}).get(DELIVERY, ViewUseCounters())
    learned_use, default_use = wilson(mine.used, mine.deliveries), wilson(theirs.used, theirs.deliveries)
    learned_rep, default_rep = (
        wilson(mine.repeated, mine.questions),
        wilson(theirs.repeated, theirs.questions),
    )
    if (
        learned_use is None
        or default_use is None
        or min(mine.deliveries, theirs.deliveries) < rules.min_deliveries
    ):
        return ArmGuard(ArmState.TOO_SMALL, learned_use, default_use, learned_rep, default_rep)
    tolerance = rules.steps.tolerance
    worse_use = learned_use.high < default_use.value - tolerance
    worse_repeats = (
        learned_rep is not None
        and default_rep is not None
        and min(mine.questions, theirs.questions) >= rules.steps.min_questions
        and learned_rep.low > default_rep.value + tolerance
    )
    state = ArmState.WORSE if worse_use or worse_repeats else ArmState.HOLDS
    return ArmGuard(state, learned_use, default_use, learned_rep, default_rep)


def learn_views(
    current: ViewBudgets | None,
    counters: Mapping[str, ReaderCounters],
    guard: Guard,
    *,
    run_id: UUID,
    day: date,
    now: datetime,
    rules: ViewRules = VIEWS_RULES,
) -> ViewBudgets:
    """One nightly run. Same inputs, same factors: the steps are fixed and readers go in key order."""
    factors = {key: dict(f) for key, f in (current.factors if current else {}).items()}
    version = current.version if current else 0
    baseline = current.baseline_lift if current else None
    evidence: dict[str, Any] = {"window_days": rules.window_days, "guard": guard.to_json(), "readers": {}}
    total = sum(c.deliveries for arms in counters.values() for s in arms.values() for c in s.values())

    def run(outcome: WeightOutcome, moved: bool = False) -> ViewBudgets:
        lift = guard.lift if moved and guard.lift is not None else baseline
        evidence["baseline_lift"] = round(lift, 6) if lift is not None else None
        return ViewBudgets(run_id, day, now, version + int(moved), outcome, factors, evidence, VIEW_RULES)

    if total == 0:
        return run(WeightOutcome.NO_VERDICTS)
    if guard.vetoes:
        return run(WeightOutcome.VETOED)
    moved = vetoed = False
    for key, arms in sorted(counters.items()):
        view = key.rpartition("|")[2]
        if view not in LEARNED_VIEWS:
            continue
        learned = factors.get(key, {})
        own = arm_guard(arms, any(f != 1.0 for f in learned.values()), rules)
        sections = _pooled(arms)
        entry: dict[str, Any] = {
            "arms": own.to_json(),
            "sections": {k: c.as_dict() for k, c in sections.items()},
        }
        evidence["readers"][key] = entry
        if own.vetoes:
            vetoed = True
            continue
        for section, (before, after) in _moves(learned, sections, rules).items():
            factors.setdefault(key, {})[section] = after
            entry["sections"][section] |= {"from": round(before, 6), "to": round(after, 6)}
            moved = True
    if moved:
        return run(WeightOutcome.APPLIED, moved=True)
    return run(WeightOutcome.VETOED if vetoed else WeightOutcome.UNCHANGED)


def _pooled(arms: ReaderCounters) -> dict[str, ViewUseCounters]:
    """A reader's section counters over both arms: what an agent uses does not depend on the arm."""
    found: dict[str, ViewUseCounters] = {}
    for sections in arms.values():
        for name, c in sections.items():
            if name in SECTIONS:
                found.setdefault(name, ViewUseCounters()).add(c)
    return dict(sorted(found.items()))


def _moves(
    learned: Mapping[str, float], sections: Mapping[str, ViewUseCounters], rules: ViewRules
) -> dict[str, tuple[float, float]]:
    judged = {name: c for name, c in sections.items() if c.deliveries >= rules.min_deliveries}
    carried = sum(c.deliveries for c in judged.values())
    pooled = sum(c.used for c in judged.values()) / carried if carried else 0.0
    moves = {}
    for name, c in judged.items():
        use = wilson(c.used, c.deliveries)
        assert use is not None
        before = learned.get(name, 1.0)
        if c.missed >= rules.min_misses or (len(judged) >= 2 and use.low > pooled):
            factor = rules.steps.up
        elif c.missed == 0 and len(judged) >= 2 and use.high < pooled:
            factor = rules.steps.down
        else:
            continue
        after = step_within(before, factor, rules.steps.floor, rules.steps.ceiling)
        if after != before:
            moves[name] = (before, after)
    return moves


def _rate(rate: Rate | None) -> dict[str, float | int] | None:
    return None if rate is None else {"value": round(rate.value, 6), "n": rate.n}
