"""The protections against wrong merges and the anchor rule (5.3, 5.8, 21.3)."""

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from niadra.domain.identity.handles import PHONE_FAMILY, WEAK_TYPES
from niadra.domain.records import HandleRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification

METHOD_CONFIDENCE: dict[AssertionMethod, float] = {
    AssertionMethod.OTP: 1.0,
    AssertionMethod.LOGIN: 1.0,
    AssertionMethod.EXPLICIT_IDENTIFY: 0.95,
    AssertionMethod.SYSTEM_IMPORT: 0.9,
    AssertionMethod.SAME_EVENT: 0.9,
    AssertionMethod.ACCEPTED_SUGGESTION: 0.9,
    AssertionMethod.EXTERNAL_RESOLVER: 0.85,
    AssertionMethod.CHANNEL_ROTATION: 0.8,
    AssertionMethod.DECLARED: 0.5,
    AssertionMethod.CO_OCCURRENCE: 0.5,
}

LINK_METHODS = frozenset(
    {
        AssertionMethod.SYSTEM_IMPORT,
        AssertionMethod.CO_OCCURRENCE,
        AssertionMethod.DECLARED,
        AssertionMethod.LOGIN,
    }
)
"""How a person <-> organization link may be born (21.2)."""

ORG_MERGE_METHODS = frozenset(
    {
        AssertionMethod.EXPLICIT_IDENTIFY,
        AssertionMethod.SYSTEM_IMPORT,
        AssertionMethod.ACCEPTED_SUGGESTION,
    }
)
"""Organizations never merge automatically: only someone who knows both ids can join them (21.3)."""

ONE_PER_SCOPE = frozenset({HandleType.GOV_ID_HMAC, HandleType.SYSTEM_ID, HandleType.ORG_REGISTRY_HMAC})
"""One document per country, one system id per namespace, one registry per organization."""
PHONE_LIMIT = 3
PHONE_LIMITED = PHONE_FAMILY | {HandleType.WA_JID}
FANOUT_LIMIT = 3
FANOUT_WINDOW = timedelta(hours=24)
# A store's number used by a different person each day never reaches 3 in a day; over a month, 5
# different ids of one type still mean a shared handle, and a person with two or three e-mails does not.
FANOUT_LONG_LIMIT = 5
FANOUT_LONG_WINDOW = timedelta(days=30)
RECYCLE_AFTER = timedelta(days=180)
VERIFIED = Verification.V3
CLAIM_METHODS = frozenset({AssertionMethod.DECLARED, AssertionMethod.EXPLICIT_IDENTIFY})
"""What a caller states without proof: a handle it joins stays provisional, and two profiles it names are
merged only on a record id of one of them (5.3)."""
PROVISIONAL_READ_CAP = Verification.V1
"""A read through a provisional handle reaches at most what its own channel shows ("it came from that
number"): a code sent to that number proves who holds the number, not that the profile is theirs."""


class Rejection(StrEnum):
    SUBJECT_KIND = "subject_kind_mismatch"
    HANDLE_NOT_ACTIVE = "handle_not_active"
    VERIFIED_PROFILES = "both_profiles_verified"
    ORGANIZATION = "organization_needs_explicit_merge"
    ONE_PER_SCOPE = "one_per_scope_conflict"
    ROTATION = "rotation_is_alias_only"
    SEPARATED = "separated_by_split"


def is_strong(handle: HandleRecord) -> bool:
    return handle.type not in WEAK_TYPES


def can_assert(handle: HandleRecord) -> bool:
    """Only strong, active handles become identity evidence; the rest are candidates at most (5.3)."""
    return is_strong(handle) and handle.status is HandleStatus.ACTIVE


def priority_key(handle: HandleRecord) -> tuple[int, bytes]:
    """The anchor rule (5.8): highest verification level first, then the smallest handle id."""
    return (-handle.level.rank, handle.handle_id.bytes)


def choose_anchor(handles: Iterable[HandleRecord], *, system_event: bool = False) -> HandleRecord:
    """A system event's facts belong to the subject's `system_id` when it has one (5.8)."""
    pool = list(handles)
    if system_event:
        pool = [h for h in pool if h.type is HandleType.SYSTEM_ID] or pool
    return min(pool, key=priority_key)


def weakest_first(handles: Iterable[HandleRecord]) -> list[HandleRecord]:
    """Provisional handles first: a claim never pushes out the numbers the profile already had."""
    return sorted(handles, key=lambda h: (not h.provisional, h.level.rank, h.last_seen, -h.handle_id.int))


def verified(handles: Iterable[HandleRecord]) -> bool:
    return any(h.level.rank >= VERIFIED.rank for h in handles)


def one_per_scope_conflict(side_a: Iterable[HandleRecord], side_b: Iterable[HandleRecord]) -> bool:
    """Two different documents of one country, or two ids of one namespace, mean two subjects."""
    ids_a = {(h.type, h.scope): h.handle_id for h in side_a if h.type in ONE_PER_SCOPE}
    return any(
        (h.type, h.scope) in ids_a and ids_a[(h.type, h.scope)] != h.handle_id
        for h in side_b
        if h.type in ONE_PER_SCOPE
    )


