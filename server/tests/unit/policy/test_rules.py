"""The context firewall's truth table (5.12, 2.2, 21.4)."""

from uuid import UUID, uuid4

import pytest

from niadra.config.models import CategoryRule, Policy, PolicyCondition, PolicyRule
from niadra.domain.common.errors import VerificationNotAllowed
from niadra.domain.policy import Effect, ItemAttrs, PolicyContext, Reader, effective_verification, evaluate
from niadra.domain.vocabulary import Audience, ExclusionReason, Sensitivity, Verification, Visibility

SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c009")
PARTNER = UUID("0192f5a0-0000-7000-8000-00000000e009")

POLICY = Policy(
    version="p1",
    categories=[
        CategoryRule(name="contact", min_verification=Verification.V1),
        CategoryRule(
            name="billing",
            min_verification=Verification.V2,
            purposes=frozenset({"billing"}),
            no_customer_allowed=True,
        ),
        CategoryRule(name="health", sensitivity=Sensitivity.SENSITIVE, min_verification=Verification.V3),
        CategoryRule(name="public", min_verification=Verification.V0),
        CategoryRule(name="contract", min_verification=Verification.V0),
    ],
    rules=[
        PolicyRule(
            effect="mask", when=PolicyCondition(category="contract", audience=Audience.CUSTOMER_AGENT)
        ),
        PolicyRule(
            effect="allow",
            when=PolicyCondition(category="health", purpose="care", min_verification=Verification.V3),
        ),
    ],
    competes_with={str(SOURCE): [str(PARTNER)]},
)

AGENT = Reader(Audience.CUSTOMER_AGENT, frozenset({"support"}))
BILLING = Reader(Audience.INTERNAL_AGENT, frozenset({"billing"}))
HUMAN = Reader(Audience.HUMAN, frozenset({"support"}))
CARE = Reader(Audience.CUSTOMER_AGENT, frozenset({"care"}))


def at(level: Verification, **extra: object) -> PolicyContext:
    return PolicyContext(level, **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reader", "item", "context", "effect", "rule"),
    [
        # Level gates by category (2.2).
        (AGENT, ItemAttrs("fact", "contact"), at(Verification.V1), Effect.ALLOW, ""),
        (AGENT, ItemAttrs("fact", "contact"), at(Verification.V0), Effect.DENY, "min_verification"),
        # Deny by default: a fact whose category the policy never declared reaches nobody, at any level.
        (AGENT, ItemAttrs("fact", "unknown"), at(Verification.V4), Effect.DENY, "undeclared_category"),
        (HUMAN, ItemAttrs("fact", "unknown"), at(Verification.V4), Effect.DENY, "undeclared_category"),
        # Labels that are not content categories (an episode's, an object type) stay open at V1.
        (AGENT, ItemAttrs("episode", "unknown"), at(Verification.V1), Effect.ALLOW, ""),
        (AGENT, ItemAttrs("episode", "unknown"), at(Verification.V0), Effect.DENY, "min_verification"),
        (AGENT, ItemAttrs("fact", "public"), at(Verification.V0), Effect.ALLOW, ""),
        # Purpose matched with category: the billing agent sees billing, support does not.
        (BILLING, ItemAttrs("fact", "billing"), at(Verification.V2), Effect.ALLOW, ""),
        (AGENT, ItemAttrs("fact", "billing"), at(Verification.V2), Effect.DENY, "purpose"),
        # Sensitive is denied by default, even at a high level, unless a rule allows it.
        (AGENT, ItemAttrs("fact", "health"), at(Verification.V4), Effect.DENY, "sensitive_default"),
        (
            AGENT,
            ItemAttrs("fact", "contact", sensitivity=Sensitivity.SENSITIVE),
            at(Verification.V4),
            Effect.DENY,
            "sensitive_default",
        ),
        (CARE, ItemAttrs("fact", "health"), at(Verification.V3), Effect.ALLOW, ""),
        (
            CARE,
            ItemAttrs("fact", "health"),
            at(Verification.V3, deny_sensitive=True),
            Effect.DENY,
            "sensitive_default",
        ),
        # Internal notes only for human audiences.
        (
            AGENT,
            ItemAttrs("fact", "contact", visibility=Visibility.INTERNAL),
            at(Verification.V2),
            Effect.DENY,
            "internal_visibility",
        ),
        (
            HUMAN,
            ItemAttrs("fact", "contact", visibility=Visibility.INTERNAL),
            at(Verification.V2),
            Effect.ALLOW,
            "",
        ),
        # no_customer: internal agents only, and only categories released for it.
        (BILLING, ItemAttrs("fact", "billing"), at(Verification.NO_CUSTOMER), Effect.ALLOW, ""),
        (
            BILLING,
            ItemAttrs("fact", "contact"),
            at(Verification.NO_CUSTOMER),
            Effect.DENY,
            "no_customer_category",
        ),
        (
            AGENT,
            ItemAttrs("fact", "billing"),
            at(Verification.NO_CUSTOMER),
            Effect.DENY,
            "no_customer_audience",
        ),
        # Tenant rules: masks, and a trait released to one purpose only (20.3).
        (AGENT, ItemAttrs("fact", "contract"), at(Verification.V1), Effect.MASK, "rule:0"),
        (BILLING, ItemAttrs("fact", "contract"), at(Verification.V1), Effect.ALLOW, ""),
        (
            AGENT,
            ItemAttrs("trait", "trait", purposes=frozenset({"billing"})),
            at(Verification.V1),
            Effect.DENY,
            "purpose",
        ),
        # Quarantine wins over everything.
        (
            HUMAN,
            ItemAttrs("fact", "public", quarantined=True),
            at(Verification.V4),
            Effect.DENY,
            "quarantine",
        ),
        # A contact without `can_see_contacts` sees other contacts only as a count and theme (21.4).
        (
            AGENT,
            ItemAttrs("episode", "public", other_contact=True),
            at(Verification.V1, can_see_contacts=False),
            Effect.MASK,
            "other_contact",
        ),
        (AGENT, ItemAttrs("episode", "public", other_contact=True), at(Verification.V1), Effect.ALLOW, ""),
    ],
)
def test_truth_table(
    reader: Reader, item: ItemAttrs, context: PolicyContext, effect: Effect, rule: str
) -> None:
    decision = evaluate(POLICY, reader, item, context)
    assert (decision.effect, decision.rule) == (effect, rule)


