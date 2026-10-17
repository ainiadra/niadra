"""The readers a subject's package pages through: each page starts after the last row read."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.audit import AuditService
from niadra.domain.common.ids import uuid7
from niadra.domain.records import Episode
from tests.integration.audit.test_receipts_pg import draft, service
from tests.integration.ingest.test_pg_events import NOW, _event
from tests.integration.objects.test_pg_objects import INVOICE, _action, _object


async def test_every_reader_pages_back_to_the_first_row(pg_uow: PgUnitOfWorkFactory) -> None:
    space, handle = uuid4(), uuid7()
    episode = Episode(
        uuid7(),
        1,
        handle,
        uuid7(),
        "whatsapp",
        uuid7(),
        "turn",
        "question",
        "billing",
        "billing",
        "resolved",
        NOW,
        NOW,
    )
    episodes = [
        replace(episode, episode_id=uuid7(), session_id=uuid7(), ended_at=NOW + timedelta(minutes=i % 2))
        for i in range(5)
    ]
    events = [_event(space, at=NOW + timedelta(seconds=i), handles=(handle,)) for i in range(5)]
    async with pg_uow(space) as uow:
        for e in episodes:
            await uow.memory.add_episode_version(e)
        await uow.ingest.insert_events(events)
        objects = [
            await uow.objects.add_object(_object(replace(INVOICE, external_id=str(i)), handle))
            for i in range(5)
        ]
        for i, obj in enumerate(objects):
            await uow.objects.add_action(_action(obj.object_id, minutes=i % 2))

    async with pg_uow(space) as uow:
        seen_episodes, before = [], None
        while page := await uow.memory.episodes_for([handle], limit=2, before=before):
            seen_episodes += page
            before = (page[-1].ended_at, page[-1].episode_id)
        seen_objects, cursor = [], None
        while page_o := await uow.objects.objects_for([handle], limit=2, before=cursor):
            seen_objects += page_o
            cursor = (page_o[-1].as_of, page_o[-1].object_id)
        seen_actions, after = [], None
        while page_a := await uow.objects.actions_for([handle], limit=2, before=after):
            seen_actions += page_a
            after = (page_a[-1].occurred_at, page_a[-1].action_id)
        seen_events, before_id = [], None
        while page_e := await uow.ingest.events_for_handles(
            [handle], NOW - timedelta(days=1), 2, before_id=before_id
        ):
            seen_events += page_e
            before_id = min(e.event_id for e in page_e)

    assert sorted(e.episode_id for e in seen_episodes) == sorted(e.episode_id for e in episodes)
    assert len(seen_objects) == len({o.object_id for o in seen_objects}) == 5
    assert len(seen_actions) == len({a.action_id for a in seen_actions}) == 5
    assert sorted(e.event_id for e in seen_events) == sorted(e.record.event_id for e in events)


async def test_receipts_of_a_profile_page_back(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    audit: AuditService = service(pg_uow)
    drafts = [draft(space, i, producer=f"p-{space}", profile_id=profile) for i in range(1, 6)]
    await audit.write(drafts)
    seen, before = [], None
    async with pg_uow(space) as uow:
        while page := await uow.audit.receipts_for_profile(profile, limit=2, before_id=before):
            seen += page
            before = page[-1]["receipt_id"]
    assert [r["receipt_id"] for r in seen] == [d.receipt_id for d in reversed(drafts)]
