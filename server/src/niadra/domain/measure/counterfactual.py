"""The tool-level counterfactual: pure.

The company's CI runs each recorded tool call live three times at the same moment, twice with the element
(the base) and once without it (the variant), and reports per case the overlap of the first `k` positions of
the base with the variant and of the base with itself: the second is the tool's own noise, the floor under
which a change says nothing. The server adds the cases up. The answer is whether the element changes what the
tool returns; never whether the result got better (a hard constraint changes a list by construction), nor how
the model reacts to either list: `limits` says so in every report.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from niadra.domain.signals.daily import sign_test

FEW_CASES = 30
"""Under this many completed cases a report says its number is fragile."""
NOISY_TOOL = 0.9
"""A tool whose two identical calls overlap less than this, on average, drowns small changes in noise."""
ALWAYS = ("not_quality", "model_reaction_not_measured")
HARD_ELEMENTS = frozenset({"constraints", "hard"})


def weight(position: int) -> float:
    """The weight of a depth, the exposure weight of the interaction spec: the first positions count most."""
    return 1 / math.log2(position + 1)


def overlap_at_k(a: Sequence[str], b: Sequence[str], k: int) -> float:
    """The average overlap of the first `k` positions, weighted by depth: at each depth `d`, up to the longer
    list, the share of the two prefixes of length `d` they hold in common. Two equal lists overlap 1 however
    short, two empty ones too; a repeated item counts once, where it first appears."""
    first, second = _unique(a), _unique(b)
    depth = min(k, max(len(first), len(second)))
    if depth == 0:
        return 1.0
    total = weighted = 0.0
    for d in range(1, depth + 1):
        common = len(set(first[:d]) & set(second[:d]))
        total += weight(d)
        weighted += weight(d) * common / d
    return weighted / total


def _unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


@dataclass(frozen=True, slots=True)
class Case:
    status: str
    k: int | None = None
    overlap: float | None = None
    noise: float | None = None
    dry_run: bool = False
    engaged: tuple[tuple[int | None, int | None], ...] = ()
    """`(base, variant)` positions of each engaged item."""


@dataclass(frozen=True, slots=True)
class Engagement:
    items: int = 0
    shown: int = 0
    kept: int = 0
    lost: int = 0
    gained: int = 0
    mean_shift: float | None = None


@dataclass(frozen=True, slots=True)
class Summary:
    cases: int
    completed: int
    skipped: dict[str, int]
    overlap: float | None
    noise_floor: float | None
    effect: float | None
    below_noise: int
    above_noise: int
    ties: int
    p_value: float | None
    engaged: Engagement
    limits: list[str] = field(default_factory=list)


def summarize(cases: Sequence[Case], element: str) -> Summary:
    done = [c for c in cases if c.status == "completed"]
    skipped: dict[str, int] = {}
    for case in cases:
        if case.status != "completed":
            skipped[case.status] = skipped.get(case.status, 0) + 1
    overlaps = [c.overlap for c in done if c.overlap is not None]
    noises = [c.noise for c in done if c.noise is not None]
    overlap = _mean(overlaps)
    noise = _mean(noises)
    below = sum(1 for c in done if c.overlap is not None and c.noise is not None and c.overlap < c.noise)
    above = sum(1 for c in done if c.overlap is not None and c.noise is not None and c.overlap > c.noise)
    limits = list(ALWAYS)
    if element in HARD_ELEMENTS:
        limits.append("trivial_for_hard")
    if len(done) < FEW_CASES:
        limits.append("few_cases")
    if noise is not None and noise < NOISY_TOOL:
        limits.append("noisy_tool")
    if skipped:
        limits.append("cases_skipped")
    if any(c.dry_run for c in done):
        limits.append("dry_run")
    return Summary(
        cases=len(cases),
        completed=len(done),
        skipped=skipped,
        overlap=overlap,
        noise_floor=noise,
        effect=round(noise - overlap, 6) if overlap is not None and noise is not None else None,
        below_noise=below,
        above_noise=above,
        ties=len(done) - below - above,
        p_value=sign_test(below, above),
        engaged=engagement(done),
        limits=limits,
    )


def engagement(cases: Sequence[Case]) -> Engagement:
    items = shown = kept = lost = gained = 0
    shifts: list[int] = []
    for case in cases:
        k = case.k or 0
        for base, variant in case.engaged:
            items += 1
            in_base = base is not None and base <= k
            in_variant = variant is not None and variant <= k
            shown += in_base
            kept += in_base and in_variant
            lost += in_base and not in_variant
            gained += in_variant and not in_base
            if base is not None and variant is not None:
                shifts.append(variant - base)
    return Engagement(items, shown, kept, lost, gained, _mean(shifts))


def _mean(values: Sequence[float]) -> float | None:
    return round(math.fsum(values) / len(values), 6) if values else None
