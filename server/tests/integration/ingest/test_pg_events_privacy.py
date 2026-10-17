"""`PgEventRepository` as a privacy participant, an export and archive source, a sample source for the
configuration assistant, and the store of file imports."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import ConversationTouch, NewEvent
from niadra.domain.ingest.files import FileFormat, FileImport, ImportStatus
from niadra.domain.ingest.storage import media_key, media_ref, raw_key
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import EventRecord
from niadra.domain.vocabulary import EventKind, Speaker

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
SOURCE = UUID(int=1)


def _event(
    space: UUID,
    handles: tuple[UUID, ...],
    *,
    conversation: str = "conv-1",
    at: datetime = NOW,
    raw: str | None = None,
    media: str | None = None,
    kind: EventKind = EventKind.MESSAGE,
    text_: str = "a fatura veio errada",
) -> NewEvent:
    record = EventRecord(
        event_id=uuid7(int(at.timestamp() * 1000)),
        kind=kind,
        source_id=SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=handles,
        text=text_,
        conversation_id=conversation,
        content_meta={"type": "audio", "media_ref": media} if media else None,
    )
    return NewEvent(space, record, f"key-{record.event_id}", raw_ref=raw)


def _scope(kind: TargetKind, handles: tuple[UUID, ...] = (), conversation: str | None = None) -> ErasureScope:
    return ErasureScope(uuid4(), kind, handles, None, conversation, NOW)


async def _touch(pg_uow: PgUnitOfWorkFactory, space: UUID, conversation: str, handle: UUID) -> None:
    async with pg_uow(space) as uow:
        await uow.ingest.upsert_conversation(
            space,
            ConversationTouch(
                conversation, "whatsapp", NOW, NOW, frozenset({"alias-" + conversation}), handle
            ),
        )


async def test_erasure_by_handle_deletes_events_their_index_and_threads(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana, bruno = uuid4(), uuid4(), uuid4()
    ref = media_ref(ana, uuid7())
    raw = raw_key(space, ana, NOW, uuid7())
    events = [
        _event(space, (ana,), raw=raw),
        _event(space, (ana, bruno), media=ref),
        _event(space, (bruno,), conversation="conv-2"),
    ]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events(events)
    await _touch(pg_uow, space, "conv-1", ana)
    await _touch(pg_uow, space, "conv-2", bruno)
    target = _scope(TargetKind.HANDLE, (ana,))

    async with pg_uow.system(space) as uow:
        first = await uow.ingest.erase_for(target, limit=1)
    async with pg_uow.system(space) as uow:
        rest = await uow.ingest.erase_for(target, limit=10)
        again = await uow.ingest.erase_for(target, limit=10)
        mine = {"space": space}
        left = (
            await uow.session.execute(
                text("SELECT count(*) FROM event_handles WHERE space_id = :space"), mine
            )
        ).scalar()
        threads = (
            (
                await uow.session.execute(
                    text("SELECT conversation_id FROM conversations WHERE space_id = :space"), mine
                )
            )
            .scalars()
            .all()
        )

    rows = first + rest
    assert [r.table for r in rows] == ["events", "events", "conversations"]
    assert {k for r in rows for k in r.blob_keys} == {raw, media_key(space, ref)}
    assert all(r.change_seq for r in rows)
    assert {r.key["event_id"] for r in rows[:2]} == {str(e.record.event_id) for e in events[:2]}
    assert again == []
    assert left == 1
    assert threads == ["conv-2"]


async def test_erasure_by_conversation_keeps_the_rest_of_the_person(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([_event(space, (ana,)), _event(space, (ana,), conversation="conv-2")])
    await _touch(pg_uow, space, "conv-1", ana)

    async with pg_uow.system(space) as uow:
        rows = await uow.ingest.erase_for(_scope(TargetKind.CONVERSATION, (ana,), "conv-1"), limit=10)
        remaining = await uow.ingest.events_for_handles([ana], NOW - timedelta(days=1), 10)

    assert [r.table for r in rows] == ["events", "conversations"]
    assert [e.conversation_id for e in remaining] == ["conv-2"]


async def test_export_rows_are_decrypted_in_change_seq_order(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        before = int((await uow.session.execute(text("SELECT nextval('change_seq')"))).scalar_one())
        await uow.ingest.insert_events(
            [_event(space, (ana,), raw="raw/x"), _event(space, (ana,), text_="segunda")]
        )
    await _touch(pg_uow, space, "conv-1", ana)

    async with pg_uow(space) as uow:
        events = await uow.ingest.export_rows("events", after_seq=before, upto_seq=before + 10_000, limit=10)
        threads = await uow.ingest.export_rows(
            "conversations", after_seq=before, upto_seq=before + 10_000, limit=1
        )
        page = await uow.ingest.export_rows(
            "events", after_seq=events[0]["change_seq"], upto_seq=10**12, limit=10
        )

    assert [e["text"] for e in events] == ["a fatura veio errada", "segunda"]
    assert events[0]["handle_ids"] == [ana]
    assert "raw_ref" not in events[0]
    assert threads[0]["aliases"] == ["alias-conv-1"]
    assert [e["text"] for e in page] == ["segunda"]


async def test_archivable_events_page_through_one_window(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana = uuid4(), uuid4()
    inside = [_event(space, (ana,), at=NOW + timedelta(seconds=i)) for i in range(3)]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([*inside, _event(space, (ana,), at=NOW + timedelta(days=8))])

    window = {"since": NOW, "until": NOW + timedelta(days=1)}
    async with pg_uow(space) as uow:
        first = await uow.ingest.archivable_events(**window, after_id=None, limit=2)
        second = await uow.ingest.archivable_events(**window, after_id=first[-1].event_id, limit=2)

    assert [e.event_id for e in first + second] == [e.record.event_id for e in inside]


async def test_an_archived_window_leaves_the_live_database(pg_uow: PgUnitOfWorkFactory) -> None:
    # Once the cold archive holds a month, its events and their handle index rows go.
    space, other_space, ana = uuid4(), uuid4(), uuid4()
    inside = [_event(space, (ana,), at=NOW + timedelta(seconds=i)) for i in range(3)]
    later = _event(space, (ana,), at=NOW + timedelta(days=8))
    elsewhere = _event(other_space, (ana,), at=NOW)
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([*inside, later])
    async with pg_uow(other_space) as uow:
        await uow.ingest.insert_events([elsewhere])

    window = {"since": NOW, "until": NOW + timedelta(days=1)}
    async with pg_uow.system(space) as uow:
        assert await uow.ingest.purge_archived(**window, limit=2) == 2
    async with pg_uow.system(space) as uow:
        assert await uow.ingest.purge_archived(**window, limit=2) == 1
        assert await uow.ingest.purge_archived(**window, limit=2) == 0
        left = await uow.ingest.get_events([e.record.event_id for e in [*inside, later]])
        indexed = await uow.ingest.events_for_handles([ana], NOW - timedelta(days=1), limit=10)
    async with pg_uow(other_space) as uow:
        untouched = await uow.ingest.get_events([elsewhere.record.event_id])

    assert [e.event_id for e in left] == [later.record.event_id]
    assert [e.event_id for e in indexed] == [later.record.event_id]
    assert [e.event_id for e in untouched] == [elsewhere.record.event_id]


async def test_raw_refs_and_recent_messages_of_a_source(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana = uuid4(), uuid4()
    one, two = "raw/one", "raw/two"
    events = [
        _event(space, (ana,), raw=one, at=NOW),
        _event(space, (ana,), raw=one, at=NOW + timedelta(seconds=1), kind=EventKind.SYSTEM_EVENT),
        _event(space, (ana,), raw=two, at=NOW + timedelta(seconds=2)),
    ]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events(events)

    async with pg_uow(space) as uow:
        refs = await uow.ingest.raw_refs(SOURCE, limit=5)
        messages = await uow.ingest.recent_messages(SOURCE, limit=5)
        other = await uow.ingest.raw_refs(uuid4(), limit=5)

    assert refs == [(events[2].record.event_id, two), (events[1].record.event_id, one)]
    assert [m.event_id for m in messages] == [events[2].record.event_id, events[0].record.event_id]
    assert other == []


async def test_file_imports_round_trip(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    record = FileImport(
        import_id=uuid7(),
        source_id=SOURCE,
        key_id="k1",
        scopes=frozenset({"track", "identify"}),
        format=FileFormat.CSV,
        status=ImportStatus.QUEUED,
        size_bytes=120,
        sha256="ab" * 32,
        part_rows=(500, 3),
        created_at=NOW,
        header=("email", "phone_e164"),
    )
    async with pg_uow(space) as uow:
        await uow.ingest.create_import(space, record)
        await uow.ingest.create_import(space, record)
    progressed = record.progressed(
        next_part=1, next_row=0, accepted=499, duplicates=0, errors=[{"row": 7, "code": "x", "detail": None}]
    )
    async with pg_uow(space) as uow:
        await uow.ingest.save_import(progressed)
    async with pg_uow(space) as uow:
        stored = await uow.ingest.get_import(record.import_id)
    async with pg_uow(uuid4()) as uow:
        elsewhere = await uow.ingest.get_import(record.import_id)

    assert stored == progressed
    assert elsewhere is None


async def test_coverage_sums_heartbeats_per_source_and_utc_day(pg_uow: PgUnitOfWorkFactory) -> None:
    space, quiet = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        for at, sent, received in (
            (NOW, 5, 4),
            (NOW + timedelta(minutes=1), 2, 2),
            (NOW + timedelta(days=1), 1, 0),
        ):
            await uow.ingest.bump_heartbeat(space, SOURCE, at, sent=sent, received=received, last_event_at=at)
        await uow.ingest.bump_heartbeat(space, quiet, NOW - timedelta(days=3), received=9)
    stranger = uuid4()
    async with pg_uow(stranger) as uow:
        await uow.ingest.bump_heartbeat(stranger, SOURCE, NOW, sent=100)

    async with pg_uow(space) as uow:
        found = await uow.ingest.coverage(NOW - timedelta(hours=1))

    assert list(found) == [SOURCE]
    today, tomorrow = found[SOURCE]
    assert (today.day, today.sent, today.received) == (NOW.date(), 7, 6)
    assert today.last_event_at == NOW + timedelta(minutes=1)
    assert (tomorrow.sent, tomorrow.received) == (1, 0)


async def test_received_since_is_true_only_for_an_accepted_event_in_the_window(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        await uow.ingest.bump_heartbeat(space, SOURCE, NOW - timedelta(days=3), sent=4, received=4)
        await uow.ingest.bump_heartbeat(space, SOURCE, NOW, sent=2, rejected=2)

    async with pg_uow(space) as uow:
        ever = await uow.ingest.received_since(NOW - timedelta(days=7))
        today = await uow.ingest.received_since(NOW - timedelta(hours=1))
    async with pg_uow(uuid4()) as uow:
        elsewhere = await uow.ingest.received_since(NOW - timedelta(days=7))

    assert (ever, today, elsewhere) == (True, False, False)
