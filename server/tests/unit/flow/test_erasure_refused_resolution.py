"""A resolution of an open item the memory module refused (8.4): the item was already closed, or it is not
the subject's. `open_items.closed_by_event_id` names only the resolution that closed the item, so the
refused one is recorded with the item it names (`open_item_feedback_events`), and an erasure of the item
leaves it with ids and kinds only, live and in the cold archive, like the one that closed it."""

from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.errors import NotFound
from niadra.domain.records import OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleType, OpenItemStatus
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_erasure_cold_feedback import archive_bytes, archived_contents
from tests.unit.flow.test_erasure_feedback import EPOCH, ended, forget
from tests.unit.flow.test_erasure_leftovers import archive_month_of, phone_handle, with_item_and_value
from tests.unit.flow.test_first_read import PHONE, remembered, said

CLOSED = "resolvido no balcao"
REFUSED = "a cliente ligou de novo do +5511955554444 e disse que mora na Rua das Flores 12"
OTHER_PHONE = "+5511966667777"


async def _item(flow: Flow) -> OpenItem:
    handle = await phone_handle(flow)
    async with flow.cell.uow(SPACE_ID) as uow:
        [item] = await uow.memory.open_items_for([handle], tuple(OpenItemStatus))
    return item


async def _resolve(flow: Flow, item: OpenItem, key: str, reason: str, phone: str = PHONE) -> None:
    # A second apart, so the log's order is the order sent.
    flow.cell.clock.advance(seconds=1)
    request = FeedbackRequest(
        idempotency_key=key,
        subject=Handle.model_validate({"type": "phone_e164", "value": phone}),
        action="resolve_open_item",
        open_item_id=str(item.item_id),
        reason=reason,
    )
    config = flow.cell.config.space(SPACE_ID)
    assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), request)).accepted == 1
    await flow.drain()


async def _resolutions(flow: Flow) -> list[tuple[str | None, dict[str, Any]]]:
    """The text and fields of every `feedback.resolve_open_item` event in the live log, both customers'."""
    async with flow.cell.uow(SPACE_ID) as uow:
        handles = [
            found.handle_id
            for phone in (PHONE, OTHER_PHONE)
            if (found := await uow.identity.find_handle(HandleType.PHONE_E164, "", phone)) is not None
        ]
        events = await uow.ingest.system_events_for_handles(handles, EPOCH, 100)
    return [
        (e.text, e.fields)
        for e in sorted(events, key=lambda e: e.event_id)
        if e.canonical_type == "feedback.resolve_open_item"
    ]


def _audit(item: OpenItem) -> dict[str, Any]:
    return {"action": "resolve_open_item", "open_item_id": str(item.item_id)}


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(with_item_and_value(remembered())))
    now = cell.clock.now().isoformat()
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    return flow


async def test_an_erasure_strips_the_resolution_refused_because_the_item_was_closed(flow: Flow) -> None:
    item = await _item(flow)
    await _resolve(flow, item, "fb-1", CLOSED)
    # The item is closed now: a second resolution is refused, and its reason says more than the first.
    await _resolve(flow, item, "fb-2", REFUSED)
    [(_, closing), (_, refused)] = await _resolutions(flow)
    assert (closing["reason"], refused["reason"]) == (CLOSED, REFUSED)

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    assert await _resolutions(flow) == [(None, _audit(item))] * 2
    assert out.erased["feedback_values"] == 2

    # A retried erasure changes nothing.
    await flow.privacy.erase(Task(TaskName.ERASE, SPACE_ID, {"request_id": out.request_id}))
    assert await _resolutions(flow) == [(None, _audit(item))] * 2
    assert (await flow.privacy.erasure_status(SPACE_ID, UUID(out.request_id))).erased == out.erased


async def test_an_erasure_strips_a_resolution_sent_for_another_customer(flow: Flow) -> None:
    """The item is not the subject's, so the resolution is refused: `/v1/feedback` answers 404 and stores
    nothing, and the same event sent raw through the batch is skipped by memory. That event lives on the other
    customer's handle, which the erasure of the conversation does not take."""
    item = await _item(flow)
    with pytest.raises(NotFound):
        await _resolve(flow, item, "fb-other", REFUSED, phone=OTHER_PHONE)
    assert await _resolutions(flow) == []
    raw = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "fb-other-raw",
        "channel": "feedback",
        "handles": [{"type": "phone_e164", "value": OTHER_PHONE}],
        "speaker": {"role": "system"},
        "canonical_type": "feedback.resolve_open_item",
        "fields": {"action": "resolve_open_item", "open_item_id": str(item.item_id), "reason": REFUSED},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, raw)).errors == []
    await flow.drain()
    assert (await _item(flow)).status is OpenItemStatus.OPEN
    [(_, refused)] = await _resolutions(flow)
    assert refused["reason"] == REFUSED

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    assert await _resolutions(flow) == [(None, _audit(item))]
    assert out.erased["feedback_values"] == 1


async def test_an_erasure_strips_the_refused_resolution_in_the_cold_archive(flow: Flow) -> None:
    started = flow.cell.clock.now()
    item = await _item(flow)
    await _resolve(flow, item, "fb-1", CLOSED)
    await _resolve(flow, item, "fb-2", REFUSED)
    flow.cell.clock.advance(days=45)
    await archive_month_of(flow, started)
    assert await _resolutions(flow) == []
    assert REFUSED in str(await archived_contents(flow))

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    after = await archived_contents(flow)
    assert [c["fields"] for c in after] == [_audit(item), _audit(item)]
    assert all(c["text"] is None for c in after)
    assert REFUSED not in str(after)
    assert CLOSED not in str(after)
    async with flow.cell.uow(SPACE_ID) as uow:
        request = await uow.privacy.get_erasure(SPACE_ID, UUID(out.request_id))
    assert request is not None
    assert request.counts["cold_feedback_values"] == 2

    # A retried `erase_cold` rewrites nothing.
    stored = archive_bytes(flow)
    await flow.privacy.erase_cold(Task(TaskName.ERASE_COLD, SPACE_ID, {"request_id": out.request_id}))
    assert archive_bytes(flow) == stored