def test_exclusion_reasons_are_the_manifest_vocabulary() -> None:
    assert (
        evaluate(POLICY, AGENT, ItemAttrs("fact", "contact"), at(Verification.V0)).reason
        is ExclusionReason.VERIFICATION
    )
    assert (
        evaluate(POLICY, AGENT, ItemAttrs("fact", "billing"), at(Verification.V2)).reason
        is ExclusionReason.POLICY
    )
    assert evaluate(POLICY, AGENT, ItemAttrs("fact", "x", quarantined=True), at(Verification.V2)).reason is (
        ExclusionReason.QUARANTINE
    )


def test_a_source_never_reads_a_partner_it_competes_with() -> None:
    competitor = Reader(Audience.CUSTOMER_AGENT, frozenset({"support"}), SOURCE)
    item = ItemAttrs("fact", "public", handle_ids=(PARTNER,))
    assert evaluate(POLICY, competitor, item, at(Verification.V1)).rule == "competes_with"
    assert evaluate(POLICY, AGENT, item, at(Verification.V1)).included


def test_a_competing_partner_named_by_its_profile_covers_its_new_handles() -> None:
    # A list of handles left out every handle the partner gained later.
    from niadra.domain.vocabulary import SubjectKind

    profile = UUID("0192f5a0-0000-7000-8000-00000000e00a")
    policy = POLICY.model_copy(update={"competes_with": {str(SOURCE): [str(profile)]}})
    carrier = Reader(Audience.INTERNAL_AGENT, frozenset({"logistics"}), SOURCE)
    item = ItemAttrs(
        "fact", "public", subject_kind=SubjectKind.PARTNER, handle_ids=(uuid4(),), profile_id=profile
    )
    assert evaluate(policy, carrier, item, at(Verification.V1)).rule == "competes_with"
    other = Reader(Audience.INTERNAL_AGENT, frozenset({"logistics"}), UUID(int=7))
    assert evaluate(policy, other, item, at(Verification.V1)).included


