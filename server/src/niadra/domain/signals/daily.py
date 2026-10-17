"""What the nightly pass of subject signals computes from a day of raw interactions (24.3.2, 24.7): pure.

- The daily aggregate, per pseudonym and field value: the effective exposure, the weighted signal and the
  events behind it, derived by the same fold a subject's body is, so the 45-day raw can go and the 13-month
  aggregate still recalibrates. A pseudonym is a keyed hash of the handle, never the handle.
- The segment's priors (`μ` of `lift = (S + α·μ) / (E + α)`): the population's signal over its exposure for
  a value, only where at least `min_people` pseudonyms saw it, so a prior never describes a person. The
  segment `all` is the space's; a space that asks for it also gets one per source (the entry point that
  showed the value), which a read by that source smooths toward first.
- Interleaving: per impression of a list two rankings contributed to (team draft, `team` `a` or `b` on each
  item), which ranking the person's engagement credited; impressions without engagement are ties, and the
  sign test compares the two rankings on the decided ones.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from niadra.domain.signals.derive import Catalog, Entry, fold

PRIOR_DAYS = 90
"""The daily aggregates one night's priors are computed over."""
PRIOR_SEGMENT = "all"
"""The population as one segment; a space that asks for them adds one per source."""


@dataclass(frozen=True, slots=True)
class DailyRow:
    pseudonym: str
    attr: str
    value: str
    exposure: float
    signal: float
    events: float
    segment: str = PRIOR_SEGMENT


@dataclass(frozen=True, slots=True)
class Prior:
    attr: str
    value: str
    prior: float
    exposures: int
    signals: float
    people: int
    segment: str = PRIOR_SEGMENT


def end_of(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(), UTC)


def aggregate(
    by_pseudonym: Mapping[str, Sequence[Entry]], day: date, catalog: Catalog, *, by_source: bool = False
) -> list[DailyRow]:
    """Each pseudonym's day folded as its body would be, at the end of the day; with `by_source`, once per
    source that showed them something, so each source's priors see only what it showed."""
    rows: list[DailyRow] = []
    for pseudonym, entries in sorted(by_pseudonym.items()):
        segments: dict[str, list[Entry]] = defaultdict(list)
        for entry in entries:
            segments[entry.source if by_source and entry.source else PRIOR_SEGMENT].append(entry)
        for segment, part in sorted(segments.items()):
            body = fold(None, part, end_of(day), catalog)
            for v in sorted(body.values.values(), key=lambda v: (v.attr, v.value)):
                rows.append(DailyRow(pseudonym, v.attr, v.value, round(v.e, 6), round(v.s, 6), v.n, segment))
    return rows


def priors(rows: Iterable[DailyRow], min_people: int) -> list[Prior]:
    """Each value's population lift, where enough people saw it: `Σ signal / Σ exposure`, for the whole space
    (`all`) and for each source segment the rows carry."""
    exposure: dict[tuple[str, str, str], float] = defaultdict(float)
    signal: dict[tuple[str, str, str], float] = defaultdict(float)
    people: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        segments = {PRIOR_SEGMENT, row.segment}
        for segment in segments:
            key = (segment, row.attr, row.value)
            exposure[key] += row.exposure
            signal[key] += row.signal
            people[key].add(row.pseudonym)
    return [
        Prior(attr, value, round(signal[key] / exposure[key], 6), round(exposure[key]), round(signal[key], 6),
              len(people[key]), segment)
        for key in sorted(exposure)
        for segment, attr, value in (key,)
        if len(people[key]) >= min_people and exposure[key] > 0
    ]  # fmt: skip


@dataclass(frozen=True, slots=True)
class Interleaved:
    impressions: int = 0
    wins_a: int = 0
    wins_b: int = 0
    ties: int = 0


def interleaved(entries: Iterable[Entry], tools: Mapping[str, str]) -> dict[tuple[str, str], Interleaved]:
    """`(experiment, tool)` to the day's counts. `tools` names the experiment interleaving each tool; an
    interleaved list says its tool in `list_kind`."""
    teams: dict[str, tuple[str, dict[str, str]]] = {}
    credit: dict[str, dict[str, int]] = defaultdict(lambda: {"a": 0, "b": 0})
    ordered = sorted(entries, key=lambda e: (e.at, e.key))
    for entry in ordered:
        data = entry.interaction
        if entry.kind != "presented" or str(data.get("list_kind")) not in tools:
            continue
        items = [i for i in data.get("items", ()) if i.get("team") and "ref" in i]
        by_ref = {str(i["ref"]): str(i["team"]) for i in items}
        if by_ref:
            teams[str(data["exposure_id"])] = (str(data["list_kind"]), by_ref)
    for entry in ordered:
        data = entry.interaction
        exposure = str(data.get("exposure_id") or "")
        if entry.kind != "engaged" or exposure not in teams:
            continue
        team = teams[exposure][1].get(str(data.get("ref")))
        if team is not None:
            credit[exposure][team] += 1
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for exposure, (tool, _) in teams.items():
        got = credit.get(exposure, {"a": 0, "b": 0})
        acc = counts[(tools[tool], tool)]
        acc[0] += 1
        if got["a"] > got["b"]:
            acc[1] += 1
        elif got["b"] > got["a"]:
            acc[2] += 1
        else:
            acc[3] += 1
    return {key: Interleaved(*acc) for key, acc in counts.items()}


def sign_test(wins_a: int, wins_b: int) -> float | None:
    """The two-sided sign test on the decided impressions: how likely a split this uneven is when neither
    ranking is better. Exact up to 1,000 decided impressions, the normal approximation above."""
    n = wins_a + wins_b
    if n == 0:
        return None
    k = min(wins_a, wins_b)
    if n <= 1000:
        tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
        return float(min(1.0, round(2 * tail, 6)))
    z = (abs(wins_a - wins_b) - 1) / math.sqrt(n)
    return min(1.0, round(math.erfc(z / math.sqrt(2)), 6))
