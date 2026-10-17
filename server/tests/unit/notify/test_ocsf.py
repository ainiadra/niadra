"""Receipts as OCSF API Activity events for the tenant's SIEM."""

import json
from datetime import date
from uuid import uuid4

from niadra.domain.audit.chain import StoredReceipt
from niadra.domain.notify.ocsf import hec_event, ndjson, receipt_event, siem_body
from niadra.domain.notify.webhooks import assert_ids_only
from niadra.domain.vocabulary import ReceiptKind, ReceiptSurface
from niadra.testing.cell import WHATSAPP_SOURCE
from tests.unit.audit.conftest import draft


def stored(**fields: object) -> StoredReceipt:
    return StoredReceipt(draft(seq=7, **fields), 0, date(2026, 9, 22), 7, b"\x01" * 32, b"\x02" * 32)


def test_a_read_is_an_api_activity_read_with_its_place_on_the_chain() -> None:
    profile = uuid4()
    event = receipt_event(stored(profile_id=profile, withheld=2, item_ids=("fact:1", "episode:2")), {})
    assert (event["class_uid"], event["activity_id"], event["type_uid"], event["status"]) == (
        6003,
        2,
        600302,
        "Success",
    )
    assert event["actor"] == {"app_uid": str(WHATSAPP_SOURCE), "app_name": "customer_agent"}
    assert event["resources"] == [{"type": "customer_profile", "uid": str(profile)}]
    niadra = event["unmapped"]["niadra"]
    assert (niadra["items"], niadra["withheld"], niadra["chain"]["seq"]) == (2, 2, 7)
    assert niadra["chain"]["hash"] == "02" * 32
    assert_ids_only(event)  # ids and hashes only, as the webhook guard demands


def test_a_refused_read_is_a_failure_with_its_reason() -> None:
    event = receipt_event(stored(kind=ReceiptKind.DENIED, detail={"reason": "scope_missing"}), {})
    assert (event["status_id"], event["status"], event["status_detail"], event["severity_id"]) == (
        2,
        "Failure",
        "scope_missing",
        3,
    )


def test_a_person_in_the_console_is_the_actor_of_an_admin_event() -> None:
    person = uuid4()
    receipt = stored(
        kind=ReceiptKind.ADMIN,
        surface=ReceiptSurface.CONSOLE,
        person_id=person,
        source_id=None,
        detail={"action": "privacy.forget", "target": "erasure:1"},
    )
    event = receipt_event(receipt, {})
    assert event["actor"]["user"] == {"uid": str(person), "type": "User"}
    assert (event["activity_id"], event["api"]["operation"]) == (99, "privacy.forget")


def test_hec_and_ndjson_bodies_are_line_per_event_and_stable() -> None:
    events = [receipt_event(stored(), {"x": "y"}), receipt_event(stored(), {})]
    body, content_type = siem_body("ocsf", events)
    assert content_type == "application/x-ndjson"
    assert [json.loads(line)["class_uid"] for line in body.splitlines()] == [6003, 6003]
    assert body == ndjson(events)
    hec, _ = siem_body("hec", events)
    first = json.loads(hec.splitlines()[0])
    assert (first["sourcetype"], first["event"]["class_uid"]) == ("niadra:ocsf:api_activity", 6003)
    assert first["time"] == events[0]["time"] / 1000
    assert hec_event(events[0])["source"] == "niadra"
