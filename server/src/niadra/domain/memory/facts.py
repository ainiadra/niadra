"""Fact arithmetic (5.8): the LLM points at conflicts, this code decides who wins.

A fact row is one value of one predicate for one origin handle. Every observation of the same
value folds into the row with order-free aggregates: `valid_at` is the earliest, and
`last_confirmed_at` the latest, valid time seen. Who is active is then a pure function of the rows
that contend for the same slot, so processing sessions out of order converges to the same state:

- the value with the latest `last_confirmed_at` wins and is active;
- every other value expires when the first newer value was seen after it (`invalid_at`);
- a retired row keeps its retire time in `invalid_at` and stays retracted until a later
  observation of the same value revives it;
- quarantined rows never contend.

Single-valued predicates contend per (predicate, `about`) across all handles of the profile;
other predicates contend only through `contradicts`. What the customer said and what a system
recorded never expire each other by recency: they get the `contradicts` relation (5.8, rev. 10).

Precedence by origin (11, 3.2) settles what recency leaves open. Between observations of the
same moment the origin decides: system of record, human agent, AI agent, customer. And a single-valued
slot keeps one current value across the record line: an active record supersedes the other side's
active values stated at or before its last confirmation (`supersede`); a statement newer than the record
stays active beside it, since a customer's move is news the record has not caught up with yet.
"""

import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.values import origin_rank
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, RelationType, Speaker, Verification

HALF_LIFE_DAYS = 180.0
_SPEAKER_WEIGHT = {
    Speaker.SYSTEM: 1.0,
    Speaker.HUMAN_AGENT: 0.9,
    Speaker.CUSTOMER: 0.85,
    Speaker.AI_AGENT: 0.7,
}
_LEVEL_WEIGHT = {
    Verification.V0: 0.6,
    Verification.V1: 0.75,
    Verification.V2: 0.9,
    Verification.V3: 1.0,
    Verification.V4: 1.0,
    Verification.NO_CUSTOMER: 0.9,
}
_VOICE_WEIGHT = 0.85

Settled = dict[MemoryRef, tuple[FactStatus, datetime | None]]
type RelationOrigin = Literal["model", "rule", "band"]
"""Who drew an edge: the extraction's model, a deterministic rule (precedence, a correction), or the
nightly similarity band (`domain.memory.contradictions`), whose edges only warn and never settle a status."""


@dataclass(frozen=True, slots=True)
class Relation:
    """A typed edge between two facts; the graph is N:N, never a forced linear chain (5.8)."""

    a: MemoryRef
    b: MemoryRef
    type: RelationType
    origin: RelationOrigin = "model"

    @property
    def settles(self) -> bool:
        """Whether the edge takes part in settling statuses: every edge but the band's warnings."""
        return self.origin != "band"


def normalize_value(value: str) -> str:
    """The dedupe key: case, accents, spacing and edge punctuation never make a new fact."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.split()).strip(" .,;:!?\"'")


def ref(fact: Fact) -> MemoryRef:
    return MemoryRef(fact.origin_handle_id, fact.fact_id)


def confidence(speaker: Speaker, level: Verification | None, certainty: float, from_voice: bool) -> float:
    """Speaker x session verification x certainty: "the customer said" is not "the system confirmed"."""
    value = _SPEAKER_WEIGHT[speaker] * _LEVEL_WEIGHT[level or Verification.V0] * certainty
    return round(value * (_VOICE_WEIGHT if from_voice else 1.0), 4)


def decayed(fact: Fact, now: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Confidence capped by age since the last confirmation; running it twice changes nothing."""
    if fact.speaker is Speaker.SYSTEM:
        return fact.confidence
    age_days = max(0.0, (now - fact.last_confirmed_at).total_seconds() / 86400)
    ceiling: float = 0.5 ** (age_days / half_life_days)
    return round(min(fact.confidence, ceiling), 4)


def merge_observation(existing: Fact, seen: Fact) -> Fact:
    """Folds a new observation of the same value into its row; `settle` decides the status."""
    mentions = tuple(sorted({*existing.mention_handle_ids, *seen.mention_handle_ids}))
    if seen.status is FactStatus.QUARANTINED and existing.status is not FactStatus.QUARANTINED:
        return existing
    if existing.status is FactStatus.QUARANTINED and seen.status is not FactStatus.QUARANTINED:
        # A clean observation replaces untrusted ones instead of mixing with their times.
        return replace(
            existing,
            status=FactStatus.ACTIVE,
            valid_at=seen.valid_at,
            last_confirmed_at=seen.last_confirmed_at,
            confidence=seen.confidence,
            speaker=seen.speaker,
            invalid_at=None,
            mention_handle_ids=mentions,
        )
    return replace(
        existing,
        valid_at=min(existing.valid_at, seen.valid_at),
        last_confirmed_at=max(existing.last_confirmed_at, seen.last_confirmed_at),
        confidence=max(existing.confidence, seen.confidence),
        mention_handle_ids=mentions,
        # The latest statement says how long the value holds: a renewed offer extends it, a plain
        # restatement makes it open-ended again.
        valid_until=seen.valid_until
        if seen.last_confirmed_at >= existing.last_confirmed_at
        else existing.valid_until,
    )


