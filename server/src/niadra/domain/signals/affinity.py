"""Affinity by field value, without a model (24.3.2; round 5 of the retail annex, section A).

For each (subject or beneficiary, field, value), two decayed accumulators: the effective exposure `E` (each
exposure weighted by the chance its position was seen, `1 / log2(pos + 1)`) and the weighted signal `S`
(engagement, feedback, a stated preference). Then:

- `lift = (S + α·μ) / (E + α)`, with `α = 5` and `μ` the segment's prior for the value, 0 without one: a
  value that is simply shown a lot does not become a preference;
- a session counts for at most 30% of a value's signal;
- a preference comes out only with `n_eff ≥ 3` events of signal;
- the implicit negative (8 or more of exposure in the first three positions and no positive signal) is at
  most -0.5, and never more than soft;
- decay halves every `half_life` days, by the field's attribute family (color 90, style and brand 180,
  price 60, size 365, and 90 for any other).

The size family never becomes a soft preference: its strongest value becomes an inferred attribute, which
applies only when the person asks for their size.
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from niadra.domain.signals.body import TRACKED_SESSIONS, Value

ALPHA = 5.0
SESSION_SHARE = 0.3
MIN_EVIDENCE = 3.0
IMPLICIT_EXPOSURE = 8.0
IMPLICIT_STEP = -0.2
IMPLICIT_FLOOR = -0.5
MIN_LIFT = 0.01
"""Below this distance from the prior a value is neither preferred nor avoided."""
PER_ATTRIBUTE = 3
"""Soft entries per field and polarity: the strongest only."""
SIZE = "size"
HALF_LIFE_DAYS = {
    "color": 90.0,
    "style": 180.0,
    "brand": 180.0,
    "price": 60.0,
    "price_band": 60.0,
    "size": 365.0,
}
DEFAULT_HALF_LIFE_DAYS = 90.0

Prior = Callable[[str, str], float]


def no_prior(attr: str, value: str) -> float:  # noqa: ARG001
    return 0.0


def half_life(family: str) -> float:
    return HALF_LIFE_DAYS.get(family, DEFAULT_HALF_LIFE_DAYS)


def decay(age_days: float, family: str) -> float:
    """`0.5 ^ (age / half_life)`; what is in the future does not grow."""
    return 1.0 if age_days <= 0 else math.pow(0.5, age_days / half_life(family))


def age_days(then: datetime, now: datetime) -> float:
    return (now - then).total_seconds() / 86_400


def position_weight(pos: int) -> float:
    """The chance position `pos` (from 1) was seen."""
    return 1.0 / math.log2(pos + 1)


def scale(v: Value, factor: float) -> None:
    """Decays the accumulators; `n` counts events, so the evidence rule is about how many there were."""
    v.e *= factor
    v.e_top *= factor
    v.s *= factor
    v.sessions = [(k, c * factor) for k, c in v.sessions]


def add_signal(v: Value, session: str, amount: float, events: float = 1.0) -> None:
    """`amount` of signal from `session`, kept apart among the largest contributions to the value."""
    v.s += amount
    v.n += events
    sessions = dict(v.sessions)
    sessions[session] = sessions.get(session, 0.0) + amount
    v.sessions = sorted(sessions.items(), key=lambda kc: abs(kc[1]), reverse=True)[:TRACKED_SESSIONS]


def capped_signal(v: Value) -> float:
    """`S` with no session above 30% of it. The sessions not kept apart are each smaller than the third one
    kept, so with four or more none of them can pass the cap."""
    rest = v.s - sum(c for _, c in v.sessions)
    total = sum(abs(c) for _, c in v.sessions) + abs(rest)
    cap = SESSION_SHARE * total
    return rest + sum(math.copysign(min(abs(c), cap), c) for _, c in v.sessions)


def implicit(v: Value) -> float:
    """The negative of a value shown often at the top and never engaged with."""
    if v.e_top < IMPLICIT_EXPOSURE or v.s > 0:
        return 0.0
    return max(IMPLICIT_FLOOR, IMPLICIT_STEP * math.floor(v.e_top / IMPLICIT_EXPOSURE))


def lift(v: Value, prior: float) -> float:
    return (capped_signal(v) + implicit(v) + ALPHA * prior) / (v.e + ALPHA)


@dataclass(frozen=True, slots=True)
class Affinity:
    attr: str
    value: str
    polarity: str
    """`prefer` or `avoid`."""
    lift: float
    weight: float
    confidence: float
    evidence: float
    implicit: bool = False


def affinity(v: Value, prior: float) -> Affinity | None:
    """The soft entry a value gives, if its evidence is enough."""
    value_lift = lift(v, prior)
    delta = value_lift - prior
    if v.n >= MIN_EVIDENCE and abs(delta) >= MIN_LIFT:
        confidence = v.n / (v.n + ALPHA)
        polarity = "prefer" if delta > 0 else "avoid"
        return Affinity(v.attr, v.value, polarity, value_lift, min(1.0, abs(delta)), confidence, v.n)
    negative = implicit(v)
    if negative < 0:
        # Only exposure speaks for it: as soft and as weak as the rule allows.
        return Affinity(
            v.attr, v.value, "avoid", value_lift, abs(negative), abs(negative), v.n, implicit=True
        )
    return None


def soft_entries(values: Iterable[Value], family: Callable[[str], str], prior: Prior) -> list[Affinity]:
    """The strongest preferred and avoided values of each field, the size family aside."""
    by_attr: dict[tuple[str, str], list[Affinity]] = {}
    for v in values:
        if family(v.attr) == SIZE:
            continue
        found = affinity(v, prior(v.attr, v.value))
        if found is not None:
            by_attr.setdefault((found.attr, found.polarity), []).append(found)
    out: list[Affinity] = []
    for group in by_attr.values():
        out += sorted(group, key=lambda a: (-a.weight, a.value))[:PER_ATTRIBUTE]
    return sorted(out, key=lambda a: (-a.weight, a.attr, a.value))


def sizes(values: Iterable[Value], family: Callable[[str], str], prior: Prior) -> list[Affinity]:
    """Per field of the size family, the value the person's own signal points to, if any."""
    best: dict[str, Affinity] = {}
    for v in values:
        if family(v.attr) != SIZE:
            continue
        found = affinity(v, prior(v.attr, v.value))
        if found is None or found.polarity != "prefer" or found.implicit:
            continue
        if v.attr not in best or (found.lift, found.value) > (best[v.attr].lift, best[v.attr].value):
            best[v.attr] = found
    return [best[attr] for attr in sorted(best)]
