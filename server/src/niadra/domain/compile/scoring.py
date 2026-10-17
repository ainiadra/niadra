"""P2: utility = type x recency x importance x confirmation (5.10). The customer's turn never scores the
pinned body: it selects the per-turn slots (11, 3.3).

Scores are integers so the knapsack is exact and platform-independent. Recency is measured
from the snapshot's `as_of`, never from the wall clock, so the same memory scores the same.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.compile.model import Candidate, ItemKind

SCALE = 1_000_000
# Below this the marginal item is not worth its tokens: stop before filling the budget (15.5, rule 2).
MIN_VALUE = 20_000
HALF_LIFE_DAYS = 30.0
DUE_SOON = timedelta(days=3)

TYPE_WEIGHT = {
    # A directive about the client said by someone who works with them: as much as a promise.
    ItemKind.INSTRUCTION: 1.0,
    ItemKind.OPEN_ITEM: 1.0,
    ItemKind.ATTR: 0.9,
    ItemKind.ACTION: 0.8,
    ItemKind.TRAIT: 0.7,
    ItemKind.FACT: 0.6,
    ItemKind.OBJECT: 0.6,
    ItemKind.EPISODE: 0.5,
    ItemKind.SUMMARY: 0.4,
}


@dataclass(frozen=True, slots=True)
class Focus:
    as_of: datetime | None
    object_types: Collection[str] = ()
    object_id: UUID | None = None


def score(candidate: Candidate, focus: Focus) -> int:
    value = TYPE_WEIGHT[candidate.kind] * candidate.weight * _recency(candidate, focus.as_of)
    if (
        candidate.due_at is not None
        and focus.as_of is not None
        and candidate.due_at - focus.as_of <= DUE_SOON
    ):
        value *= 2.0
    if candidate.object_type and candidate.object_type in focus.object_types:
        value *= 2.0
    if focus.object_id is not None and candidate.object_id == focus.object_id:
        value *= 3.0
    return round(value * SCALE)


def _recency(candidate: Candidate, as_of: datetime | None) -> float:
    # Durable items do not decay: their rank must not drift with time, or B1 would churn (15.2).
    if candidate.stable or as_of is None:
        return 1.0
    age_days = max((as_of - candidate.at).total_seconds() / 86400, 0.0)
    return max(0.05, float(0.5 ** (age_days / HALF_LIFE_DAYS)))
