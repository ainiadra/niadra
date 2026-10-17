"""A conversation already pinned keeps its body, and still learns (5.11, 2.4): what memory extracted after
the pin reaches the agent through `delta` and the per-turn slots, and the customer's retrieval cache is the
current generation's, never the pin's."""

from datetime import UTC, datetime
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.compile import profile_target
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511955553333"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
START = datetime(2026, 9, 27, 4, 40, tzinfo=UTC)


def extraction(summary: str, pending: str | None = None) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "complaint",
            "category": "technical_visit",
            "outcome": "unresolved",
            "resolution": None,
            "sentiment": -0.3,
        },
        "facts": [],
        "open_items": (
            [
                {
                    "description": pending,
                    "owner": "company",
                    "due_at": None,
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


def said(conversation: str, text: str, at: datetime, *, channel: str = "whatsapp") -> list[dict[str, Any]]:
    turn = {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": channel,
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at.isoformat(),
    }
    ended = {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at.isoformat(),
    }
    return [turn, ended]


async def read(flow: Flow, query: str | None = None, *, delta: bool = False) -> ContextResponse:
    request = ContextRequest(
        subject=SUBJECT,
        view="voice",
        verification=Verification.V1,
        conversation_id="call-1",
        query=query,
        delta=delta,
    )
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(
        cell,
        ScriptedLlm(
            extraction("A internet caiu de novo ontem à noite"),
            extraction("Pediu um roteador novo", pending="Envio de roteador novo"),
        ),
    )
    cell.clock.at = START
    assert (
        await flow.send(WHATSAPP_SOURCE, *said("wa-1", "A internet caiu de novo ontem", START))
    ).errors == []
    await flow.drain()
    return flow


async def generation(flow: Flow, profile_id: Any) -> int:
    async with flow.cell.uow(SPACE_ID) as uow:
        return await uow.compile.current_generation(profile_target(profile_id))


async def test_a_pinned_conversation_learns_what_was_extracted_after_the_pin(flow: Flow) -> None:
    clock = flow.cell.clock
    clock.advance(minutes=5)
    call = {
        "type": "verify",
        "idempotency_key": "call-1-attestation",
        "method": "network_attestation",
        "level": "V1",
        "conversation_id": "call-1",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": clock.now().isoformat(),
    }
    assert (await flow.send(VOICE_SOURCE, call)).errors == []
    await flow.drain()
    pinned = await read(flow)
    assert "roteador" not in (pinned.text or "")
    profile_id = flow.receipts.sent[-1].profile_id
    assert profile_id is not None

    # 04:55: another conversation leaves the open item while the call goes on.
    clock.advance(minutes=10)
    turns = said("wa-2", "Preciso de um roteador novo, o meu queimou", clock.now())
    assert (await flow.send(WHATSAPP_SOURCE, *turns)).errors == []
    await flow.drain()
    current = await generation(flow, profile_id)
    assert current > pinned_generation(pinned)
    # T0 has no pack of this reader's variant at the current generation: evicted, or a variant the
    # compilation did not prepare. A new conversation would take the stored pack or compile one.
    for key in [k for k in flow.cell.cache.values if k.startswith("t0:")]:
        del flow.cell.cache.values[key]

    clock.advance(minutes=1)
    asked = await read(flow, "e o roteador?")
    assert asked.text == pinned.text  # the pinned body keeps its bytes
    assert "Envio de roteador novo" in (asked.slots or "")
    # Asked with `delta`, the new item comes as the delta, and the slots do not repeat it.
    changed = await read(flow, "e o roteador?", delta=True)
    assert changed.text == pinned.text
    assert "Envio de roteador novo" in (changed.delta or "")
    # The customer's retrieval cache is the current generation's, not the pin's.
    await flow.serve.retrieval.drain()
    held = flow.serve.retrieval.cache.peek(SPACE_ID, profile_id)
    assert held is not None
    assert held.generation == current


def pinned_generation(response: ContextResponse) -> int:
    return int(response.version.removeprefix("g"))
