"""The context firewall (5.12): one pure decision per memory item.

Compilation, the read-time recheck and the simulator all call `evaluate`, so the three can
never disagree. The configuration models live above the domain, so the policy document is
described here by the attributes the decision reads (any object with them works).

Access is denied by default and released by purpose: a fact whose category the policy never
declared reaches nobody; a sensitive category (the product's list, whatever the tenant says) reaches
an agent only through a release that names a purpose; and an item about a conversation carries that
conversation's category too, so a promise made in a health conversation is health data.

An item is withheld for the level (reason `verification`) only when proving that level would release it; one
the reader could not read at any level (a purpose it lacks) is denied for the policy's own reason, so nothing
downstream tells an agent to verify the customer for it.

The mirror rule: what the customer said themselves (`own_words`), through a confirmed handle of their own,
reaches a reader from V1 in a category that declares `mirror`, whatever level the category asks for; the
company only hands back to the customer what they told it. What the company holds about them keeps the
category's level, a sensitive category never mirrors, and purposes, rules and audiences apply as for any
other item.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from niadra.domain.common.errors import VerificationNotAllowed
from niadra.domain.policy.purposes import PARTNER_PURPOSES
from niadra.domain.policy.sensitive import is_product_sensitive
from niadra.domain.vocabulary import (
    Audience,
    ExclusionReason,
    Sensitivity,
    SpokenBy,
    SubjectKind,
    Verification,
    Visibility,
)

HUMAN_AUDIENCES = frozenset({Audience.HUMAN, Audience.REVIEWER})
_SENSITIVITY_RANK = {Sensitivity.NORMAL: 0, Sensitivity.RESTRICTED: 1, Sensitivity.SENSITIVE: 2}
# Structural kinds every space has; who reads them follows the categories they are about (`topics`).
PRODUCT_CATEGORIES = frozenset({"profile", "open_item", "action", "history", "trait", "object"})
"""`object`: the category of what was said about an object that no predicate of the space holds;
a space declares it to give it a level or purposes of its own."""
# Kinds whose category is the tenant's classification of content: undeclared means released to nobody.
CONTENT_KINDS = frozenset({"fact"})


class CategorySpec(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def sensitivity(self) -> Sensitivity: ...
    @property
    def min_verification(self) -> Verification: ...
    @property
    def purposes(self) -> frozenset[str]: ...
    @property
    def no_customer_allowed(self) -> bool: ...
    @property
    def mirror(self) -> bool: ...


class ConditionSpec(Protocol):
    @property
    def audience(self) -> Audience | None: ...
    @property
    def purpose(self) -> str | None: ...
    @property
    def subject_kind(self) -> SubjectKind | None: ...
    @property
    def category(self) -> str | None: ...
    @property
    def sensitivity(self) -> Sensitivity | None: ...
    @property
    def visibility(self) -> Visibility | None: ...
    @property
    def min_verification(self) -> Verification | None: ...
    @property
    def link_role(self) -> str | None: ...


class RuleSpec(Protocol):
    @property
    def effect(self) -> str: ...
    @property
    def when(self) -> ConditionSpec: ...


class PolicySpec(Protocol):
    @property
    def version(self) -> str: ...
    @property
    def categories(self) -> Sequence[CategorySpec]: ...
    @property
    def rules(self) -> Sequence[RuleSpec]: ...
    @property
    def competes_with(self) -> Mapping[str, Sequence[str]]: ...


@dataclass(frozen=True, slots=True)
class DefaultCategory:
    """A category the tenant did not declare: normal, V1 and open to every purpose, unless its name is
    sensitive for the product; then it is sensitive and released to no purpose."""

    name: str
    sensitivity: Sensitivity = Sensitivity.NORMAL
    min_verification: Verification = Verification.V1
    purposes: frozenset[str] = frozenset()
    no_customer_allowed: bool = False
    mirror: bool = False


class Effect(StrEnum):
    ALLOW = "allow"
    MASK = "mask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Reader:
    """Who reads: a source (agent or system) or a person in the Console."""

    audience: Audience
    purposes: frozenset[str] = frozenset()
    source_id: UUID | None = None

    @property
    def is_human(self) -> bool:
        return self.audience in HUMAN_AUDIENCES


@dataclass(frozen=True, slots=True)
class ItemAttrs:
    """What the policy may look at on a memory item; never its content."""

    kind: str
    category: str
    sensitivity: Sensitivity = Sensitivity.NORMAL
    visibility: Visibility = Visibility.PUBLIC
    subject_kind: SubjectKind = SubjectKind.PERSON
    quarantined: bool = False
    handle_ids: tuple[UUID, ...] = ()
    other_contact: bool = False
    purposes: frozenset[str] = frozenset()
    topics: tuple[str, ...] = ()
    """Categories the item is about besides its own: an open item is about its conversation's category,
    a summary about every conversation it covers. The item must pass each of them."""
    audiences: frozenset[Audience] = frozenset()
    """Item-level audience classes, e.g. declining sentiment only for customer agents (20.3)."""
    profile_id: UUID | None = None
    """The profile the item belongs to: a competing partner is named by it, so its new handles are
    covered too (21.5)."""
    spoken_by: SpokenBy | None = None
    """Whose words the item is, from its evidence; None where memory never recorded it."""
    own_words: bool = False
    """The customer's own words about themselves (`spoken_by` customer or mixed), through a confirmed handle
    of the person the read is about, for a reader through no unconfirmed link, naming nothing the product
    holds sensitive: what the mirror rule may hand back from V1."""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    verification: Verification
    link_role: str | None = None
    can_see_contacts: bool = True
    deny_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    effect: Effect
    reason: ExclusionReason | None = None
    rule: str = ""

    @property
    def included(self) -> bool:
        return self.effect is not Effect.DENY


_ALLOW = Decision(Effect.ALLOW)
MIRROR_RULE = "mirror"
"""The rule an item released by the mirror rule carries in its decision, so a manifest and the policy
simulation say why a reader at V1 saw it."""
MIRROR_LEVEL = Verification.V1


def _deny(reason: ExclusionReason, rule: str) -> Decision:
    return Decision(Effect.DENY, reason, rule)


def category_rule(policy: PolicySpec, name: str) -> CategorySpec:
    """The declared category, never less than sensitive when its name is; an undeclared one by default."""
    declared = next((c for c in policy.categories if c.name == name), None)
    sensitive = is_product_sensitive(name)
    if declared is None:
        if sensitive:
            return DefaultCategory(name, Sensitivity.SENSITIVE, Verification.V3)
        return DefaultCategory(name)
    if sensitive and declared.sensitivity is not Sensitivity.SENSITIVE:
        # A tenant cannot make health data ordinary; it can only release it to purposes (LGPD art. 11).
        return DefaultCategory(
            name,
            Sensitivity.SENSITIVE,
            declared.min_verification,
            declared.purposes,
            declared.no_customer_allowed,
            # A sensitive category never mirrors, whatever the document says.
            False,
        )
    return declared


def is_declared(policy: PolicySpec, name: str) -> bool:
    return name in PRODUCT_CATEGORIES or any(c.name == name for c in policy.categories)


def item_sensitivity(policy: PolicySpec, item: ItemAttrs) -> Sensitivity:
    """The item's own sensitivity or its categories', whichever is higher."""
    categories = [category_rule(policy, name) for name in dict.fromkeys((item.category, *item.topics))]
    return max((item.sensitivity, *(c.sensitivity for c in categories)), key=_SENSITIVITY_RANK.__getitem__)


def released_by_name(policy: PolicySpec, reader: Reader, item: ItemAttrs) -> bool:
    """Whether the tenant released the item to this reader by naming one of its purposes: a category of
    the item whose purposes include the reader's, or an allow rule on one of its categories that names
    the reader's purpose. What the policy asks for by name is never left out for lack of use (15.8)."""
    names = (item.category, *item.topics)
    if any(category_rule(policy, name).purposes & reader.purposes for name in names):
        return True
    return any(
        rule.effect == Effect.ALLOW and rule.when.purpose in reader.purposes and rule.when.category in names
        for rule in policy.rules
    )


