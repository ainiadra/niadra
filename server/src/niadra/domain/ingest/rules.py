"""Per-item checks of a batch: scopes, action operations, namespaces, verification ceiling (5.1, 5.2, 2.2).

Every rule raises a `NiadraError` whose `code` becomes the item's error in the 207 response;
one bad item never fails the batch.
"""

from collections.abc import Sequence, Set

from niadra.domain.common.errors import (
    Forbidden,
    InvalidInput,
    ScopeMissing,
    UnknownObject,
    VerificationNotAllowed,
)
from niadra.domain.objects.operations import canonical_operation, canonical_operations
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import (
    AssertionMethod,
    Audience,
    EventKind,
    HandleType,
    Scope,
    Speaker,
    SubjectKind,
    Verification,
)

ORG_ONLY_TYPES = frozenset({HandleType.ORG_REGISTRY_HMAC, HandleType.EMAIL_DOMAIN})

# Methods a caller may declare in `identify`; the others are minted by the identity module itself.
DECLARABLE_METHODS = frozenset(
    {
        AssertionMethod.EXPLICIT_IDENTIFY,
        AssertionMethod.OTP,
        AssertionMethod.LOGIN,
        AssertionMethod.SYSTEM_IMPORT,
        AssertionMethod.EXTERNAL_RESOLVER,
        AssertionMethod.DECLARED,
        # A new number or BSUID of the same customer: an alias of the old one, never a merge.
        AssertionMethod.CHANNEL_ROTATION,
    }
)
_PROVING_METHODS = frozenset({AssertionMethod.OTP, AssertionMethod.LOGIN})

_VERIFY_METHOD = {
    "otp_whatsapp": AssertionMethod.OTP,
    "otp_sms": AssertionMethod.OTP,
    "login": AssertionMethod.LOGIN,
}

# Network attestation of a call: STIR/SHAKEN level A is attested by the channel (2.2, 5.3).
_ATTESTATION = {"A": Verification.V2, "B": Verification.V1, "C": Verification.V1}


class OperationNotAllowed(Forbidden):
    code = "operation_not_allowed"


def default_subject_kind(handle_type: HandleType, declared: SubjectKind | None) -> SubjectKind:
    if declared is not None:
        return declared
    return SubjectKind.ACCOUNT if handle_type in ORG_ONLY_TYPES else SubjectKind.PERSON


def required_scope(item_type: str, kind: EventKind | None = None) -> Scope:
    if item_type == "identify":
        return Scope.IDENTIFY
    if item_type == "event" and kind is EventKind.ACTION:
        return Scope.ACT
    return Scope.TRACK


def check_scope(scopes: Set[Scope], item_type: str, kind: EventKind | None = None) -> None:
    scope = required_scope(item_type, kind)
    if scope not in scopes:
        raise ScopeMissing(f"the key lacks the {scope} scope")


def check_action(operation: str, trusted_ops: Set[str]) -> None:
    """With `act`, a source records any operation unless it declared a closed list (18.4)."""
    if trusted_ops and canonical_operation(operation) not in canonical_operations(trusted_ops):
        raise OperationNotAllowed("the source may not record this operation")


def check_namespaces(namespaces: Set[str], *, coordination: bool) -> None:
    """Only coordination names objects in its own namespace: an action there never turns divergent (18.4), so
    a forged action in it would never be flagged."""
    if COORDINATION_NAMESPACE in namespaces and not coordination:
        raise InvalidInput("the coordination namespace holds only coordination's own records")


CUSTOMER_FACING_SOURCES = frozenset({Audience.CUSTOMER_AGENT, Audience.HUMAN})


def sent_by_systems(
    audience: Audience, kind: EventKind, refs: Sequence[ObjectKey], mirrored: Set[str]
) -> tuple[ObjectKey, ...]:
    """The objects a customer-facing agent's action names that must already exist: those of a type that
    mirrors a company system (`mirror_of`). A system or an internal agent may name one the system has not sent
    yet (it is created empty and waits for the system's event); a customer-facing agent acts on what it was
    given, so a key it made up (juridico-sandbox, 05/10/2026: "legaldesk:<uuid>" for a case) never becomes
    an object."""
    if audience not in CUSTOMER_FACING_SOURCES or kind is not EventKind.ACTION:
        return ()
    # Coordination's own records (its namespace holds nothing a system sends) are never checked here.
    return tuple(
        dict.fromkeys(ref for ref in refs if ref.type in mirrored and ref.namespace != COORDINATION_NAMESPACE)
    )


def check_known(named: Sequence[ObjectKey], known: Set[ObjectKey], systems: dict[str, str]) -> None:
    """Refuses the item when one of `named` is not `known`; `systems` gives each type's system."""
    for ref in named:
        if ref not in known:
            raise UnknownObject(
                f"{ref.type} {ref.namespace}:{ref.external_id} is unknown in this space: objects of type "
                f"{ref.type} come from {systems.get(ref.type, 'a company system')}, and an agent names only "
                "one it was given (check the type, namespace and id)"
            )


def check_identify_method(method: AssertionMethod, ceiling: Verification) -> None:
    if method not in DECLARABLE_METHODS:
        raise InvalidInput("the method cannot be declared")
    if method in _PROVING_METHODS and ceiling.rank < Verification.V3.rank:
        raise VerificationNotAllowed("the source cannot assert a challenge")


def capped_hint(hint: Verification | None, ceiling: Verification, audience: Audience) -> Verification | None:
    """A hint is never proof; it is stored capped at the ceiling. `no_customer` is for internal agents."""
    if hint is None:
        return None
    if hint is Verification.NO_CUSTOMER:
        if audience is not Audience.INTERNAL_AGENT:
            raise VerificationNotAllowed("no_customer is only for internal agents")
        return hint
    if ceiling is Verification.NO_CUSTOMER:
        return Verification.V0
    return hint if hint.rank <= ceiling.rank else ceiling


# The most a channel can show on its own: V1 "arrived from that number", V2 an authenticated sender
# (2.2). Anything above is a challenge or a document and needs an explicit `verify`.
CHANNEL_PROOF_MAX = Verification.V2


def channel_proof(
    hint: Verification | None, ceiling: Verification, speaker: Speaker, direction: str | None
) -> Verification | None:
    """What an inbound customer turn proves for its conversation: the channel's own plausibility."""
    if hint is None or speaker is not Speaker.CUSTOMER or direction == "outbound":
        return None
    if hint is Verification.NO_CUSTOMER or ceiling is Verification.NO_CUSTOMER:
        return None
    level = min((hint, ceiling, CHANNEL_PROOF_MAX), key=lambda v: v.rank)
    return level if level.rank > Verification.V0.rank else None


def attestation_level(attestation: str | None) -> Verification | None:
    return _ATTESTATION.get(attestation or "")


def verify_method_assertion(method: str) -> AssertionMethod:
    return _VERIFY_METHOD.get(method, AssertionMethod.DECLARED)


def highest(*levels: Verification | None) -> Verification:
    customer = [lv for lv in levels if lv is not None and lv is not Verification.NO_CUSTOMER]
    return max(customer, key=lambda lv: lv.rank, default=Verification.V0)
