"""Receipts streamed to the tenant's SIEM within seconds: OCSF events, signed like webhooks,
retried, from the chain writer's transaction."""

import base64
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.notify import enqueue_event
from niadra.config.models import SourceConfig, WebhookEndpoint
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.domain.notify.webhooks import DeliveryStatus
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Audience, HandleType, ReceiptSurface, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511955554444"
SIEM_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c05e")
SECRET = "whsec_" + base64.b64encode(b"siem-signing-key-0123456789abcd").decode()
URL = "https://siem.example/ingest"


def with_siem(cell: Cell, **overrides: Any) -> None:
    config = cell.config.space(SPACE_ID)
    source = SourceConfig(source_id=SIEM_SOURCE, name="siem", audience=Audience.TRIGGER_TARGET)
    values: dict[str, Any] = {
        "endpoint_id": "siem",
        "url": URL,
        "source_id": SIEM_SOURCE,
        "event_types": ["*"],
        "secret_refs": [f"spaces/{SPACE_ID}/webhook/siem"],
        "format": "ocsf",
        "authorization_ref": f"spaces/{SPACE_ID}/webhook/siem-token",
    }
    endpoint = WebhookEndpoint(**(values | overrides))
    cell.config.put(
        config.model_copy(update={"sources": [*config.sources, source], "webhook_endpoints": [endpoint]})
    )
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook/siem"] = SECRET
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook/siem-token"] = (
        "Splunk 11111111-2222-3333-4444-555555555555"
    )


async def a_customer(flow: Flow) -> None:
    said = {
        "type": "event",
        "idempotency_key": "m1",
        "channel": "whatsapp",
        "conversation_id": "wa-siem",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "oi"},
        "verification_hint": "V1",
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, said)).errors == []
    await flow.drain(only={TaskName.CLOSE_SESSION})


def verify(headers: dict[str, str], body: bytes) -> bool:
    """What the docs tell the SIEM side to run (Standard Webhooks)."""
    key = base64.b64decode(SECRET.removeprefix("whsec_"))
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(c.partition(",")[2] == expected for c in headers["webhook-signature"].split(" "))


@pytest.fixture
def flow(cell: Cell) -> Flow:
    with_siem(cell)
    return Flow.build(cell, ScriptedLlm())


async def test_a_read_reaches_the_siem_as_a_signed_ocsf_event(flow: Flow) -> None:
    await a_customer(flow)
    ask = ContextRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        verification=Verification.V1,
        conversation_id="wa-siem",
    )
    await flow.serve.context(principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID)), ask)
    [receipt] = flow.receipts.sent
    await flow.drain(only={TaskName.DELIVER_WEBHOOK})

    [(url, body, headers)] = flow.cell.egress.sent
    assert url == URL
    assert headers["content-type"] == "application/x-ndjson"
    assert headers["authorization"].startswith("Splunk ")
    assert verify(headers, body)
    [event] = [json.loads(line) for line in body.splitlines()]
    assert (event["class_uid"], event["metadata"]["uid"], event["status"]) == (
        6003,
        str(receipt.receipt_id),
        "Success",
    )
    assert event["unmapped"]["niadra"]["chain"]["seq"] == 1
    assert PHONE not in body.decode()


async def test_a_refused_read_reaches_it_as_a_failure(flow: Flow) -> None:
    caller = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    await flow.serve.record_denial(caller, ReceiptSurface.SEARCH, "scope_missing")
    await flow.drain(only={TaskName.DELIVER_WEBHOOK})
    [(_, body, _)] = flow.cell.egress.sent
    [event] = [json.loads(line) for line in body.splitlines()]
    assert (event["status"], event["status_detail"], event["api"]["operation"]) == (
        "Failure",
        "scope_missing",
        "search",
    )


async def test_a_stream_can_take_only_some_kinds_and_a_failing_siem_is_retried(cell: Cell) -> None:
    with_siem(cell, event_types=["denied"])
    flow = Flow.build(cell, ScriptedLlm())
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    cell.egress.status = 503
    await flow.serve.record_denial(caller, ReceiptSurface.PACK, "forbidden")
    await flow.drain(only={TaskName.DELIVER_WEBHOOK})
    [delivery] = cell.uow.store.module("notify")["deliveries"].values()
    assert (delivery.status, delivery.last_status_code) == (DeliveryStatus.FAILING, 503)
    retries = [
        t for t, _, _ in cell.uow.store.module("pipeline")["scheduled"] if t.name is TaskName.DELIVER_WEBHOOK
    ]
    assert len(retries) == 1


def test_a_siem_stream_takes_receipt_kinds_only() -> None:
    with pytest.raises(ValidationError):
        WebhookEndpoint(
            endpoint_id="siem",
            url=URL,
            source_id=SIEM_SOURCE,
            event_types=["trait.added"],
            secret_refs=["s"],
            format="ocsf",
        )


async def test_webhook_events_never_go_to_a_siem_stream(flow: Flow) -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        sent = await enqueue_event(
            uow, flow.cell.config.space(SPACE_ID), "trait.added", None, {"trait_id": "1"}
        )
    assert sent == []


async def test_a_test_delivery_reaches_the_stream_signed_as_one_ocsf_event(flow: Flow) -> None:
    view = await flow.notify.send_test(SPACE_ID, "siem")
    assert (view.event_type, view.status) == ("webhook.test", "pending")
    await flow.drain(only={TaskName.DELIVER_WEBHOOK})
    [(url, body, headers)] = flow.cell.egress.sent
    assert url == URL
    assert headers["content-type"] == "application/x-ndjson"
    assert verify(headers, body)
    [event] = [json.loads(line) for line in body.splitlines()]
    assert (event["class_uid"], event["api"]["operation"], event["unmapped"]["niadra"]["test"]) == (
        6003,
        "test",
        True,
    )
    [delivery] = flow.cell.uow.store.module("notify")["deliveries"].values()
    assert (delivery.event_type, delivery.status, delivery.attempts) == (
        "webhook.test",
        DeliveryStatus.DELIVERED,
        1,
    )
