"""What each event's text names, kept beside it on PostgreSQL (5.14.0): an erasure finds the events of others
that name the target in one statement a page, whatever the size of the log, leaves out the target's own, and
an event's row follows it when it goes."""

from dataclasses import replace
from uuid import uuid4

from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_erasure_feedback_pg import _said
from tests.integration.privacy.test_raw_index_pg import statements


def _naming(space: object, handle: object, *tokens: bytes) -> NewEvent:
    event = _said(space, handle, f"c-{uuid4()}")  # type: ignore[arg-type]
    return replace(event, mentions=frozenset(tokens))


async def test_a_lookup_costs_one_statement_and_leaves_the_targets_own_out(pg_uow: CloningUnitOfWork) -> None:
    space, marcos, clara = uuid4(), uuid7(), uuid7()
    hers = _naming(space, clara, b"t-clara")
    his = _naming(space, marcos, b"t-clara", b"t-other")
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([hers, his])

    async def lookup() -> tuple[list[object], int]:
        async with pg_uow(space) as uow:
            with statements() as seen:
                found = await uow.ingest.events_naming([b"t-clara"], besides=[clara], after=None, limit=500)
        return found, len(seen)

    small = await lookup()
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([_naming(space, uuid7(), f"t-{n}".encode()) for n in range(2_000)])
    large = await lookup()

    assert small == large == ([his.record.event_id], 1)
    async with pg_uow(space) as uow:
        await uow.ingest.index_mentions([(his.record.event_id, frozenset({b"t-other"}))])
        assert await uow.ingest.events_naming([b"t-clara"], besides=[clara], after=None, limit=500) == []
        assert not await uow.ingest.mentions_ready()
        await uow.ingest.mark_mentions_ready()
        assert await uow.ingest.mentions_ready()
