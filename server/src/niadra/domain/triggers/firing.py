"""A trigger firing as stored in `trigger_firings` (22.1)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from niadra.domain.privacy.erasure import ErasureScope, TargetKind, origin_step
from niadra.domain.traits.evidence import EvidenceRef, evidence_handles


@dataclass(frozen=True, slots=True)
class Firing:
    firing_id: UUID
    rule_id: str
    rule_version: str
    profile_id: UUID
    """The subject: a profile, or the source itself for `source.silent`."""
    window_key: str
    idempotency_key: bytes
    evidence: tuple[EvidenceRef, ...]
    membership_version: int
    fired_at: datetime
    late: bool = False
    retracted_at: datetime | None = None

    def separated_from(self, handle_ids: frozenset[UUID]) -> bool:
        """True when part of the evidence left the profile, as after an unmerge (22.1)."""
        return not evidence_handles(self.evidence) <= handle_ids


def erased_by(firing: Firing, scope: ErasureScope) -> bool:
    """On the evidence step, a firing that pointed at an erased event; then the target's own firings. A
    conversation erasure keeps the person's other firings."""
    if not origin_step(scope):
        return any(e.kind == "event" and e.id in scope.event_ids for e in firing.evidence)
    if scope.kind is TargetKind.CONVERSATION:
        return False
    if scope.kind is TargetKind.PROFILE and firing.profile_id == scope.profile_id:
        return True
    return bool(evidence_handles(firing.evidence) & set(scope.handle_ids))
