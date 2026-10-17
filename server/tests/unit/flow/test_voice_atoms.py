"""A voice agent asked for a number said weeks ago (17, 6.3 and 6.5), end to end: conversations on other
channels are extracted as the extraction model writes them, and the caller asks for the container seal number
or the protocol of the late tow truck. The turn's slots carry an atom, the matter with the number, within the
question's budget; the pinned body is the same bytes on every turn of the call."""

from datetime import UTC, datetime
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_voice_first_read import attestation, ended, said, starter_space

PHONE = "+5511977776666"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


def extraction(
    summary: str, category: str, resolution: str | None, facts: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "complaint" if category == "technical" else "request",
            "category": category,
            "outcome": "resolved",
            "resolution": resolution,
            "sentiment": 0.0,
        },
        "facts": facts,
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


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


# A two-turn session that fixes a value ("the seal is 69600" / "noted, thanks"), a complaint with the
# protocol the agent gave, and the newest conversation about something else; the solutions repeat the
# summaries' numbers, as the extraction model's do.
SEAL = extraction(
    "O cliente informou que o lacre do contêiner está numerado 69600; o agente confirmou o registro.",
    "orders",
    "O agente confirmou o registro.",
    [],
)
TOW = extraction(
    "Cliente relata que o guincho não chegou no horário e pede que a ocorrência seja registrada. O agente "
    "informa que registrou a ocorrência e fornece o protocolo 28206.",
    "technical",
    "Ocorrência registrada; protocolo 28206.",
    [fact("technical_issue", "O guincho não chegou no horário.")],
)
COVERAGE = extraction(
    "Cliente perguntou se a apólice cobre carro cortesia; o agente respondeu que a cobertura é por sete "
    "dias, cláusula 91511.",
    "orders",
    "O agente informou cobertura por sete dias, conforme a cláusula 91511.",
    [],
)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    starter_space(cell)
    flow = Flow.build(cell, ScriptedLlm(SEAL, TOW, COVERAGE))
    clock = cell.clock
    for n, (at, text, answer) in enumerate(
        (
            (datetime(2026, 8, 27, 14, 0, tzinfo=UTC), "O lacre do contêiner tem a numeração 69600.", None),
            (
                datetime(2026, 8, 17, 18, 0, tzinfo=UTC),
                "O guincho não chegou no horário, registrem por favor.",
                "Registrei a ocorrência, protocolo 28206.",
            ),
            (datetime(2026, 9, 22, 13, 0, tzinfo=UTC), "A apólice cobre carro cortesia?", None),
        )
    ):
        clock.at = at
        conversation = f"wa-{n}"
        turns = [said("whatsapp", conversation, 1, text, at)]
        if answer:
            agent = said("whatsapp", conversation, 2, answer, at)
            turns.append({**agent, "speaker": {"role": "ai_agent"}, "direction": "outbound"})
        batch = (*turns, ended(conversation, at.replace(minute=9)))
        assert (await flow.send(WHATSAPP_SOURCE, *batch)).errors == []
        await flow.drain()
    clock.at = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
    assert (await flow.send(VOICE_SOURCE, attestation(clock.now()))).errors == []
    await flow.drain()
    return flow


async def read(flow: Flow, query: str | None = None) -> ContextResponse:
    request = ContextRequest(
        subject=SUBJECT, view="voice", verification=Verification.V1, conversation_id="call-0922", query=query
    )
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


async def test_a_voice_question_for_an_old_number_gets_the_matter_with_it(flow: Flow) -> None:
    first = await read(flow)
    body = first.text or ""
    # The newest conversation is pinned; its solution says nothing its summary does not, so it is left out.
    assert "carro cortesia" in body
    assert "solução: O agente informou cobertura" not in body
    assert "atualizado em" not in body

    seal = await read(flow, "Qual identificador do lacre do contêiner eu passei para vocês?")
    tow = await read(
        flow, "Há um tempo reclamei que o guincho não chegou no horário. Qual protocolo me passaram?"
    )
    for turn in (seal, tow):
        assert turn.text == first.text  # the body is the same bytes all call long
    seal_line = next(x for x in (seal.slots or "").split("\n") if "69600" in x)
    assert "lacre do contêiner" in seal_line
    tow_line = next(x for x in (tow.slots or "").split("\n") if "28206" in x)
    assert "guincho não chegou no horário" in tow_line
    assert "..." not in tow_line  # an atom, not a cut summary
