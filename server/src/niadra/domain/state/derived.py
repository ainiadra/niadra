"""Derived fields of a composite object (24.2.7): `all`, `any`, `count`, `min` and `max` over its related
objects (a look's pieces, a kit's items), computed when the object is read and never stored.

Pure. Each related object the aggregate counts comes in as a `Piece`: whether the field's condition holds for
it (`yes`, `no`, or not known) and, for `min` and `max`, the value compared. A piece whose data is not known
leaves the result not known, unless the answer is already decided: one `no` decides `all`, one `yes` decides
`any` (L25: not observed is never false).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from niadra.domain.state.logic import Logic

Aggregate = Literal["min", "max", "count", "all", "any"]


@dataclass(frozen=True, slots=True)
class Piece:
    holds: Logic
    """Whether the condition holds for it: `yes` without one, `unobserved` when its data is not known."""
    value: Any = None
    """For `min` and `max`: the compared field's value when it is known."""


@dataclass(frozen=True, slots=True)
class Derived:
    logic: Logic
    value: Any
    over: int
    """The related objects counted."""
    unknown: int
    """How many of them were not known."""


def aggregate(agg: Aggregate, pieces: Sequence[Piece]) -> Derived:
    """`count` counts the pieces the condition holds for; `all` and `any` say whether it holds for every one
    or for some; `min` and `max` compare the field among the pieces it holds for, and are absent (`no`) when
    it holds for none."""
    over = len(pieces)
    unknown = sum(1 for p in pieces if not p.holds.known)
    held = [p for p in pieces if p.holds is Logic.YES]
    fails = any(p.holds is Logic.NO for p in pieces)
    if agg == "all":
        if fails:
            return Derived(Logic.NO, False, over, unknown)
        return _known(True, over, unknown) if over and not unknown else _unknown(over, unknown)
    if agg == "any":
        if held:
            return Derived(Logic.YES, True, over, unknown)
        return Derived(Logic.NO, False, over, unknown) if not unknown else _unknown(over, unknown)
    if unknown:
        return _unknown(over, unknown)
    if agg == "count":
        return _known(len(held), over, unknown)
    values = [p.value for p in held]
    if any(v is None for v in values):
        return _unknown(over, sum(1 for v in values if v is None))
    if not values:
        return Derived(Logic.NO, None, over, unknown)
    try:
        chosen = min(values) if agg == "min" else max(values)
    except TypeError:  # values of different kinds do not compare
        return _unknown(over, over)
    return _known(chosen, over, unknown)


def _known(value: Any, over: int, unknown: int) -> Derived:
    return Derived(Logic.YES, value, over, unknown)


def _unknown(over: int, unknown: int) -> Derived:
    return Derived(Logic.UNOBSERVED, None, over, unknown)
