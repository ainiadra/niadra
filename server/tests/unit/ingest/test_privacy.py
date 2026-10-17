"""Ingest as a privacy participant and as a sample source: erasure, export, archive, raw payloads."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import orjson

from niadra.adapters.memory.ingest import InMemoryEventRepository
from niadra.adapters.memory.sessions import InMemorySessionRepository
from niadra.config.models import HandleExpr, Mapping, MappingRule, WebhookAuth
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.storage import event_blob_keys, media_key, media_ref, raw_key
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import SessionRecord
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.ingest.fakes import build, handle_id, held, message

PHONE = Handle(type=HandleType.PHONE_E164, value="+5511988887777")
EMAIL = Handle(type=HandleType.EMAIL, value="ana@example.com")


def repo(cell: Cell) -> InMemoryEventRepository:
    return InMemoryEventRepository(cell.uow.store)


def scope(kind: TargetKind, handles: tuple[UUID, ...] = (), conversation: str | None = None) -> ErasureScope:
    return ErasureScope(uuid4(), kind, handles, None, conversation, Cell().clock.now())


async def ingest(cell: Cell, *items: dict[str, Any]) -> None:
    service, _, _ = build(cell)
    valid, errors = validate_items(list(items))
    assert not errors
    await service.ingest_items(principal(), valid)


def with_media(key: str, ref: str, **overrides: Any) -> dict[str, Any]:
    return message(key, content={"type": "audio", "media_ref": ref, "transcript": "oi"}, **overrides)


async def test_forgetting_a_handle_erases_its_events_threads_and_blob_keys(cell: Cell) -> None:
    ref = media_ref(handle_id(PHONE), uuid7())
    other = {"handles": [EMAIL.model_dump()], "conversation_id": "conv-2"}
    await ingest(cell, message("m1"), with_media("m2", ref), message("m3", **other))
    target = scope(TargetKind.HANDLE, (handle_id(PHONE),))

    first = await repo(cell).erase_for(target, limit=1)
    rest = await repo(cell).erase_for(target, limit=10)

    rows = first + rest
    assert [r.table for r in rows] == ["events", "events", "conversations"]
    assert rows[2].key == {"conversation_id": "conv-1"}
    assert [media_key(SPACE_ID, ref)] == [k for r in rows for k in r.blob_keys]
    assert all(r.change_seq for r in rows)
    [left] = repo(cell).stored
    assert left.record.handle_ids == (handle_id(EMAIL),)
    assert await repo(cell).erase_for(target, limit=10) == []


async def test_forgetting_a_conversation_keeps_the_people_in_it(cell: Cell) -> None:
    await ingest(cell, message("m1"), message("m2", conversation_id="conv-2"))

    rows = await repo(cell).erase_for(scope(TargetKind.CONVERSATION, (handle_id(PHONE),), "conv-1"), limit=10)

    assert [(r.table, r.key) for r in rows][-1] == ("conversations", {"conversation_id": "conv-1"})
    assert [e.record.conversation_id for e in repo(cell).stored] == ["conv-2"]


def test_blob_keys_ignore_references_this_cell_never_issued() -> None:
    assert event_blob_keys(SPACE_ID, None, {"media_ref": "../../etc/passwd"}) == ()
    assert event_blob_keys(SPACE_ID, "raw/x", None) == ("raw/x",)
    assert media_key(SPACE_ID, media_ref(None, UUID(int=1))) == f"media/{SPACE_ID}/_/{UUID(int=1)}"


async def test_export_rows_follow_change_seq_and_leave_storage_keys_out(cell: Cell) -> None:
    await ingest(cell, message("m1"), message("m2"))

    events = await repo(cell).export_rows("events", after_seq=0, upto_seq=10**6, limit=10)
    threads = await repo(cell).export_rows("conversations", after_seq=0, upto_seq=10**6, limit=10)
    later = await repo(cell).export_rows(
        "events", after_seq=events[0]["change_seq"], upto_seq=10**6, limit=10
    )

    assert [e["text"] for e in events] == ["a fatura veio errada"] * 2
    assert "raw_ref" not in events[0]
    assert threads[0]["conversation_id"] == "conv-1"
    assert [e["event_id"] for e in later] == [events[1]["event_id"]]


async def test_archivable_events_page_by_id_inside_the_window(cell: Cell) -> None:
    await ingest(cell, message("m1"), message("m2"), message("m3"))
    now = cell.clock.now()

    first = await repo(cell).archivable_events(
        since=now, until=now + timedelta(seconds=1), after_id=None, limit=2
    )
    second = await repo(cell).archivable_events(
        since=now, until=now + timedelta(seconds=1), after_id=first[-1].event_id, limit=2
    )
    outside = await repo(cell).archivable_events(
        since=now - timedelta(days=1), until=now, after_id=None, limit=5
    )

    assert len(first) == 2
    assert len(second) == 1
    assert outside == []


async def test_webhook_payload_is_stored_under_the_first_handle_and_found_by_source(cell: Cell) -> None:
    config = sample_config().model_copy(
        update={
            "mappings": [
                Mapping(
                    source_id=BILLING_SOURCE,
                    version="m1",
                    auth=WebhookAuth(mode="url_token", secret_ref=f"spaces/{SPACE_ID}/webhook-in/wh"),
                    rules=[
                        MappingRule(
                            match="type == 'paid'",
                            canonical_type="invoice.paid",
                            idempotency_key="id",
                            occurred_at="at",
                            channel="erp",
                            handles=[HandleExpr(type=HandleType.SYSTEM_ID, value="customer", scope="erp")],
                        )
                    ],
                )
            ]
        }
    )
    cell.config.put(config)
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/wh"] = "t0k"
    service, _, _ = build(cell)
    body = b'{"type": "paid", "id": "e1", "at": "2026-09-22T16:00:00Z", "customer": "C-1"}'

    await service.ingest_webhook(BILLING_SOURCE, {}, body, {"token": "t0k"})
    again = await service.ingest_webhook(BILLING_SOURCE, {}, body, {"token": "t0k"})

    [event] = repo(cell).stored
    customer = handle_id(Handle(type=HandleType.SYSTEM_ID, value="C-1", scope="erp"))
    assert event.raw_ref is not None
    assert event.raw_ref.startswith(f"raw/{SPACE_ID}/{customer}/")
    assert again.duplicates == 1
    assert len([k for k in cell.objects.objects if k.startswith("raw/")]) == 1
    [(event_id, ref)] = await repo(cell).raw_refs(BILLING_SOURCE, limit=5)
    assert (event_id, ref) == (event.record.event_id, event.raw_ref)
    assert orjson.loads(cell.objects.objects[ref]) == orjson.loads(body)


async def test_recent_messages_are_the_sources_newest_first(cell: Cell) -> None:
    await ingest(cell, message("m1", text="primeira"), message("m2", text="segunda"))

    found = await repo(cell).recent_messages(WHATSAPP_SOURCE, limit=1)

    assert [m.text for m in found] == ["segunda"]
    assert await repo(cell).recent_messages(BILLING_SOURCE, limit=5) == []


async def test_feedback_is_reconciled_under_the_subjects_lease(cell: Cell) -> None:
    service, _, identity = build(cell)
    fact_id = await held(cell, PHONE)
    request = FeedbackRequest(idempotency_key="f1", subject=PHONE, action="retract_fact", fact_id=fact_id)

    await service.record_feedback(principal(), request)

    [event] = repo(cell).stored
    [task] = [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name is TaskName.RECONCILE]
    assert task.payload == {"event_id": str(event.record.event_id)}
    assert task.profile_id == identity.profiles[handle_id(PHONE)]


def test_raw_keys_name_the_handle_or_underscore() -> None:
    at = Cell().clock.now()
    assert raw_key(SPACE_ID, None, at, UUID(int=2)).startswith(f"raw/{SPACE_ID}/_/{at:%Y-%m-%d}/")
    assert raw_key(SPACE_ID, UUID(int=3), at, UUID(int=2)).startswith(f"raw/{SPACE_ID}/{UUID(int=3)}/")


async def test_sessions_are_erased_by_profile_or_conversation(cell: Cell) -> None:
    sessions = InMemorySessionRepository(cell.uow.store)
    now = cell.clock.now()
    profile = uuid4()
    for conversation, owner in (("c-1", profile), ("c-2", profile), ("c-3", uuid4())):
        record = SessionRecord(
            uuid7(), "conversation", WHATSAPP_SOURCE, "whatsapp", now, now, conversation, profile_id=owner
        )
        await sessions.create_session(SPACE_ID, record)

    by_conversation = await sessions.erase_for(scope(TargetKind.CONVERSATION, (), "c-3"), limit=10)
    handle_only = await sessions.erase_for(
        ErasureScope(uuid4(), TargetKind.HANDLE, (uuid4(),), profile, None, now), limit=10
    )
    by_profile = await sessions.erase_for(
        ErasureScope(uuid4(), TargetKind.PROFILE, (), profile, None, now), limit=1
    )
    rest = await sessions.erase_for(
        ErasureScope(uuid4(), TargetKind.PROFILE, (), profile, None, now), limit=10
    )

    assert len(by_conversation) == 1
    assert handle_only == []
    assert (len(by_profile), len(rest)) == (1, 1)
    assert sessions.all == []
