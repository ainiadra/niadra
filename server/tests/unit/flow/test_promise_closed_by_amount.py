"""A promise closed by what was done and how much (17, WP-10): on a call the agent promises to refund the
R$ 41,78 shipping fee of a damaged pan, naming no order; days later the ERP records `order.refunded` for
41.78 on the order. The promise waits for `refund_fee`, the ERP says `refund`: same verb, same amount, so
the pending line leaves the pack whichever arrives first."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.items import Closing, amounts_in, closes, same_family
from niadra.domain.records import ObjectKey, OpenItem
from niadra.domain.vocabulary import ActionStatus, HandleType, OpenItemStatus, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511977776666"
PT = Formatter("America/Sao_Paulo", "pt-BR")
ORDER = {"type": "order", "namespace": "erp", "id": "67133"}


def extraction(operation: str | None, description: str) -> dict[str, Any]:
    """What the extraction model answers for the call: one company promise, on no object."""
    return {
        "episode": {
            "summary": "Cliente relata que a panela chegou com a tampa quebrada. O agente vai solicitar o "
            "reembolso de R$ 41,78 do frete.",
            "intent": "complaint",
            "category": "orders",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.5,
            "matter": "item_damaged",
            "matter_label": "produto danificado",
        },
        "facts": [],
        "open_items": [
            {
                "description": description,
                "owner": "company",
                "due_at": None,
                "evidence": ["e2"],
                "object": None,
                "expected_operation": operation,
                "about": None,
            }
        ],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
        "values": [],
    }


def _at(flow: Flow, days: float = 0) -> str:
    return (flow.cell.clock.now() + timedelta(days=days)).isoformat()


async def promise(flow: Flow, *, days: float = -0.01) -> None:
    """The call, `days` from the clock (in the past: a turn is never ahead of the clock), ended at once."""
    turns = [
        ("customer", "A panela chegou com a tampa quebrada, e isso me deu prejuízo."),
        ("ai_agent", "Peço desculpas. Vou solicitar o reembolso de R$ 41,78 do frete."),
        ("customer", "Obrigado, fico no aguardo."),
    ]
    events: list[dict[str, Any]] = [
        {
            "type": "event",
            "idempotency_key": f"call-{n}",
            "channel": "whatsapp",
            "conversation_id": "call-pan",
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": role},
            "content": {"type": "text", "text": text},
            "occurred_at": _at(flow, days + n / 1440),
        }
        for n, (role, text) in enumerate(turns, start=1)
    ]
    events.append(
        {
            "type": "conversation.ended",
            "idempotency_key": "call-end",
            "conversation_id": "call-pan",
            "occurred_at": _at(flow, days + 5 / 1440),
        }
    )
    assert (await flow.send(WHATSAPP_SOURCE, *events)).errors == []
    await flow.drain()


async def refunded(flow: Flow, amount: str, *, key: str = "erp-67133-refunded") -> None:
    event = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": key,
        "channel": "erp",
        "canonical_type": "order.refunded",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [ORDER],
        "speaker": {"role": "system"},
        "fields": {"amount": amount, "receipt": "76867"},
        "occurred_at": _at(flow),
    }
    assert (await flow.send(BILLING_SOURCE, event)).errors == []
    await flow.drain()


async def items(flow: Flow) -> list[OpenItem]:
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
        assert profile is not None
        return await uow.memory.open_items_for(profile.handle_ids, tuple(OpenItemStatus))


REFUND_FEE = "Solicitar o reembolso de R$ 41,78 do frete."


@pytest.fixture
def flow(cell: Cell) -> Flow:
    return Flow.build(cell, ScriptedLlm(extraction("refund_fee", REFUND_FEE)))


async def test_the_erps_refund_of_the_same_amount_closes_the_promise_days_later(flow: Flow) -> None:
    await promise(flow)
    [item] = await items(flow)
    assert item.status is OpenItemStatus.OPEN
    assert item.object is None
    flow.cell.clock.advance(days=4)  # past the 72 hours an operation alone may close in
    await refunded(flow, "41.78")
    [closed] = await items(flow)
    assert closed.status is OpenItemStatus.RESOLVED
    assert closed.closed_by_action_id is None  # the system of record closed it
    # The pack keeps the promise beside the record, saying it was kept and when: the agent ties the ERP's
    # refund to the shipping fee instead of reading the two as a doubt.
    closed_at = closed.closed_at or flow.cell.clock.now()
    # A date of the year of the latest thing memory holds goes without it (tpl-12).
    this_year = replace(PT, current_year=closed_at.year)
    kept = PT.word("kept_on", date=this_year.date(closed_at))
    assert f"{REFUND_FEE} · {kept}" in await pack(flow, "call-1")
    # A week later it is no longer news.
    flow.cell.clock.advance(days=8)
    await flow.send(BILLING_SOURCE, _note(flow))
    await flow.drain()
    assert REFUND_FEE not in await pack(flow, "call-2")


async def pack(flow: Flow, conversation: str) -> str:
    """What a voice agent reads on a call from the customer's number (V1 from the network)."""
    turn = {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "voice",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Oi, é sobre o meu pedido"},
        "occurred_at": _at(flow),
        "verification_hint": "V1",
    }
    assert (await flow.send(VOICE_SOURCE, turn)).errors == []
    request = ContextRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        view="voice",
        verification=Verification.V1,
        conversation_id=conversation,
    )
    read = await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID)), request)
    return read.text or ""


