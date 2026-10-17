"""An item a search returned in a call opens for the rest of the call, whatever the customer's memory does
meanwhile (benchmark of 09/10/2026, case pt-001): a voice call proved at its start, then read for minutes
without a turn, had its session closed on a quiet after 3 minutes (`QUIET_MINUTES`), and its proof went
with it. Every read after ran at V0, the episodes were withheld, and an open said "item not found" while
the ids had not changed: a fresh proved conversation opened the same ids."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, SearchRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_voice_first_read import extraction, starter_space

PHONE = "+5511973318842"
WA = {"type": "wa_id", "value": "5511973318842"}
CRM = {"type": "system_id", "value": "C-881", "scope": "crm"}
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


def message(conversation: str, n: int, text: str, at: datetime) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [WA],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at.isoformat(),
    }


def ended(conversation: str, at: datetime, n: int = 1) -> dict[str, Any]:
    return {"type": "conversation.ended", "idempotency_key": f"{conversation}-end-{n}",
            "conversation_id": conversation, "occurred_at": at.isoformat()}  # fmt: skip


def attested(conversation: str, at: datetime) -> dict[str, Any]:
    return {"type": "verify", "idempotency_key": f"{conversation}-proof", "method": "network_attestation",
            "level": "V1", "conversation_id": conversation,
            "handle": {"type": "phone_e164", "value": PHONE}, "occurred_at": at.isoformat()}  # fmt: skip


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """The customer of a benchmark case: a CRM record that ties the phone, the WhatsApp id and the record id
    together, and three WhatsApp conversations over two months, every one ended and extracted."""
    starter_space(cell)
    answers = [
        extraction(f"Ana disse que a fatura {n} veio errada.", "unresolved", None, None, [])
        for n in range(12)
    ]
    flow = Flow.build(cell, ScriptedLlm(*answers))
    clock = cell.clock
    now = clock.now()
    clock.at = now - timedelta(days=67)
    record = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "crm-1",
        "channel": "crm",
        "handles": [{"type": "phone_e164", "value": PHONE}, WA, CRM],
        "speaker": {"role": "system"},
        "canonical_type": "customer.updated",
        "fields": {"name": "Ana"},
        "occurred_at": clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, record)).errors == []
    await flow.drain()
    for n, days in enumerate((62, 20, 2)):
        clock.at = now - timedelta(days=days)
        said = message(f"wa-{n}", 1, f"Minha fatura {n} veio errada, preciso até dia 30", clock.now())
        assert (await flow.send(WHATSAPP_SOURCE, said, ended(f"wa-{n}", clock.now()))).errors == []
        await flow.drain()
    clock.at = now
    return flow


async def search(flow: Flow, conversation: str) -> list[str]:
    voice = principal(VOICE_SOURCE, flow.cell.config.spaces()[0])
    request = SearchRequest(
        subject=SUBJECT, query="fatura errada", verification=Verification.V1, conversation_id=conversation
    )
    return [i.id for i in (await flow.serve.search(voice, request)).items if i.kind == "episode"]


async def open_in(flow: Flow, conversation: str, item: str) -> str:
    voice = principal(VOICE_SOURCE, flow.cell.config.spaces()[0])
    opened = await flow.serve.open_item(
        voice,
        item,
        verification=Verification.V1,
        conversation_id=conversation,
        profile_id=None,
        subject=SUBJECT,
    )
    return opened.id


async def a_call_reads_without_turns(flow: Flow, minutes: int) -> None:
    """Other calls of the same customer, proved and read, never ended: they close on a quiet and are
    extracted, the customer's memory processed again, while the first call goes on reading."""
    voice = principal(VOICE_SOURCE, flow.cell.config.spaces()[0])
    clock = flow.cell.clock
    for n in range(3):
        assert (await flow.send(VOICE_SOURCE, attested(f"loop-{n}", clock.now()))).errors == []
        request = ContextRequest(subject=SUBJECT, conversation_id=f"loop-{n}", verification=Verification.V1)
        await flow.serve.context(voice, request)
    clock.advance(minutes=minutes)
    assert await flow.sessions.sweep_idle(flow.cell.config.spaces()[0].space_id) >= 1
    await flow.drain()


async def test_an_item_a_search_returned_opens_for_the_rest_of_the_call(flow: Flow) -> None:
    assert (await flow.send(VOICE_SOURCE, attested("call-1", flow.cell.clock.now()))).errors == []
    await flow.drain()
    found = await search(flow, "call-1")
    assert len(found) == 3
    assert await open_in(flow, "call-1", found[0]) == found[0]
    # Four quiet minutes: the call's session closed on a quiet, and the other calls were extracted.
    await a_call_reads_without_turns(flow, minutes=4)
    assert set(found) <= set(await search(flow, "call-1"))
    assert await open_in(flow, "call-1", found[0]) == found[0]


async def test_the_ids_hold_when_a_conversation_is_extracted_again(flow: Flow) -> None:
    clock = flow.cell.clock
    said = message("wa-3", 1, "A fatura de setembro também veio errada", clock.now() - timedelta(minutes=10))
    assert (
        await flow.send(WHATSAPP_SOURCE, said, ended("wa-3", clock.now() - timedelta(minutes=9)))
    ).errors == []
    await flow.drain()
    assert (await flow.send(VOICE_SOURCE, attested("call-1", clock.now()))).errors == []
    await flow.drain()
    found = await search(flow, "call-1")
    assert len(found) == 4
    # A late turn reopens the conversation, which is extracted again: a new version of the same
    # episode, under the same id.
    late = message(
        "wa-3", 2, "Esqueci de dizer: a de agosto também", clock.now() - timedelta(minutes=9, seconds=30)
    )
    assert (
        await flow.send(WHATSAPP_SOURCE, late, ended("wa-3", clock.now() - timedelta(minutes=9), 2))
    ).errors == []
    await flow.drain()
    assert sorted(await search(flow, "call-1")) == sorted(found)
    async with flow.cell.uow(flow.cell.config.spaces()[0].space_id) as uow:
        versions = [e.version for e in await uow.memory.episodes_by_ids([UUID(i) for i in found])]
    assert 2 in versions  # extracted again, under its id
    for item in found:
        assert await open_in(flow, "call-1", item) == item


async def test_a_call_silent_past_its_channels_inactivity_proves_nothing_any_more(flow: Flow) -> None:
    """Voice holds an activity for 5 minutes without a turn: past it, a turn would start a new session, and
    the reads of the old call prove nothing, as before quiet closes (a withheld item opens as not found)."""
    assert (await flow.send(VOICE_SOURCE, attested("call-1", flow.cell.clock.now()))).errors == []
    await flow.drain()
    found = await search(flow, "call-1")
    await a_call_reads_without_turns(flow, minutes=6)
    assert await search(flow, "call-1") == []
    with pytest.raises(NotFound):
        await open_in(flow, "call-1", found[0])
