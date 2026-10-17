"""Which sessions go to review (22.2): a base rate, boosted by conditions, under a daily cap."""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from niadra.domain.privacy.erasure import ErasureScope, TargetKind

BASE_CONDITION = "base"
MEASURE_SIGNAL = "measure_signal"
SENSITIVE = "sensitive"
NEW_SOURCE = "new_source"
NEW_EXTRACTOR = "new_extractor"
NEW_TRAIT = "new_trait"
CONTRADICTION_BAND = "contradiction_band"
"""The nightly similarity band found a fact of the session in likely contradiction with an older one of the
same predicate (`domain.memory.contradictions`); its samples are judged, and counted, on their own."""
UNBACKED_VALUE = "unbacked_value"
"""An agent's answer stated a number, date, code or amount the SDK's backing check found in no source the
agent had (13, 6.2): the pack, the system records, the customer's words, the tools it called."""
# Order of precedence when a session meets several conditions: the stored condition is the first one.
CONDITIONS = (
    NEW_SOURCE,
    NEW_EXTRACTOR,
    MEASURE_SIGNAL,
    UNBACKED_VALUE,
    CONTRADICTION_BAND,
    SENSITIVE,
    NEW_TRAIT,
)


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    base_rate: float
    boosted_rate: float
    boost_on: frozenset[str]
    daily_cap: int


@dataclass(frozen=True, slots=True)
class ReviewSampleRecord:
    sample_id: UUID
    session_id: UUID
    condition: str
    purpose: str | None
    created_at: datetime
    verdicts: tuple[dict[str, object], ...] = ()
    reviewer_id: UUID | None = None
    reviewed_at: datetime | None = None
    profile_id: UUID | None = None
    conversation_id: str | None = None
    """Copied from the session when sampled, so an erasure finds the sample without the session (8.4)."""


def draw(space_id: UUID, session_id: UUID) -> float:
    """A stable number in [0, 1): a redelivered task makes the same decision."""
    digest = hashlib.sha256(space_id.bytes + session_id.bytes).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


SECOND_REVIEW_FRACTION = 0.1
"""Reviewed samples offered to a second reviewer, so inter-reviewer agreement has pairs to count (22.2)."""


def wants_second_review(sample: ReviewSampleRecord, reviewer_id: UUID) -> bool:
    """A sample one person judged, drawn into the fraction, offered to anyone else once."""
    reviewers = {str(v.get("reviewer_id")) for v in sample.verdicts}
    if len(reviewers) != 1 or str(reviewer_id) in reviewers:
        return False
    digest = hashlib.sha256(b"second-review" + sample.sample_id.bytes).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < SECOND_REVIEW_FRACTION


def sampling_condition(met: Iterable[str], policy: SamplingPolicy) -> str:
    boosted = set(met) & policy.boost_on
    return next((c for c in CONDITIONS if c in boosted), BASE_CONDITION)


def should_sample(condition: str, value: float, policy: SamplingPolicy, taken_today: int) -> bool:
    if taken_today >= policy.daily_cap:
        return False
    rate = policy.base_rate if condition == BASE_CONDITION else policy.boosted_rate
    return value < rate


def erased_by(sample: ReviewSampleRecord, scope: ErasureScope) -> bool:
    """A handle's erasure takes the samples of its whole profile: a session is not tied to one handle, and
    a sample is worth nothing next to a person's right to be forgotten."""
    if scope.kind is TargetKind.CONVERSATION:
        return scope.conversation_id is not None and sample.conversation_id == scope.conversation_id
    return scope.profile_id is not None and sample.profile_id == scope.profile_id
