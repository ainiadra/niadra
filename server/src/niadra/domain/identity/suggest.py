"""Offline match suggestions: a Fellegi-Sunter score over cheap blocking keys (5.3).

Suggestions only feed a review queue; nothing here ever merges. Each feature carries its m
(agreement among true matches) and u (agreement among non-matches) probabilities, and a pair of
profiles scores the sum of log2(m/u) of the features its handles agree on: an e-mail local part
and a phone's subscriber digits, each too weak alone, add up to a suggestion. Names are never a
feature: two people with the same name are two people (case 12 of 5.19), and organizations match
only by id (21.3).
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from niadra.domain.identity.handles import gmail_canonical
from niadra.domain.vocabulary import HandleType


@dataclass(frozen=True, slots=True)
class Feature:
    m: float
    u: float

    @property
    def weight(self) -> float:
        return math.log2(self.m / self.u)


FEATURES = {
    "gmail_canonical": Feature(m=0.95, u=0.0005),
    "email_local": Feature(m=0.7, u=0.01),
    "phone_subscriber": Feature(m=0.3, u=0.001),
}
THRESHOLD = 10.0
MAX_BLOCK = 20
"""Blocks larger than this are common values (`contact@`, a PBX line) and carry no signal."""


@dataclass(frozen=True, slots=True)
class Candidate:
    handle_id: UUID
    profile_id: UUID
    type: HandleType
    value: str


@dataclass(frozen=True, slots=True)
class Suggestion:
    handle_a: UUID
    handle_b: UUID
    score: float
    features: tuple[str, ...]


def blocking_keys(type: HandleType, value: str) -> list[tuple[str, str]]:
    if type is HandleType.EMAIL:
        keys = []
        if canonical := gmail_canonical(value):
            keys.append(("gmail_canonical", canonical))
        local = value.split("@", 1)[0].split("+", 1)[0]
        if len(local) >= 6:
            keys.append(("email_local", local))
        return keys
    if type is HandleType.PHONE_E164:
        return [("phone_subscriber", value[-8:])]
    return []


def suggest(candidates: Iterable[Candidate]) -> list[Suggestion]:
    """One suggestion per pair of profiles, named by the handles of its strongest agreement."""
    blocks: dict[tuple[str, str], list[Candidate]] = {}
    for c in candidates:
        for key in blocking_keys(c.type, c.value):
            blocks.setdefault(key, []).append(c)
    agreements: dict[tuple[UUID, UUID], dict[str, tuple[UUID, UUID]]] = {}
    for (feature, _), members in blocks.items():
        if len(members) > MAX_BLOCK:
            continue
        for a, b in combinations(members, 2):
            if a.profile_id == b.profile_id or a.value == b.value:
                continue
            first, second = (a, b) if a.profile_id < b.profile_id else (b, a)
            pair = (first.handle_id, second.handle_id)
            found = agreements.setdefault((first.profile_id, second.profile_id), {})
            found[feature] = min(found.get(feature, pair), pair)
    kept: list[Suggestion] = []
    for found in agreements.values():
        score = round(sum(FEATURES[f].weight for f in found), 3)
        if score < THRESHOLD:
            continue
        strongest = max(found, key=lambda f: (FEATURES[f].weight, f))
        low, high = sorted(found[strongest])
        kept.append(Suggestion(low, high, score, tuple(sorted(found))))
    return sorted(kept, key=lambda s: (-s.score, s.handle_a, s.handle_b))
