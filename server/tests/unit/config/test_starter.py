import subprocess
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from niadra.config.models import MappingRule, SpaceConfig
from niadra.config.starter import STARTER_ANCHORS, STARTER_POLICY, STARTER_VERSION
from niadra.domain.policy import ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.vocabulary import Audience, Verification


def test_a_new_space_extracts_with_the_starter_catalog() -> None:
    config = SpaceConfig(space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox")
    assert config.extraction.version == STARTER_VERSION
    assert config.extraction.predicates


def test_every_starter_predicate_has_a_policy_category() -> None:
    config = SpaceConfig(space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox")
    categories = {c.name for c in STARTER_POLICY.categories}
    assert {p.category for p in config.extraction.predicates} <= categories


def test_spaces_do_not_share_one_mutable_starter() -> None:
    a = SpaceConfig(space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox")
    b = SpaceConfig(space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox")
    assert a.extraction is not b.extraction


def test_out_of_the_box_billing_never_reads_health_and_technical_matters() -> None:
    # The starter policy releases by purpose, with no tenant setup.
    policy = SpaceConfig(
        space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox"
    ).policy
    billing = Reader(Audience.INTERNAL_AGENT, frozenset({"billing"}))
    support = Reader(Audience.CUSTOMER_AGENT, frozenset({"support"}))
    care = Reader(Audience.CUSTOMER_AGENT, frozenset({"care"}))
    top = PolicyContext(Verification.V4)
    for kind in ("fact", "episode"):
        assert evaluate(policy, billing, ItemAttrs(kind, "health"), top).rule == "purpose"
        assert evaluate(policy, billing, ItemAttrs(kind, "technical"), top).rule == "purpose"
        assert evaluate(policy, billing, ItemAttrs(kind, "billing"), top).included
        assert evaluate(policy, support, ItemAttrs(kind, "health"), top).rule == "purpose"
        assert evaluate(policy, care, ItemAttrs(kind, "health"), PolicyContext(Verification.V3)).included
        assert not evaluate(policy, care, ItemAttrs(kind, "health"), PolicyContext(Verification.V2)).included
    analyst = Reader(Audience.ANALYST, frozenset({"care"}))
    assert not evaluate(policy, analyst, ItemAttrs("episode", "health"), top).included


def test_no_starter_predicate_collects_health_data_but_conversations_about_it_are_filed_as_health() -> None:
    config = SpaceConfig(space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox")
    assert all(p.category != "health" for p in config.extraction.predicates)
    assert "health" in {c.normalized_as for c in config.extraction.episode_categories}


def test_the_starter_anchors_name_the_events_spaces_send_most() -> None:
    lexicon = SpaceConfig(
        space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox"
    ).anchor_lexicon()
    for event_type in (
        "inspection.completed",
        "order.delivered",
        "shipment.delivered",
        "pickup.completed",
        "card.replaced",
        "card.reissued",
        "field_visit.completed",
        "installation.completed",
        "invoice.issued",
        "payment.received",
    ):
        assert any(entry.covers(event_type) for entry in lexicon.entries), event_type
    for entry in STARTER_ANCHORS:
        assert set(entry.terms) == {"pt", "en", "es"}, entry.name


def test_anchor_words_on_a_mapping_rule_need_a_fixed_event_type() -> None:
    with pytest.raises(ValidationError):
        MappingRule(
            match="true",
            canonical_type_expr="type",
            idempotency_key="id",
            occurred_at="at",
            anchor_terms=["vistoria"],
        )


@pytest.mark.parametrize("module", ["niadra.config.starter", "niadra.config.models"])
def test_the_starter_and_the_models_import_in_either_order(module: str) -> None:
    done = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr.decode()
