"""The company's feedback in the cold archive on PostgreSQL (5.14.0, 8.4): a month archived with the
conversation, a correction and the resolution of an item; `forget` of the conversation, then `erase_cold`,
leaves in the archive no row of the conversation and no value or reason in the feedback rows, which stay
with ids and kinds only. A second `erase_cold` rewrites nothing."""

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from niadra.adapters.memory.fakes import InMemoryObjectStore
from niadra.app.archive import content_aad
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import ConversationTouch
from niadra.domain.privacy.files import from_parquet
from niadra.domain.records import OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, OpenItemStatus
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_erasure_feedback_pg import NOW, REASON, _corrected, _feedback, _service

ITEM_REASON = "a cliente pediu pelo telefone, mora na Rua das Flores 12"


async def _archived(
    pg_uow: CloningUnitOfWork, space: UUID, objects: InMemoryObjectStore
) -> list[dict[str, Any]]:
    found = []
    for key, data in sorted(objects.objects.items()):
        if key.startswith(f"archive/events/{space}/") and key.endswith(".parquet"):
            for row in from_parquet(data):
                plain = await pg_uow._keys.decrypt(
                    space, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
                )
                found.append(json.loads(plain))
    return found


async def test_an_erasure_rewrites_the_archived_feedback_to_ids_and_kinds(pg_uow: CloningUnitOfWork) -> None:
    space, rafa = uuid4(), uuid7()
    original, fixed, said, correction = await _corrected(pg_uow, space, rafa, rafa)
    item = OpenItem(
        item_id=uuid7(),
        origin_handle_id=rafa,
        description="Enviar a segunda via",
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=NOW,
        origin={"event_ids": [str(said.event_id)]},
    )
    fields = {"action": "resolve_open_item", "open_item_id": str(item.item_id), "reason": ITEM_REASON}
    resolution = _feedback(space, rafa, fields)
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([resolution])
        await uow.ingest.upsert_conversation(
            space, ConversationTouch("wa-1", "whatsapp", NOW, NOW, primary_handle=rafa)
        )
        assert await uow.memory.insert_open_item(item)
        resolved = replace(item, status=OpenItemStatus.RESOLVED, closed_at=NOW + timedelta(minutes=1))
        await uow.memory.update_open_item(resolved, closed_by_event=resolution.record.event_id)
    service = _service(pg_uow, space)
    objects = service._objects
    assert isinstance(objects, InMemoryObjectStore)
    await service.archive_events(Task(TaskName.ARCHIVE_EVENTS, space, {"month": f"{NOW:%Y-%m}"}))
    async with pg_uow(space) as uow:
        live = [said.event_id, correction.event_id, resolution.record.event_id]
        assert await uow.ingest.get_events(live) == []
    before = await _archived(pg_uow, space, objects)
    assert len(before) == 3
    assert REASON in str(before)
    assert ITEM_REASON in str(before)
    assert fixed.value in str(before)

    accepted = await service.forget(
        space,
        ForgetRequest(target="conversation", conversation_id="wa-1"),
        idempotency_key="k1",
        requested_by="user:dpo",
    )
    request_id = UUID(accepted.request_id)
    await service.erase(Task(TaskName.ERASE, space, {"request_id": str(request_id)}))
    cold = Task(TaskName.ERASE_COLD, space, {"request_id": str(request_id)})
    await service.erase_cold(cold)

    async with pg_uow(space) as uow:
        assert await uow.memory.facts_for([rafa], tuple(FactStatus)) == []
        assert await uow.memory.open_items_for([rafa], tuple(OpenItemStatus)) == []
        done = await uow.privacy.get_erasure(space, request_id)
    assert done is not None
    after = await _archived(pg_uow, space, objects)
    assert {e["event_id"]: e["fields"] for e in after} == {
        str(correction.event_id): {"action": "correct_fact", "fact_id": str(original.fact_id)},
        str(resolution.record.event_id): {"action": "resolve_open_item", "open_item_id": str(item.item_id)},
    }
    assert all(e["text"] is None for e in after)
    assert REASON not in str(after)
    assert ITEM_REASON not in str(after)
    assert fixed.value not in str(after)
    assert (done.counts["cold_rows"], done.counts["cold_feedback_values"]) == (1, 2)
    assert sorted(done.counts["cold_feedback"]) == sorted(
        [str(correction.event_id), str(resolution.record.event_id)]
    )

    stored = dict(objects.objects)
    await service.erase_cold(cold)
    assert objects.objects == stored
