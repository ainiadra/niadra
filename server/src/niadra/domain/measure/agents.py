"""What an agent's turns claimed, honored and cost over a period (24.3.3, 24.6.9): claim
verdicts by category, the hard constraints sent, verifiable and violated, and the calls, tokens and money per
turn, summed from the turns' daily totals across builds, per source and agent. The daily totals name no
person."""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from uuid import UUID

from niadra.domain.turns.records import DailyTotals

EVIDENCED = frozenset({"matched", "quoted_found", "anchored"})
"""Verdicts that stand on evidence the turn held."""


@dataclass(slots=True)
class ClaimTally:
    source_id: UUID
    agent: str
    turns: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def claims(self) -> int:
        return sum(sum(v.values()) for v in self.by_category.values())

    @property
    def evidenced(self) -> int:
        return sum(n for v in self.by_category.values() for verdict, n in v.items() if verdict in EVIDENCED)


@dataclass(slots=True)
class ConstraintTally:
    source_id: UUID
    agent: str
    turns: int = 0
    sent: int = 0
    checked: int = 0
    violated: int = 0
    unverifiable: int = 0
    relaxed_declared: int = 0
    relaxed_suspected: int = 0
    items_presented: int = 0

    @property
    def verifiable(self) -> int:
        return self.checked - self.unverifiable

    @property
    def violations_per_1000_items(self) -> float | None:
        """The signals' guard: hard constraints violated, and not relaxed, per 1,000 items shown."""
        return round(self.violated * 1000 / self.items_presented, 3) if self.items_presented else None


@dataclass(slots=True)
class CostTally:
    source_id: UUID
    agent: str
    turns: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    cost_micros: int = 0

    def per_turn(self, value: int) -> float | None:
        return round(value / self.turns, 3) if self.turns else None


def costs(rows: Iterable[DailyTotals], sources: Collection[UUID] | None) -> list[CostTally]:
    """By source and agent, for the sources a reader may see."""
    found: dict[tuple[UUID, str], CostTally] = {}
    for row in rows:
        if sources is not None and row.source_id not in sources:
            continue
        t = found.setdefault((row.source_id, row.agent), CostTally(row.source_id, row.agent))
        t.turns += row.turns
        t.model_calls += row.model_calls
        t.tool_calls += row.tool_calls
        t.tokens_in += row.tokens_in
        t.tokens_cached += row.tokens_cached
        t.tokens_out += row.tokens_out
        t.cost_micros += row.cost_micros
    return [found[k] for k in sorted(found) if found[k].turns]


def tallies(
    rows: Iterable[DailyTotals], sources: Collection[UUID] | None
) -> tuple[list[ClaimTally], list[ConstraintTally]]:
    """By source and agent, for the sources a reader may see (all when `sources` is None). An agent with
    no claim or no constraint in the period has no row of that kind."""
    claims: dict[tuple[UUID, str], ClaimTally] = {}
    constraints: dict[tuple[UUID, str], ConstraintTally] = {}
    for row in rows:
        if sources is not None and row.source_id not in sources:
            continue
        key = (row.source_id, row.agent)
        c = claims.setdefault(key, ClaimTally(*key))
        c.turns += row.turns
        for name, count in row.claims.items():
            category, _, verdict = name.partition("|")
            by_verdict = c.by_category.setdefault(category, {})
            by_verdict[verdict] = by_verdict.get(verdict, 0) + count
        k = constraints.setdefault(key, ConstraintTally(*key))
        k.turns += row.turns
        k.sent += row.constraints_sent
        k.checked += row.results_checked
        k.violated += row.violations
        k.unverifiable += row.unverifiable
        k.relaxed_declared += row.relaxed_declared
        k.relaxed_suspected += row.relaxed_suspected
        k.items_presented += row.items_presented
    order = sorted(claims)
    return (
        [claims[k] for k in order if claims[k].by_category],
        [constraints[k] for k in order if constraints[k].sent or constraints[k].items_presented],
    )
