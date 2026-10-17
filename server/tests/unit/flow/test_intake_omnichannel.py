"""The omnichannel promise at the write path (5.2): the customer writes on WhatsApp for the first time, and
the voice agent that already verified their phone reads the turn in `live` as soon as the batch is
answered, before it is stored. The turn goes under the phone the WhatsApp id derives, which identity let
stand for the customer, and moves to the WhatsApp id's own handle once stored. A phone identity keeps
apart (a test or placeholder value, a company line) gets nothing ahead of the store."""

from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.ingest import normalized
from niadra.domain.identity.handles import HandleKey
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType, SubjectKind
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import remembered

PHONE = "+5511955554444"
PLACEHOLDER = "+5511999999999"


def verified(phone: str, at: str) -> dict[str, Any]:
    return {
        "type": "verify",
        "idempotency_key": f"call-{phone}-proof",
        "method": "network_attestation",
        "level": "V1",
        "conversation_id": f"call-{phone}",
        "handle": {"type": "phone_e164", "value": phone},
        "occurred_at": at,
    }


def whatsapp(phone: str, text: str, at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"wa-{phone}-{text[:6]}",
        "channel": "whatsapp",
        "conversation_id": f"wa-{phone}",
        "handles": [{"type": "wa_id", "value": phone.lstrip("+")}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at,
    }


def handle(cell: Cell, type: HandleType, value: str) -> UUID:
    from niadra.contracts.common import Handle

    key: HandleKey = normalized(Handle(type=type, value=value), SubjectKind.PERSON).key
    found: UUID = cell.uow.store.data["identity"]["keys"][key]
    return found


async def live(cell: Cell, handle_id: UUID) -> list[UUID]:
    return [t.event_id for t in (await cell.hot.read(SPACE_ID, [handle_id], 0)).turns]


def deferred(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    flow.ingest._workers = cell.bus  # as production wires it: the answer hands the store to a worker
    return flow


async def test_a_first_whatsapp_turn_is_live_for_the_verified_phone_before_it_is_stored(cell: Cell) -> None:
    flow = deferred(cell)
    now = cell.clock.now().isoformat()
    assert (await flow.send(VOICE_SOURCE, verified(PHONE, now))).errors == []
    phone = handle(cell, HandleType.PHONE_E164, PHONE)

    answer = await flow.send(WHATSAPP_SOURCE, whatsapp(PHONE, "o numero do meu pedido novo e 734211", now))

    assert answer.accepted == 1
    assert cell.uow.store.data["ingest"]["events"] == {} or all(
        e.record.channel != "whatsapp" for e in cell.uow.store.data["ingest"]["events"].values()
    )
    [early] = await live(cell, phone)

    await flow.drain(only={TaskName.APPLY_INTAKE})

    wa = handle(cell, HandleType.WA_ID, PHONE.lstrip("+"))
    [event] = [e for e in cell.uow.store.data["ingest"]["events"].values() if e.record.channel == "whatsapp"]
    assert event.record.event_id == early
    assert event.record.handle_ids == (wa,)
    assert await live(cell, phone) == []
    assert await live(cell, wa) == [early]


async def test_a_phone_identity_keeps_apart_gets_no_turn_ahead_of_the_store(cell: Cell) -> None:
    flow = deferred(cell)
    now = cell.clock.now().isoformat()
    await flow.send(VOICE_SOURCE, verified(PLACEHOLDER, now))
    phone = handle(cell, HandleType.PHONE_E164, PLACEHOLDER)

    await flow.send(WHATSAPP_SOURCE, whatsapp(PLACEHOLDER, "teste", now))

    assert await live(cell, phone) == []
    await flow.drain(only={TaskName.APPLY_INTAKE})
    assert await live(cell, phone) == []


async def test_a_company_line_gets_no_turn_ahead_of_the_store(cell: Cell) -> None:
    # A phone known as an organization's reaches many people (5.3): identity never lets it stand for one.
    flow = deferred(cell)
    now = cell.clock.now().isoformat()
    line = "+5511933332222"
    proof = verified(line, now)
    proof["handle"]["subject_kind"] = "account"
    assert (await flow.send(VOICE_SOURCE, proof)).errors == []
    phone = handle(cell, HandleType.PHONE_E164, line)

    await flow.send(WHATSAPP_SOURCE, whatsapp(line, "quero falar com o suporte", now))

    assert await live(cell, phone) == []
    await flow.drain(only={TaskName.APPLY_INTAKE})
