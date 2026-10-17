"""The identity tables as the continuous export writes them (22.3), and what `forget` reaches (8.4).

Without these tables the customer cannot tie a fact to a customer, because facts are pinned to
handles (L6), so both adapters build their rows here and agree on the shape.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.identity.model import AssertionRecord, MergeLogEntry
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import HandleRecord, LinkRecord, ProfileRecord
from niadra.domain.vocabulary import AssertionMethod

EXPORTED = (
    "handles",
    "profile_membership",
    "profiles",
    "profile_links",
    "assertions",
    "merge_log",
)


def erases_identity(scope: ErasureScope) -> bool:
    """A conversation is erased by what it produced; the people in it keep their identity."""
    return scope.kind is not TargetKind.CONVERSATION


def handle_row(handle: HandleRecord, value_hmac: bytes | None, change_seq: int) -> dict[str, Any]:
    """The value leaves decrypted beside its HMAC; with `pseudonymize` the export drops the value."""
    return {
        "handle_id": handle.handle_id,
        "type": handle.type.value,
        "scope": handle.scope,
        "subject_kind": handle.subject_kind.value,
        "value": handle.value,
        "value_hmac": value_hmac.hex() if value_hmac is not None else None,
        "status": handle.status.value,
        "level": handle.level.value,
        "first_seen": handle.first_seen,
        "last_seen": handle.last_seen,
        "change_seq": change_seq,
    }


def membership_row(handle_id: UUID, profile_id: UUID, version: int, change_seq: int) -> dict[str, Any]:
    return {"handle_id": handle_id, "profile_id": profile_id, "version": version, "change_seq": change_seq}


def profile_row(
    profile: ProfileRecord,
    *,
    created_at: datetime,
    retired_at: datetime | None,
    erased: bool,
    change_seq: int,
) -> dict[str, Any]:
    if erased:
        return {"profile_id": profile.profile_id, "change_seq": change_seq, "erased": True}
    return {
        "profile_id": profile.profile_id,
        "kind": profile.kind.value,
        "org_type": profile.org_type,
        "pseudonym": profile.pseudonym,
        "membership_version": profile.membership_version,
        "privacy_version": profile.privacy_version,
        "created_at": created_at,
        "retired_at": retired_at,
        "change_seq": change_seq,
    }


def link_row(
    link: LinkRecord,
    *,
    method: AssertionMethod,
    origin_event_id: UUID | None,
    confidence: float,
    change_seq: int,
) -> dict[str, Any]:
    return {
        "link_id": link.link_id,
        "person_handle_id": link.person_handle_id,
        "org_handle_id": link.org_handle_id,
        "role": link.role,
        "can_see_contacts": link.can_see_contacts,
        "valid_from": link.valid_from,
        "valid_to": link.valid_to,
        "method": method.value,
        "origin_event_id": origin_event_id,
        "confidence": confidence,
        "change_seq": change_seq,
    }


def assertion_row(assertion: AssertionRecord, change_seq: int) -> dict[str, Any]:
    return {
        "assertion_id": assertion.assertion_id,
        "handle_a": assertion.handle_a,
        "handle_b": assertion.handle_b,
        "method": assertion.method.value,
        "origin_event_id": assertion.origin_event_id,
        "confidence": assertion.confidence,
        "level_at_capture": assertion.level_at_capture.value,
        "state": assertion.state.value,
        "actor": assertion.actor,
        "reason": assertion.reason,
        "created_at": assertion.created_at,
        "retracted_at": assertion.retracted_at,
        "change_seq": change_seq,
    }


def merge_row(entry: MergeLogEntry, change_seq: int) -> dict[str, Any]:
    return {
        "merge_id": entry.merge_id,
        "op": entry.op.value,
        "from_profiles": list(entry.from_profiles),
        "to_profiles": list(entry.to_profiles),
        "assertion_id": entry.assertion_id,
        "handles_moved": list(entry.handles_moved),
        "actor": entry.actor,
        "reason": entry.reason,
        "created_at": entry.created_at,
        "reversed_at": entry.reversed_at,
        "change_seq": change_seq,
    }


def merge_touches(entry: MergeLogEntry, profile_id: UUID | None, handle_ids: set[UUID]) -> bool:
    """A profile's merge history goes with it; forgetting one handle keeps the profile's history,
    whose handle ids then point at nothing."""
    return profile_id is not None and (
        profile_id in entry.from_profiles
        or profile_id in entry.to_profiles
        or bool(handle_ids.intersection(entry.handles_moved))
    )
