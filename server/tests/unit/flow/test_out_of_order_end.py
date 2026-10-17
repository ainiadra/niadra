"""Batches that arrive out of order: the end of a conversation carries its own time, and the turns at or
before it belong to the ended session wherever they fall in the arrival order. A turn after the end starts
a new session; a retried turn is a duplicate and changes nothing."""

from datetime import datetime, timedelta
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.ingest import IngestStatusRequest
from niadra.domain.tasks import TaskName
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_late_turns import answer, episodes, sessions, turn

CONVERSATION = "wa-late"


def ended(at: datetime, key: str = "end") -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": key,
        "conversation_id": CONVERSATION,
        "occurred_at": at.isoformat(),
    }


async def state(flow: Flow) -> str:
    status = await flow.extract.status(principal(), IngestStatusRequest(conversation_id=CONVERSATION))
    return status.state


def extracted(flow: Flow) -> str:
    return str(flow.llm.calls[-1]["user"])


async def test_a_turn_before_the_end_that_arrives_after_it_closes_again_as_ended(cell: Cell) -> None:
    flow = Flow.build(
        cell, ScriptedLlm(answer("Pediu estorno", "pending"), answer("Estorno pedido", "pending"))
    )
    start = cell.clock.now()
    first = turn("t1", "Minha fatura veio em dobro", start)
    third = turn("t3", "Vou abrir o pedido de estorno", start + timedelta(minutes=2), "ai_agent")
    assert (await flow.send(WHATSAPP_SOURCE, first, third)).errors == []
    assert (await flow.send(WHATSAPP_SOURCE, ended(start + timedelta(minutes=3)))).errors == []
    await flow.drain()
    assert await state(flow) == "ready"
    # The channel delivers the customer's second turn, sent before the end, five minutes later.
    cell.clock.advance(minutes=5)
    second = turn("t2", "Quero o estorno do valor cobrado a mais", start + timedelta(minutes=1))
    assert (await flow.send(WHATSAPP_SOURCE, second)).errors == []
    # No sweep: the recorded end closes it again, and the episode gets a new version.
    await flow.drain()
    [session] = sessions(cell)
    assert (session.close_reason, session.reopened) == ("ended", 1)
    assert session.ended_at == start + timedelta(minutes=3)
    assert await state(flow) == "ready"
    assert [e.version for e in episodes(cell)] == [1, 2]
    assert "Quero o estorno do valor cobrado a mais" in extracted(flow)


async def test_the_end_of_a_conversation_before_any_turn_closes_the_session_its_turns_open(
    cell: Cell,
) -> None:
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    start = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, ended(start + timedelta(minutes=2)))).errors == []
    assert await state(flow) == "unknown"
    cell.clock.advance(minutes=1)
    items = [
        turn("t1", "Minha fatura veio em dobro", start),
        turn("t2", "Vou abrir o pedido de estorno", start + timedelta(minutes=1), "ai_agent"),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    assert [t.payload["reason"] for t in flow.pending(TaskName.CLOSE_SESSION)] == ["ended"]
    await flow.drain()
    [session] = sessions(cell)
    assert (session.close_reason, session.ended_at) == ("ended", start + timedelta(minutes=2))
    assert await state(flow) == "ready"
    assert [e.version for e in episodes(cell)] == [1]


async def test_turns_of_an_early_end_in_reverse_order_all_reach_the_extraction(cell: Cell) -> None:
    flow = Flow.build(
        cell, ScriptedLlm(answer("Pediu estorno", "pending"), answer("Pediu estorno", "pending"))
    )
    start = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, ended(start + timedelta(minutes=3)))).errors == []
    later = turn("t2", "Vou abrir o pedido de estorno", start + timedelta(minutes=2), "ai_agent")
    assert (await flow.send(WHATSAPP_SOURCE, later)).errors == []
    await flow.drain()
    # The first turn, retried by the SDK after an aborted request, arrives last and before the session.
    cell.clock.advance(minutes=2)
    assert (await flow.send(WHATSAPP_SOURCE, turn("t1", "Minha fatura veio em dobro", start))).errors == []
    await flow.drain()
    [session] = sessions(cell)
    assert (session.opened_at, session.close_reason, session.reopened) == (start, "ended", 1)
    assert await state(flow) == "ready"
    assert "Minha fatura veio em dobro" in extracted(flow)


async def test_a_turn_after_the_end_starts_a_new_session(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    start = cell.clock.now()
    items = [
        turn("t1", "Minha fatura veio em dobro", start),
        turn("t2", "Vou abrir o pedido de estorno", start + timedelta(minutes=1), "ai_agent"),
        ended(start + timedelta(minutes=2)),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    # Within 30 minutes of the close and of the last turn, but after the end: a new conversation.
    cell.clock.advance(minutes=10)
    after = turn("t3", "Oi, voltei com outra dúvida", start + timedelta(minutes=3))
    assert (await flow.send(WHATSAPP_SOURCE, after)).errors == []
    first, second = sorted(sessions(cell), key=lambda s: s.opened_at)
    assert (first.close_reason, first.reopened) == ("ended", 0)
    assert (second.closed_at, second.opened_at) == (None, start + timedelta(minutes=3))
    assert await state(flow) == "open"
    assert [e.version for e in episodes(cell)] == [1]


async def test_turns_on_both_sides_of_an_end_split_at_its_time(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    start = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, ended(start + timedelta(minutes=2)))).errors == []
    items = [
        turn("t1", "Minha fatura veio em dobro", start),
        turn("t3", "Oi, voltei com outra dúvida", start + timedelta(minutes=4)),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    first, second = sorted(sessions(cell), key=lambda s: s.opened_at)
    assert (first.close_reason, first.last_event_at) == ("ended", start)
    assert (second.closed_at, second.opened_at) == (None, start + timedelta(minutes=4))
    await flow.drain()
    assert await state(flow) == "open"


async def test_a_retried_turn_is_a_duplicate_and_never_reopens_the_session(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    start = cell.clock.now()
    t1 = turn("t1", "Minha fatura veio em dobro", start)
    t2 = turn("t2", "Vou abrir o pedido de estorno", start + timedelta(minutes=1), "ai_agent")
    assert (await flow.send(WHATSAPP_SOURCE, t1, t2, ended(start + timedelta(minutes=2)))).errors == []
    await flow.drain()
    # The SDK resends the batch whose response it never read: same items, same idempotency keys.
    cell.clock.advance(minutes=5)
    again = await flow.send(WHATSAPP_SOURCE, t1, t2)
    assert (again.accepted, again.duplicates, again.errors) == (0, 2, [])
    assert flow.pending() == []
    [session] = sessions(cell)
    assert (session.close_reason, session.reopened) == ("ended", 0)
    assert await state(flow) == "ready"
    assert [e.version for e in episodes(cell)] == [1]
