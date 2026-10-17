"""P3: exact 0/1 knapsack in 8-token buckets, with a floor and a ceiling per section (15.5).

With 10 to 500 candidates the optimum is cheap, so there is no greedy approximation. Each
section is solved alone (best value for every reachable exact weight under its ceiling), then
sections are combined under the total budget. A floor reserves room for a section: when its
candidates can fill at least the floor, the solution must give it that much. Ties keep the
earlier choice in id order and the smaller total weight, so the result is deterministic.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

BUCKET = 8


@dataclass(frozen=True, slots=True)
class Choice:
    item_id: str
    group: str
    tokens: int
    value: int


@dataclass(frozen=True, slots=True)
class GroupLimit:
    floor: int = 0
    ceiling: int | None = None


def buckets(tokens: int) -> int:
    return math.ceil(tokens / BUCKET)


# weight -> (value, bitmask over the group's members in id order)
_Table = dict[int, tuple[int, int]]


def _group_table(members: Sequence[Choice], capacity: int) -> _Table:
    table: _Table = {0: (0, 0)}
    for index, item in enumerate(members):
        weight = buckets(item.tokens)
        for base in sorted(table, reverse=True):
            total = base + weight
            if total > capacity:
                continue
            value = table[base][0] + item.value
            if total not in table or value > table[total][0]:
                table[total] = (value, table[base][1] | (1 << index))
    return table


def _allowed(table: _Table, limit: GroupLimit, use_floor: bool) -> list[int]:
    if not use_floor or limit.floor <= 0:
        return sorted(table)
    required = min(buckets(limit.floor), max(table))
    return sorted(w for w in table if w >= required)


def select(items: Sequence[Choice], budget: int, limits: Mapping[str, GroupLimit]) -> frozenset[str]:
    capacity = budget // BUCKET
    groups = sorted({item.group for item in items})
    solved = []
    for group in groups:
        members = sorted((i for i in items if i.group == group), key=lambda i: i.item_id)
        limit = limits.get(group, GroupLimit())
        cap = capacity if limit.ceiling is None else min(capacity, limit.ceiling // BUCKET)
        solved.append((members, _group_table(members, cap), limit))
    picks = _combine(solved, capacity, use_floor=True)
    if picks is None:
        # The floors together do not fit the budget; drop them rather than return nothing.
        picks = _combine(solved, capacity, use_floor=False)
    assert picks is not None
    chosen: set[str] = set()
    for (members, _, _), mask in zip(solved, picks, strict=True):
        chosen.update(m.item_id for i, m in enumerate(members) if mask >> i & 1)
    return frozenset(chosen)


def _combine(
    solved: Sequence[tuple[Sequence[Choice], _Table, GroupLimit]], capacity: int, *, use_floor: bool
) -> tuple[int, ...] | None:
    totals: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for _, table, limit in solved:
        weights = _allowed(table, limit, use_floor)
        merged: dict[int, tuple[int, tuple[int, ...]]] = {}
        for base in sorted(totals):
            base_value, base_picks = totals[base]
            for weight in weights:
                total = base + weight
                if total > capacity:
                    break
                value = base_value + table[weight][0]
                if total not in merged or value > merged[total][0]:
                    merged[total] = (value, (*base_picks, table[weight][1]))
        if not merged:
            return None
        totals = merged
    best = max(sorted(totals), key=lambda w: totals[w][0])
    return totals[best][1]
