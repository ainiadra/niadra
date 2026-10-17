"""The privacy category under the mirror rule: the customer tells the company a
balance, a diagnosis or a document number, and a caller nobody verified (V0) asks for it. Under the starter
policy, with the mirror on, the caller hears none of it and the agent is told to ask for the verification.
A diagnosis stays withheld at V1 too, whatever category the extraction filed it under: the mirror never
hands back health data."""

from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.starter import STARTER_EXTRACTION, STARTER_POLICY
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_bank_dispute_v1 import extraction, fact

PHONE = {"type": "phone_e164", "value": "+5511922221111"}


def case(said: str, summary: str, category: str, facts: list[dict[str, Any]], values: list[str]) -> Any:
    return said, extraction(summary, category, facts, values)


CASES = {
    "balance": case(
        "Meu saldo devedor no cheque especial é R$ 171,61.",
        "Cliente informou o saldo devedor no cheque especial de R$ 171,61.",
        "billing",
        [fact("billing_issue", "saldo devedor no cheque especial de R$ 171,61", "e1")],
        ["amount R$ 171,61"],
    ),
    "diagnosis": case(
        "Para o seguro de vida: meu diagnóstico é asma crônica.",
        "Cliente informou para o seguro de vida que o diagnóstico é asma crônica.",
        "health",
        [],
        [],
    ),
    # The same diagnosis filed under an ordinary category: its words still name health.
    "diagnosis_filed_elsewhere": case(
        "Para o seguro de vida: meu diagnóstico é asma crônica.",
        "Cliente informou para o seguro de vida que o diagnóstico é asma crônica.",
        "account",
        [fact("important_date", "diagnóstico de asma crônica", "e1")],
        [],
    ),
    "document": case(
        "O número do meu documento é 82661763.",
        "Cliente informou o número do documento 82661763.",
        "account",
        [],
        [],
    ),
}
SECRETS = {
    "balance": ("171,61", "171.61"),
    "diagnosis": ("asma",),
    "diagnosis_filed_elsewhere": ("asma",),
    "document": ("82661763",),
}
HEALTH = frozenset({"diagnosis", "diagnosis_filed_elsewhere"})
QUESTIONS = {
    "balance": "Oi, qual é o meu saldo devedor do cheque especial?",
    "diagnosis": "Oi, qual diagnóstico está no meu cadastro do seguro?",
    "diagnosis_filed_elsewhere": "Oi, qual diagnóstico está no meu cadastro do seguro?",
    "document": "Oi, qual número de documento vocês têm cadastrado?",
}


async def told(cell: Cell, name: str) -> Flow:
    config = cell.config.spaces()[0]
    cell.config.put(config.model_copy(update={"policy": STARTER_POLICY, "extraction": STARTER_EXTRACTION}))
    said, answer = CASES[name]
    flow = Flow.build(cell, ScriptedLlm(answer))
    now = cell.clock.now().isoformat()
    turns = [
        (1, "customer", said),
        (2, "ai_agent", "Obrigado, atualizei seu cadastro."),
    ]
    items = [
        {
            "type": "event",
            "idempotency_key": f"app-{n}",
            "channel": "app",
            "conversation_id": "app-1",
            "handles": [PHONE],
            "speaker": {"role": role},
            "content": {"type": "text", "text": text},
            "occurred_at": now,
        }
        for n, role, text in turns
    ]
    ended = {"type": "conversation.ended", "idempotency_key": "app-end", "conversation_id": "app-1"}
    assert (await flow.send(WHATSAPP_SOURCE, *items, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    cell.clock.advance(days=3)
    return flow


async def ask(flow: Flow, name: str, level: Verification) -> str:
    now = flow.cell.clock.now().isoformat()
    conversation = f"call-{level.value}"
    line = {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "voice",
        "conversation_id": conversation,
        "handles": [PHONE],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": QUESTIONS[name]},
        "occurred_at": now,
        "verification_hint": "V1",
    }
    assert (await flow.send(VOICE_SOURCE, line)).errors == []
    request = ContextRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE["value"]),
        view="voice",
        verification=level,
        conversation_id=conversation,
        query=QUESTIONS[name],
    )
    response = await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)
    assert response.verification.effective is level
    return "\n".join(p for p in (response.text, response.slots) if p)


@pytest.mark.parametrize("name", sorted(CASES))
async def test_a_v0_caller_never_hears_what_the_customer_told(cell: Cell, name: str) -> None:
    flow = await told(cell, name)
    said = await ask(flow, name, Verification.V0)
    for secret in SECRETS[name]:
        assert secret not in said
    # The agent is told to ask for a verification only where one releases what is withheld: a diagnosis
    # waits for a purpose this support agent lacks at every level, so nothing asks for one.
    assert ("peça a verificação" in said) is (name not in HEALTH)


@pytest.mark.parametrize("name", ["diagnosis", "diagnosis_filed_elsewhere"])
async def test_the_mirror_never_hands_back_a_diagnosis(cell: Cell, name: str) -> None:
    flow = await told(cell, name)
    said = await ask(flow, name, Verification.V1)
    assert "asma" not in said


async def test_at_v1_the_customer_hears_back_the_balance_they_stated(cell: Cell) -> None:
    # The customer's own words about their balance, back to the attested number: the mirror rule.
    flow = await told(cell, "balance")
    assert "171,61" in await ask(flow, "balance", Verification.V1)
