"""`PgEventRepository` against PostgreSQL: encryption round trip, reads, the rotating dedupe under
concurrency, and row-level security between spaces."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import ConversationTouch, NewEvent
from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.vocabulary import EventKind, Speaker

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def _event(
    space: UUID,
    *,
    at: datetime = NOW,
    conversation: str = "conv-1",
    handles: tuple[UUID, ...] = (),
    kind: EventKind = EventKind.MESSAGE,
    **extra: object,
) -> NewEvent:
    record = EventRecord(
        event_id=uuid7(int(at.timestamp() * 1000)),
        kind=kind,
        source_id=UUID(int=1),
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=handles or (uuid4(),),
        conversation_id=conversation,
        content_meta={"type": "text", "stt_confidence": 0.9},
        **extra,  # type: ignore[arg-type]
    )
    return NewEvent(space, record, f"key-{record.event_id}")


async def test_events_round_trip_encrypted(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    handle = uuid4()
    message = _event(space, handles=(handle,), text="a fatura veio errada")
    action = _event(
        space,
        at=NOW + timedelta(seconds=5),
        handles=(handle,),
        kind=EventKind.ACTION,
        text=None,
        action={"operation": "credit", "result": "credited 12"},
        fields={"amount": 12},
        object_refs=(ObjectKey("invoice", "erp", "0823"),),
    )
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([message, action])

    async with pg_uow(space) as uow:
        raw = (
            await uow.session.execute(text("SELECT content_ct, action FROM events ORDER BY event_id"))
        ).all()
        by_id = await uow.ingest.get_events([action.record.event_id, message.record.event_id])
        between = await uow.ingest.events_between(
            conversation_id="conv-1", since=NOW, until=NOW + timedelta(minutes=1)
        )
        recent = await uow.ingest.events_for_handles([handle], NOW - timedelta(minutes=1), limit=1)
        rebuilt = await uow.ingest.recent_events(NOW - timedelta(minutes=1), 10)
        first = await uow.ingest.recent_events(NOW - timedelta(minutes=1), 1)
        second = await uow.ingest.recent_events(NOW - timedelta(minutes=1), 1, after=first[0].event_id)

    assert b"fatura" not in raw[0].content_ct
    assert raw[1].action == {"operation": "credit"}  # the free-text result stays encrypted
    assert [e.text for e in by_id] == ["a fatura veio errada", None]
    assert by_id[0].stt_confidence == pytest.approx(0.9)
    assert by_id[1].action == {"operation": "credit", "result": "credited 12"}
    assert by_id[1].fields == {"amount": 12}
    assert by_id[1].object_refs == (ObjectKey("invoice", "erp", "0823"),)
    assert [e.event_id for e in between] == [message.record.event_id, action.record.event_id]
    assert [e.event_id for e in recent] == [action.record.event_id]
    assert len(rebuilt) == 2
    assert [e.event_id for e in first + second] == [message.record.event_id, action.record.event_id]


async def test_conversation_upsert_merges_aliases_and_widens_times(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    first = ConversationTouch("trunk-9", "voice", NOW, NOW, frozenset(), None, dirty=False)
    later = ConversationTouch(
        "trunk-9",
        "voice",
        NOW - timedelta(minutes=1),
        NOW + timedelta(minutes=2),
        frozenset({"platform-1"}),
        uuid4(),
        dirty=True,
    )
    async with pg_uow(space) as uow:
        await uow.ingest.upsert_conversation(space, first)
        await uow.ingest.upsert_conversation(space, later)
        found = await uow.ingest.find_conversation(["platform-1"])
        row = (await uow.session.execute(text("SELECT * FROM conversations"))).one()

    assert found == "trunk-9"
    assert row.aliases == ["platform-1"]
    assert row.dirty is True
    assert (row.first_event_at, row.last_event_at) == (NOW - timedelta(minutes=1), NOW + timedelta(minutes=2))


async def test_heartbeats_accumulate(pg_uow: PgUnitOfWorkFactory) -> None:
    space, source = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        await uow.ingest.bump_heartbeat(space, source, NOW, received=3, rejected=1, last_event_at=NOW)
        await uow.ingest.bump_heartbeat(space, source, NOW, sent=5)
        await uow.ingest.bump_heartbeat(space, source, NOW, received=2, unmapped=1)
        row = (await uow.session.execute(text("SELECT * FROM source_heartbeats"))).one()

    assert (row.sent, row.received, row.rejected, row.unmapped, row.last_event_at) == (5, 5, 1, 1, NOW)


async def _rotate(pg_uow: PgUnitOfWorkFactory, space: UUID, at: datetime) -> int | None:
    # As the maintenance role: the rotation truncates the retired table.
    async with pg_uow.system(space) as uow:
        return await uow.ingest.rotate_dedupe(at, timedelta(0))


async def _claim(pg_uow: PgUnitOfWorkFactory, space: UUID, key: str) -> set[str]:
    async with pg_uow(space) as uow:
        return (await uow.ingest.claim_keys(space, [(key, uuid7())], NOW))[1]


async def test_dedupe_spans_the_previous_generation_and_forgets_the_retired_one(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid4()
    assert await _claim(pg_uow, space, "k1") == set()
    assert await _claim(pg_uow, space, "k1") == {"k1"}

    await _rotate(pg_uow, space, NOW + timedelta(days=30))
    assert await _claim(pg_uow, space, "k1") == {"k1"}  # found in the previous table

    await _rotate(pg_uow, space, NOW + timedelta(days=60))
    assert await _claim(pg_uow, space, "k1") == set()  # its table was truncated


async def test_released_keys_can_be_claimed_again(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        generation, _ = await uow.ingest.claim_keys(space, [("k1", uuid7())], NOW)
        await uow.ingest.release_keys(space, generation, ["k1"])
    assert await _claim(pg_uow, space, "k1") == set()


async def test_concurrent_claims_of_one_key_let_exactly_one_through(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    first_claimed = asyncio.Event()
    release_first = asyncio.Event()

    async def slow_writer() -> set[str]:
        async with pg_uow(space) as uow:
            _, known = await uow.ingest.claim_keys(space, [("same", uuid7())], NOW)
            first_claimed.set()
            await release_first.wait()
            return known

    first = asyncio.create_task(slow_writer())
    await first_claimed.wait()
    second = asyncio.create_task(_claim(pg_uow, space, "same"))
    await asyncio.sleep(0.3)
    assert not second.done()  # waits on the unique index until the first commits
    release_first.set()

    assert await first == set()
    assert await second == {"same"}


async def test_rotation_waits_for_ingestion_in_flight(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    claimed = asyncio.Event()
    release = asyncio.Event()

    async def ingestion() -> None:
        async with pg_uow(space) as uow:
            await uow.ingest.claim_keys(space, [("in-flight", uuid7())], NOW)
            claimed.set()
            await release.wait()

    writer = asyncio.create_task(ingestion())
    await claimed.wait()
    rotation = asyncio.create_task(_rotate(pg_uow, space, NOW + timedelta(days=90)))
    await asyncio.sleep(0.3)
    assert not rotation.done()  # the exclusive lock waits for the shared one
    release.set()
    await writer
    assert await rotation is not None

    # The key committed under the old generation is visible in the new previous table.
    assert await _claim(pg_uow, space, "in-flight") == {"in-flight"}


async def test_row_level_security_isolates_spaces(
    pg_uow: PgUnitOfWorkFactory, restricted_uow: PgUnitOfWorkFactory
) -> None:
    mine, theirs = uuid4(), uuid4()
    async with restricted_uow(mine) as uow:
        await uow.ingest.insert_events([_event(mine, conversation="c-mine", text="meu")])
        await uow.ingest.upsert_conversation(mine, ConversationTouch("c-mine", "chat", NOW, NOW))

    async with restricted_uow(theirs) as uow:
        assert await uow.ingest.events_between(conversation_id="c-mine", since=NOW, until=NOW) == []
        assert await uow.ingest.find_conversation(["c-mine"]) is None
    with pytest.raises(DBAPIError, match="row-level security"):
        async with restricted_uow(theirs) as uow:
            await uow.ingest.upsert_conversation(mine, ConversationTouch("c-other", "chat", NOW, NOW))

    async with restricted_uow(mine) as uow:
        assert len(await uow.ingest.events_between(conversation_id="c-mine", since=NOW, until=NOW)) == 1


async def test_the_system_events_of_some_handles_newest_first(pg_uow: PgUnitOfWorkFactory) -> None:
    # History shows system events as rows; messages of the same handle never crowd them out.
    space, handle = uuid4(), uuid4()
    chatter = [_event(space, at=NOW + timedelta(seconds=i), handles=(handle,)) for i in range(5)]
    system = [
        _event(
            space, at=NOW + timedelta(minutes=i), handles=(handle,), kind=EventKind.SYSTEM_EVENT, text=None
        )
        for i in range(1, 4)
    ]
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([*chatter, *system])
    async with pg_uow(space) as uow:
        found = await uow.ingest.system_events_for_handles([handle], NOW - timedelta(hours=1), 2)
        other = await uow.ingest.system_events_for_handles([uuid4()], NOW - timedelta(hours=1), 2)
    assert [e.event_id for e in found] == [system[2].record.event_id, system[1].record.event_id]
    assert other == []


async def test_the_application_role_can_add_events_but_never_change_or_delete_them(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    # The log only grows for the role every request runs as; only the system role, in the erasure
    # cascade, the archive purge and the content marks, may change or remove a stored event.
    space = uuid4()
    event = _event(space)
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([event])
    week_ms = 7 * 24 * 3600 * 1000
    later = (int(NOW.timestamp() * 1000) // week_ms + 40) * week_ms
    async with pg_uow.system(space) as uow:
        # A partition made after the revision is born without the rights too.
        await uow.session.execute(text("SELECT ensure_week_partition('events', :ms)"), {"ms": later})
        existing = (
            await uow.session.execute(
                text(
                    "SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent = 'events'::regclass "
                    "ORDER BY 1 LIMIT 1"
                )
            )
        ).scalar_one()
    for statement in (
        "UPDATE events SET content_meta = '{}'::jsonb",
        "DELETE FROM events",
        f"DELETE FROM events_w{later // week_ms}",
        f"UPDATE {existing} SET content_meta = NULL",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with pg_uow(space) as uow:
                await uow.session.execute(text(statement))
    async with pg_uow.system(space) as uow:
        await uow.ingest.mark_content([event.record.event_id], {"injection": True})
    async with pg_uow(space) as uow:
        [stored] = await uow.ingest.get_events([event.record.event_id])
    assert stored.content_meta is not None
    assert stored.content_meta["injection"] is True
