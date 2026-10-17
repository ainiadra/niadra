"""Atomic lines (17, 6.3): a conversation read back as a matter and a value, and chosen over a cut summary
when it answers the turn. The lines are shaped like the extraction model's summaries over dataset v2 (two
sentences, the matter first, the number the agent gave last, a solution that repeats it)."""

from datetime import UTC, datetime, timedelta

from niadra.domain.compile.model import Section
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.serve.atoms import ATOM_TOKENS, atoms_of, best_atom
from niadra.domain.serve.slots import SlotItem, SlotPlan, excerpt, plan_slots, run_channels, standard_channels
from niadra.domain.serve.turn import analyze
from niadra.testing.cell import sample_config

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FMT = Formatter("America/Sao_Paulo", "pt-BR")
LEXICON = sample_config().lexicon()

TOW = (
    "17/08/2026 · voice · Cliente relata que o guincho não chegou no horário e pede que a ocorrência seja "
    "registrada. O agente informa que registrou a ocorrência e fornece o protocolo 28206. · resolvido · "
    "solução: Ocorrência registrada; protocolo 28206."
)
INTERNET = (
    "06/06/2026 · voice · Cliente relata: “A internet está caindo toda noite. Podem registrar?” O agente "
    "informa que registrou, protocolo 21059. · resolvido · solução: Solicitação registrada, protocolo 21059."
)
SEAL = (
    "27/08/2026 · whatsapp · O cliente informou que o lacre do contêiner está numerado 69600; o agente "
    "confirmou o registro. · resolvido · solução: O agente confirmou o registro."
)
DEADLINE = (
    "23/09/2026 · whatsapp · O cliente informou que a panela chegou com a tampa quebrada no pedido 10467. "
    "Precisa da troca até o dia 20, pois vai receber visitas. O agente anotou a solicitação no registro "
    "10467. · pendente"
)
COURTESY = (
    "22/09/2026 · app · Cliente perguntou se a apólice cobre carro cortesia; o agente respondeu que a "
    "cobertura "
    "é por sete dias, cláusula 91511. · resolvido"
)


def voice_item(n: int, text: str, days: float) -> SlotItem:
    line = FMT.line(Section.EPISODES, text, voice=True)
    return SlotItem(
        f"episode:{n:04d}", "episode", text, line, estimate_tokens(line + "\n"), at=NOW - timedelta(days=days)
    )


def test_an_atom_is_the_matter_and_the_value_said_together() -> None:
    [atom] = atoms_of(TOW)
    assert atom.text == "17/08/2026 · voice · o guincho não chegou no horário · protocolo 28206"
    assert atom.values == ("28206",)
    assert "protocol" in atom.types
    assert estimate_tokens(f"{atom.matter} · {atom.value}") <= ATOM_TOKENS
    [internet] = atoms_of(INTERNET)
    assert internet.text == "06/06/2026 · voice · A internet está caindo toda noite · protocolo 21059"


def test_a_short_exchange_that_fixes_a_value_is_one_atom_with_its_matter() -> None:
    # Two turns, "o lacre do contêiner tem a numeração 69600" and "anotado": the matter holds the value.
    [atom] = atoms_of(SEAL)
    assert atom.text == "27/08/2026 · whatsapp · o lacre do contêiner está numerado 69600"
    assert atom.value == ""


def test_a_day_the_customer_asked_for_is_an_atom_of_its_own() -> None:
    atoms = atoms_of(DEADLINE)
    assert [a.dated for a in atoms] == [False, True]
    assert atoms[1].text == (
        "23/09/2026 · whatsapp · a panela chegou com a tampa quebrada · troca até o dia 20"
    )
    # The same record said again in the solution is not a third atom.
    assert len(atoms) == 2
    when = best_atom(DEADLINE, analyze("Até que dia eu disse que precisava da troca?"))
    assert when is not None
    assert when.dated


def test_lines_that_are_not_conversations_or_hold_no_value_have_no_atoms() -> None:
    assert atoms_of("technical issue: O aplicativo bloqueou a transferência · whatsapp 09/2026") == ()
    assert atoms_of("technical: (mascarado)") == ()
    assert atoms_of("12/03/2026 · voice · A visita técnica não aconteceu · resolvido") == ()


def test_an_atom_answers_only_what_the_turn_asks() -> None:
    protocol = analyze("Qual protocolo vocês me passaram quando o guincho atrasou?")
    assert best_atom(TOW, protocol) is not None
    assert best_atom(COURTESY, analyze("Como está o tempo hoje?")) is None


def plan(
    question: str, pool: list[SlotItem], *, budget: int, voice: bool = True, max_items: int = 3
) -> SlotPlan:
    turn = analyze(question)
    hits = run_channels(turn, pool, standard_channels(lexicon=LEXICON))
    return plan_slots(
        turn, pool, hits, fmt=FMT, voice=voice, budget=budget, max_items=max_items, now=NOW, lexicon=LEXICON
    )


def test_on_a_voice_question_the_atom_wins_over_the_cut_summary() -> None:
    # en-137 and pt-141: the cut kept "fornece o protocolo 28206" without the tow truck, and the agent said
    # the number was of another matter. The atom carries both, in fewer tokens than the cut.
    tow, courtesy = voice_item(1, TOW, 39), voice_item(2, COURTESY, 3)
    question = (
        "Há um tempo eu reclamei que o guincho não chegou no horário. Qual protocolo vocês me passaram?"
    )
    got = plan(question, [tow, courtesy], budget=100)
    [slot] = [s for s in got.slots if s.item.item_id == tow.item_id]
    assert (
        slot.item.line == "Conversa: 17/08/2026 · voice · o guincho não chegou no horário · protocolo 28206."
    )
    assert slot.why is not None
    assert slot.why.atom
    cut = excerpt(tow, analyze(question), 60)
    assert cut is not None
    assert slot.item.tokens < cut.tokens
    # The manifest and the receipt say it was an atom; never its words or its number.
    assert got.why()["items"][0]["atom"] is True
    assert "28206" not in str(got.why())
    assert "guincho" not in str(got.manifest())


def test_a_turn_that_asks_nothing_keeps_the_whole_line_when_it_fits() -> None:
    tow = voice_item(1, TOW, 39)
    got = plan("O guincho de novo, que coisa", [tow], budget=100)
    assert [s.item.line for s in got.slots] == [tow.line]
    chat = plan("Qual protocolo do guincho?", [tow], budget=120, voice=False)
    assert chat.slots[0].item.text == TOW  # chat reads the whole conversation when it fits


def test_the_same_turn_gives_the_same_atoms() -> None:
    pool = [voice_item(1, TOW, 39), voice_item(2, INTERNET, 110), voice_item(3, SEAL, 29)]
    question = "Qual protocolo vocês me passaram da internet caindo?"
    first = plan(question, pool, budget=100)
    again = plan(question, list(reversed(pool)), budget=100)
    assert first.render(FMT) == again.render(FMT)
    assert first.manifest() == again.manifest()
