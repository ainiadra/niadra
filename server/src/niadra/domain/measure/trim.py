"""Which item units each source leaves unused, from the measurement (15.8, 19.2).

Every measured delivery an agent saw counts, per unit it carried, one delivery and whether the agent
used an item of it; a trimmed item the agent then needed (it asked about it, stated it or acted on it)
counts as a miss. A unit carried in at least `min_deliveries` deliveries of the window, never used and
never missed, is trimmed for that source and view. The way back is built in: a miss returns the unit at
the next measurement, and a trimmed unit stops being delivered, so it leaves the window and is measured
again once the window has passed.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from niadra.domain.compile.trim import MAX_TRIM_UNITS, trimmable_kind
from niadra.domain.measure.model import ContextUseRow


@dataclass(slots=True)
class UnitCounters:
    deliveries: int = 0
    used: int = 0
    missed: int = 0

    def add(self, other: "UnitCounters", sign: int = 1) -> None:
        self.deliveries += sign * other.deliveries
        self.used += sign * other.used
        self.missed += sign * other.missed

    def negated(self) -> "UnitCounters":
        return UnitCounters(-self.deliveries, -self.used, -self.missed)


@dataclass(frozen=True, slots=True)
class UnitKey:
    day: date
    source_id: UUID
    view: str
    unit: str


@dataclass(frozen=True, slots=True)
class TrimUnit:
    unit: str
    deliveries: int
    """Measured deliveries of the window that carried it, none used."""


@dataclass(frozen=True, slots=True)
class ContextTrim:
    """The units one source leaves unused in one view, as the last measurement derived them."""

    source_id: UUID
    view: str
    units: tuple[TrimUnit, ...]
    derived_at: datetime
    window_days: int
    min_deliveries: int

    @property
    def unit_names(self) -> frozenset[str]:
        return frozenset(u.unit for u in self.units)


def unit_counters(row: ContextUseRow) -> dict[str, UnitCounters]:
    """What one measured row adds to the units it carried. A row not measured, or measured before units
    existed, adds nothing, so re-measuring subtracts exactly what was added."""
    if row.not_measured_reason is not None:
        return {}
    delivered: dict[str, bool] = {}
    missed: set[str] = set()
    for item in row.items:
        unit = item.get("unit")
        if not isinstance(unit, str) or not unit:
            continue
        if item.get("delivered", True):
            delivered[unit] = delivered.get(unit, False) or bool(item.get("used"))
        elif item.get("trimmed"):
            missed.add(unit)
    counters = {unit: UnitCounters(deliveries=1, used=int(used)) for unit, used in delivered.items()}
    for unit in missed:
        counters.setdefault(unit, UnitCounters()).missed = 1
    return counters


def window(today: date, days: int) -> tuple[date, date]:
    return today - timedelta(days=days - 1), today


def totals(rows: Iterable[tuple[str, UnitCounters]]) -> dict[str, UnitCounters]:
    found: dict[str, UnitCounters] = {}
    for unit, counters in rows:
        found.setdefault(unit, UnitCounters()).add(counters)
    return found


def derive_units(counted: Mapping[str, UnitCounters], min_deliveries: int) -> tuple[TrimUnit, ...]:
    """The trimmed units: enough deliveries, no use and no miss. Kinds the measurement cannot see used
    never qualify; past the cap, the most delivered win, so a pack key stays short."""
    found = [
        TrimUnit(unit, c.deliveries)
        for unit, c in counted.items()
        if trimmable_kind(unit) and c.deliveries >= min_deliveries and c.used == 0 and c.missed == 0
    ]
    kept = sorted(found, key=lambda t: (-t.deliveries, t.unit))[:MAX_TRIM_UNITS]
    return tuple(sorted(kept, key=lambda t: t.unit))


def active_units(
    trim: ContextTrim | None, *, enabled: bool, opted_out: bool, critical: bool
) -> frozenset[str]:
    """What a read of that source and view trims: nothing when the space turned trimming off, when the
    source opted out, or when it is critical (critical sources also never join a control group)."""
    if trim is None or not enabled or opted_out or critical:
        return frozenset()
    return trim.unit_names
