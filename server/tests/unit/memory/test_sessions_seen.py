"""Confirmation by distinct sessions, not by mentions: a value repeated in one conversation
is confirmed once; each other conversation confirms it again (5.3)."""

from datetime import timedelta

from niadra.domain.extract.result import ExtractionResult
from niadra.domain.records import EventRecord, SessionRecord
from niadra.domain.vocabulary import Speaker
from niadra.testing.cell import Cell
from tests.unit.extract.world import Services
from tests.unit.memory.test_service import EPISODE, add, facts, reconcile, setup

POSTAL_CODE = "CEP de entrega 88221-615"


def conversation(
    s: Services, profile: object, turns: int, *, days_ago: int
) -> tuple[SessionRecord, list[EventRecord]]:
    lines = [(Speaker.CUSTOMER, f"{POSTAL_CODE}, repito ({i})") for i in range(turns)]
    session = s.world.session(profile, lines, start=s.cell.clock.now() - timedelta(days=days_ago))  # type: ignore[arg-type]
    return session, s.world.session_events(session)


async def said_in(s: Services, session: SessionRecord, events: list[EventRecord]) -> None:
    result = ExtractionResult(episode=EPISODE, facts=tuple(add(POSTAL_CODE, e, "note") for e in events))
    await reconcile(s, session, result)


async def test_ten_mentions_in_one_session_confirm_once_and_three_sessions_three_times(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    first, turns = conversation(s, profile, 10, days_ago=30)
    await said_in(s, first, turns)
    row = (await facts(s, profile.handle_ids))[POSTAL_CODE]
    assert (row.times_seen, row.sessions_seen) == (10, 1)

    # Extracting the same session again (a new extractor, a redelivered task) confirms nothing new.
    await said_in(s, first, turns)
    assert (await facts(s, profile.handle_ids))[POSTAL_CODE].sessions_seen == 1

    for days_ago in (20, 10):
        session, again = conversation(s, profile, 2, days_ago=days_ago)
        await said_in(s, session, again)
    row = (await facts(s, profile.handle_ids))[POSTAL_CODE]
    assert (row.times_seen, row.sessions_seen) == (14, 3)


async def test_a_quarantined_observation_never_confirms(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    first, turns = conversation(s, profile, 1, days_ago=10)
    await said_in(s, first, turns)
    second, injected = conversation(s, profile, 1, days_ago=5)
    result = ExtractionResult(
        episode=EPISODE, facts=(add(POSTAL_CODE, injected[0], "note", quarantined=True),)
    )
    await reconcile(s, second, result)
    assert (await facts(s, profile.handle_ids))[POSTAL_CODE].sessions_seen == 1
