"""The home page's call, end to end (CTX): before Marina says hello at 14:07, the voice agent's
first read already carries what WhatsApp heard at 14:02, that the same failure happened in March, and
the credit the billing agent posted at 14:06. The pinned body carries them, not the per-turn slots:
there is no customer turn yet, and the body stays byte for byte the same for the rest of the call."""

from datetime import UTC, datetime
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.starter import STARTER_EXTRACTION, STARTER_POLICY
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, PrefetchRequest
from niadra.domain.compile.model import slot_budget
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.compile.views import VIEWS
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import BILLING_SOURCE, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511977776666"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
# Her record id in the company's customer base. The number was silent for more than 180 days, so without
# a record id on the WhatsApp conversation it would be taken as possibly recycled and March left apart (5.3).
CRM_ID = {"type": "system_id", "value": "48213", "scope": "crm"}
MARCH = datetime(2026, 3, 12, 17, 20, tzinfo=UTC)  # 14:20 in São Paulo
WHATSAPP = datetime(2026, 9, 22, 17, 2, tzinfo=UTC)  # 14:02


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


def extraction(
    summary: str,
    outcome: str,
    resolution: str | None,
    pending: str | None,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "complaint",
            "category": "technical",
            "outcome": outcome,
            "resolution": resolution,
            "sentiment": -0.5,
        },
        "facts": facts,
        "open_items": (
            [
                {
                    "description": pending,
                    "owner": "company",
                    "due_at": "2026-09-23T15:00:00+00:00",
                    "evidence": ["e1"],
                    "object": None,
                    "expected_operation": None,
                    "about": None,
                }
            ]
            if pending
            else []
        ),
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


# What the extraction model writes for sessions like these (over dataset v2: summaries
# of two sentences, 104 characters at the median and 150 at the 90th percentile; a resolution sentence; a fact
# or two per session), so the voice body is as full as it is in production. On the code before, the credit of
# 14:06 did not fit the body and came back only as a slot once the customer mentioned it.
MARCH_CALL = extraction(
    "Marina relatou que a visita técnica agendada para a manhã não aconteceu. O agente lançou um crédito na "
    "fatura.",
    "resolved",
    "Crédito de R$ 40 lançado na fatura pela visita perdida.",
    None,
    [fact("product_or_service_used", "Plano Família")],
)
WHATSAPP_CHAT = extraction(
    "Marina disse que o técnico não apareceu hoje de manhã e que vai ligar. O agente registrou a reclamação.",
    "unresolved",
    None,
    "Remarcar a visita técnica que não aconteceu hoje de manhã, com prioridade.",
    [
        fact("technical_issue", "O técnico não apareceu para a visita prometida para hoje de manhã."),
        fact("preferred_channel", "whatsapp"),
    ],
)


def starter_space(cell: Cell) -> None:
    """A new space, as the home page's example: the starter policy and extraction."""
    config = cell.config.spaces()[0]
    cell.config.put(config.model_copy(update={"policy": STARTER_POLICY, "extraction": STARTER_EXTRACTION}))


def said(source_channel: str, conversation: str, n: int, text: str, at: datetime) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": source_channel,
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}, CRM_ID],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at.isoformat(),
    }


def ended(conversation: str, at: datetime) -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at.isoformat(),
    }


def credit(at: datetime) -> dict[str, Any]:
    return {
        "type": "event",
        "kind": "action",
        "idempotency_key": "billing-credit-0922",
        "channel": "erp",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0823"}],
        "speaker": {"role": "ai_agent", "id": "billing-agent"},
        "action": {
            "operation": "credit",
            "result": "crédito de R$ 40 na fatura de agosto",
            "purpose": "billing",
        },
        "occurred_at": at.isoformat(),
    }


