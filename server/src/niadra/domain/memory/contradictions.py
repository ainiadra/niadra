"""Contradictions between free-text facts of one predicate, found without a model.

The extraction's model marks `contradicts` when it notices a conflict inside the session it reads, and the
precedence rules settle single-valued slots. Two free-text statements of the same predicate said months
apart ("prefers to be served in the morning", later "in the evening") conflict only if the model happened
to see both. This nightly pass is the safety net, from the vectors the facts already carry.

It only warns. Its edges carry `origin="band"`: they never settle a status (`Relation.settles`), they send
the conversation to the review by sample, and they cost each fact a fifth of its weight in the pack while
both stay active. A person decides, through feedback, which of the two holds.

The band is on cosine similarity. Below `low` the two statements are about different things; above `high`
they are the same statement in other words, which is dedupe territory. A pair above the band still counts
when its words oppose: a negation on one side only, or two different poles of a closed set (times of day,
contact channels, liking and disliking). Near-identical sentences that differ by "morning" and "evening"
are exactly the conflict this pass exists for. Opposed pairs come first under the per-profile cap.
"""

import math
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from niadra.domain.memory.facts import Relation, normalize_value, ref
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, RelationType

LOW = 0.40
HIGH = 0.75
MAX_PAIRS = 20
SLOT_WINDOW = 50
"""The newest facts of one slot compared with each other: a slot with hundreds of rows would otherwise cost
a quadratic pass every night, and an old statement that nobody repeated matters less than a recent one."""

_NEGATIONS = frozenset(
    {
        # Portuguese
        "nao",
        "nunca",
        "jamais",
        "nenhum",
        "nenhuma",
        "nem",
        "sem",
        # English
        "not",
        "never",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "cannot",
        "cant",
        "wont",
        "without",
        # Spanish ("no" is left out: in Portuguese it means "in the")
        "tampoco",
        "ningun",
        "ninguna",
        "jamas",
        "sin",
    }
)

# Closed sets whose members exclude each other: one pole per inner set. Two statements that name different
# poles of the same set, and no pole in common, oppose.
_EXCLUSIVE: tuple[tuple[frozenset[str], ...], ...] = (
    (
        frozenset({"manha", "morning", "manana", "matutino"}),
        frozenset({"tarde", "afternoon"}),
        frozenset({"noite", "evening", "night", "noche", "noturno", "nocturno"}),
        frozenset({"madrugada", "dawn"}),
    ),
    (
        frozenset({"email", "mail", "correo"}),
        frozenset({"whatsapp", "zap"}),
        frozenset({"sms"}),
        frozenset({"telefone", "ligacao", "phone", "call", "telefono", "llamada"}),
    ),
    (
        frozenset({"gosta", "adora", "like", "likes", "loves", "gusta", "encanta"}),
        frozenset({"odeia", "detesta", "dislikes", "hates", "odia"}),
    ),
)


@dataclass(frozen=True, slots=True)
class BandPair:
    newer: Fact
    older: Fact
    similarity: float
    opposed: bool

    def relation(self) -> Relation:
        return Relation(ref(self.newer), ref(self.older), RelationType.CONTRADICTS, origin="band")


def band_pairs(
    facts: Sequence[Fact],
    vectors: Mapping[UUID, ItemVector],
    *,
    single_valued: Callable[[Fact], bool] = lambda _: False,
    provisional: Collection[UUID] = (),
    related: Iterable[tuple[UUID, UUID]] = (),
    low: float = LOW,
    high: float = HIGH,
    max_pairs: int = MAX_PAIRS,
) -> list[BandPair]:
    """The pairs the band draws among `facts`, strongest first, at most `max_pairs`: each becomes a
    `CONTRADICTS` edge, newer first, with its similarity and whether its words oppose.

    A pair qualifies when both facts are active, of the same predicate, namespace and subject, anchored on
    confirmed handles (a row of an unconfirmed link, in `provisional`, counts nowhere until the link is
    confirmed, 5.3), with different normalized values, of a predicate that is not single-valued (those
    already pass through precedence), not linked by any edge yet (`related`, fact ids either way), and both
    with a vector of the same encoder; and when their similarity lies in `[low, high]`, or above `high`
    with opposed words."""
    unconfirmed = set(provisional)
    linked = {frozenset(pair) for pair in related}
    slots: dict[tuple[str, str, UUID | None], list[Fact]] = {}
    for fact in facts:
        if (
            fact.status is FactStatus.ACTIVE
            and fact.origin_handle_id not in unconfirmed
            and fact.fact_id in vectors
            and not single_valued(fact)
        ):
            slots.setdefault((fact.predicate, fact.namespace, fact.about_handle_id), []).append(fact)
    found: list[BandPair] = []
    for rows in slots.values():
        newest = sorted(rows, key=_recency, reverse=True)[:SLOT_WINDOW]
        units = {f.fact_id: _unit(vectors[f.fact_id]) for f in newest}
        for i, newer in enumerate(newest):
            for older in newest[i + 1 :]:
                pair = _pair(newer, older, units, linked, low, high)
                if pair is not None:
                    found.append(pair)
    found.sort(key=lambda p: (not p.opposed, -p.similarity, str(p.newer.fact_id), str(p.older.fact_id)))
    return found[:max_pairs]


def opposed(first: str, second: str) -> bool:
    """Whether two statements' words oppose: a negation on one side only, or different poles of a closed
    set with no pole in common."""
    a, b = _words(first), _words(second)
    if bool(a & _NEGATIONS) != bool(b & _NEGATIONS):
        return True
    for poles in _EXCLUSIVE:
        named_a = {i for i, pole in enumerate(poles) if a & pole}
        named_b = {i for i, pole in enumerate(poles) if b & pole}
        if named_a and named_b and named_a.isdisjoint(named_b):
            return True
    return False


def _pair(
    newer: Fact,
    older: Fact,
    units: Mapping[UUID, tuple[str, tuple[float, ...]]],
    linked: set[frozenset[UUID]],
    low: float,
    high: float,
) -> BandPair | None:
    if normalize_value(newer.value) == normalize_value(older.value):
        return None
    if frozenset((newer.fact_id, older.fact_id)) in linked:
        return None
    (model_a, a), (model_b, b) = units[newer.fact_id], units[older.fact_id]
    if model_a != model_b or len(a) != len(b):
        return None  # vectors of two encoders are not comparable
    similarity = _dot(a, b)
    if similarity < low:
        return None
    against = opposed(newer.value, older.value)
    if similarity > high and not against:
        return None
    return BandPair(newer, older, round(similarity, 6), against)


def _unit(vector: ItemVector) -> tuple[str, tuple[float, ...]]:
    return vector.model, _scaled(vector.vector)


def _scaled(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(math.fsum(x * x for x in vector))
    return tuple(x / norm for x in vector) if norm else tuple(0.0 for _ in vector)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def _recency(fact: Fact) -> tuple[datetime, datetime, str]:
    return (fact.last_confirmed_at, fact.valid_at, str(fact.fact_id))


def _words(text: str) -> set[str]:
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("'", "").replace("’", ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return set(re.findall(r"[a-z0-9]+", plain))