def phones_to_demote(handles: Iterable[HandleRecord]) -> list[HandleRecord]:
    """A profile keeps a few phones; the weakest beyond the limit is demoted, never rejected (5.3)."""
    active = [h for h in handles if h.type in PHONE_LIMITED and h.status is HandleStatus.ACTIVE]
    demoted: list[HandleRecord] = []
    for type in PHONE_LIMITED:
        same_type = [h for h in active if h.type is type]
        demoted += weakest_first(same_type)[: max(0, len(same_type) - PHONE_LIMIT)]
    return demoted


def fanout_exceeded(counterparts: Sequence[HandleRecord], limit: int = FANOUT_LIMIT) -> bool:
    """A handle joined to many distinct ids of one type in a short window is shared: a store
    tablet, a PBX, a service WhatsApp (5.3, protection 4)."""
    counts = Counter((h.type, h.scope) for h in {h.handle_id: h for h in counterparts}.values())
    return any(n >= limit for n in counts.values())


def looks_recycled(handle: HandleRecord, at: datetime) -> bool:
    """A phone silent for months that reappears with another subject's ids was likely reassigned."""
    return handle.type in PHONE_LIMITED and at - handle.last_seen > RECYCLE_AFTER


# Ids that come from a system of record or an authenticated session, never typed by whoever holds
# the phone: they are what can vouch that a number still belongs to its profile (5.3).
RECORD_TYPES = frozenset({HandleType.SYSTEM_ID, HandleType.GOV_ID_HMAC, HandleType.APP_USER_ID})
# Assertions that can restore a suspended number: the tenant's own systems or a login, never an OTP
# sent to the number itself, which whoever holds the phone now would pass.
RECONFIRMING_METHODS = frozenset(
    {AssertionMethod.EXPLICIT_IDENTIFY, AssertionMethod.LOGIN, AssertionMethod.SYSTEM_IMPORT}
)


def vouches_for(handle: HandleRecord, other: HandleRecord, profile_of: dict[UUID, UUID]) -> bool:
    """`other` is an active, confirmed record id of the same profile as `handle` (5.3)."""
    return (
        other.handle_id != handle.handle_id
        and other.type in RECORD_TYPES
        and other.status is HandleStatus.ACTIVE
        and not other.provisional
        and profile_of.get(other.handle_id) is not None
        and profile_of.get(other.handle_id) == profile_of.get(handle.handle_id)
    )


def merge_rejection(
    method: AssertionMethod,
    endpoints: Sequence[HandleRecord],
    side_a: Sequence[HandleRecord],
    side_b: Sequence[HandleRecord],
    *,
    force: bool,
    separated: bool = False,
) -> Rejection | None:
    """Why two profiles must not merge on this evidence, or None when they may (5.3, 21.3).

    `force` is the manual merge with actor and reason: it overrides the verified-profiles and the
    organization rules, never the subject kind, a blocked handle or a document conflict.
    `separated` says someone split the two profiles before and the evidence came from no person.
    """
    kinds = {h.subject_kind for h in (*side_a, *side_b)}
    if len(kinds) > 1:
        return Rejection.SUBJECT_KIND
    if any(h.status is not HandleStatus.ACTIVE for h in endpoints):
        return Rejection.HANDLE_NOT_ACTIVE
    if one_per_scope_conflict(side_a, side_b):
        return Rejection.ONE_PER_SCOPE
    if force:
        return None
    if separated:
        return Rejection.SEPARATED
    if method is AssertionMethod.CHANNEL_ROTATION:
        return Rejection.ROTATION
    if kinds.pop().is_organization and method not in ORG_MERGE_METHODS:
        return Rejection.ORGANIZATION
    if verified(side_a) and verified(side_b):
        return Rejection.VERIFIED_PROFILES
    return None


def profile_kind(handles: Iterable[HandleRecord]) -> SubjectKind:
    """A profile's kind comes from its handles; a component never mixes kinds (5.3)."""
    kinds = {h.subject_kind for h in handles}
    if len(kinds) != 1:
        raise ValueError("a profile's handles must share one subject kind")
    return kinds.pop()


SYSTEM_TYPES = frozenset({HandleType.SYSTEM_ID, HandleType.ORG_REGISTRY_HMAC})


def handle_weight(handle: HandleRecord) -> str:
    """The weight the site names for an identifier: a hint, declared, verified, authenticated,
    or from a system of record. Derived from the type and the highest level the handle ever proved."""
    if handle.type in WEAK_TYPES:
        return "hint"
    if handle.type in SYSTEM_TYPES:
        return "system"
    if handle.level.rank >= Verification.V4.rank:
        return "authenticated"
    if handle.level.rank >= Verification.V2.rank:
        return "verified"
    return "declared"
