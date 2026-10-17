"""An `identify()` that names two customers' handles answers with one customer: the merge runs
before the answer, under the profiles' lease, and the very next `context()` reads the merged profile,
with no worker run in between.

Bia's WhatsApp conversation is memory on her number; her CRM id arrived alone and has a profile of its
own (a message from the app, which names only the CRM id). The CRM tells the cell both are hers, and the
voice agent reads by the CRM id right after.
"""

from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_claimed_identity import identify, turn
from tests.unit.flow.test_verification import remembered

PHONE = {"type": "phone_e164", "value": "+5511966665555"}
CRM = {"type": "system_id", "value": "48213", "scope": "crm"}


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    first = turn("wa-1", 1, PHONE, "Me chama de Bia, a fatura de agosto veio em dobro", now, "whatsapp")
    ended = {
        "type": "conversation.ended",
        "idempotency_key": "wa-1-end",
        "conversation_id": "wa-1",
        "occurred_at": now,
    }
    assert (await flow.send(WHATSAPP_SOURCE, first, ended)).errors == []
    by_crm = turn("app-1", 1, CRM, "Oi", now, "app")
    assert (await flow.send(WHATSAPP_SOURCE, by_crm)).errors == []
    await flow.drain()
    cell.clock.advance(hours=2)
    return flow


async def read(flow: Flow, who: dict[str, Any]) -> ContextResponse:
    subject = Handle(type=HandleType(who["type"]), value=who["value"], scope=who.get("scope"))
    request = ContextRequest(subject=subject, view="voice", verification=Verification.V0)
    return await flow.serve.context(principal(VOICE_SOURCE, flow.cell.config.spaces()[0]), request)


async def test_the_read_right_after_an_identify_sees_the_merged_customer(flow: Flow) -> None:
    # At V0 the pack holds Bia's items back and counts them: none before, her three after the merge.
    assert "retidos" not in ((await read(flow, CRM)).text or "")

    now = flow.cell.clock.now().isoformat()
    answer = await flow.send(VOICE_SOURCE, identify("link-1", [PHONE, CRM], "explicit_identify", now))

    assert answer.errors == []
    assert flow.pending(TaskName.IDENTITY_APPLY) == []
    merged = await read(flow, CRM)
    assert "3 itens retidos" in (merged.text or "")
