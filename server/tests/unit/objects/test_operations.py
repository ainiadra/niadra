"""The operation vocabulary shared by actions, system events and open items (5.8, 18.4)."""

import pytest

from niadra.domain.objects.operations import (
    canonical_operation,
    canonical_operations,
    operation_of_event_type,
    same_operation,
)


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("credit", "credit"),
        ("invoice.credited", "credit"),
        ("Invoice.Credited", "credit"),
        ("credited", "credit"),
        ("fatura.credited", "credit"),
        ("invoice.credit", "credit"),
        (" Credit ", "credit"),
        ("order.canceled", "cancel"),
        ("subscription.cancelled", "cancel"),
        ("change delivery", "change_delivery"),
        ("order.delivery_changed", "change_delivery"),
        ("send_second_copy", "send_second_copy"),
        ("", None),
        (None, None),
    ],
)
def test_every_spelling_reads_as_the_canonical_operation(name: str | None, canonical: str | None) -> None:
    assert canonical_operation(name) == canonical


def test_only_known_event_types_name_an_operation() -> None:
    assert operation_of_event_type("invoice.credited") == "credit"
    assert operation_of_event_type("visit.rescheduled") == "reschedule"
    assert operation_of_event_type("invoice.issued") is None
    assert operation_of_event_type("invoice.viewed") is None
    assert operation_of_event_type(None) is None


def test_sets_and_pairs_compare_canonically() -> None:
    assert canonical_operations(["invoice.credited", "credit", "Refund", ""]) == {"credit", "refund"}
    assert same_operation("invoice.credited", "credit")
    assert not same_operation("credit", "refund")
    assert not same_operation(None, None)
