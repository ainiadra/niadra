"""A history sent in one go keeps the time of each conversation (the benchmark's seeding pattern).

A tenant that backfills months of conversations closes each one with `conversation.ended` right away, so
every session closes at the moment of the import. The memory must still date each conversation by its own
turns: recency ranks the pack, the pack's "updated at" is the newest conversation, and the extractor
resolves "tomorrow" against the day the customer said it.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.domain.compile.candidates import _confirmation, fact_weight
from niadra.domain.memory.values import SettledValue, ValueType, current
from niadra.domain.records import ProfileRecord
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_patterns_in_the_package import package

PHONE = "+5511955553333"
TZ = ZoneInfo("America/Sao_Paulo")  # the space's default time zone


def episode(summary: str) -> dict[str, Any]:
    return {
        "episode": {
            "summary": summary,
            "intent": "request",
            "category": "invoice",
            "outcome": "resolved",
            "resolution": None,
            "sentiment": None,
        },
        "facts": [],
        "open_items": [
            {
                "description": f"Retorno sobre {summary}",
                "owner": "company",
                "due_at": None,
                "evidence": ["e1"],
                "object": None,
                "expected_operation": None,
                "about": None,
            }
        ],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def conversation(name: str, at: datetime, customer: str, agent: str) -> list[dict[str, Any]]:
    common = {"type": "event", "channel": "whatsapp", "conversation_id": name}
    handles = [{"type": "phone_e164", "value": PHONE}]
    return [
        {
            **common,
            "idempotency_key": f"{name}-1",
            "handles": handles,
            "speaker": {"role": "customer"},
            "direction": "inbound",
            "content": {"type": "text", "text": customer},
            "occurred_at": at.isoformat(),
        },
        {
            **common,
            "idempotency_key": f"{name}-2",
            "handles": handles,
            "speaker": {"role": "ai_agent"},
            "direction": "outbound",
            "content": {"type": "text", "text": agent},
            "occurred_at": (at + timedelta(seconds=40)).isoformat(),
        },
        {
            "type": "conversation.ended",
            "idempotency_key": f"{name}-end",
            "conversation_id": name,
            "occurred_at": (at + timedelta(seconds=80)).isoformat(),
        },
    ]


async def test_an_imported_history_keeps_the_time_of_each_conversation(cell: Cell) -> None:
    now = cell.clock.now()
    old, recent = now - timedelta(days=60), now - timedelta(days=3)
    llm = ScriptedLlm(episode("segunda via 11111"), episode("protocolo 22222"))
    flow = Flow.build(cell, llm)
    # Oldest first, in one import: both sessions close now, at the time of the import.
    items = [
        *conversation("imp-1", old, "Quero a segunda via da conta, por favor", "Enviei, código 11111"),
        *conversation("imp-2", recent, "Me passa o protocolo da reclamação?", "Seu protocolo é 22222"),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()

    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
        assert profile is not None
        episodes = sorted(await uow.memory.episodes_for(profile.handle_ids), key=lambda e: e.started_at)
    assert [e.ended_at for e in episodes] == [old + timedelta(seconds=40), recent + timedelta(seconds=40)]
    # One call for the import, and the extractor reads each session against the day it happened.
    [prompt] = [call["user"] for call in llm.calls]
    ends = [line.split("ended ")[1][:10] for line in prompt.splitlines() if line.startswith("[s")]
    assert ends == [
        (old + timedelta(seconds=40)).astimezone(TZ).date().isoformat(),
        (recent + timedelta(seconds=40)).astimezone(TZ).date().isoformat(),
    ]

    subject = {"type": "phone_e164", "value": PHONE}
    proof = {
        "type": "verify",
        "idempotency_key": "call-1-proof",
        "method": "otp_sms",
        "level": "V2",
        "conversation_id": "call-1",
        "handle": subject,
        "occurred_at": now.isoformat(),
    }
    assert (await flow.send(VOICE_SOURCE, proof)).errors == []
    await flow.drain()
    request = ContextRequest(
        subject=Handle.model_validate(subject),
        view="voice",
        verification=Verification.V2,  # the starter policy releases billing conversations at V2
        conversation_id="call-1",
    )
    pack = await flow.serve.context(principal(VOICE_SOURCE, cell.config.space(SPACE_ID)), request)
    # "Updated at" is the newest conversation or promise, not the import.
    assert pack.as_of == recent + timedelta(seconds=40)
    assert "22222" in (pack.text or "")


def settled(summary: str, *values: str) -> dict[str, Any]:
    return {**episode(summary), "open_items": [], "values": list(values)}


async def customer(cell: Cell) -> ProfileRecord:
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
    assert profile is not None
    return profile


async def customer_values(cell: Cell) -> list[SettledValue]:
    profile = await customer(cell)
    async with cell.uow(SPACE_ID) as uow:
        return await uow.memory.values_for(profile.handle_ids)


async def test_an_imported_history_with_two_postal_codes_leaves_one_current(cell: Cell) -> None:
    now = cell.clock.now()
    llm = ScriptedLlm(
        settled("CEP 88221-615", "postal_code 88221-615"), settled("CEP 45778-204", "postal_code 45778-204")
    )
    flow = Flow.build(cell, llm)
    items = [
        *conversation(
            "cep-1", now - timedelta(days=39), "Para atualizar: o CEP de coleta é 88221-615.", "Atualizado."
        ),
        *conversation(
            "cep-2",
            now - timedelta(days=4),
            "Mudei de endereço. Agora o CEP de coleta é 45778-204.",
            "Pronto, atualizei para 45778-204.",
        ),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    found = await customer_values(cell)
    assert sorted(v.raw for v in found) == ["45778-204", "88221-615"]
    now_holding = current(found, ValueType.POSTAL_CODE)
    assert now_holding is not None
    assert now_holding.raw == "45778-204"
    assert now_holding.said_at == now - timedelta(days=4)
    # The customer's access package carries them too.
    sent = await package(flow, SPACE_ID, (await customer(cell)).profile_id)
    assert sorted(v["raw"] for v in sent["settled_values"]) == ["45778-204", "88221-615"]


async def test_no_document_or_card_number_reaches_the_settled_values(cell: Cell) -> None:
    now = cell.clock.now()
    # The model lists the numbers anyway: validation and the net must both refuse them.
    llm = ScriptedLlm(
        settled("Documento informado", "code 54726939", "receipt 4111 1111 1111 1111"),
        settled("Cupom", "code 40997"),
    )
    flow = Flow.build(cell, llm)
    items = [
        *conversation(
            "doc-1",
            now - timedelta(days=19),
            "Meu documento para a nota é 54726939 e paguei no cartão 4111 1111 1111 1111.",
            "Obrigado, atualizei seu cadastro.",
        ),
        *conversation(
            "doc-2", now - timedelta(days=15), "Tem cupom para primeira compra?", "Tem, o cupom 40997."
        ),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    found = await customer_values(cell)
    assert [(v.type, v.raw) for v in found] == [(ValueType.CODE, "40997")]


def repeated(value: str, turns: int) -> dict[str, Any]:
    """An extraction that states the same fact once per turn, as a model does with a value the customer
    repeats in every message."""
    fact = {
        "op": "add",
        "predicate": "preferred_name",
        "value": value,
        "target": None,
        "valid_at": None,
        "certainty": 0.9,
        "contradicts": [],
        "about": None,
        "object": None,
    }
    return {
        **episode(f"Chamar de {value}"),
        "open_items": [],
        "facts": [{**fact, "evidence": [f"e{i + 1}"]} for i in range(turns)],
    }


def insistent(name: str, at: datetime, turns: int) -> list[dict[str, Any]]:
    common = {"type": "event", "channel": "whatsapp", "conversation_id": name}
    said = [
        {
            **common,
            "idempotency_key": f"{name}-{i}",
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": "customer"},
            "direction": "inbound",
            "content": {"type": "text", "text": f"Me chama de Rafa, por favor ({i})"},
            "occurred_at": (at + timedelta(seconds=10 * i)).isoformat(),
        }
        for i in range(turns)
    ]
    ended = {
        "type": "conversation.ended",
        "idempotency_key": f"{name}-end",
        "conversation_id": name,
        "occurred_at": (at + timedelta(seconds=10 * turns)).isoformat(),
    }
    return [*said, ended]


async def test_thirty_mentions_in_two_sessions_confirm_the_value_twice(cell: Cell) -> None:
    # An imported history repeats the name in every turn of two conversations. Counted by
    # mentions it would weigh as thirty confirmations; by sessions it weighs as two.
    now = cell.clock.now()
    flow = Flow.build(cell, ScriptedLlm(repeated("Rafa", 15), repeated("Rafa", 15)))
    items = [
        *insistent("rep-1", now - timedelta(days=40), 15),
        *insistent("rep-2", now - timedelta(days=4), 15),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    profile = await customer(cell)
    async with cell.uow(SPACE_ID) as uow:
        [name] = await uow.memory.facts_for(profile.handle_ids)
    assert (name.value, name.times_seen, name.sessions_seen) == ("Rafa", 30, 2)
    assert fact_weight(name) == name.confidence * _confirmation(2)
    assert fact_weight(name) < name.confidence * _confirmation(3)
