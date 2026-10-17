"""A person named only in the words of someone else's conversation (5.14.0): erasing them finds those events
by what each event's text names, kept beside it, and they keep, with the person's phone and e-mail replaced;
their own events go with the cascade. Only the events that name them are read and rewritten."""

from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.privacy.mentions import Redacted
from niadra.domain.privacy.raw_index import ERASED
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, remembered, said

CLARA = "+5511987654321"
EMAIL = "clara@exemplo.com"


def from_clara(conversation: str, text: str, at: str) -> dict[str, Any]:
    handles = [{"type": "phone_e164", "value": CLARA}, {"type": "email", "value": EMAIL}]
    return {**said(conversation, text, at, channel="whatsapp"), "handles": handles}


def texts(cell: Cell) -> dict[str, str | None]:
    return {
        e.record.conversation_id or "": e.record.text
        for e in cell.uow.store.data["ingest"]["events"].values()
    }


async def erase(flow: Flow, handle: Handle) -> None:
    accepted = await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="handle", handle=handle),
        idempotency_key=f"forget-{uuid4()}",
        requested_by="user:dpo",
    )
    await flow.drain()
    out = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert out.status == ErasureStatus.COMPLETED.value


async def test_erasing_a_person_redacts_them_out_of_someone_elses_conversation(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    marcos = f"o telefone da Clara é (11) 98765-4321 e o e-mail {EMAIL}, pedido 734211"
    await flow.send(WHATSAPP_SOURCE, said("wa-marcos", marcos, now, channel="whatsapp"))
    await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi, aqui é a Clara", now))
    await flow.drain()
    assert texts(cell)["wa-clara"] is not None

    await erase(flow, Handle(type=HandleType.PHONE_E164, value=CLARA))
    await erase(flow, Handle(type=HandleType.EMAIL, value=EMAIL))

    left = texts(cell)
    assert "wa-clara" not in left, "her own conversation goes with the cascade"
    assert left["wa-marcos"] == f"o telefone da Clara é {ERASED} e o e-mail {ERASED}, pedido 734211"


async def test_the_lookup_reads_only_the_events_that_name_the_person(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    await flow.send(WHATSAPP_SOURCE, said("wa-1", f"liga pra Clara no {CLARA}", now, channel="whatsapp"))
    for n in range(40):
        await flow.send(
            WHATSAPP_SOURCE, said(f"wa-x{n}", f"pedido {700000 + n} de {PHONE}", now, channel="whatsapp")
        )
    await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi", now))
    await flow.drain()
    read: list[int] = []
    original = flow.ingest.redact_named

    async def counted(*args: Any) -> Redacted:
        found = await original(*args)
        read.append(found.rows)
        return found

    monkeypatch.setattr(flow.ingest, "redact_named", counted)

    await erase(flow, Handle(type=HandleType.PHONE_E164, value=CLARA))

    assert read == [1], "one event named her, and only it was rewritten"
    assert texts(cell)["wa-1"] == f"liga pra Clara no {ERASED}"
    assert all(PHONE in (t or "") for c, t in texts(cell).items() if c.startswith("wa-x"))
