"""A claimed identifier never takes over a profile (5.3): an `identify()` that names a customer's e-mail
next to the caller's own number joins the number only provisionally, and a code sent to that number then
proves who holds the number, never that the customer's memory is theirs."""

from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_verification import remembered

VICTIM = {"type": "phone_e164", "value": "+5511966665555"}
VICTIM_EMAIL = {"type": "email", "value": "bia.costa@acme.com.br"}
VICTIM_CRM = {"type": "system_id", "value": "48213", "scope": "crm"}
ATTACKER = {"type": "phone_e164", "value": "+5521977774444"}


def turn(conversation: str, n: int, who: dict[str, Any], text: str, at: str, channel: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": channel,
        "conversation_id": conversation,
        "handles": [who],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        "verification_hint": "V1",
    }


def identify(key: str, handles: list[dict[str, Any]], method: str, at: str) -> dict[str, Any]:
    return {
        "type": "identify",
        "idempotency_key": key,
        "handles": handles,
        "method": method,
        "occurred_at": at,
    }


def otp(conversation: str, who: dict[str, Any], at: str) -> dict[str, Any]:
    return {
        "type": "verify",
        "idempotency_key": f"{conversation}-otp",
        "method": "otp_sms",
        "level": "V3",
        "conversation_id": conversation,
        "handle": who,
        "occurred_at": at,
    }


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """Bia's WhatsApp conversation leaves a contact fact (V1) and a billing fact (V2) on her profile,
    which holds her number, her e-mail and her CRM id."""
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    first = turn("wa-1", 1, VICTIM, "Me chama de Bia, a fatura de agosto veio em dobro", now, "whatsapp")
    first["handles"] = [VICTIM, VICTIM_EMAIL, VICTIM_CRM]
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


async def read(flow: Flow, who: dict[str, Any], conversation: str, level: Verification) -> ContextResponse:
    subject = Handle(type=HandleType(who["type"]), value=who["value"])
    request = ContextRequest(subject=subject, view="voice", verification=level, conversation_id=conversation)
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


async def attacker_call(flow: Flow, conversation: str, *items: dict[str, Any]) -> None:
    now = flow.cell.clock.now().isoformat()
    line = turn(conversation, 1, ATTACKER, "Oi, é sobre a minha fatura", now, "voice")
    assert (await flow.send(VOICE_SOURCE, line, *items)).errors == []
    await flow.drain()


async def claim_then_call(flow: Flow, method: str) -> None:
    """An identify that pulls a brand-new number next to Bia's e-mail, then a call from that number that
    passes an OTP sent to it."""
    now = flow.cell.clock.now().isoformat()
    assert (
        await flow.send(VOICE_SOURCE, identify("claim-1", [ATTACKER, VICTIM_EMAIL], method, now))
    ).errors == []
    await attacker_call(flow, "call-1", otp("call-1", ATTACKER, now))


async def test_a_declared_e_mail_never_lets_an_otp_to_the_callers_number_read_the_profile(flow: Flow) -> None:
    await claim_then_call(flow, "declared")

    stolen = await read(flow, ATTACKER, "call-1", Verification.V3)
    # The number resolves to the profile for continuity, but reads through it stay at V1 until the join
    # is confirmed: the OTP proved the caller holds that number, not that Bia's memory is theirs.
    assert (stolen.verification.effective, stolen.verification.reason) == (
        Verification.V1,
        "unconfirmed_link",
    )
    assert "em dobro" not in (stolen.text or "")

    # Bia herself, proving her own number, still reads her memory in full.
    now = flow.cell.clock.now().isoformat()
    await flow.send(VOICE_SOURCE, turn("call-2", 1, VICTIM, "Oi", now, "voice"), otp("call-2", VICTIM, now))
    own = await read(flow, VICTIM, "call-2", Verification.V3)
    assert own.verification.effective is Verification.V3
    assert "em dobro" in (own.text or "")


async def test_an_explicit_identify_of_a_new_number_is_provisional_too(flow: Flow) -> None:
    await claim_then_call(flow, "explicit_identify")
    stolen = await read(flow, ATTACKER, "call-1", Verification.V3)
    assert (stolen.verification.effective, stolen.verification.reason) == (
        Verification.V1,
        "unconfirmed_link",
    )
    assert "em dobro" not in (stolen.text or "")


async def test_an_explicit_identify_never_merges_the_callers_profile_into_the_customers(flow: Flow) -> None:
    # The caller is known already and proves their own number first, so their profile counts as verified.
    now = flow.cell.clock.now().isoformat()
    await attacker_call(flow, "call-1", otp("call-1", ATTACKER, now))
    flow.cell.clock.advance(minutes=1)
    later = flow.cell.clock.now().isoformat()
    await attacker_call(
        flow, "call-1", identify("claim-2", [ATTACKER, VICTIM_EMAIL], "explicit_identify", later)
    )

    after = await read(flow, ATTACKER, "call-1", Verification.V3)
    assert after.verification.effective is Verification.V3
    assert "em dobro" not in (after.text or "")
    assert "Bia" not in (after.text or "")


async def test_the_companys_systems_confirm_a_declared_number(flow: Flow) -> None:
    await claim_then_call(flow, "declared")
    assert (await read(flow, ATTACKER, "call-1", Verification.V3)).verification.effective is Verification.V1

    # Another OTP or another declaration confirms nothing.
    now = flow.cell.clock.now().isoformat()
    again = identify("claim-9", [ATTACKER, VICTIM_EMAIL], "declared", now)
    assert (await flow.send(VOICE_SOURCE, again, otp("call-1", ATTACKER, now))).errors == []
    assert (await read(flow, ATTACKER, "call-1", Verification.V3)).verification.effective is Verification.V1

    # The CRM says the number is Bia's: the join is confirmed and the proven call reads in full.
    confirm = identify("crm-1", [ATTACKER, VICTIM_CRM], "system_import", now)
    assert (await flow.send(WHATSAPP_SOURCE, confirm)).errors == []
    confirmed = await read(flow, ATTACKER, "call-1", Verification.V3)
    assert confirmed.verification.effective is Verification.V3
    assert "em dobro" in (confirmed.text or "")
