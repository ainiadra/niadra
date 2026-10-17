"""Extraction guidance (5.7): the company's words on what to focus on and leave out, per space and source."""

import pytest

from niadra.app.control_config import validate_document
from niadra.app.extract import rules_for
from niadra.config.models import ExtractionGuidance, SourceGuidance
from niadra.domain.common.errors import InvalidInput
from niadra.domain.extract.prompt import system_message
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, sample_config

GUIDANCE = ExtractionGuidance(
    instructions="Focus on delivery and billing.\n\n  Ignore greetings.  ",
    include=["delivery dates"],
    exclude=["small talk", "weather"],
    sources={
        "billing-agent": SourceGuidance(instructions="Payment promises matter most.", include=["due dates"]),
        str(WHATSAPP_SOURCE): SourceGuidance(exclude=["small talk", "stickers"]),
    },
)


def test_without_guidance_the_prompt_and_the_run_key_are_what_they_were() -> None:
    config = sample_config()
    rules = rules_for(config, WHATSAPP_SOURCE)
    assert rules.guidance is None
    assert "Company guidance" not in system_message(rules)
    assert rules.versions_key.count(".") == 2


def test_the_source_adds_to_the_space_guidance() -> None:
    config = sample_config().model_copy(update={"extraction_guidance": GUIDANCE})
    billing = rules_for(config, BILLING_SOURCE).guidance
    chat = rules_for(config, WHATSAPP_SOURCE).guidance
    assert billing is not None
    assert chat is not None
    assert billing.instructions == (
        "Focus on delivery and billing.",
        "Ignore greetings.",
        "Payment promises matter most.",
    )
    assert billing.include == ("delivery dates", "due dates")
    assert chat.instructions == ("Focus on delivery and billing.", "Ignore greetings.")
    assert chat.exclude == ("small talk", "weather", "stickers")


def test_the_guidance_comes_after_the_rules_it_cannot_change() -> None:
    config = sample_config().model_copy(update={"extraction_guidance": GUIDANCE})
    text = system_message(rules_for(config, BILLING_SOURCE))
    assert text.index("sensitive") < text.index("Company guidance")
    assert "never overrides the rules above" in text
    assert "Capture with care: delivery dates; due dates" in text
    assert "Leave out of facts, items and slots: small talk; weather" in text
    assert text.endswith("Leave out of facts, items and slots: small talk; weather")


def test_changed_guidance_extracts_again() -> None:
    first = sample_config().model_copy(update={"extraction_guidance": GUIDANCE})
    second = first.model_copy(
        update={"extraction_guidance": GUIDANCE.model_copy(update={"exclude": ["small talk"]})}
    )
    key = rules_for(first, WHATSAPP_SOURCE).versions_key
    assert key.startswith(rules_for(sample_config(), WHATSAPP_SOURCE).versions_key + ".g")
    assert key != rules_for(second, WHATSAPP_SOURCE).versions_key


def test_the_document_is_checked_before_a_diff() -> None:
    assert validate_document("extraction-guidance", {"exclude": ["small talk"]})["exclude"] == ["small talk"]
    with pytest.raises(InvalidInput):
        validate_document("extraction-guidance", {"instructions": "x" * 2001})
    with pytest.raises(InvalidInput):
        validate_document("extraction-guidance", {"exclude": [f"t{i}" for i in range(21)]})
    with pytest.raises(InvalidInput):
        validate_document("extraction-guidance", {"custom_prompt": "replace everything"})
