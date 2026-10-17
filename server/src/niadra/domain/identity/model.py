"""Records owned by the identity module (5.3). Handles, profiles and links are shared records."""

import base64
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.vocabulary import AssertionMethod, Verification


class AssertionState(StrEnum):
    APPLIED = "applied"
    PENDING = "pending"
    REJECTED = "rejected"


class MergeOp(StrEnum):
    MERGE = "merge"
    UNMERGE = "unmerge"


class ApplyOp(StrEnum):
    """What an `identity_apply` task does; every op runs under the lease of the profiles it touches."""

    APPLY = "apply"
    RETRACT = "retract"
    UNMERGE = "unmerge"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class AssertionRecord:
    """Immutable evidence that two handles are the same subject; only state and retraction change."""

    assertion_id: UUID
    handle_a: UUID
    handle_b: UUID
    method: AssertionMethod
    confidence: float
    level_at_capture: Verification
    state: AssertionState
    created_at: datetime
    origin_event_id: UUID | None = None
    actor: str | None = None
    reason: str | None = None
    retracted_at: datetime | None = None

    @property
    def live(self) -> bool:
        return self.state is AssertionState.APPLIED and self.retracted_at is None

    def other(self, handle_id: UUID) -> UUID:
        return self.handle_b if handle_id == self.handle_a else self.handle_a


@dataclass(frozen=True, slots=True)
class MergeLogEntry:
    merge_id: UUID
    op: MergeOp
    from_profiles: tuple[UUID, ...]
    to_profiles: tuple[UUID, ...]
    handles_moved: tuple[UUID, ...]
    actor: str
    created_at: datetime
    assertion_id: UUID | None = None
    reason: str | None = None
    reversed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SuggestionRecord:
    suggestion_id: UUID
    handle_a: UUID
    handle_b: UUID
    score: float
    created_at: datetime
    features: dict[str, Any] = field(default_factory=dict)
    status: str = "open"


def pseudonym(digest: bytes) -> str:
    """Stable per profile and space: `p_` plus 16 base32 characters of a keyed hash of the profile id."""
    return "p_" + base64.b32encode(digest[:10]).decode().lower()
