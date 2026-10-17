"""The first `context()` of a conversation is a cache hit: the conversation's first
event asks the compiler for its reader's pack, so the agent's first read takes T0 instead of compiling
on the read path (T2)."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.compile import profile_target
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import DeliveryPath, HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511955554444"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


def remembered() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Pediu a segunda via da fatura",
            "intent": "request",
            "category": "invoice",
            "outcome": "resolved",
            "resolution": "Segunda via enviada",
            "sentiment": 0.1,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "preferred_name",
                "value": "Rafa",
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


def said(conversation: str, text: str, at: str, *, channel: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{text[:8]}",
        "channel": channel,
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        **extra,
    }


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """A customer with memory from an earlier, closed conversation."""
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    ended = {"type": "conversation.ended", "idempotency_key": "wa-1-end", "conversation_id": "wa-1"}
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    cell.clock.advance(hours=3)
    return flow


async def read(
    flow: Flow, source: Any, conversation: str, *, view: str = "chat", level: Verification = Verification.V0
) -> ContextResponse:
    request = ContextRequest(subject=SUBJECT, view=view, verification=level, conversation_id=conversation)
    return await flow.serve.context(principal(source, flow.cell.config.spaces()[0]), request)


async def generation(flow: Flow, profile_id: UUID) -> int:
    async with flow.cell.uow(flow.cell.config.spaces()[0].space_id) as uow:
        return await uow.compile.current_generation(profile_target(profile_id))


async def test_the_first_read_of_a_new_conversation_is_served_from_the_cache(flow: Flow) -> None:
    await read(flow, WHATSAPP_SOURCE, "warm-check")
    profile_id = flow.receipts.sent[-1].profile_id
    assert profile_id is not None
    stored = await generation(flow, profile_id)
    flow.cell.cache.values.clear()  # a cache that lost everything, as after a failover

    now = flow.cell.clock.now().isoformat()
    assert (
        await flow.send(WHATSAPP_SOURCE, said("wa-2", "Oi, de novo", now, channel="whatsapp"))
    ).errors == []
    ran = await flow.drain()
    assert TaskName.COMPILE in {t.name for t in ran}
    # The packs stored for the current memory are published as they are, never compiled again.
    assert await generation(flow, profile_id) == stored

    # The SDK's default level is V0, which is what a conversation that proved nothing reads.
    first = await read(flow, WHATSAPP_SOURCE, "wa-2")
    assert first.path is DeliveryPath.T0
    assert "Rafa" not in (first.text or "")  # V0 withholds contact details
    assert first.text


async def test_a_call_that_proves_a_level_gets_its_pack_ready_at_that_level(flow: Flow) -> None:
    now = flow.cell.clock.now().isoformat()
    call = said("call-1", "Alô, é a Rafa", now, channel="voice", verification_hint="V1")
    assert (await flow.send(VOICE_SOURCE, call)).errors == []
    await flow.drain()

    # The voice guide asks for V2; the call proved V1 through the channel, which is what it reads.
    first = await read(flow, VOICE_SOURCE, "call-1", view="voice", level=Verification.V2)
    assert first.verification.effective is Verification.V1
    assert first.path is DeliveryPath.T0
    assert "Rafa" in (first.text or "")


async def test_without_the_warm_up_the_first_read_compiles_on_the_read_path(flow: Flow) -> None:
    # The measured effect: the same read, with the conversation's compile request left in the queue.
    now = flow.cell.clock.now().isoformat()
    assert (
        await flow.send(WHATSAPP_SOURCE, said("wa-3", "Oi, voltei", now, channel="whatsapp"))
    ).errors == []
    await flow.drain(skip={TaskName.COMPILE})
    flow.cell.cache.values.clear()

    cold = await read(flow, WHATSAPP_SOURCE, "wa-3")
    assert cold.path in (DeliveryPath.T1, DeliveryPath.T2)
    warmed = [t for t in flow.pending(TaskName.COMPILE) if t.payload.get("session_id")]
    assert len(warmed) == 1
    await flow.drain(only={TaskName.COMPILE})
    again = await read(flow, WHATSAPP_SOURCE, "wa-4")
    assert again.path is DeliveryPath.T0


async def test_an_existing_conversation_asks_for_nothing_more(flow: Flow) -> None:
    now = flow.cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-5", "Primeira", now, channel="whatsapp"))).errors == []
    await flow.drain()
    flow.cell.clock.advance(minutes=1)
    later = flow.cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-5", "Segunda", later, channel="whatsapp"))).errors == []
    assert [t for t in flow.pending(TaskName.COMPILE) if t.payload.get("session_id")] == []


async def test_an_import_of_past_conversations_warms_nothing(flow: Flow) -> None:
    long_ago = (flow.cell.clock.now() - timedelta(days=40)).isoformat()
    assert (
        await flow.send(WHATSAPP_SOURCE, said("old-1", "Antiga", long_ago, channel="whatsapp"))
    ).errors == []
    assert [t for t in flow.pending(TaskName.COMPILE) if t.payload.get("session_id")] == []


async def test_a_warm_up_never_compiles_itself(flow: Flow) -> None:
    # The warm-up is one short read in the workers: what is missing goes to the coalesced compilation.
    flow.cell.uow.store.module("compile")["bundles"].clear()
    flow.cell.cache.values.clear()
    now = flow.cell.clock.now().isoformat()
    turn = said("wa-9", "Oi, tudo bem?", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, turn)).errors == []
    [warm] = [t for t in flow.pending(TaskName.COMPILE) if t.payload.get("session_id")]
    await flow.compile.compile(warm)
    flow.cell.uow.store.module("pipeline")["outbox"].remove(warm)
    assert flow.cell.uow.store.module("compile")["bundles"] == {}
    [coalesced] = [t for t in flow.pending(TaskName.COMPILE) if not t.payload.get("session_id")]
    assert coalesced.payload == {"reason": "warm"}
    await flow.drain(only={TaskName.COMPILE})
    assert (await read(flow, WHATSAPP_SOURCE, "wa-9")).path is DeliveryPath.T0


async def test_forgetting_the_customer_takes_its_compiled_context_out_of_the_cache(flow: Flow) -> None:
    # The read path refused the stored context by privacy version; now it also leaves the store.
    await read(flow, WHATSAPP_SOURCE, "wa-7")
    profile_id = flow.receipts.sent[-1].profile_id
    assert profile_id is not None
    assert any(f"{{{profile_id}}}" in key for key in flow.cell.cache.values)
    space = flow.cell.config.spaces()[0].space_id
    await flow.privacy.forget(
        space,
        ForgetRequest(target="profile", profile_id=profile_id),
        idempotency_key="forget-rafa",
        requested_by="user:dpo",
    )
    await flow.drain()
    assert not any(f"{{{profile_id}}}" in key for key in flow.cell.cache.values)