def evaluate(policy: PolicySpec, reader: Reader, item: ItemAttrs, context: PolicyContext) -> Decision:
    if item.quarantined:
        return _deny(ExclusionReason.QUARANTINE, "quarantine")
    if item.kind in CONTENT_KINDS and not is_declared(policy, item.category):
        # Deny by default (5.12): content the policy never classified is released to nobody.
        return _deny(ExclusionReason.POLICY, "undeclared_category")
    categories = [category_rule(policy, name) for name in dict.fromkeys((item.category, *item.topics))]
    sensitivity = item_sensitivity(policy, item)
    mirrored = False

    if context.verification is Verification.NO_CUSTOMER:
        # Tasks without the customer present (2.2): internal agents only, and only categories
        # whose purpose releases them for `no_customer`.
        if reader.audience is not Audience.INTERNAL_AGENT:
            return _deny(ExclusionReason.VERIFICATION, "no_customer_audience")
        if not all(c.no_customer_allowed for c in categories):
            return _deny(ExclusionReason.VERIFICATION, "no_customer_category")
    elif short := [c for c in categories if context.verification.rank < c.min_verification.rank]:
        if not _mirrored(item, short, sensitivity, context):
            # Withheld for the level only when proving it would release the item: one kept for a purpose
            # the reader lacks stays kept at any level, and an agent told to verify would ask for nothing.
            needed = max((c.min_verification for c in short), key=lambda level: level.rank)
            proven = evaluate(policy, reader, item, replace(context, verification=needed))
            if not proven.included:
                return proven
            return _deny(ExclusionReason.VERIFICATION, "min_verification")
        mirrored = True

    if item.visibility is Visibility.INTERNAL and not reader.is_human:
        return _deny(ExclusionReason.POLICY, "internal_visibility")
    if any(c.purposes and not (c.purposes & reader.purposes) for c in categories):
        return _deny(ExclusionReason.POLICY, "purpose")
    if item.purposes and not (item.purposes & reader.purposes):
        # Item-level purposes, e.g. a trait rule released only to billing (20.3).
        return _deny(ExclusionReason.POLICY, "purpose")
    if item.audiences and not reader.is_human and reader.audience not in item.audiences:
        return _deny(ExclusionReason.POLICY, "audience")
    if _competes(policy, reader, item):
        return _deny(ExclusionReason.POLICY, "competes_with")
    if (
        item.subject_kind is SubjectKind.PARTNER
        and not reader.is_human
        and not reader.purposes & PARTNER_PURPOSES
    ):
        # A partner's context is for the agents that work with partners (21.5): by default a vendor's
        # support or sales agent never reads another company's partner.
        return _deny(ExclusionReason.POLICY, "partner_purpose")

    matched = [
        (index, rule.effect)
        for index, rule in enumerate(policy.rules)
        if _matches(rule.when, reader, item, sensitivity, context)
    ]
    if (denied := _first(matched, Effect.DENY)) is not None:
        return _deny(ExclusionReason.POLICY, f"rule:{denied}")
    if sensitivity is Sensitivity.SENSITIVE:
        allowed = _first(matched, Effect.ALLOW)
        if context.deny_sensitive or allowed is None:
            return _deny(ExclusionReason.POLICY, "sensitive_default")
        if not reader.is_human and not _released_by_purpose(policy, categories, matched):
            # An allow rule that names no purpose would hand health data to every agent, billing too.
            return _deny(ExclusionReason.POLICY, "sensitive_without_purpose")
    if item.other_contact and not context.can_see_contacts:
        # A contact without `can_see_contacts` sees what other contacts said only as a count and theme (21.4).
        return Decision(Effect.MASK, ExclusionReason.POLICY, "other_contact")
    if (masked := _first(matched, Effect.MASK)) is not None:
        return Decision(Effect.MASK, ExclusionReason.POLICY, f"rule:{masked}")
    return Decision(Effect.ALLOW, None, MIRROR_RULE) if mirrored else _ALLOW


