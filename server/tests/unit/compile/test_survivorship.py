"""A slot's survivorship rule decides the merged profile's value (5.8): a merge leaves one row per
handle and source, and the schema's `survivorship` picks among them."""

from dataclasses import replace
from datetime import timedelta

from niadra.app.compile import slot_rules
from niadra.config.models import ExtractionSchema, SlotSpec
from niadra.domain.compile import CompileInput, Variant
from niadra.domain.compile.candidates import CandidateBuilder
from niadra.domain.compile.templates import Formatter
from niadra.domain.records import ProfileAttr
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, sample_config
from tests.unit.compile.factories import PHONE, T0, WA, marina, settings

OLD_NAME = ProfileAttr(PHONE, "preferred_name", "Mari", BILLING_SOURCE, T0 - timedelta(days=90), 7)
NEW_NAME = ProfileAttr(WA, "preferred_name", "Marina", WHATSAPP_SOURCE, T0 - timedelta(days=1), 1)


def customer_line(rule: str, priority: list[str] | None = None) -> str:
    config = sample_config().model_copy(
        update={
            "extraction": ExtractionSchema(
                version="s1",
                slots=[SlotSpec(name="preferred_name", survivorship=rule, source_priority=priority or [])],
            )
        }
    )
    snapshot = replace(marina(), attrs=(OLD_NAME, NEW_NAME))
    built = CandidateBuilder(
        CompileInput(snapshot, Variant(), settings(slot_rules=slot_rules(config))), Formatter("UTC", "pt-BR")
    ).build()
    return next(c.text for c in built if c.item_id == "attr:0:preferred_name")


def test_latest_keeps_the_most_recent_value() -> None:
    assert customer_line("latest") == "Marina"


def test_most_frequent_keeps_the_value_seen_most_often() -> None:
    assert customer_line("most_frequent") == "Mari"


def test_source_priority_keeps_the_first_source_named_by_name_or_id() -> None:
    assert customer_line("source_priority", ["billing-agent", "whatsapp-bot"]) == "Mari"
    assert customer_line("source_priority", [str(WHATSAPP_SOURCE)]) == "Marina"
    # A name no source has is skipped, not a failure.
    assert customer_line("source_priority", ["gone", "whatsapp-bot"]) == "Marina"
