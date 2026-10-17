"""A turn that arrives after its conversation closed: within 30 minutes of the close, and part of
that activity, it reopens the session, and the episode gets a new version instead of a second episode.
A session closed by an end reopens only for turns at or before the end's time (test_out_of_order_end)."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.testing.cell import WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_system_text import extraction

PHONE = {"type": "phone_e164", "value": "+5511944445555"}


def answer(summary: str, outcome: str) -> dict[str, Any]:
    body = extraction()
    body["episode"] |= {"summary": summary, "outcome": outcome}
    body["facts"] = []
    return body


def turn(key: str, text: str, at: Any, role: str = "customer") -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": key,
        "channel": "whatsapp",
        "conversation_id": "wa-late",
        "handles": [PHONE],
        "speaker": {"role": role},
        "content": {"type": "text", "text": text},
        "occurred_at": at.isoformat(),
    }


def episodes(cell: Cell) -> list[Any]:
    return sorted(
        (e for e, _ in cell.uow.store.module("memory")["episodes"].values()), key=lambda e: e.version
    )


def sessions(cell: Cell) -> list[Any]:
    return [s for _, s in cell.uow.store.module("sessions")["sessions"].values()]


async def _first_conversation(flow: Flow, *, end: bool = True) -> Any:
    clock = flow.cell.clock
    start = clock.now()
    ended = {"type": "conversation.ended", "idempotency_key": "end", "conversation_id": "wa-late",
             "occurred_at": (start + timedelta(minutes=2)).isoformat()}  # fmt: skip
    items = [
        turn("t1", "Minha fatura veio em dobro, quero o estorno", start),
        turn("t2", "Vou verificar e abrir o pedido de estorno", start + timedelta(minutes=1), "ai_agent"),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items, *([ended] if end else []))).errors == []
    if not end:
        clock.advance(minutes=31)
        assert await flow.sessions.sweep_idle(flow.cell.config.spaces()[0].space_id) == 1
    await flow.drain()
    return start


async def test_a_late_turn_reopens_the_session_and_versions_the_episode(cell: Cell) -> None:
    flow = Flow.build(
        cell, ScriptedLlm(answer("Pediu estorno", "pending"), answer("Estorno confirmado", "resolved"))
    )
    start = await _first_conversation(flow, end=False)
    [first] = episodes(cell)
    assert first.version == 1
    # Ten minutes after the close, the channel delivers a turn the customer sent right after the agent's.
    cell.clock.advance(minutes=10)
    late = turn("t3", "Obrigada, recebi a confirmação do estorno", start + timedelta(minutes=3))
    assert (await flow.send(WHATSAPP_SOURCE, late)).errors == []
    [session] = sessions(cell)
    assert (session.closed_at, session.reopened) == (None, 1)
    cell.clock.advance(minutes=30)
    space = cell.config.spaces()[0].space_id
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    versions = episodes(cell)
    assert [e.version for e in versions] == [1, 2]
    assert {e.episode_id for e in versions} == {first.episode_id}
    assert versions[-1].outcome == "resolved"


async def test_a_turn_long_after_the_close_starts_a_new_session(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    await _first_conversation(flow)
    cell.clock.advance(minutes=45)
    assert (await flow.send(WHATSAPP_SOURCE, turn("t9", "Oi de novo", cell.clock.now()))).errors == []
    assert sorted(s.reopened for s in sessions(cell)) == [0, 0]
    assert len(sessions(cell)) == 2


async def test_a_conversation_nobody_ends_reaches_memory_once_it_goes_quiet(cell: Cell) -> None:
    """juridico-zero, 07/10/2026: the firm's assistant never ended its chats, so what the lawyer promised
    reached the client's agent only when the session closed on 30 minutes of inactivity, 32 minutes after his
    last turn. A conversation quiet for a few minutes closes and is extracted; a turn of the same activity
    reopens it and extracts it again."""
    flow = Flow.build(
        cell, ScriptedLlm(answer("Pediu estorno", "pending"), answer("Estorno confirmado", "resolved"))
    )
    space = cell.config.spaces()[0].space_id
    start = cell.clock.now()
    items = [
        turn("q1", "Minha fatura veio em dobro, quero o estorno", start),
        turn("q2", "Vou abrir o pedido de estorno hoje", start + timedelta(minutes=1), "ai_agent"),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    cell.clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    [first] = episodes(cell)
    assert first.summary == "Pediu estorno"
    # Ten minutes later the customer answers: the same activity, so the same session, extracted again.
    cell.clock.advance(minutes=10)
    later = turn("q3", "Obrigada, recebi a confirmação", cell.clock.now())
    assert (await flow.send(WHATSAPP_SOURCE, later)).errors == []
    [session] = sessions(cell)
    assert (session.closed_at, session.reopened) == (None, 1)
    # Reopened once, it waits twice the quiet before it closes again.
    cell.clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(space) == 0
    cell.clock.advance(minutes=3)
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    assert [e.version for e in episodes(cell)] == [1, 2]
    assert episodes(cell)[-1].outcome == "resolved"


async def test_a_reply_within_the_channels_inactivity_reopens_a_session_closed_on_quiet(cell: Cell) -> None:
    """E-mail's inactivity is four hours: a session closes once quiet, and a reply two hours later is still
    the same activity, so it reopens the session rather than starting a second one."""
    flow = Flow.build(
        cell, ScriptedLlm(answer("Pediu estorno", "pending"), answer("Estorno confirmado", "resolved"))
    )
    space = cell.config.spaces()[0].space_id
    start = cell.clock.now()
    mail = {
        **turn("m1", "Minha fatura veio em dobro", start),
        "channel": "email",
        "conversation_id": "mail-1",
    }
    assert (await flow.send(WHATSAPP_SOURCE, mail)).errors == []
    cell.clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    cell.clock.advance(hours=2)
    reply = {**turn("m2", "Obrigada", cell.clock.now()), "channel": "email", "conversation_id": "mail-1"}
    assert (await flow.send(WHATSAPP_SOURCE, reply)).errors == []
    [session] = sessions(cell)
    assert (session.closed_at, session.reopened) == (None, 1)


async def test_a_slow_conversation_is_extracted_a_few_times_not_once_per_pause(cell: Cell) -> None:
    """A WhatsApp exchange answered every five minutes for half an hour: closing on every pause longer than
    the quiet minutes extracted the whole conversation again after each answer, six model calls for one
    activity. Each reopen doubles the quiet the session waits for (never past the channel's inactivity), so
    the first answer still reaches memory within minutes and the rest of the activity once."""
    flow = Flow.build(cell, ScriptedLlm(*(answer(f"Passo {n}", "pending") for n in range(8))))
    space = cell.config.spaces()[0].space_id
    start = cell.clock.now()
    said = {0: "customer", 5: "ai_agent", 10: "customer", 15: "ai_agent", 20: "customer", 25: "ai_agent"}
    for minute in range(45):
        if minute in said:
            item = turn(
                f"s{minute}", f"Mensagem do minuto {minute}", start + timedelta(minutes=minute), said[minute]
            )
            assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
        await flow.sessions.sweep_idle(space)
        await flow.drain()
        cell.clock.advance(minutes=1)
    [session] = sessions(cell)
    assert session.closed_at is not None
    assert len(episodes(cell)) <= 2, [e.version for e in episodes(cell)]


async def test_a_close_the_sweeper_asked_for_after_a_turn_came_in_never_leaves_the_session_open_forever(
    cell: Cell,
) -> None:
    """The sweeper reads a session as quiet, a turn commits, and the sweeper's request to close lands after
    it: the close task finds the session busy and stops, and the request it left made the sweeper skip the
    session from then on, so with no later turn it never closed and its memory was never extracted."""
    flow = Flow.build(cell, ScriptedLlm(answer("Pediu estorno", "pending")))
    space = cell.config.spaces()[0].space_id
    start = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, turn("r1", "Quero o estorno", start))).errors == []
    cell.clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(space) == 1
    # The turn that raced the sweeper: its time is on the session, with the sweeper's request left on it.
    store = cell.uow.store.module("sessions")["sessions"]
    [(key, (owner, session))] = list(store.items())
    store[key] = (owner, replace(session, last_event_at=cell.clock.now()))
    await flow.drain()
    [session] = sessions(cell)
    assert session.closed_at is None
    cell.clock.advance(minutes=4)
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    [session] = sessions(cell)
    assert session.closed_at is not None
    assert len(episodes(cell)) == 1
