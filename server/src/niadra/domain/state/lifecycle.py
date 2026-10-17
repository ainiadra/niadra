"""The lifecycle of an object (24.3.8): the state it is in, the latches it reached and its outcome.

The state comes from the observations of its `state` field: a system's code is translated to the type's
canonical state by that source's vocabulary, the newest in the world wins, and at the same instant the
vocabulary with the lower precedence. Niadra never enforces a transition (L24): what a source reports is
the state. An outcome is final, provisional, or expired without one once its deadline passed; the last is
an outcome of its own, never "unknown".
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from niadra.domain.state.observed import Observation

EXPIRED_WITHOUT_OUTCOME = "expired_without_outcome"
"""The outcome of an object whose deadline passed with no final state; the clock writes it as its state."""
UNRANKED = 1000
"""Where a code no vocabulary translates stands against the translated ones at the same instant."""


@dataclass(frozen=True, slots=True)
class Vocabulary:
    map: Mapping[str, str]
    precedence: int


@dataclass(frozen=True, slots=True)
class Stage:
    """The lifecycle state and when it became true in the world."""

    state: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Outcome:
    state: str
    final: bool
    at: datetime | None


def stage_of(
    observations: Iterable[tuple[str, Observation]],
    states: Collection[str],
    vocabularies: Mapping[str, Vocabulary],
) -> Stage | None:
    """`observations` are (source name, observation) of the `state` field; `states` the type's declared ones,
    when it declares any."""
    best: tuple[tuple[datetime, int, datetime], Stage] | None = None
    for source, observation in observations:
        value = observation.value
        if not isinstance(value, str):
            continue
        rank = UNRANKED
        vocabulary = vocabularies.get(source)
        if vocabulary is not None and value in vocabulary.map:
            value, rank = vocabulary.map[value], vocabulary.precedence
        if value != EXPIRED_WITHOUT_OUTCOME and states and value not in states:
            continue
        key = (observation.valid_at, -rank, observation.known_at)
        if best is None or key > best[0]:
            best = (key, Stage(value, observation.valid_at))
    return best[1] if best else None


def latched(
    latches: Mapping[str, datetime], stage: Stage | None, latching: Collection[str]
) -> dict[str, datetime]:
    """The latches after `stage`: a latching state keeps the first time it was reached, even when the object
    leaves it ("was paid" and "is paid" are two readings)."""
    kept = dict(latches)
    if stage is not None and stage.state in latching:
        first = kept.get(stage.state)
        kept[stage.state] = stage.at if first is None or stage.at < first else first
    return kept


def outcome_of(
    stage: Stage | None,
    provisional: Collection[str],
    final: Collection[str],
    deadline: datetime | None,
    now: datetime,
) -> Outcome | None:
    """A final state is the outcome; past the deadline without one, the outcome is expired without outcome;
    a provisional state until then."""
    if stage is not None and (stage.state in final or stage.state == EXPIRED_WITHOUT_OUTCOME):
        return Outcome(stage.state, True, stage.at)
    if deadline is not None and now >= deadline:
        return Outcome(EXPIRED_WITHOUT_OUTCOME, True, deadline)
    if stage is not None and stage.state in provisional:
        return Outcome(stage.state, False, stage.at)
    return None
