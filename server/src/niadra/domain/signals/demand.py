"""Unmet demand: what people asked the tools for that the tools did not return. Pure.

A tool call counts when the producer said how many results it checked (`applied.results_checked` of the turn
record): none is `empty`, fewer than three is `poor`. What was asked is the turn's preferences captured from
the tools' arguments (`source: tool_args`), on fields the type registry declares, never a hidden field or a
sensitive type, each value normalized (text folded, numbers to two significant digits) so that people who
asked the same thing count together. The call rides the interactions' path as a `demand` entry: kept raw,
aggregated each night by pseudonym, and reported by week and combination only where at least the space's
minimum of distinct people asked it, so no row describes a person.
"""

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from niadra.domain.signals.derive import Catalog, Entry

KIND = "demand"
POOR_BELOW = 3
"""A result with fewer items than this is poor; one with none, empty."""


def normalized(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if value == 0 or not math.isfinite(value):
            return 0
        digits = 1 - math.floor(math.log10(abs(value)))
        rounded = round(value, digits)
        return int(rounded) if float(rounded).is_integer() else rounded
    return " ".join(str(value).casefold().split())


def asked_of(preferences: Iterable[Mapping[str, Any]], catalog: Catalog) -> list[dict[str, Any]]:
    """What the tool was asked, as attributes the registry declares, normalized and in a stable order."""
    asked: dict[tuple[str, str], set[Any]] = defaultdict(set)
    for p in preferences:
        attr = str(p.get("attr") or "")
        if p.get("kind") != "preference" or p.get("source") != "tool_args" or not attr:
            continue
        declared = attr.split(".", 1)[0]
        if attr in catalog.hidden or declared in catalog.sensitive_types or declared not in catalog.tracked:
            continue
        asked[(attr, str(p.get("op")))] |= {normalized(v) for v in p.get("values") or ()}
    return [
        {"attr": attr, "op": op, "values": sorted(values, key=lambda v: (str(type(v)), v))}
        for (attr, op), values in sorted(asked.items())
    ]


def demand_entry(tool: str, results: int, asked: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The `demand` entry of one tool call, or None when it returned enough."""
    if results >= POOR_BELOW:
        return None
    return {"kind": KIND, "tool": tool, "results": results, "asked": list(asked)}


@dataclass(frozen=True, slots=True)
class DemandRow:
    """One pseudonym's day of one tool and one combination: its calls and how many came back empty."""

    pseudonym: str
    tool: str
    asked: str
    """The combination as canonical JSON."""
    calls: int
    empty: int


def combination(asked: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(list(asked), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def daily(by_pseudonym: Mapping[str, Sequence[Entry]]) -> list[DemandRow]:
    """The day's demand entries by pseudonym, tool and combination."""
    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for pseudonym, entries in by_pseudonym.items():
        for entry in entries:
            data = entry.interaction
            if data.get("kind") != KIND:
                continue
            acc = counts[(pseudonym, str(data.get("tool")), combination(data.get("asked") or []))]
            acc[0] += 1
            acc[1] += int(data.get("results") or 0) == 0
    return [DemandRow(p, t, a, c[0], c[1]) for (p, t, a), c in sorted(counts.items())]


@dataclass(frozen=True, slots=True)
class Weekly:
    week: date
    """The Monday that starts it."""
    tool: str
    asked: list[dict[str, Any]]
    calls: int
    empty: int
    people: int


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly(days: Iterable[tuple[date, Sequence[DemandRow]]], min_people: int) -> list[Weekly]:
    """The days added up by week, tool and combination; a group fewer than `min_people` distinct pseudonyms
    asked is left out whole. The busiest first within a week, the newest week first."""
    calls: dict[tuple[date, str, str], list[int]] = defaultdict(lambda: [0, 0])
    people: dict[tuple[date, str, str], set[str]] = defaultdict(set)
    for day, rows in days:
        for row in rows:
            key = (week_of(day), row.tool, row.asked)
            calls[key][0] += row.calls
            calls[key][1] += row.empty
            people[key].add(row.pseudonym)
    found = [
        Weekly(week, tool, json.loads(asked), c[0], c[1], len(people[(week, tool, asked)]))
        for (week, tool, asked), c in calls.items()
        if len(people[(week, tool, asked)]) >= min_people
    ]
    return sorted(
        found, key=lambda w: (-w.week.toordinal(), -w.people, -w.calls, w.tool, combination(w.asked))
    )