def _mirrored(
    item: ItemAttrs, short: Sequence[CategorySpec], sensitivity: Sensitivity, context: PolicyContext
) -> bool:
    """Whether the mirror rule lifts the level every category in `short` asks for above the reader's: the
    customer's own words, at V1 or above, in categories that all declare it, none of them sensitive."""
    return (
        item.own_words
        and item.spoken_by in (SpokenBy.CUSTOMER, SpokenBy.MIXED)
        and item.subject_kind is SubjectKind.PERSON
        and not item.other_contact
        and sensitivity is not Sensitivity.SENSITIVE
        and context.verification.rank >= MIRROR_LEVEL.rank
        and all(c.mirror and c.sensitivity is not Sensitivity.SENSITIVE for c in short)
    )


def _first(matched: list[tuple[int, str]], effect: Effect) -> int | None:
    return next((index for index, rule_effect in matched if rule_effect == effect), None)


def _released_by_purpose(
    policy: PolicySpec, categories: Sequence[CategorySpec], matched: list[tuple[int, str]]
) -> bool:
    """A sensitive item needs a release bound to a purpose: the category's own purposes, which the
    reader already holds by now, or an allow rule that names the purpose."""
    if any(c.purposes for c in categories if c.sensitivity is Sensitivity.SENSITIVE):
        return True
    return any(effect == Effect.ALLOW and policy.rules[index].when.purpose for index, effect in matched)