def retire(fact: Fact, at: datetime) -> Fact | None:
    """`None` when the value was confirmed after the retire: the older command loses."""
    if fact.status is FactStatus.QUARANTINED or at < fact.last_confirmed_at:
        return None
    ended = max(at, fact.invalid_at) if fact.status is FactStatus.RETRACTED and fact.invalid_at else at
    return replace(fact, status=FactStatus.RETRACTED, invalid_at=ended)


def contention_group(
    seed: Fact,
    facts: Sequence[Fact],
    contradicts: Iterable[tuple[MemoryRef, MemoryRef]],
    single_valued: Callable[[Fact], bool],
) -> list[Fact]:
    """The rows whose statuses depend on each other: same single-valued slot, or linked by `contradicts`."""
    by_ref = {ref(f): f for f in facts}
    edges: dict[MemoryRef, set[MemoryRef]] = {}
    for a, b in contradicts:
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)
    seen = {ref(seed)}
    queue = deque([seed])
    while queue:
        fact = queue.popleft()
        near = [by_ref[r] for r in edges.get(ref(fact), ()) if r in by_ref]
        if single_valued(fact):
            near += [f for f in facts if _same_slot(f, fact)]
        for other in near:
            if ref(other) not in seen and _same_side(other, fact):
                seen.add(ref(other))
                queue.append(other)
    return [by_ref[r] for r in sorted(seen, key=lambda r: (r.origin_handle_id, r.id)) if r in by_ref]


def settle(group: Sequence[Fact]) -> Settled:
    """Status and `invalid_at` of every contending row, as a function of their aggregates only;
    observations of the same moment are ranked by who made them (11, 3.2)."""
    points = [f for f in group if f.status is not FactStatus.QUARANTINED]
    if not points:
        return {}

    def rank(f: Fact) -> tuple[datetime, int, str]:
        return (f.last_confirmed_at, origin_rank(f.speaker), normalize_value(f.value))

    winner = normalize_value(max(points, key=rank).value)
    out: Settled = {}
    for fact in points:
        value = normalize_value(fact.value)
        if _still_retired(fact):
            out[ref(fact)] = (FactStatus.RETRACTED, fact.invalid_at)
        elif value == winner:
            out[ref(fact)] = (FactStatus.ACTIVE, None)
        else:
            newer = [g for g in points if normalize_value(g.value) != value and rank(g) > rank(fact)]
            ends = [g.valid_at if g.valid_at > fact.last_confirmed_at else g.last_confirmed_at for g in newer]
            out[ref(fact)] = (FactStatus.EXPIRED, min(ends))
    return out


def supersede(facts: Sequence[Fact], single_valued: Callable[[Fact], bool]) -> tuple[Settled, list[Relation]]:
    """Across the record line, the statuses precedence changes and the `SUPERSEDES` edges it draws.

    `facts` carry their settled statuses. For each single-valued slot with an active record, every active
    value of the other side that differs from it and was last confirmed no later than the record expires
    when the record took over; the loser keeps its row, its evidence and the edge that explains it.
    """
    active = [f for f in facts if f.status is FactStatus.ACTIVE and single_valued(f)]
    records = [f for f in active if f.speaker is Speaker.SYSTEM]
    out: Settled = {}
    edges: list[Relation] = []
    for fact in active:
        if fact.speaker is Speaker.SYSTEM:
            continue
        value = normalize_value(fact.value)
        winners = [
            r
            for r in records
            if _same_slot(r, fact)
            and normalize_value(r.value) != value
            and r.last_confirmed_at >= fact.last_confirmed_at
        ]
        if not winners:
            continue
        ends = {
            ref(r): r.valid_at if r.valid_at > fact.last_confirmed_at else r.last_confirmed_at
            for r in winners
        }
        winner = min(winners, key=lambda r: (ends[ref(r)], r.origin_handle_id, r.fact_id))
        out[ref(fact)] = (FactStatus.EXPIRED, ends[ref(winner)])
        edges.append(Relation(ref(winner), ref(fact), RelationType.SUPERSEDES, origin="rule"))
    return out, edges


def slot_mates(seed: Fact, facts: Sequence[Fact], single_valued: Callable[[Fact], bool]) -> list[Fact]:
    """Every row of a single-valued seed's slot, both sides of the record line: precedence reads them
    together, so each side is settled again whenever the other one moves."""
    if not single_valued(seed):
        return [seed]
    return [f for f in facts if _same_slot(f, seed)]


def _still_retired(fact: Fact) -> bool:
    return (
        fact.status is FactStatus.RETRACTED
        and fact.invalid_at is not None
        and fact.invalid_at >= fact.last_confirmed_at
    )


def _same_slot(a: Fact, b: Fact) -> bool:
    return (a.predicate, a.namespace, a.about_handle_id) == (b.predicate, b.namespace, b.about_handle_id)


def _same_side(a: Fact, b: Fact) -> bool:
    return (a.speaker is Speaker.SYSTEM) == (b.speaker is Speaker.SYSTEM)
