"""The `claim-contract` document: what it refuses, each rule with the one change that breaks it."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.config.claims import ClaimContract
from tests.spec import load


def _contract(**change: Any) -> dict[str, Any]:
    contract: dict[str, Any] = load("examples/claim-contract/health-plan-sales.json")
    contract.update(change)
    return contract


def _price(**change: Any) -> dict[str, Any]:
    category = dict(_contract()["categories"][0])
    category.update(change)
    return category


def test_an_empty_contract_checks_nothing() -> None:
    empty = ClaimContract()
    assert empty.categories == []
    assert empty.summary().negative_corpus_version is None


def test_the_summary_keeps_all_but_the_corpus_phrases() -> None:
    contract = ClaimContract.model_validate(_contract())
    summary = contract.summary()
    assert summary.negative_corpus_version == "2026-09-29"
    assert summary.categories == contract.categories
    assert "negative_corpus" not in summary.model_dump()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"categories": [_price(), _price()]}, "its own id"),
        ({"categories": [_price(id="internal_text")]}, "category of a redacted internal text"),
        ({"negative_corpus": None}, "negative corpus"),
        ({"languages": ["pt", "pt"]}, "once"),
        ({"languages": ["fr"]}, "Input should be"),
        ({"outputs": {"immutable": ["chat"], "mutable": ["chat"]}}, "either immutable or mutable"),
        (
            {
                "categories": [
                    _price(actions={"default": "warn", "contexts": {"proposal": "rewrite_if_unequivocal"}})
                ]
            },
            "no mutable output to rewrite",
        ),
        ({"categories": [_price(actions={"default": "rewrite_if_unequivocal"})]}, "never as default"),
        ({"categories": [_price(evidence={"anchor": {"min_match": 0.85, "coverage": "fact"}})]}, "0.9"),
        ({"categories": [_price(evidence={"tool": "a", "tool_any": ["b"]})]}, "one of"),
        (
            {"categories": [_price(detect={"terms": ["x"], "roles": {"price_full": ["cheio"]}})]},
            "needs `classes`",
        ),
        ({"categories": [_price(detect={"classes": ["money"]})]}, "`same_role` needs `detect.roles`"),
        ({"categories": [_price(detect={"terms": ["x"]})]}, "needs `detect.classes`"),
        ({"categories": [_price(actions={"default": "discard_anchor_and_count"})]}, "action of anchors only"),
        ({"categories": [_price(detect={})]}, "detects numbers, terms"),
        (
            {"categories": [_price(detect={"classes": ["money"], "roles": {"price_full": []}})]},
            "between 1 and 50",
        ),
    ],
)
def test_a_contract_that_breaks_a_rule_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ClaimContract.model_validate(_contract(**change))