def test_by_default_only_partner_purposes_read_a_partner() -> None:
    # Without any list, a support or sales agent never sees a partner company's context.
    from niadra.domain.vocabulary import SubjectKind

    item = ItemAttrs("fact", "public", subject_kind=SubjectKind.PARTNER)
    support = Reader(Audience.CUSTOMER_AGENT, frozenset({"support", "sales"}))
    assert evaluate(POLICY, support, item, at(Verification.V1)).rule == "partner_purpose"
    for purpose in ("partnership", "logistics"):
        partner_desk = Reader(Audience.INTERNAL_AGENT, frozenset({purpose}))
        assert evaluate(POLICY, partner_desk, item, at(Verification.V1)).included
    person = Reader(Audience.HUMAN)
    assert evaluate(POLICY, person, item, at(Verification.V1)).included
    customer = ItemAttrs("fact", "public", subject_kind=SubjectKind.ACCOUNT)
    assert evaluate(POLICY, support, customer, at(Verification.V1)).included


@pytest.mark.parametrize(
    ("requested", "ceiling", "proven", "effective", "reason"),
    [
        (Verification.V1, Verification.V2, Verification.V2, Verification.V1, None),
        (Verification.V3, Verification.V2, Verification.V4, Verification.V2, "source_ceiling"),
        (Verification.V2, Verification.V3, Verification.V1, Verification.V1, "not_proven"),
        (Verification.V4, Verification.V2, Verification.V0, Verification.V0, "not_proven"),
    ],
)
def test_effective_level_is_the_minimum_with_its_reason(
    requested: Verification,
    ceiling: Verification,
    proven: Verification,
    effective: Verification,
    reason: str | None,
) -> None:
    result = effective_verification(
        requested, ceiling=ceiling, proven=proven, audience=Audience.CUSTOMER_AGENT
    )
    assert (result.requested, result.effective, result.reason) == (requested, effective, reason)


def test_no_customer_is_refused_outside_internal_agents() -> None:
    with pytest.raises(VerificationNotAllowed):
        effective_verification(
            Verification.NO_CUSTOMER,
            ceiling=Verification.V2,
            proven=Verification.V0,
            audience=Audience.CUSTOMER_AGENT,
        )
    ok = effective_verification(
        Verification.NO_CUSTOMER,
        ceiling=Verification.V2,
        proven=Verification.V0,
        audience=Audience.INTERNAL_AGENT,
    )
    assert ok.effective is Verification.NO_CUSTOMER


# The starter release of health: care agents only, at V3, through the category's purpose.
HEALTH_POLICY = Policy(
    version="p2",
    categories=[
        CategoryRule(name="billing", min_verification=Verification.V2, purposes=frozenset({"billing"})),
        CategoryRule(
            name="health",
            sensitivity=Sensitivity.SENSITIVE,
            min_verification=Verification.V3,
            purposes=frozenset({"care"}),
        ),
        CategoryRule(name="orders"),
        CategoryRule(name="medical"),
    ],
    rules=[
        PolicyRule(
            effect="allow",
            when=PolicyCondition(category="health", purpose="care", min_verification=Verification.V3),
        ),
        # A careless rule: releases every sensitive item without naming a purpose.
        PolicyRule(effect="allow", when=PolicyCondition(category="medical")),
    ],
)
BILLING_AGENT = Reader(Audience.INTERNAL_AGENT, frozenset({"billing"}))
CARE_AGENT = Reader(Audience.CUSTOMER_AGENT, frozenset({"care"}))
SUPPORT = Reader(Audience.CUSTOMER_AGENT, frozenset({"support"}))


def decide(reader: Reader, item: ItemAttrs, level: Verification = Verification.V4) -> tuple[Effect, str]:
    decision = evaluate(HEALTH_POLICY, reader, item, at(level))
    return decision.effect, decision.rule


def test_the_billing_agent_never_sees_health_data_whatever_the_level() -> None:
    health = ItemAttrs("fact", "health")
    assert decide(BILLING_AGENT, health) == (Effect.DENY, "purpose")
    assert decide(SUPPORT, health) == (Effect.DENY, "purpose")
    assert decide(CARE_AGENT, health, Verification.V3) == (Effect.ALLOW, "")
    assert decide(CARE_AGENT, health, Verification.V2) == (Effect.DENY, "min_verification")


