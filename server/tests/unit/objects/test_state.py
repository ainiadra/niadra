"""Derived object state and action reconciliation rules (5.8, 18.4)."""

from datetime import timedelta

import pytest

from niadra.domain.objects.state import (
    choose_owner,
    closes_items,
    flagged_for_injection,
    object_of_action,
    operation_of,
    reconciled_status,
)
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import ActionStatus, EventKind, HandleType, SubjectKind
from tests.unit.objects.fakes import T0, at, event, handle

INVOICE = ObjectKey("invoice", "erp", "0823")


def test_owner_is_the_organization_then_the_system_id() -> None:
    phone = handle()
    system_id = handle(HandleType.SYSTEM_ID)
    account = handle(HandleType.ORG_REGISTRY_HMAC, SubjectKind.ACCOUNT)
    assert choose_owner([phone, system_id]) == system_id
    assert choose_owner([phone, system_id, account]) == account
    assert choose_owner([]) is None


def test_operation_comes_from_the_action_the_fields_or_the_canonical_catalog() -> None:
    h = [handle()]
    action = event(EventKind.ACTION, T0, h, action={"operation": "credit"})
    explicit = event(EventKind.SYSTEM_EVENT, T0, h, canonical_type="x.y", fields={"operation": "reschedule"})
    catalog = event(EventKind.SYSTEM_EVENT, T0, h, canonical_type="invoice.credited")
    unknown = event(EventKind.SYSTEM_EVENT, T0, h, canonical_type="invoice.viewed")
    assert [operation_of(e) for e in (action, explicit, catalog, unknown)] == [
        "credit",
        "reschedule",
        "credit",
        None,
    ]


def test_action_object_prefers_the_action_block() -> None:
    h = [handle()]
    e = event(
        EventKind.ACTION,
        T0,
        h,
        objects=[ObjectKey("order", "erp", "1")],
        action={"operation": "credit", "object": {"type": "invoice", "namespace": "erp", "id": "0823"}},
    )
    assert object_of_action(e) == INVOICE


@pytest.mark.parametrize(
    ("declared", "reported", "status"),
    [
        ("R$ 40", "40.00", ActionStatus.CONFIRMED),
        ("R$ 40", "30", ActionStatus.DIVERGENT),
        ("R$ 40,50", "40.5", ActionStatus.CONFIRMED),
        (None, "40", ActionStatus.CONFIRMED),
        ("done", None, ActionStatus.CONFIRMED),
    ],
)
def test_system_of_record_wins_on_the_result(
    declared: str | None, reported: str | None, status: ActionStatus
) -> None:
    fields = {} if reported is None else {"amount": reported}
    system = event(EventKind.SYSTEM_EVENT, T0, [handle()], canonical_type="invoice.credited", fields=fields)
    assert reconciled_status(declared, system) is status


def test_injection_flag_and_trusted_closing() -> None:
    assert flagged_for_injection({"injection": True})
    assert flagged_for_injection({"guard": "injection"})
    assert not flagged_for_injection(None)
    assert closes_items(ActionStatus.CONFIRMED, "credit", frozenset())
    assert closes_items(ActionStatus.DECLARED, "credit", frozenset({"credit"}))
    assert not closes_items(ActionStatus.DECLARED, "credit", frozenset({"refund"}))
    # No list declared: every operation may close, and the closing's own gates decide.
    assert closes_items(ActionStatus.DECLARED, "credit", frozenset())
    assert not closes_items(ActionStatus.QUARANTINED, "credit", frozenset({"credit"}))
    assert not closes_items(ActionStatus.DIVERGENT, "credit", frozenset({"credit"}))


def test_window_constant_is_positive() -> None:
    from niadra.domain.objects.state import CONFIRMATION_WINDOW, within_window

    assert within_window(at(0), at(0) + CONFIRMATION_WINDOW)
    assert not within_window(at(0), at(0) + CONFIRMATION_WINDOW + timedelta(seconds=1))
