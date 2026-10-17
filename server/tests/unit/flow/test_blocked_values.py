"""A test or placeholder value never gathers memory: an event that names nobody but a blocked
value is stored, and belongs to no customer."""

from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.domain.tasks import TaskName
from niadra.testing.cell import WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow

PLACEHOLDER = "+5511999999999"  # "+55 11 99999-9999": the classic QA number, on the fixed list
REAL = "+5511944443333"


def extraction() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Pediu troca de endereço",
            "intent": "request",
            "category": "address",
            "outcome": "resolved",
            "resolution": "Endereço trocado",
            "sentiment": 0.0,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "preferred_name",
                "value": "Pessoa de teste",
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def conversation(name: str, phone: str, at: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "event",
            "idempotency_key": f"{name}-1",
            "channel": "whatsapp",
            "conversation_id": name,
            "handles": [{"type": "phone_e164", "value": phone}],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": "Me chama de Pessoa de teste, quero trocar o endereço"},
            "occurred_at": at,
        },
        {
            "type": "conversation.ended",
            "idempotency_key": f"{name}-end",
            "conversation_id": name,
            "occurred_at": at,
        },
    ]


async def test_an_event_from_a_blocked_value_only_gathers_no_memory(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(extraction(), extraction(), extraction()))
    now = cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, *conversation("qa-1", PLACEHOLDER, now))).errors == []
    assert (await flow.send(WHATSAPP_SOURCE, *conversation("qa-2", PLACEHOLDER, now))).errors == []
    await flow.drain()
    memory = cell.uow.store.module("memory")
    assert not memory.get("facts")
    assert not memory.get("episodes")
    # Reconciliation finds no customer and the compiler is never asked: nothing of what two people
    # typed lands under the fake number.
    assert TaskName.COMPILE not in {t.name for t in flow.ran}

    # A real number in the same space still remembers.
    assert (await flow.send(WHATSAPP_SOURCE, *conversation("real-1", REAL, now))).errors == []
    await flow.drain()
    assert memory.get("facts")
