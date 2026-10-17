"""Verification belongs to the conversation and a number in doubt tells nothing,
from the batch API to `context()`."""

from datetime import timedelta
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511966665555"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
CRM_ID = {"type": "system_id", "value": "48213", "scope": "crm"}


def remembered() -> dict[str, Any]:
    """The first conversation leaves a contact fact (V1) and a billing fact (V2)."""

    def fact(predicate: str, value: str) -> dict[str, Any]:
        return {
            "op": "add",
            "predicate": predicate,
            "value": value,
            "target": None,
            "evidence": ["e1"],
            "valid_at": None,
            "certainty": 0.9,
            "contradicts": [],
            "about": None,
            "object": None,
        }

    return {
        "episode": {
            "summary": "Contestou a fatura",
            "intent": "complaint",
            "category": "invoice",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.2,
        },
        "facts": [fact("preferred_name", "Bia"), fact("disputed_invoice", "fatura de agosto em dobro")],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def said(
    conversation: str, n: int, text: str, at: str, *, channel: str = "voice", **extra: Any
) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": channel,
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}, *extra.pop("handles", [])],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        **extra,
    }


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    first = said("wa-1", 1, "Me chama de Bia, a fatura de agosto veio em dobro", now, channel="whatsapp")
    first["handles"].append(CRM_ID)
    ended = {
        "type": "conversation.ended",
        "idempotency_key": "wa-1-end",
        "conversation_id": "wa-1",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, first, ended)).errors == []
    await flow.drain()
    cell.clock.advance(hours=2)
    return flow


async def read(flow: Flow, conversation: str | None, level: Verification) -> ContextResponse:
    request = ContextRequest(subject=SUBJECT, view="voice", verification=level, conversation_id=conversation)
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


async def call(
    flow: Flow, conversation: str, *, verify_until: str | None = None, verify: bool = False, turn: int = 1
) -> None:
    """An inbound call from the number: the channel makes it plausible (V1); an OTP proves V3."""
    now = flow.cell.clock.now().isoformat()
    items: list[dict[str, Any]] = [
        said(conversation, turn, "Oi, é sobre a minha fatura", now, verification_hint="V1")
    ]
    if verify:
        items.append(
            {
                "type": "verify",
                "idempotency_key": f"{conversation}-otp",
                "method": "otp_sms",
                "level": "V3",
                "conversation_id": conversation,
                "handle": {"type": "phone_e164", "value": PHONE},
                "valid_until": verify_until,
                "occurred_at": now,
            }
        )
    assert (await flow.send(VOICE_SOURCE, *items)).errors == []


async def test_a_later_call_from_the_same_number_starts_over(flow: Flow) -> None:
    await call(flow, "call-1", verify=True)
    proven = await read(flow, "call-1", Verification.V3)
    assert proven.verification.effective is Verification.V3
    assert "em dobro" in (proven.text or "")

    # The next call from the same number, maybe spoofed: the OTP of call-1 proves nothing here.
    flow.cell.clock.advance(hours=1)
    await call(flow, "call-2")
    again = await read(flow, "call-2", Verification.V3)
    assert (again.verification.effective, again.verification.reason) == (Verification.V1, "not_proven")
    assert "em dobro" not in (again.text or "")
    assert "Bia" in (again.text or "")
    assert (again.withheld or 0) >= 1

    # Without a conversation nothing was proven at all.
    anonymous = await read(flow, None, Verification.V3)
    assert anonymous.verification.effective is Verification.V0


async def test_a_proof_stops_counting_when_it_expires(flow: Flow) -> None:
    until = (flow.cell.clock.now() + timedelta(minutes=5)).isoformat()
    await call(flow, "call-1", verify=True, verify_until=until)
    assert (await read(flow, "call-1", Verification.V3)).verification.effective is Verification.V3
    flow.cell.clock.advance(minutes=10)
    late = await read(flow, "call-1", Verification.V3)
    # The conversation keeps one proof: once the OTP expires it proves nothing, until the channel
    # speaks again and makes it plausible (V1) once more.
    assert (late.verification.effective, late.verification.reason) == (Verification.V0, "not_proven")
    await call(flow, "call-1", turn=2)
    assert (await read(flow, "call-1", Verification.V3)).verification.effective is Verification.V1


async def test_a_number_back_after_months_reads_as_a_first_contact_until_reconfirmed(flow: Flow) -> None:
    # Seven months later a message comes from the number: it may be someone else's now.
    flow.cell.clock.advance(days=210)
    await call(flow, "call-new", verify=True)  # the new holder passes an OTP to the number
    stranger = await read(flow, "call-new", Verification.V3)
    # The number starts over in a provisional profile of its own: nothing of Bia's comes back.
    assert "Bia" not in (stranger.text or "")
    assert "retidos" not in (stranger.text or "")  # nothing held back either: there is nothing to hold

    # The company's CRM ties the number to the customer's CRM id again: memory comes back.
    now = flow.cell.clock.now().isoformat()
    identify = {
        "type": "identify",
        "idempotency_key": "crm-reconfirm",
        "handles": [{"type": "phone_e164", "value": PHONE}, CRM_ID],
        "method": "system_import",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, identify)).errors == []
    await flow.drain()  # the provisional profile joins Bia's again, through the merge worker
    known = await read(flow, "call-new", Verification.V3)
    assert "Bia" in (known.text or "")


async def test_a_call_that_proved_nothing_hears_nothing_of_the_whatsapp_conversation(flow: Flow) -> None:
    # What the customer wrote on WhatsApp minutes ago is raw text, not yet memory; a
    # spoofed call gets none of it, only how many lines it missed, and a call that proved V2 gets it.
    quiet = await read(flow, None, Verification.V2)
    now = flow.cell.clock.now().isoformat()
    line = said("wa-2", 1, "Chegou outra cobrança na fatura de setembro", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, line)).errors == []
    flow.cell.clock.advance(minutes=2)

    await call(flow, "call-9")
    spoofed = await read(flow, None, Verification.V2)
    assert (spoofed.verification.effective, spoofed.live) == (Verification.V0, [])
    # The WhatsApp line and the call's own first line, neither read under this conversation.
    assert spoofed.withheld == quiet.withheld + 2

    at = flow.cell.clock.now().isoformat()
    kba = {
        "type": "verify",
        "idempotency_key": "call-9-kba",
        "method": "kba",
        "level": "V2",
        "conversation_id": "call-9",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": at,
    }
    assert (await flow.send(VOICE_SOURCE, kba)).errors == []
    proven = await read(flow, "call-9", Verification.V2)
    assert proven.verification.effective is Verification.V2
    assert [t.text for t in proven.live] == ["Chegou outra cobrança na fatura de setembro"]
