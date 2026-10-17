"""The question sets the slots' budget (17, 6.5): a voice turn that asks for a value, a count or a day gets
up to 100 tokens of slots, made of atoms; a turn of plain conversation keeps its 60. The rule is written,
not learned, so the same turn over the same memory is the same bytes, and the receipt says which rule set
the budget."""

import json
from dataclasses import replace

from niadra.contracts.context import ContextRequest
from niadra.domain.compile.model import QUESTION_SLOT_BUDGETS, MemorySnapshot, slot_budget
from niadra.domain.compile.views import learned_slot_budget
from niadra.domain.serve.slots import MAX_SLOTS, QUESTION_MAX_SLOTS, asks_value, max_slots, slot_budget_rule
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import ReceiptSurface, Verification
from niadra.testing.cell import Cell, principal, sample_config
from tests.unit.compile.factories import episode, marina
from tests.unit.serve.harness import SUBJECT, Harness, default_world

AGENT = principal()


def test_a_turn_that_asks_for_a_value_a_count_or_a_day_is_a_question() -> None:
    for asked in (
        "Qual protocolo vocês me passaram?",
        "What is the tracking number?",
        "Quantas vezes eu já reclamei disso?",
        "Até que dia eu disse que precisava?",
        "É sobre o 45778-204",
    ):
        assert asks_value(analyze(asked)), asked
        assert slot_budget_rule(analyze(asked)) == "question"
    for said in ("O técnico de novo, que coisa", "Bom dia, tudo bem?", "Preciso de ajuda com a internet"):
        assert not asks_value(analyze(said)), said
        assert slot_budget_rule(analyze(said)) == "turn"


def test_voice_questions_get_the_larger_budget_and_every_other_view_keeps_its_own() -> None:
    assert slot_budget("voice") == 60
    assert slot_budget("voice", question=True) == QUESTION_SLOT_BUDGETS["voice"] == 100
    assert slot_budget("chat", question=True) == slot_budget("chat") == 120
    assert slot_budget("brief", question=True) == 0
    # A learned factor scales the budget of the turn.
    assert learned_slot_budget("voice", 0.5, question=True) == 50
    question, plain = analyze("Qual protocolo?"), analyze("O técnico de novo")
    assert max_slots("voice", question) == QUESTION_MAX_SLOTS["voice"] == 3
    assert max_slots("voice", plain) == MAX_SLOTS["voice"] == 2
    assert max_slots("chat", question) == MAX_SLOTS["chat"]


def long_history() -> MemorySnapshot:
    """Conversations shaped like the extraction model's: the one asked about is months old, behind two newer
    ones."""
    return replace(
        marina(),
        episodes=(
            episode(
                1,
                "technical_visit",
                days_ago=0.2,
                summary="Cliente perguntou se a apólice cobre carro cortesia; o agente respondeu que a "
                "cobertura é por sete dias, cláusula 91511.",
            ),
            episode(
                2,
                "technical_visit",
                days_ago=39,
                channel="voice",
                summary="Cliente relata que o guincho não chegou no horário e pede que a ocorrência seja "
                "registrada. O agente informa que registrou a ocorrência e fornece o protocolo 28206.",
                resolution="Ocorrência registrada; protocolo 28206.",
            ),
            episode(
                3,
                "technical_visit",
                days_ago=70,
                summary="Cliente perguntou como imprimir a etiqueta; o agente informou que a etiqueta 58748 "
                "está pronta no portal.",
            ),
        ),
    )


def harness(cell: Cell) -> Harness:
    cell.config.put(sample_config())
    world = default_world()
    world.snapshots = [long_history()]
    return Harness(cell, world)


async def read_slots(h: Harness, query: str, conversation: str) -> tuple[str, dict[str, object]]:
    request = ContextRequest(
        subject=SUBJECT, view="voice", verification=Verification.V1, conversation_id=conversation, query=query
    )
    before = len(h.receipts.sent)
    response = await h.serve.context(AGENT, request)
    receipt = next(r for r in h.receipts.sent[before:] if r.surface is ReceiptSurface.SLOTS)
    return response.slots or "", json.loads(receipt.detail["slots"])


async def test_on_the_read_path_a_voice_question_gets_its_budget_and_answers_with_an_atom(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    question = (
        "Há um tempo eu reclamei que o guincho não chegou no horário. Qual protocolo vocês me passaram?"
    )
    slots, manifest = await read_slots(h, question, "call-1")
    assert (manifest["budget"], manifest["budget_rule"]) == (100, "question")
    assert manifest["tokens"] <= 100
    # The matter and the number in one short line; never the number alone.
    line = next(x for x in slots.split("\n") if "28206" in x)
    assert "guincho não chegou no horário" in line
    assert line.startswith("Conversa: ")
    assert len(line) < 90
    _, plain = await read_slots(h, "O guincho não chegou no horário de novo", "call-1")
    assert (plain["budget"], plain["budget_rule"]) == (60, "turn")
    # The same turn over the same memory is the same bytes.
    again, repeat = await read_slots(h, question, "call-1")
    assert again == slots
    assert repeat == manifest
