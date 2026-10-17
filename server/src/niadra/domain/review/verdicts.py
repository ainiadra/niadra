"""Verdicts and the agreement between reviewers and the system (22.2).

A verdict never changes memory by itself: a wrong fact leaves through `POST /v1/feedback` and a
wrong trait through `retract`, each with its own lineage.
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from uuid import UUID


class TargetKind(StrEnum):
    FACT = "fact"
    SIGNAL = "signal"
    TRAIT = "trait"
    OPEN_ITEM = "open_item"
    """A pending item or a promise the conversation left (a promise is an item the company owns)."""
    CONTRADICTION = "contradiction"
    """A pair of facts in conflict, named `<fact id>:<fact id>:<origin>`: `holds` when the two really
    conflict. The pairs the nightly similarity band found count apart, as `contradiction:band`."""


ALLOWED = {
    TargetKind.FACT: frozenset({"correct", "wrong", "missing"}),
    TargetKind.OPEN_ITEM: frozenset({"correct", "wrong", "missing"}),
    TargetKind.SIGNAL: frozenset({"holds", "does_not_hold"}),
    TargetKind.TRAIT: frozenset({"holds", "does_not_hold"}),
    TargetKind.CONTRADICTION: frozenset({"holds", "does_not_hold"}),
}
AGREES_WITH_SYSTEM = frozenset({"correct", "holds"})
SIGNALS = ("used", "repeated", "contradicted", "selection_miss")
"""The measurement's signals a reviewer judges; a signal's target id ends with its name."""
CONTRADICTION_ORIGINS = ("model", "rule", "band")
"""Who found a contradiction; a contradiction's target id ends with it."""


def contradiction_target(first: UUID, second: UUID, origin: str) -> str:
    return f"{first}:{second}:{origin}"


def visible_to(purpose: str | None, category: str, category_purposes: Mapping[str, frozenset[str]]) -> bool:
    """The reviewer never sees more than the agent of the sample's purpose would (22.2)."""
    allowed = category_purposes.get(category)
    return not allowed or (purpose is not None and purpose in allowed)


@dataclass(frozen=True, slots=True)
class Agreement:
    agreed: int
    judged: int
    reviewer_pairs: int
    reviewer_pairs_agreeing: int


def is_valid(target: TargetKind, verdict: str) -> bool:
    return verdict in ALLOWED[target]


def kinds_of(verdict: Mapping[str, object]) -> tuple[str, ...]:
    """The rows a verdict counts in: its target kind and, for a signal, `signal:<name>` too, so each
    number of the measurement has the agreement of its own signal (19.2, 22.2); for a contradiction,
    `contradiction:<origin>`, so the similarity band's pairs are judged apart from the model's."""
    kind = str(verdict["target"])
    name = str(verdict["target_id"]).rsplit(":", 1)[-1]
    if kind == TargetKind.SIGNAL:
        return (kind, f"signal:{name}") if name in SIGNALS else (kind,)
    if kind == TargetKind.CONTRADICTION:
        return (kind, f"contradiction:{name}") if name in CONTRADICTION_ORIGINS else (kind,)
    return (kind,)


def scoped(
    verdicts: Iterable[Mapping[str, object]],
    visible: frozenset[UUID] | None,
    source_id: UUID | None = None,
) -> Iterator[Mapping[str, object]]:
    """Verdicts on samples of the sources a caller sees: vendor A never reads the agreement on vendor
    B's agent. A verdict recorded before verdicts named their source counts only for whoever sees
    every source, and never under a source filter."""
    for verdict in verdicts:
        named = verdict.get("source_id")
        if named is None:
            if visible is None and source_id is None:
                yield verdict
            continue
        source = UUID(str(named))
        if (visible is None or source in visible) and (source_id is None or source == source_id):
            yield verdict


def agreement(verdicts: Iterable[Mapping[str, object]]) -> dict[str, Agreement]:
    """Per target kind, per signal and `all`: how often reviewers confirm the system, and how often two
    reviewers agree.

    A `missing` fact is a disagreement: the system failed to extract what the reviewer saw.
    """
    judged: dict[str, list[bool]] = defaultdict(list)
    by_target: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for v in verdicts:
        verdict = str(v["verdict"])
        agrees = verdict in AGREES_WITH_SYSTEM
        for kind in kinds_of(v):
            judged[kind].append(agrees)
            by_target[(kind, str(v["target_id"]))][str(v["reviewer_id"])] = verdict
        judged["all"].append(agrees)
    pairs: dict[str, list[bool]] = defaultdict(list)
    for (kind, _), reviewers in by_target.items():
        for a, b in combinations(sorted(reviewers.values()), 2):
            pairs[kind].append(a == b)
            if ":" not in kind:
                pairs["all"].append(a == b)
    return {
        kind: Agreement(sum(results), len(results), len(pairs[kind]), sum(pairs[kind]))
        for kind, results in judged.items()
    }


@dataclass(frozen=True, slots=True)
class CaseRun:
    """One verdict run as a test against what the system says now."""

    now: str
    """`marked` or `not_marked` for a signal, `active` or `gone` for a fact or an open item, and
    `not_measured` or `unknown` when there is nothing to compare with."""
    passes: bool | None


def signal_case(verdict: Mapping[str, object], marks: Mapping[tuple[str, str, str], bool]) -> CaseRun:
    """A verdict on a signal against the current measurement's mark of the same item in the same pack."""
    manifest, _, rest = str(verdict["target_id"]).partition(":")
    item, _, signal = rest.rpartition(":")
    mark = marks.get((manifest, item, signal))
    if mark is None:
        return CaseRun("not_measured", None)
    return CaseRun("marked" if mark else "not_marked", (str(verdict["verdict"]) == "holds") == mark)


def item_case(verdict: Mapping[str, object], active: bool | None) -> CaseRun:
    """A verdict on a fact or an open item against memory now: a correct one still stands, a wrong one left.
    `missing` says what extraction failed to take, and runs against nothing."""
    if active is None or str(verdict["verdict"]) == "missing":
        return CaseRun("unknown", None)
    return CaseRun("active" if active else "gone", active == (str(verdict["verdict"]) == "correct"))