def _note(flow: Flow) -> dict[str, Any]:
    """Any later event of the customer's, so memory is compiled again at the new time."""
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-67133-delivered",
        "channel": "erp",
        "canonical_type": "order.delivered",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [ORDER],
        "speaker": {"role": "system"},
        "fields": {"status": "delivered"},
        "occurred_at": _at(flow),
    }


async def test_a_refund_recorded_before_the_call_was_extracted_closes_the_promise_on_arrival(
    flow: Flow,
) -> None:
    # The ERP's event reaches memory first; the call, said four days before it, is extracted after.
    await refunded(flow, "41.78")
    await promise(flow, days=-4)
    [born_closed] = await items(flow)
    assert born_closed.status is OpenItemStatus.RESOLVED


async def test_another_amount_leaves_the_promise_open(flow: Flow) -> None:
    await promise(flow)
    flow.cell.clock.advance(days=1)
    await refunded(flow, "189.90")
    [still] = await items(flow)
    assert still.status is OpenItemStatus.OPEN


async def test_a_promise_that_names_no_operation_is_never_closed_by_an_amount(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(extraction(None, REFUND_FEE)))
    await promise(flow)
    flow.cell.clock.advance(days=1)
    await refunded(flow, "41.78")
    [still] = await items(flow)
    assert still.status is OpenItemStatus.OPEN


def test_the_rule_same_verb_same_amount_same_object_when_named_within_thirty_days() -> None:
    said = datetime(2026, 9, 16, tzinfo=UTC)
    item = OpenItem(
        item_id=uuid4(),
        origin_handle_id=uuid4(),
        description=REFUND_FEE,
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=said,
        origin={},
        expected_operation="refund_fee",
    )
    order = ObjectKey("order", "erp", "67133")
    [amount] = amounts_in("R$ 41,78")

    def closing(operation: str, days: float, *, status: ActionStatus = ActionStatus.CONFIRMED) -> Closing:
        return Closing(operation, said + timedelta(days=days), status, order, amount=amount)

    assert closes(item, closing("refund", 9))
    assert not closes(item, closing("refund", 31))
    assert not closes(item, replace(closing("refund", 9), amount=None))
    assert not closes(item, closing("charge", 9))
    # A declared action from a source not trusted for it closes nothing, amount or not.
    assert not closes(item, closing("refund", 1, status=ActionStatus.DECLARED))
    assert not closes(replace(item, object=ObjectKey("order", "erp", "1")), closing("refund", 9))
    assert same_family("refund_fee", "payment.refunded")
    assert not same_family("refund", "credit")


async def test_the_refund_the_system_issued_reads_done_when_the_promise_left_out_its_amount(
    cell: Cell,
) -> None:
    """Benchmark promise_action (09/10/2026, 81% on 30/09 to 20%): the call's promise was written without its
    amount, the billing agent asked for the refund on the dispute and the ERP issued it with a receipt, all
    before the call was extracted, as the region's pipeline runs. The promise stayed open. It keeps the amount
    its turn said and closes by it (the system's line, `test_customer_lines`)."""
    flow = Flow.build(cell, ScriptedLlm(extraction("refund_fee", "Solicitar o reembolso do frete.")))
    dispute = {"type": "dispute", "namespace": "erp", "id": "23805"}
    asked = {
        "type": "event",
        "kind": "action",
        "idempotency_key": "billing-23805",
        "channel": "erp",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [dispute],
        "speaker": {"role": "ai_agent", "id": "billing-agent"},
        "action": {"operation": "refund_fee", "result": "Agente de cobrança pediu reembolso de R$ 41,78."},
        "occurred_at": _at(flow, -3),
    }
    issued = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-23805-refunded",
        "channel": "erp",
        "canonical_type": "fee.refunded",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [dispute],
        "speaker": {"role": "system"},
        "fields": {"amount": "41.78", "receipt": "23058"},
        "occurred_at": _at(flow, -1),
    }
    assert (await flow.send(WHATSAPP_SOURCE, asked)).errors == []
    assert (await flow.send(BILLING_SOURCE, issued)).errors == []
    await flow.drain()
    await promise(flow, days=-4)
    [item] = await items(flow)
    assert item.status is OpenItemStatus.RESOLVED
    assert "R$ 41,78" in item.description
    read = await pack(flow, "call-1")
    assert "Solicitar o reembolso do frete (R$ 41,78) · cumprida em" in read
