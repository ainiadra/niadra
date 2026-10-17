"""P3 is an exact knapsack: on small inputs it matches exhaustive search (15.5)."""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.compile.knapsack import BUCKET, Choice, GroupLimit, buckets, select

GROUPS = ("a", "b", "c")

choices = st.lists(
    st.builds(
        Choice,
        item_id=st.text("xyz", min_size=1, max_size=3),
        group=st.sampled_from(GROUPS),
        tokens=st.integers(1, 120),
        value=st.integers(0, 1000),
    ),
    max_size=9,
    unique_by=lambda c: c.item_id,
)
limits = st.dictionaries(
    st.sampled_from(GROUPS),
    st.builds(GroupLimit, floor=st.integers(0, 80), ceiling=st.one_of(st.none(), st.integers(0, 200))),
)


def _weight(items: list[Choice]) -> int:
    return sum(buckets(i.tokens) for i in items)


def _feasible(
    subset: list[Choice], items: list[Choice], budget: int, limits: dict[str, GroupLimit], use_floor: bool
) -> bool:
    capacity = budget // BUCKET
    if _weight(subset) > capacity:
        return False
    for group in {i.group for i in items}:
        limit = limits.get(group, GroupLimit())
        cap = capacity if limit.ceiling is None else min(capacity, limit.ceiling // BUCKET)
        chosen = _weight([i for i in subset if i.group == group])
        if chosen > cap:
            return False
        if use_floor and limit.floor > 0:
            members = [i for i in items if i.group == group]
            reachable = [
                _weight(list(c))
                for r in range(len(members) + 1)
                for c in itertools.combinations(members, r)
                if _weight(list(c)) <= cap
            ]
            if chosen < min(buckets(limit.floor), max(reachable)):
                return False
    return True


def _best(items: list[Choice], budget: int, limits: dict[str, GroupLimit]) -> int:
    for use_floor in (True, False):
        values = [
            sum(i.value for i in subset)
            for r in range(len(items) + 1)
            for subset in map(list, itertools.combinations(items, r))
            if _feasible(subset, items, budget, limits, use_floor)
        ]
        if values:
            return max(values)
    raise AssertionError("the empty selection is always feasible without floors")


@settings(max_examples=300, deadline=None)
@given(items=choices, budget=st.integers(0, 400), limits=limits)
def test_matches_exhaustive_search(items: list[Choice], budget: int, limits: dict[str, GroupLimit]) -> None:
    picked = [i for i in items if i.item_id in select(items, budget, limits)]
    assert _feasible(picked, items, budget, limits, use_floor=True) or _feasible(
        picked, items, budget, limits, use_floor=False
    )
    assert sum(i.value for i in picked) == _best(items, budget, limits)


@given(items=choices, budget=st.integers(0, 400), limits=limits)
def test_is_deterministic_whatever_the_input_order(
    items: list[Choice], budget: int, limits: dict[str, GroupLimit]
) -> None:
    assert select(items, budget, limits) == select(list(reversed(items)), budget, limits)


def test_a_floor_reserves_room_for_a_low_value_section() -> None:
    items = [Choice("big", "episodes", 64, 900), Choice("promise", "pending", 32, 10)]
    assert select(items, 64, {}) == {"big"}
    assert select(items, 64, {"pending": GroupLimit(floor=32)}) == {"promise"}


def test_a_ceiling_caps_a_section() -> None:
    items = [Choice("a", "facts", 40, 5), Choice("b", "facts", 40, 5), Choice("c", "other", 40, 1)]
    assert select(items, 200, {"facts": GroupLimit(ceiling=48)}) in ({"a", "c"}, {"b", "c"})
