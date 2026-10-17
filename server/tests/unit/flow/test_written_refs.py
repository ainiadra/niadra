"""An agent's action may name its object by an identifier it read on the object's line (`_written_refs`).
A value two of the customer's objects hold names neither: the action is refused as unknown, never written
on whichever of the two came first."""

from typing import Any

from niadra.domain.records import ObjectKey
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_object_memory import CLIENT, FIRM, NUMBER, open_cases, scenario


async def test_a_number_two_of_the_clients_objects_hold_names_neither(cell: Cell) -> None:
    flow = await scenario(cell)
    clock = cell.clock
    twin = {
        "type": "event", "kind": "system_event", "idempotency_key": "court-2", "channel": "erp",
        "canonical_type": "lawsuit.opened", "handles": [FIRM],
        "object_refs": [{"type": "lawsuit", "namespace": "court", "id": "case-2"}],
        "speaker": {"role": "system"}, "fields": {"case_number": NUMBER},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    assert (await flow.send(BILLING_SOURCE, twin)).errors == []
    await flow.drain()
    clock.advance(minutes=1)
    acted: dict[str, Any] = {
        "type": "event", "kind": "action", "idempotency_key": "wa-twin", "channel": "whatsapp",
        "conversation_id": "wa-twin", "handles": [CLIENT], "speaker": {"role": "ai_agent"},
        "object_refs": [{"type": "lawsuit", "namespace": "court", "id": NUMBER}],
        "action": {"operation": "notify_client", "result": "Avisei o cliente da audiência"},
        "occurred_at": clock.now().isoformat(),
    }  # fmt: skip
    result = await flow.send(WHATSAPP_SOURCE, acted)
    assert [(e.index, e.code) for e in result.errors] == [(0, "unknown_object")]
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.objects.get_object(ObjectKey("lawsuit", "court", NUMBER)) is None


async def test_an_agent_names_a_clients_oldest_case_by_its_number_among_many(cell: Cell) -> None:
    """An identifier was resolved over the 20 newest objects of the subject, so an attendant
    that named a client's sixth case by its number was refused `unknown_object`."""

    async def many(flow: Flow) -> None:
        await open_cases(flow, cell, 60)

    flow = await scenario(cell, before_chat=many)
    cell.clock.advance(minutes=1)
    acted: dict[str, Any] = {
        "type": "event", "kind": "action", "idempotency_key": "wa-oldest", "channel": "whatsapp",
        "conversation_id": "wa-oldest", "handles": [CLIENT], "speaker": {"role": "ai_agent"},
        "object_refs": [{"type": "lawsuit", "namespace": "court", "id": NUMBER}],
        "action": {"operation": "notify_client", "result": "Avisei o cliente da audiência"},
        "occurred_at": cell.clock.now().isoformat(),
    }  # fmt: skip
    result = await flow.send(WHATSAPP_SOURCE, acted)
    assert result.errors == []
    await flow.drain()
    async with cell.uow(SPACE_ID) as uow:
        case = await uow.objects.get_object(ObjectKey("lawsuit", "court", "case-1"))
        assert case is not None
        assert await uow.objects.get_object(ObjectKey("lawsuit", "court", NUMBER)) is None