def _competes(policy: PolicySpec, reader: Reader, item: ItemAttrs) -> bool:
    """A source never reads a partner it competes with, named by a handle id or by the partner's profile
    id; the profile covers the handles the partner gains later."""
    if reader.source_id is None:
        return False
    partners = policy.competes_with.get(str(reader.source_id), ())
    if item.profile_id is not None and str(item.profile_id) in partners:
        return True
    return any(str(handle_id) in partners for handle_id in item.handle_ids)


def _matches(
    when: ConditionSpec, reader: Reader, item: ItemAttrs, sensitivity: Sensitivity, context: PolicyContext
) -> bool:
    checks = (
        when.audience is None or when.audience is reader.audience,
        when.purpose is None or when.purpose in reader.purposes,
        when.subject_kind is None or when.subject_kind is item.subject_kind,
        when.category is None or when.category == item.category or when.category in item.topics,
        when.sensitivity is None or when.sensitivity is sensitivity,
        when.visibility is None or when.visibility is item.visibility,
        # A condition's `min_verification` scopes the rule to sessions at or above that level.
        when.min_verification is None or context.verification.rank >= when.min_verification.rank,
        when.link_role is None or when.link_role == context.link_role,
    )
    return all(checks)


NOT_PROVEN = "not_proven"
"""The reason a read's level is lower than it asked: its conversation or task proves less."""


@dataclass(frozen=True, slots=True)
class EffectiveVerification:
    requested: Verification
    effective: Verification
    reason: str | None = None


def effective_verification(
    requested: Verification, *, ceiling: Verification, proven: Verification, audience: Audience
) -> EffectiveVerification:
    """min(requested, source ceiling, proven in the conversation), with the reason it is lower (2.2)."""
    if requested is Verification.NO_CUSTOMER:
        if audience is not Audience.INTERNAL_AGENT:
            raise VerificationNotAllowed("no_customer is only accepted from internal agents")
        return EffectiveVerification(requested, requested)
    effective: Verification = requested
    reason: str | None = None
    if ceiling is not Verification.NO_CUSTOMER and ceiling.rank < effective.rank:
        effective, reason = ceiling, "source_ceiling"
    if proven.rank < effective.rank:
        effective, reason = proven, NOT_PROVEN
    return EffectiveVerification(requested, effective, reason)
