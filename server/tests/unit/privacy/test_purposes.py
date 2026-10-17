"""The rules of purpose and legal basis per item (13, 6.6), without I/O."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from niadra.app.control_config import build_space_config
from niadra.config.models import Policy, PurposeSettings, PurposeSpec, SpaceConfig
from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.records import Space
from niadra.domain.policy import ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.privacy.purposes import (
    Collected,
    ItemKey,
    ItemKind,
    LegalBasis,
    Objection,
    PurposeTag,
    TagRow,
    after_objection,
    collection_purposes,
    decide_tags,
    expired,
    readers,
    retention,
    tags_from_json,
    tags_to_json,
)
from niadra.domain.vocabulary import Audience, Environment, Verification
from niadra.testing.cell import sample_config

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
RULES = PurposeSettings(
    purposes=[
        PurposeSpec(name="billing", legal_basis=LegalBasis.CONTRACT, retention_days=1825),
        PurposeSpec(name="marketing", legal_basis=LegalBasis.CONSENT, retention_days=180),
        PurposeSpec(name="support", legal_basis=LegalBasis.CONTRACT),
        PurposeSpec(name="fraud", legal_basis=LegalBasis.FRAUD_PREVENTION, retention_days=365),
    ],
    categories={"invoice": ["billing"], "offers": ["marketing"]},
    event_types={"payment.*": ["billing", "fraud"], "chargeback.opened": ["fraud"]},
    default=["support"],
)
BILLING = PurposeTag("billing", "contract")
MARKETING = PurposeTag("marketing", "consent")
SUPPORT = PurposeTag("support", "contract")


def test_the_first_rule_that_names_a_purpose_decides() -> None:
    assert collection_purposes(Collected(ItemKind.FACT, "invoice"), RULES) == {"billing"}
    by_event = Collected(ItemKind.FACT, "contact", ("payment.failed",))
    assert collection_purposes(by_event, RULES) == {"billing", "fraud"}
    by_source = Collected(ItemKind.EPISODE, "general", source_purposes=frozenset({"marketing"}))
    assert collection_purposes(by_source, RULES) == {"marketing"}
    assert collection_purposes(Collected(ItemKind.EPISODE, "general"), RULES) == {"support"}


def test_a_purpose_the_document_does_not_declare_tags_nothing() -> None:
    undeclared = Collected(ItemKind.EPISODE, "general", source_purposes=frozenset({"surveys"}))
    assert collection_purposes(undeclared, RULES) == {"support"}  # the default, not the source's word
    bare = PurposeSettings(purposes=RULES.purposes)
    assert collection_purposes(undeclared, bare) == frozenset()


def test_a_reader_sees_its_own_purpose_and_the_compatible_ones_only() -> None:
    tags = (BILLING,)
    assert readers(tags, {}) == {"billing"}
    assert readers(tags, {"billing": frozenset({"collections"})}) == {"billing", "collections"}
    assert readers(None, {"billing": frozenset({"collections"})}) == frozenset()
    assert readers((), {}) == frozenset()


def test_the_policy_denies_a_reader_of_another_purpose() -> None:
    policy = Policy(version="p", categories=[])
    item = ItemAttrs("fact", "profile", purposes=readers((BILLING,), {}))
    context = PolicyContext(Verification.V1)
    assert evaluate(policy, Reader(Audience.CUSTOMER_AGENT, frozenset({"billing"})), item, context).included
    denied = evaluate(policy, Reader(Audience.CUSTOMER_AGENT, frozenset({"marketing"})), item, context)
    assert not denied.included
    assert denied.rule == "purpose"
    # An agent that declares no purpose reads no item collected for one.
    assert not evaluate(policy, Reader(Audience.CUSTOMER_AGENT), item, context).included


def test_an_item_expires_when_its_last_purpose_does() -> None:
    keep = retention(RULES)
    anchor = NOW - timedelta(days=200)
    assert expired((MARKETING,), anchor, NOW, keep)
    assert not expired((MARKETING, BILLING), anchor, NOW, keep)
    assert expired((MARKETING, BILLING), NOW - timedelta(days=1826), NOW, keep)
    # A purpose without a limit holds the item for good; so does no purpose at all.
    assert not expired((MARKETING, SUPPORT), NOW - timedelta(days=5000), NOW, keep)
    assert not expired(None, NOW - timedelta(days=5000), NOW, keep)


def test_an_objection_narrows_or_removes() -> None:
    assert after_objection((BILLING,), {"marketing"}) == (Objection.UNTOUCHED, (BILLING,))
    assert after_objection((BILLING, MARKETING), {"marketing"}) == (Objection.NARROWED, (BILLING,))
    assert after_objection((MARKETING,), {"marketing"}) == (Objection.REMOVED, None)
    assert after_objection(None, {"marketing"}) == (Objection.UNTOUCHED, None)


def test_tags_keep_the_basis_in_force_when_collected() -> None:
    handle = uuid4()
    row = TagRow(ItemKey(ItemKind.FACT, handle, uuid4()), Collected(ItemKind.FACT, "invoice"))
    [decision] = decide_tags([row], RULES, {})
    assert decision.tags == (BILLING,)
    stored = tags_to_json(decision.tags)
    assert stored == {"billing": "contract"}
    # The document may later say another basis: what was stored is what the item was collected under.
    assert tags_from_json(stored) == (BILLING,)
    assert tags_from_json(None) is None
    assert tags_from_json({}) == ()


def test_a_row_collected_only_for_an_objected_purpose_is_not_kept() -> None:
    handle = uuid4()
    offer = TagRow(ItemKey(ItemKind.FACT, handle, uuid4()), Collected(ItemKind.FACT, "offers"))
    invoice = TagRow(ItemKey(ItemKind.FACT, handle, uuid4()), Collected(ItemKind.FACT, "invoice"))
    decisions = {d.row.key: d.tags for d in decide_tags([offer, invoice], RULES, {handle: {"marketing"}})}
    assert decisions[offer.key] is None
    assert decisions[invoice.key] == (BILLING,)


def test_a_value_takes_its_conversations_purposes() -> None:
    handle, episode = uuid4(), uuid4()
    conversation = TagRow(ItemKey(ItemKind.EPISODE, handle, episode), Collected(ItemKind.EPISODE, "offers"))
    value = TagRow(ItemKey(ItemKind.VALUE, handle, uuid4()), Collected(ItemKind.VALUE, "invoice"), episode)
    decided = {d.row.key.kind: d.tags for d in decide_tags([value, conversation], RULES, {})}
    assert decided[ItemKind.VALUE] == decided[ItemKind.EPISODE] == (MARKETING,)


def test_the_document_names_only_declared_purposes() -> None:
    with pytest.raises(ValidationError, match="undeclared purposes: sales"):
        PurposeSettings(purposes=RULES.purposes, categories={"invoice": ["sales"]})
    with pytest.raises(ValidationError, match="declared twice"):
        PurposeSettings(purposes=[*RULES.purposes, RULES.purposes[0]])


def test_compatible_purposes_must_be_declared() -> None:
    config = sample_config()
    policy = config.policy.model_copy(update={"compatible_purposes": {"billing": ["collections"]}})
    space = _space(config)
    documents = {"policies": policy.model_dump(mode="json"), "purposes": RULES.model_dump(mode="json")}
    with pytest.raises(InvalidInput, match="compatible purposes"):
        build_space_config(space, [], documents)
    documents["policies"] = config.policy.model_copy(
        update={"compatible_purposes": {"billing": ["fraud"]}}
    ).model_dump(mode="json")
    assert build_space_config(space, [], documents).policy.compatible_purposes == {"billing": ["fraud"]}


def _space(config: SpaceConfig) -> Space:
    return Space(
        space_id=config.space_id,
        tenant_id=config.tenant_id,
        project_id=uuid4(),
        environment=Environment.SANDBOX,
        slug="purposes",
        region=config.region,
        cell_id="cell-1",
        created_at=NOW,
    )