def test_an_item_no_level_releases_is_withheld_for_the_policy_not_for_the_level() -> None:
    health = ItemAttrs("fact", "health")
    # Support lacks the purpose health is released to: below V3 as at it, no verification gives it.
    assert decide(SUPPORT, health, Verification.V1) == decide(SUPPORT, health, Verification.V3)
    assert evaluate(HEALTH_POLICY, SUPPORT, health, at(Verification.V1)).reason is ExclusionReason.POLICY
    # The care agent holds the purpose: below V3 the item waits for the level.
    assert (
        evaluate(HEALTH_POLICY, CARE_AGENT, health, at(Verification.V1)).reason
        is ExclusionReason.VERIFICATION
    )


def test_a_sensitive_name_stays_sensitive_even_declared_as_ordinary() -> None:
    # `medical` is declared as a plain category and released by a rule without purpose: still no agent
    # reads it, because a sensitive release must name a purpose; people in the Console still can.
    medical = ItemAttrs("fact", "medical")
    assert decide(BILLING_AGENT, medical) == (Effect.DENY, "sensitive_without_purpose")
    assert decide(SUPPORT, medical) == (Effect.DENY, "sensitive_without_purpose")
    assert decide(HUMAN, medical) == (Effect.ALLOW, "")


def test_an_undeclared_sensitive_label_is_denied_to_every_agent() -> None:
    # A model that files a conversation under "Saúde" does not make it ordinary.
    for label in ("Saúde", "health.exams", "religion"):
        assert decide(SUPPORT, ItemAttrs("episode", label)) == (Effect.DENY, "sensitive_default")


def test_a_conversation_filed_under_a_portuguese_or_spanish_name_of_the_law_stays_sensitive() -> None:
    # "política", "sindicato" or "autorização de exame" filed by a model are as sensitive as
    # their English names; no agent reads them without a release bound to a purpose.
    for label in ("política", "sindicato", "vida_sexual", "origem_etnica", "salud", "autorizacao_de_exame"):
        assert decide(SUPPORT, ItemAttrs("episode", label)) == (Effect.DENY, "sensitive_default")
    assert decide(SUPPORT, ItemAttrs("episode", "politica_de_troca"), Verification.V1) == (Effect.ALLOW, "")


def test_an_item_about_a_conversation_carries_its_category() -> None:
    promise = ItemAttrs("open_item", "open_item", topics=("health",))
    assert decide(BILLING_AGENT, promise) == (Effect.DENY, "purpose")
    assert decide(CARE_AGENT, promise, Verification.V3) == (Effect.ALLOW, "")
    billing_promise = ItemAttrs("open_item", "open_item", topics=("billing",))
    assert decide(BILLING_AGENT, billing_promise, Verification.V1) == (Effect.DENY, "min_verification")
    assert decide(BILLING_AGENT, billing_promise, Verification.V2) == (Effect.ALLOW, "")
    assert decide(SUPPORT, ItemAttrs("open_item", "open_item", topics=("orders",)), Verification.V1) == (
        Effect.ALLOW,
        "",
    )


def test_rules_on_a_category_apply_to_items_about_it() -> None:
    policy = Policy(
        version="p3",
        categories=[CategoryRule(name="orders")],
        rules=[PolicyRule(effect="deny", when=PolicyCondition(category="orders", audience=Audience.ANALYST))],
    )
    analyst = Reader(Audience.ANALYST)
    item = ItemAttrs("open_item", "open_item", topics=("orders",))
    assert evaluate(policy, analyst, item, at(Verification.V4)).rule == "rule:0"


def test_an_item_for_some_audiences_never_reaches_the_others() -> None:
    sentiment = ItemAttrs("trait", "trait", audiences=frozenset({Audience.CUSTOMER_AGENT}))
    assert decide(SUPPORT, sentiment, Verification.V1) == (Effect.ALLOW, "")
    assert decide(BILLING_AGENT, sentiment, Verification.V1) == (Effect.DENY, "audience")
    assert decide(HUMAN, sentiment, Verification.V1) == (Effect.ALLOW, "")
