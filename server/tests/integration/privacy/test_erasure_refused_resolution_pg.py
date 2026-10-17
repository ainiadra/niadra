"""A resolution of an open item the memory module refused, on PostgreSQL (8.4): the pair (item, event) in
`open_item_feedback_events` leads the erasure of the item to the event, which it leaves with ids and kinds
only, live or in the cold archive, as the resolution that closed the item; the pairs go with the item.. Reruns
change nothing."""

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import (
    InMemoryObjectStore,
)
from niadra.app.archive import content_aad
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import ConversationTouch
from niadra.domain.privacy.files import from_parquet
from niadra.domain.records import OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import OpenItemStatus
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_erasure_feedback_pg import NOW, _feedback, _said, _service

CLOSED = "resolvido no balcao"
REFUSED = "a cliente ligou do +5511955554444, mora na Rua das Flores 12"


def _item(origin: UUID, evidence: UUID) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=origin,
        description="Enviar a segunda via",
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=NOW,
        origin={"event_ids": [str(evidence)]},
    )


def _resolution(item: OpenItem, reason: str) -> dict[str, Any]:
    return {"action": "resolve_open_item", "open_item_id": str(item.item_id), "reason": reason}


async def _pairs(pg_uow: CloningUnitOfWork, space: UUID) -> set[tuple[UUID, UUID]]:
    async with pg_uow(space) as uow:
        rows = await uow.session.execute(text("SELECT item_id, event_id FROM open_item_feedback_events"))
    return {(r.item_id, r.event_id) for r in rows}


async def _archived(pg_uow: CloningUnitOfWork, space: UUID, objects: InMemoryObjectStore) -> dict[str, Any]:
    found = {}
    for key, data in sorted(objects.objects.items()):
        if key.startswith(f"archive/events/{space}/") and key.endswith(".parquet"):
            for row in from_parquet(data):
                plain = await pg_uow._keys.decrypt(
                    space, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
                )
                content = json.loads(plain)
                found[content["event_id"]] = content
    return found


@pytest.mark.parametrize("archived", [False, True], ids=["live", "archived"])
async def test_an_erasure_strips_the_resolutions_the_item_refused(
    pg_uow: CloningUnitOfWork, archived: bool
) -> None:
    space, rafa, other = uuid4(), uuid7(), uuid7()
    said = _said(space, rafa, "wa-1")
    item = _item(rafa, said.record.event_id)
    closing = _feedback(space, rafa, _resolution(item, CLOSED))
    # Refused: the item was closed already, and one sent for another customer.
    again = _feedback(space, rafa, _resolution(item, REFUSED))
    foreign = _feedback(space, other, _resolution(item, REFUSED))
    refused = [(item.item_id, again.record.event_id), (item.item_id, foreign.record.event_id)]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([said, closing, again, foreign])
        await uow.ingest.upsert_conversation(
            space, ConversationTouch("wa-1", "whatsapp", NOW, NOW, primary_handle=rafa)
        )
        assert await uow.memory.insert_open_item(item)
        resolved = replace(item, status=OpenItemStatus.RESOLVED, closed_at=NOW + timedelta(minutes=1))
        await uow.memory.update_open_item(resolved, closed_by_event=closing.record.event_id)
        assert await uow.memory.note_item_feedback(refused) == 2
        assert await uow.memory.note_item_feedback(refused) == 0
    assert await _pairs(pg_uow, space) == set(refused)
    service = _service(pg_uow, space)
    objects = service._objects
    assert isinstance(objects, InMemoryObjectStore)
    feedback = [closing.record.event_id, again.record.event_id, foreign.record.event_id]
    if archived:
        await service.archive_events(Task(TaskName.ARCHIVE_EVENTS, space, {"month": f"{NOW:%Y-%m}"}))
        async with pg_uow(space) as uow:
            assert await uow.ingest.get_events(feedback) == []
        assert REFUSED in str(await _archived(pg_uow, space, objects))

    accepted = await service.forget(
        space,
        ForgetRequest(target="conversation", conversation_id="wa-1"),
        idempotency_key="k1",
        requested_by="user:dpo",
    )
    request_id = UUID(accepted.request_id)
    erase = Task(TaskName.ERASE, space, {"request_id": str(request_id)})
    cold = Task(TaskName.ERASE_COLD, space, {"request_id": str(request_id)})
    await service.erase(erase)
    await service.erase_cold(cold)

    audit = {"action": "resolve_open_item", "open_item_id": str(item.item_id)}
    async with pg_uow(space) as uow:
        assert await uow.memory.open_items_for([rafa], tuple(OpenItemStatus)) == []
        live = await uow.ingest.get_events(feedback)
        done = await uow.privacy.get_erasure(space, request_id)
    assert done is not None
    assert await _pairs(pg_uow, space) == set()
    if archived:
        stored = await _archived(pg_uow, space, objects)
        assert {e: stored[str(e)]["fields"] for e in feedback} == dict.fromkeys(feedback, audit)
        assert all(stored[str(e)]["text"] is None for e in feedback)
        assert REFUSED not in str(stored)
        assert CLOSED not in str(stored)
        assert done.counts["cold_feedback_values"] == 3
    else:
        assert {e.event_id: (e.text, e.fields) for e in live} == dict.fromkeys(feedback, (None, audit))
        assert done.counts["tables"]["feedback_values"] == 3

    # A retried erasure and a retried `erase_cold` change nothing.
    objects_before = dict(objects.objects)
    await service.erase(erase)
    await service.erase_cold(cold)
    assert objects.objects == objects_before
    async with pg_uow(space) as uow:
        assert await uow.ingest.get_events(feedback) == live
