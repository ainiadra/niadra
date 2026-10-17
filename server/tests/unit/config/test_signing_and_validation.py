from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from niadra.config.models import PredicateSpec, TenantSettings, TriggerRuleConfig, WebhookEndpoint
from niadra.config.validation import config_problems, parse_space
from niadra.domain.common.errors import InvalidInput
from niadra.domain.config.signing import canonical_json, content_hash, sign, verify_signed, version_tuple
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, sample_config


def test_canonical_json_ignores_key_order_and_whitespace() -> None:
    assert (
        canonical_json({"b": 1, "a": [1, {"d": "é", "c": None}]})
        == '{"a":[1,{"c":null,"d":"é"}],"b":1}'.encode()
    )
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_signature_covers_the_canonical_form_and_accepts_a_rotated_key() -> None:
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    body = {"cell_id": "c1", "seq": 3}
    signature = sign(body, new)
    assert (
        verify_signed(b'{ "seq": 3,\n "cell_id": "c1" }', signature, [old.public_key(), new.public_key()])
        == body
    )
    with pytest.raises(InvalidInput, match="bad signature"):
        verify_signed(b'{"cell_id": "c1", "seq": 4}', signature, [new.public_key()])
    with pytest.raises(InvalidInput, match="bad signature"):
        verify_signed(b'{"cell_id": "c1", "seq": 3}', signature, [old.public_key()])
    with pytest.raises(InvalidInput, match="JSON"):
        verify_signed(b"{nope", signature, [new.public_key()])


def test_version_strings_compare_numerically() -> None:
    assert version_tuple("1.10.0") > version_tuple("1.9.3")
    with pytest.raises(InvalidInput):
        version_tuple("1.x")


def test_the_sample_configuration_is_valid() -> None:
    assert config_problems(sample_config()) == []


def test_cross_references_between_documents_are_checked() -> None:
    config = sample_config().model_copy(
        update={
            "webhook_endpoints": [
                WebhookEndpoint(
                    endpoint_id="crm",
                    url="https://crm.example.com/h",
                    source_id=uuid4(),
                    event_types=["trigger.fired"],
                    secret_refs=["s1"],
                ),
            ],
            "trigger_rules": [
                TriggerRuleConfig(
                    rule_id="late", condition="promise.overdue", version="1", endpoint_id="nowhere"
                ),
            ],
        }
    )
    problems = config_problems(config)
    assert any("webhook endpoint crm belongs to an unknown source" in p for p in problems)
    assert any("trigger rule late targets unknown endpoint nowhere" in p for p in problems)


def test_a_predicate_names_a_declared_category_or_one_of_the_products_own() -> None:
    config = sample_config()
    predicates = [
        PredicateSpec(name="said_due_date", category="object", observes="due_date"),
        PredicateSpec(name="mood", category="nowhere"),
    ]
    extraction = config.extraction.model_copy(update={"predicates": predicates})
    problems = config_problems(config.model_copy(update={"extraction": extraction}))
    assert "predicate mood uses unknown category nowhere" in problems
    assert not any("said_due_date" in p for p in problems)


def test_duplicate_sources_are_rejected() -> None:
    config = sample_config()
    duplicated = config.model_copy(update={"sources": [*config.sources, config.sources[0]]})
    assert f"duplicate source {WHATSAPP_SOURCE}" in config_problems(duplicated)


def test_parse_space_rejects_another_space_and_malformed_documents_without_echoing_values() -> None:
    document = sample_config().model_dump(mode="json")
    assert parse_space(document, SPACE_ID).space_id == SPACE_ID
    with pytest.raises(InvalidInput, match="another space"):
        parse_space(document, uuid4())
    broken = {**document, "environment": "secret-staging-value"}
    with pytest.raises(InvalidInput) as error:
        parse_space(broken, SPACE_ID)
    assert "environment" in str(error.value)
    assert "secret-staging-value" not in str(error.value)


def test_tenant_retention_covers_only_tunable_classes_in_whole_days() -> None:
    assert TenantSettings(retention_days={"cold_events": 730}).retention_days == {"cold_events": 730}
    with pytest.raises(ValidationError, match="tombstones is not set by the tenant"):
        TenantSettings(retention_days={"tombstones": 30})
    with pytest.raises(ValidationError, match="at least one day"):
        TenantSettings(retention_days={"review_samples": 0})
