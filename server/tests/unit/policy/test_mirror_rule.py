"""The mirror rule (5.12): what the customer said themselves reaches a reader from V1
in a category that declares it; what the company holds about them keeps the category's level; a sensitive
category never mirrors; nothing but the level changes."""

from dataclasses import replace

import pytest

from niadra.config.models import CategoryRule, Policy
from niadra.config.starter import STARTER_POLICY
from niadra.domain.policy import MIRROR_RULE, Effect, ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.vocabulary import (
    Audience,
    ExclusionReason,
    Sensitivity,
    SpokenBy,
    SubjectKind,
    Verification,
)

POLICY = Policy(
    version="mirror-1",
    categories=[
        CategoryRule(
            name="billing",
            min_verification=Verification.V2,
            purposes=frozenset({"billing", "support"}),
            mirror=True,
        ),
        CategoryRule(name="personal", min_verification=Verification.V2, mirror=False),
        CategoryRule(name="orders", min_verification=Verification.V1, mirror=True),
        # A document that asks for the impossible: a sensitive category never mirrors.
        CategoryRule(
            name="medical_notes",
            sensitivity=Sensitivity.SENSITIVE,
            min_verification=Verification.V3,
            purposes=frozenset({"support"}),
            mirror=True,
        ),
        CategoryRule(
            name="health", min_verification=Verification.V2, purposes=frozenset({"support"}), mirror=True
        ),
    ],
)
SUPPORT = Reader(Audience.CUSTOMER_AGENT, frozenset({"support"}))
SALES = Reader(Audience.CUSTOMER_AGENT, frozenset({"sales"}))
OWN = ItemAttrs("episode", "billing", spoken_by=SpokenBy.CUSTOMER, own_words=True)


def decide(item: ItemAttrs, level: Verification, reader: Reader = SUPPORT, policy: Policy = POLICY) -> object:
    decision = evaluate(policy, reader, item, PolicyContext(level))
    return decision.effect, decision.rule


def test_the_customers_own_words_reach_a_v1_reader_by_the_mirror_rule() -> None:
    assert decide(OWN, Verification.V1) == (Effect.ALLOW, MIRROR_RULE)
    mixed = replace(OWN, spoken_by=SpokenBy.MIXED)
    assert decide(mixed, Verification.V1) == (Effect.ALLOW, MIRROR_RULE)
    # At the category's own level nothing is lifted: an ordinary release, no rule named.
    assert decide(OWN, Verification.V2) == (Effect.ALLOW, "")


def test_a_v0_reader_never_benefits() -> None:
    for spoken in (SpokenBy.CUSTOMER, SpokenBy.MIXED):
        item = replace(OWN, spoken_by=spoken)
        assert decide(item, Verification.V0) == (Effect.DENY, "min_verification")


def test_what_the_company_holds_keeps_the_categorys_level() -> None:
    company = ItemAttrs("fact", "billing", spoken_by=SpokenBy.COMPANY)
    assert decide(company, Verification.V1) == (Effect.DENY, "min_verification")
    # Even if something marked it as the customer's own words, the company's words never mirror.
    assert decide(replace(company, own_words=True), Verification.V1) == (Effect.DENY, "min_verification")
    # A row written before memory recorded whose words it holds is never lifted.
    unknown = ItemAttrs("episode", "billing", own_words=True)
    assert decide(unknown, Verification.V1) == (Effect.DENY, "min_verification")


def test_words_that_are_not_the_readers_own_are_never_lifted() -> None:
    # Through an unconfirmed link, about an organization, or naming a sensitive topic: `own_words` is off.
    said = ItemAttrs("episode", "billing", spoken_by=SpokenBy.CUSTOMER, own_words=False)
    assert decide(said, Verification.V1) == (Effect.DENY, "min_verification")
    other = replace(OWN, other_contact=True)
    assert decide(other, Verification.V1) == (Effect.DENY, "min_verification")
    account = replace(OWN, subject_kind=SubjectKind.ACCOUNT)
    assert decide(account, Verification.V1) == (Effect.DENY, "min_verification")


def test_a_category_without_the_mirror_keeps_its_level() -> None:
    personal = replace(OWN, category="personal")
    assert decide(personal, Verification.V1) == (Effect.DENY, "min_verification")
    # An item about two categories is lifted only when every one it falls short of mirrors.
    promise = ItemAttrs(
        "open_item", "open_item", topics=("billing", "personal"), spoken_by=SpokenBy.MIXED, own_words=True
    )
    assert decide(promise, Verification.V1) == (Effect.DENY, "min_verification")
    assert decide(replace(promise, topics=("billing", "orders")), Verification.V1) == (
        Effect.ALLOW,
        MIRROR_RULE,
    )


def test_a_sensitive_category_never_mirrors() -> None:
    # Withheld for the policy, not for a level: no verification releases it to this reader, so none is
    # asked for.
    declared = replace(OWN, category="medical_notes")
    assert decide(declared, Verification.V1) == (Effect.DENY, "sensitive_default")
    # A category named for health data is sensitive whatever the document says, and loses its mirror.
    named = replace(OWN, category="health")
    assert decide(named, Verification.V1) == (Effect.DENY, "sensitive_default")
    # A sensitive item in a mirrored category stays where it was.
    item = replace(OWN, sensitivity=Sensitivity.SENSITIVE)
    assert decide(item, Verification.V1) == (Effect.DENY, "sensitive_default")


def test_only_the_level_changes() -> None:
    # The purpose still decides: a sales agent reads no billing, mirrored or not.
    decision = evaluate(POLICY, SALES, OWN, PolicyContext(Verification.V1))
    assert (decision.effect, decision.reason, decision.rule) == (
        Effect.DENY,
        ExclusionReason.POLICY,
        "purpose",
    )
    # A task without the customer present follows its own rule.
    no_customer = evaluate(
        POLICY,
        Reader(Audience.INTERNAL_AGENT, frozenset({"billing"})),
        OWN,
        PolicyContext(Verification.NO_CUSTOMER),
    )
    assert no_customer.rule == "no_customer_category"


@pytest.mark.parametrize("level", [Verification.V0, Verification.V1, Verification.V2])
def test_the_starter_mirrors_billing_from_v1_and_never_health(level: Verification) -> None:
    billing = replace(OWN, category="billing")
    health = replace(OWN, category="health")
    care = Reader(Audience.CUSTOMER_AGENT, frozenset({"care"}))
    expected = Effect.DENY if level is Verification.V0 else Effect.ALLOW
    assert evaluate(STARTER_POLICY, SUPPORT, billing, PolicyContext(level)).effect is expected
    assert evaluate(STARTER_POLICY, care, health, PolicyContext(level)).effect is Effect.DENY
    health_rules = [c for c in STARTER_POLICY.categories if c.name == "health"]
    assert health_rules
    assert not health_rules[0].mirror