def attestation(at: datetime) -> dict[str, Any]:
    return {
        "type": "verify",
        "idempotency_key": "call-0922-attestation",
        "method": "network_attestation",
        "level": "V1",
        "conversation_id": "call-0922",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": at.isoformat(),
    }


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """Marina's memory as the cell holds it at 14:07 on 22/09/2026."""
    starter_space(cell)
    flow = Flow.build(cell, ScriptedLlm(MARCH_CALL, WHATSAPP_CHAT))
    clock = cell.clock
    clock.at = MARCH
    call = said("voice", "call-0312", 1, "O técnico não veio, fiquei a manhã inteira esperando", clock.now())
    reply = said("voice", "call-0312", 2, "Sinto muito. Lancei um crédito na sua fatura.", clock.now())
    thanks = said("voice", "call-0312", 3, "Obrigada.", clock.now())
    reply = {**reply, "speaker": {"role": "ai_agent"}, "direction": "outbound"}
    clock.advance(minutes=9)
    assert (await flow.send(VOICE_SOURCE, call, reply, thanks, ended("call-0312", clock.now()))).errors == []
    await flow.drain()

    # 14:02 on WhatsApp; the WhatsApp agent closes the conversation at 14:03.
    clock.at = WHATSAPP
    message = said("whatsapp", "wa-0922", 1, "O técnico não apareceu. Vou ligar aí.", clock.now())
    clock.advance(minutes=1)
    assert (await flow.send(WHATSAPP_SOURCE, message, ended("wa-0922", clock.now()))).errors == []
    await flow.drain()

    # 14:06: the billing agent posts the credit on the August bill.
    clock.advance(minutes=3)
    assert (await flow.send(BILLING_SOURCE, credit(clock.now()))).errors == []
    await flow.drain()

    # 14:07: the call rings. The voice guide's step 1 registers the operator's attestation; nobody spoke.
    clock.advance(minutes=1)
    assert (await flow.send(VOICE_SOURCE, attestation(clock.now()))).errors == []
    await flow.drain()
    return flow


def request(query: str | None = None) -> ContextRequest:
    return ContextRequest(
        subject=SUBJECT, view="voice", verification=Verification.V1, conversation_id="call-0922", query=query
    )


async def read(flow: Flow, query: str | None = None) -> ContextResponse:
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request(query))


async def test_before_hello_the_voice_agent_knows_what_just_happened_on_other_channels(flow: Flow) -> None:
    first = await read(flow)
    text = first.text or ""
    assert first.verification.effective is Verification.V1
    assert first.slots is None  # no customer turn: everything below is the pinned body
    # 14:02 on WhatsApp, and that it is still open.
    assert "o técnico não apareceu" in text.lower()  # a short conversation is told by its own turns
    assert "Remarcar a visita técnica" in text
    # The same failure in March, and how it was settled then.
    assert "já ocorreu antes: 12/03" in text
    assert "Crédito de R$ 40 lançado" in text
    # 14:06: the billing agent's credit.
    assert "crédito de R$ 40 na fatura de agosto" in text
    assert "22/09 14:06" in text
    # Within the voice view's budget, with room left for the per-turn slots.
    assert estimate_tokens(text) + slot_budget("voice") <= VIEWS["voice"].budget


async def test_the_body_stays_the_same_bytes_for_the_rest_of_the_call(flow: Flow) -> None:
    first = await read(flow)
    turn = await read(flow, "Da outra vez vocês me deram um crédito")
    assert turn.text == first.text
    assert turn.etag == first.etag
    # What the body carries never comes back as a slot.
    slots = turn.slots or ""
    assert "o técnico não apareceu hoje" not in slots
    assert "fatura de agosto" not in slots


async def test_a_prefetch_at_the_start_of_the_call_serves_the_same_body(flow: Flow) -> None:
    caller = principal(VOICE_SOURCE, flow.cell.config.spaces()[0])
    partial = PrefetchRequest(
        subject=SUBJECT,
        view="voice",
        verification=Verification.V1,
        conversation_id="call-0922",
        query="O técnico de novo",
    )
    assert flow.serve.prefetch(caller, partial)
    await flow.serve.prefetches.drain()
    warm = await read(flow, "O técnico de novo")
    plain = await read(flow)
    assert warm.text == plain.text
    assert "o técnico não apareceu" in (warm.text or "").lower()
    assert "crédito de R$ 40 na fatura de agosto" in (warm.text or "")


async def test_a_whatsapp_conversation_still_open_reaches_the_call_live(cell: Cell) -> None:
    # The WhatsApp agent never said the conversation ended, so at 14:07 nothing of it is in memory yet;
    # the billing agent's credit at 14:06 made the pack newer than the 14:02 message all the same.
    starter_space(cell)
    flow = Flow.build(cell, ScriptedLlm())
    clock = cell.clock
    clock.at = WHATSAPP
    message = said("whatsapp", "wa-open", 1, "O técnico não apareceu. Vou ligar aí.", clock.now())
    assert (await flow.send(WHATSAPP_SOURCE, message)).errors == []
    await flow.drain()
    clock.advance(minutes=4)
    assert (await flow.send(BILLING_SOURCE, credit(clock.now()))).errors == []
    await flow.drain()
    clock.advance(minutes=1)
    assert (await flow.send(VOICE_SOURCE, attestation(clock.now()))).errors == []
    await flow.drain()

    first = await read(flow)
    assert "crédito de R$ 40 na fatura de agosto" in (first.text or "")
    assert [(turn.channel, turn.text) for turn in first.live] == [
        ("whatsapp", "O técnico não apareceu. Vou ligar aí.")
    ]  # the credit is in the pack, so it is not repeated here
