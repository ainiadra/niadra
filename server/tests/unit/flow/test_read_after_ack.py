"""The deploy's end-to-end check, in memory, with the write path wired as production wires it: the answer
to a batch of turns hands the store to a worker (5.2), so an agent's read can come before the turn is
stored.

A customer's WhatsApp conversation becomes memory (write, close, extract, publish); the customer writes
again in a new conversation, and the agent reads `context()` at V1 right after sending that turn. The
channel proves V1 for the new conversation, and the read gets it while the turn still waits in the
intake log: the pack says what was extracted, and the history search finds the conversation.

Release 2026.09.28-1513-765c360 failed that check: the read found no proof for the new conversation and
answered V0, with everything withheld, until a worker had stored the turn.
"""

from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, SearchRequest
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511900001234"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


def extracted() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Marina informou que o pedido 4471 chegou com a tampa quebrada e pediu a troca",
            "intent": "complaint",
            "category": None,
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.3,
        },
        "facts": [],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def turn(
    conversation: str, n: int, text: str, at: str, *, speaker: str = "customer", **extra: Any
) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": speaker},
        "direction": "inbound" if speaker == "customer" else "outbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        **extra,
    }


async def remembered(cell: Cell) -> Flow:
    """The e2e's first conversation, stored, closed, extracted and compiled, with the answer handing the
    store of a batch of turns to a worker, as `cell.py` wires it."""
    flow = Flow.build(cell, ScriptedLlm(extracted(), extracted()))
    flow.ingest._workers = cell.bus
    now = cell.clock.now().isoformat()
    conversation = [
        turn("wa-1", 1, "Oi, aqui é a Marina. Meu pedido 4471 chegou com a tampa quebrada.", now),
        turn("wa-1", 2, "Sinto muito, Marina. Posso trocar ou devolver o valor.", now, speaker="ai_agent"),
        turn("wa-1", 3, "Prefiro a troca, mas preciso receber até sexta.", now),
        turn(
            "wa-1",
            4,
            "Combinado: abri a troca do pedido 4471 com entrega até sexta.",
            now,
            speaker="ai_agent",
        ),
        {
            "type": "conversation.ended",
            "idempotency_key": "wa-1-end",
            "conversation_id": "wa-1",
            "occurred_at": now,
        },
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *conversation)).errors == []
    await flow.drain()
    cell.clock.advance(minutes=5)
    return flow


async def hello(flow: Flow, conversation: str) -> None:
    """The customer writes again; only the answer runs, and the store waits for a worker."""
    now = flow.cell.clock.now().isoformat()
    text = "Oi, voltei. Alguma novidade da troca?"
    answer = await flow.send(WHATSAPP_SOURCE, turn(conversation, 1, text, now, verification_hint="V1"))
    assert answer.accepted == 1
    assert flow.pending(TaskName.APPLY_INTAKE), "the turn is stored by a worker, after the answer"


async def read(flow: Flow, conversation: str, source: Any = WHATSAPP_SOURCE) -> ContextResponse:
    request = ContextRequest(subject=SUBJECT, verification=Verification.V1, conversation_id=conversation)
    return await flow.serve.context(principal(source, flow.cell.config.spaces()[0]), request)


async def test_a_read_right_after_the_answer_has_the_proof_the_turn_carries(cell: Cell) -> None:
    flow = await remembered(cell)

    await hello(flow, "wa-2")
    first = await read(flow, "wa-2")

    assert first.verification.effective is Verification.V1
    text = first.text or ""
    assert "4471" in text or "troca" in text.lower()
    agent = principal(WHATSAPP_SOURCE, cell.config.spaces()[0])
    search = SearchRequest(subject=SUBJECT, query="tampa quebrada", verification="V1", conversation_id="wa-2")
    assert (await flow.serve.search(agent, search)).items

    # Once stored, the session holds the proof and nothing is held for the conversation any more.
    await flow.drain(only={TaskName.APPLY_INTAKE})
    assert cell.hot.proofs == {}
    assert (await read(flow, "wa-2")).verification.effective is Verification.V1


async def test_a_held_proof_is_the_conversations_own(cell: Cell) -> None:
    flow = await remembered(cell)

    await hello(flow, "wa-2")

    # Another conversation from the same number proves nothing by the turn of this one.
    assert (await read(flow, "wa-3")).verification.effective is Verification.V0
    # What the agent's own turn says proves nothing: only an inbound customer turn is the channel's proof.
    now = cell.clock.now().isoformat()
    answer = turn("wa-4", 1, "Olá, Marina", now, speaker="ai_agent", verification_hint="V1")
    assert (await flow.send(WHATSAPP_SOURCE, answer)).accepted == 1
    assert (await read(flow, "wa-4")).verification.effective is Verification.V0
    # The name the hold is kept under never carries the conversation's id.
    assert cell.hot.proofs
    assert not any("wa-2" in name for name in cell.hot.proofs)


async def test_a_conversation_that_ended_reads_what_the_database_proves(cell: Cell) -> None:
    flow = await remembered(cell)
    await hello(flow, "wa-2")
    assert (await read(flow, "wa-2")).verification.effective is Verification.V1

    # The end takes the full transaction, which stores the waiting turn first: the hold goes with the
    # store, and once the session closes the conversation proves nothing.
    now = cell.clock.now().isoformat()
    ended = {
        "type": "conversation.ended",
        "idempotency_key": "wa-2-end",
        "conversation_id": "wa-2",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, ended)).errors == []
    assert cell.hot.proofs == {}
    await flow.drain()

    assert (await read(flow, "wa-2")).verification.effective is Verification.V0


async def test_a_repeated_batch_holds_nothing_of_its_own(cell: Cell) -> None:
    flow = await remembered(cell)
    await hello(flow, "wa-2")
    held = {name: dict(fields) for name, fields in cell.hot.proofs.items()}

    now = cell.clock.now().isoformat()
    again = turn("wa-2", 1, "Oi, voltei. Alguma novidade da troca?", now, verification_hint="V1")
    assert (await flow.send(WHATSAPP_SOURCE, again)).duplicates == 1

    assert cell.hot.proofs == held


async def test_without_the_state_store_the_read_waits_for_the_store(cell: Cell) -> None:
    flow = await remembered(cell)
    cell.hot.fail = True
    await hello(flow, "wa-2")

    # Fails closed: nothing more than the database proves, and the stored turn proves V1.
    assert (await read(flow, "wa-2", VOICE_SOURCE)).verification.effective is Verification.V0
    await flow.drain(only={TaskName.APPLY_INTAKE})
    cell.hot.fail = False
    assert (await read(flow, "wa-2")).verification.effective is Verification.V1
