"""A bank customer's dispute under the starter policy: the customer writes about a
contested transfer and the bank gives a protocol; days later the customer calls from the same number.

At V1 (the carrier attests the number) the call hears back what the customer wrote and the protocol given
for it, by the mirror rule. A V0 caller hears none of it and the agent is told to ask for the verification.
A number joined to the profile by a claim nobody confirmed reads nothing more for it. A balance the bank
stated stays at V2."""

from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.starter import STARTER_EXTRACTION, STARTER_POLICY
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

CUSTOMER = {"type": "phone_e164", "value": "+5511944443333"}
EMAIL = {"type": "email", "value": "juliana.carvalho@mail.com.br"}
CRM = {"type": "system_id", "value": "98282", "scope": "crm"}
ATTACKER = {"type": "phone_e164", "value": "+5521977774444"}
QUESTION = "Oi, mandei uma mensagem para vocês esses dias. Qual é o número de protocolo que me passaram?"


def extraction(summary: str, category: str, facts: list[dict[str, Any]], values: list[str]) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "complaint",
            "category": category,
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.3,
        },
        "facts": facts,
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
        "values": values,
    }


def fact(predicate: str, value: str, evidence: str) -> dict[str, Any]:
    return {
        "op": "add",
        "predicate": predicate,
        "value": value,
        "target": None,
        "evidence": [evidence],
        "valid_at": None,
        "certainty": 0.9,
        "contradicts": [],
        "about": None,
        "object": None,
    }


DISPUTE = extraction(
    "Cliente escreveu sobre a contestação 70710: o aplicativo bloqueou a transferência. "
    "O banco informou o protocolo 98429.",
    "billing",
    [fact("billing_issue", "contestação 70710: o aplicativo bloqueou a transferência", "e1")],
    ["protocol 98429"],
)
BALANCE = extraction(
    "Cliente perguntou o saldo devedor. O banco informou saldo devedor de R$ 3.217,45.",
    "billing",
    [],
    ["amount R$ 3.217,45"],
)


def turn(
    conversation: str, n: int, role: str, text: str, at: str, handles: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": handles,
        "speaker": {"role": role},
        "direction": "inbound" if role == "customer" else "outbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
    }


