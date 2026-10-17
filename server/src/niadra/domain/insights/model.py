"""What the analytical surface can be asked, and the rules that shape every answer (5.21).

Aggregates run over structured columns only (intent, outcome, channel, status...); text never takes
part in a query, and is decrypted in the application only for the rows a caller may read.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import InvalidInput


class Entity(StrEnum):
    EPISODES = "episodes"
    OPEN_ITEMS = "open_items"
    TRAITS = "traits"
    ACTIONS = "actions"
    OBJECTS = "objects"
    PROFILES = "profiles"


class Metric(StrEnum):
    COUNT = "count"
    PROFILES = "profiles"
    AVG_SENTIMENT = "avg_sentiment"


@dataclass(frozen=True, slots=True)
class EntitySpec:
    dimensions: frozenset[str]
    metrics: frozenset[Metric]
    description: str
    per_profile: bool = True
    """Whether rows belong to a profile; the minimum group size then counts profiles, not rows."""


CATALOG: dict[Entity, EntitySpec] = {
    Entity.EPISODES: EntitySpec(
        frozenset(
            {
                "intent",
                "category",
                "normalized_category",
                "outcome",
                "channel",
                "source_id",
                "initiated_by",
                "sentiment_band",
            }
        ),
        frozenset(Metric),
        "One per closed conversation or task, current version only; time is when it ended.",
    ),
    Entity.OPEN_ITEMS: EntitySpec(
        frozenset({"owner", "status", "object_type", "expected_operation", "age_band"}),
        frozenset({Metric.COUNT, Metric.PROFILES}),
        "Pending items and promises; time is when they were opened; `age_band` is measured at `until`.",
    ),
    Entity.TRAITS: EntitySpec(
        frozenset({"name", "layer", "rule_version"}),
        frozenset({Metric.COUNT, Metric.PROFILES}),
        "Behavior signals with evidence, not retracted; time is the last evidence.",
    ),
    Entity.ACTIONS: EntitySpec(
        frozenset({"operation", "status", "source_id", "purpose"}),
        frozenset({Metric.COUNT}),
        "Actions agents or people declared in a system; time is when they happened.",
        per_profile=False,
    ),
    Entity.OBJECTS: EntitySpec(
        frozenset({"type", "namespace", "source_id"}),
        frozenset({Metric.COUNT, Metric.PROFILES}),
        "Business objects (orders, tickets, invoices); time is the state's `as_of`.",
    ),
    Entity.PROFILES: EntitySpec(
        frozenset({"kind"}),
        frozenset({Metric.COUNT}),
        "Subjects: people, accounts and partners; time is when they were first seen.",
        per_profile=False,
    ),
}

SENTIMENT_NEGATIVE_BELOW = -0.2
SENTIMENT_POSITIVE_ABOVE = 0.2
AGE_BANDS = ((2, "0-2d"), (7, "3-7d"), (30, "8-30d"))
OLDEST_AGE_BAND = "31d+"


@dataclass(frozen=True, slots=True)
class InsightsLimits:
    min_group_size: int = 10
    max_group_by: int = 3
    max_filter_values: int = 50
    max_rows: int = 200
    max_profiles_page: int = 200
    max_sample: int = 50
    max_hits: int = 20
    max_window_days: int = 400
    semantic_scan: int = 2000
    """Semantic search is an exact scan over the space's newest episodes; bigger spaces need the
    per-space vector index of 5.21, which is not built yet."""
    max_calls_per_day: int = 2000
    max_profiles_per_day: int = 5000
    receipt_timeout_s: float = 2.0


@dataclass(frozen=True, slots=True)
class Selection:
    """Filters and a time window, validated against the catalog."""

    entity: Entity
    filters: Mapping[str, tuple[str, ...]]
    since: datetime
    until: datetime


@dataclass(frozen=True, slots=True)
class AggregateQuery:
    selection: Selection
    metrics: tuple[Metric, ...]
    group_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AggregateRow:
    group: tuple[str | None, ...]
    count: int
    profiles: int | None = None
    avg_sentiment: float | None = None


@dataclass(frozen=True, slots=True)
class AggregateResult:
    group_by: tuple[str, ...]
    rows: list[AggregateRow]
    suppressed_groups: int
    min_group_size: int


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    pseudonym: str
    matches: int


@dataclass(frozen=True, slots=True)
class ProfileRef:
    profile_id: UUID
    pseudonym: str
    kind: str
    handle_ids: tuple[UUID, ...]
    """The confirmed members only: the analytical reads never see what came through an unconfirmed link
    (5.3)."""
    hidden_handle_ids: tuple[UUID, ...] = ()
    """The members joined by a claim nobody confirmed yet, left out of `handle_ids`."""


@dataclass(frozen=True, slots=True)
class Sealed:
    """A ciphertext and where it lives; the additional data binds it to that place (5.14.1)."""

    ciphertext: bytes
    dek_version: int
    table: str
    row: str
    column: str

    @property
    def aad(self) -> bytes:
        return f"{self.table}:{self.row}:{self.column}".encode()


@dataclass(frozen=True, slots=True)
class EpisodeText:
    episode_id: UUID
    pseudonym: str
    channel: str
    intent: str
    category: str
    outcome: str
    ended_at: datetime
    summary: Sealed
    sentiment: float | None = None


@dataclass(frozen=True, slots=True)
class RollupRow:
    dimension: str
    bucket: str
    value: int


@dataclass(frozen=True, slots=True)
class Usage:
    """What one call touched, for the volume limits and the receipt."""

    profiles: int
    items: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)


def selection(
    entity: str,
    filters: Mapping[str, Sequence[str]],
    since: datetime,
    until: datetime,
    limits: InsightsLimits,
) -> Selection:
    try:
        kind = Entity(entity)
    except ValueError:
        raise InvalidInput("unknown entity") from None
    spec = CATALOG[kind]
    unknown = set(filters) - spec.dimensions
    if unknown:
        raise InvalidInput("unknown filter: " + ", ".join(sorted(unknown)))
    if any(not values or len(values) > limits.max_filter_values for values in filters.values()):
        raise InvalidInput(f"each filter takes 1 to {limits.max_filter_values} values")
    if since.tzinfo is None or until.tzinfo is None or not since < until:
        raise InvalidInput("the window needs timezone-aware `since` < `until`")
    if until - since > timedelta(days=limits.max_window_days):
        raise InvalidInput(f"the window is limited to {limits.max_window_days} days")
    return Selection(kind, {k: tuple(v) for k, v in sorted(filters.items())}, since, until)


def aggregate_query(
    base: Selection, metrics: Sequence[str], group_by: Sequence[str], limits: InsightsLimits
) -> AggregateQuery:
    spec = CATALOG[base.entity]
    try:
        chosen = tuple(dict.fromkeys(Metric(m) for m in metrics or ("count",)))
    except ValueError:
        raise InvalidInput("unknown metric") from None
    if not set(chosen) <= spec.metrics:
        raise InvalidInput("a metric is not available for this entity")
    if len(group_by) > limits.max_group_by or not set(group_by) <= spec.dimensions:
        raise InvalidInput(f"group by up to {limits.max_group_by} dimensions of the entity")
    return AggregateQuery(base, chosen, tuple(dict.fromkeys(group_by)))


def suppress_small_groups(
    query: AggregateQuery, rows: Sequence[AggregateRow], limits: InsightsLimits
) -> AggregateResult:
    """Groups smaller than the minimum are dropped, and only their number is reported (5.21)."""
    per_profile = CATALOG[query.selection.entity].per_profile
    kept = [
        r
        for r in rows
        if (r.profiles if per_profile and r.profiles is not None else r.count) >= limits.min_group_size
    ]
    kept.sort(key=lambda r: (-r.count, [g or "" for g in r.group]))
    return AggregateResult(
        query.group_by, kept[: limits.max_rows], len(rows) - len(kept), limits.min_group_size
    )


def match_reason(base: Selection, matches: int) -> str:
    conditions = ", ".join(f"{k} in ({'|'.join(v)})" for k, v in base.filters.items()) or "any"
    return (
        f"{matches} {base.entity.value} with {conditions} between "
        f"{base.since.date().isoformat()} and {base.until.date().isoformat()}"
    )


def sentiment_band(sentiment: float | None) -> str:
    if sentiment is None:
        return "unknown"
    if sentiment < SENTIMENT_NEGATIVE_BELOW:
        return "negative"
    if sentiment > SENTIMENT_POSITIVE_ABOVE:
        return "positive"
    return "neutral"


def age_band(opened_at: datetime, at: datetime) -> str:
    days = (at - opened_at).days
    return next((label for limit, label in AGE_BANDS if days <= limit), OLDEST_AGE_BAND)


def redact(text: str, spans: Sequence[tuple[int, int, str]]) -> str:
    """Replaces each personal-data span with its kind, e.g. `[phone]`."""
    out, cursor = [], 0
    for start, end, kind in sorted(spans):
        if start < cursor:
            continue
        out.append(text[cursor:start])
        out.append(f"[{kind}]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def fingerprint(value: Any) -> bytes:
    """Canonical bytes of a query or a result, for the receipt's hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def result_hash(value: Any) -> str:
    return hashlib.sha256(fingerprint(value)).hexdigest()