async def conversation(flow: Flow, name: str, *lines: tuple[str, str]) -> None:
    now = flow.cell.clock.now().isoformat()
    handles = [CUSTOMER, EMAIL, CRM]
    items = [turn(name, n, role, text, now, handles) for n, (role, text) in enumerate(lines, start=1)]
    ended = {"type": "conversation.ended", "idempotency_key": f"{name}-end", "conversation_id": name}
    assert (await flow.send(WHATSAPP_SOURCE, *items, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    flow.cell.clock.advance(hours=3)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    config = cell.config.spaces()[0]
    cell.config.put(config.model_copy(update={"policy": STARTER_POLICY, "extraction": STARTER_EXTRACTION}))
    flow = Flow.build(cell, ScriptedLlm(DISPUTE, BALANCE))
    await conversation(
        flow,
        "wa-dispute",
        ("customer", "Escrevo sobre a contestação 70710: o aplicativo bloqueou minha transferência."),
        ("ai_agent", "O protocolo do seu atendimento é 98429. Respondemos em até dois dias úteis."),
    )
    await conversation(
        flow,
        "wa-balance",
        ("customer", "Qual é o meu saldo devedor?"),
        ("ai_agent", "Seu saldo devedor é de R$ 3.217,45."),
    )
    cell.clock.advance(days=2)
    return flow


async def call(flow: Flow, who: dict[str, Any], name: str, level: Verification) -> ContextResponse:
    now = flow.cell.clock.now().isoformat()
    line = turn(name, 1, "customer", QUESTION, now, [who])
    line |= {"channel": "voice", "verification_hint": "V1"}
    assert (await flow.send(VOICE_SOURCE, line)).errors == []
    subject = Handle(type=HandleType(who["type"]), value=who["value"])
    request = ContextRequest(
        subject=subject, view="voice", verification=level, conversation_id=name, query=QUESTION
    )
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


def everything(response: ContextResponse) -> str:
    return "\n".join(p for p in (response.text, response.slots) if p)


async def test_at_v1_the_customer_hears_back_the_dispute_and_its_protocol(flow: Flow) -> None:
    response = await call(flow, CUSTOMER, "call-1", Verification.V1)
    assert response.verification.effective is Verification.V1
    said = everything(response)
    assert "98429" in said
    assert "70710" in said
    assert "sem registro de protocolo" not in said
    # What the bank holds about the customer stays at V2.
    assert "3.217" not in said
    assert "3217" not in said


async def test_a_v0_caller_hears_none_of_it_and_the_agent_asks_for_verification(flow: Flow) -> None:
    response = await call(flow, CUSTOMER, "call-2", Verification.V0)
    assert response.verification.effective is Verification.V0
    said = everything(response)
    for value in ("98429", "70710", "3.217", "3217", "transferência"):
        assert value not in said
    assert "peça a verificação" in said
    assert "sem registro de protocolo" not in said
    assert "há protocolo registrado, retido até verificar a identidade" in said


async def test_a_bank_that_keeps_disputes_at_v2_never_says_there_is_no_protocol(flow: Flow) -> None:
    # The space turns the mirror off for billing: at V1 the dispute stays withheld, and the turn says a
    # protocol is on record until the verification instead of saying there is none (pt-023, pt-038).
    config = flow.cell.config.spaces()[0]
    categories = [c.model_copy(update={"mirror": c.name != "billing"}) for c in config.policy.categories]
    policy = config.policy.model_copy(update={"categories": categories, "version": "bank-1"})
    flow.cell.config.put(config.model_copy(update={"policy": policy}))
    response = await call(flow, CUSTOMER, "call-4", Verification.V1)
    said = everything(response)
    assert "98429" not in said
    assert "sem registro de protocolo" not in said
    assert "há protocolo registrado, retido até verificar a identidade" in said


async def test_a_number_joined_by_a_claim_reads_nothing_more_for_it(flow: Flow) -> None:
    now = flow.cell.clock.now().isoformat()
    claim = {
        "type": "identify",
        "idempotency_key": "claim-1",
        "handles": [ATTACKER, EMAIL],
        "method": "declared",
        "occurred_at": now,
    }
    assert (await flow.send(VOICE_SOURCE, claim)).errors == []
    await flow.drain()
    response = await call(flow, ATTACKER, "call-3", Verification.V1)
    # The number reads the customer's profile (the claim joined it), and the mirror lifts nothing for it.
    assert response.verification.effective is Verification.V1
    assert (response.withheld or 0) >= 3
    said = everything(response)
    for value in ("98429", "70710", "3.217", "transferência"):
        assert value not in said


async def test_a_number_said_after_three_others_is_still_looked_up(flow: Flow) -> None:
    # Only the first three numbers of a turn were looked up in the settled values. A dense
    # e-mail names more; the protocol written fifth is found, in one statement.
    from niadra.app.serve import MAX_TURN_KEYS
    from niadra.domain.compile.templates import Formatter
    from niadra.domain.serve.lexicon import EMPTY
    from niadra.domain.serve.turn import analyze

    space = flow.cell.config.spaces()[0].space_id
    async with flow.cell.uow(space) as uow:
        [record] = await uow.identity.find_handles([_key(CUSTOMER)])
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    turn = analyze("Sobre 11111, 22222, 33333 e 44444, e o protocolo 98429?")
    assert len(turn.identifiers) == 5 <= MAX_TURN_KEYS
    hits, unseen = await flow.serve._value_hits(
        space, profile, turn, [], None, Formatter("America/Sao_Paulo", "pt-BR"), False, EMPTY, frozenset()
    )
    # Found: said if its conversation is in the reader's pool, else counted as unseen; either way looked up.
    found = "98429" in repr(hits.items) or "98429" in {d for _, d in (*unseen.hidden, *unseen.withheld)}
    assert found


async def test_numbers_past_the_cap_are_unknown_never_none(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    import niadra.app.serve as serve_module
    from niadra.domain.compile.templates import Formatter
    from niadra.domain.serve.lexicon import EMPTY
    from niadra.domain.serve.turn import analyze

    monkeypatch.setattr(serve_module, "MAX_TURN_KEYS", 1)
    space = flow.cell.config.spaces()[0].space_id
    async with flow.cell.uow(space) as uow:
        [record] = await uow.identity.find_handles([_key(CUSTOMER)])
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    turn = analyze("Sobre 11111 e o protocolo 98429?")
    _, unseen = await flow.serve._value_hits(
        space, profile, turn, [], None, Formatter("America/Sao_Paulo", "pt-BR"), False, EMPTY, frozenset()
    )
    assert ("", "98429") in unseen.hidden


def _key(handle: dict[str, Any]):  # type: ignore[no-untyped-def]
    from niadra.domain.identity.handles import normalize_handle
    from niadra.domain.vocabulary import HandleType as Type

    return normalize_handle(Type(handle["type"]), handle["value"], handle.get("scope")).key
