"""Precedence by origin (11, 3.2): system of record > human agent > AI agent > customer.

Recency still decides first (5.8): a later statement is news. The origin decides between statements of
the same moment, and a record supersedes the other side's values stated at or before it, with the
`SUPERSEDES` edge that explains the change; a statement newer than the record stays beside it.
"""

from dataclasses import replace
from datetime import timedelta
from itertools import pairwise

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import ExtractionResult
from niadra.domain.memory.facts import Relation, contention_group, ref, settle, supersede
from niadra.domain.records import EventRecord, Fact, ProfileRecord, SessionRecord
from niadra.domain.vocabulary import EventKind, FactStatus, RelationType, Speaker
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell
from tests.unit.extract.world import Services
from tests.unit.memory.test_facts import T0, seen
from tests.unit.memory.test_service import EPISODE, add, reconcile, say, setup


def settled(rows: list[Fact]) -> list[Fact]:
    wanted = settle(contention_group(rows[0], rows, [], lambda f: True))
    return [replace(f, status=wanted[ref(f)][0], invalid_at=wanted[ref(f)][1]) for f in rows]


def test_between_statements_of_the_same_moment_the_origin_decides() -> None:
    order = [Speaker.CUSTOMER, Speaker.AI_AGENT, Speaker.HUMAN_AGENT]
    for low, high in pairwise(order):
        pair = [seen("Ana", 3, speaker=low), seen("Bia", 3, speaker=high)]
        for arranged in (pair, pair[::-1]):
            status = {f.value: f.status for f in settled(arranged)}
            assert status == {"Ana": FactStatus.EXPIRED, "Bia": FactStatus.ACTIVE}


def test_a_later_statement_still_wins_whoever_made_the_earlier_one() -> None:
    status = {
        f.value: f.status for f in settled([seen("Ana", 1, speaker=Speaker.HUMAN_AGENT), seen("Bia", 2)])
    }
    assert status == {"Ana": FactStatus.EXPIRED, "Bia": FactStatus.ACTIVE}


def test_a_record_supersedes_what_the_other_side_said_before_it() -> None:
    said = seen("Rua A", 2)
    record = seen("Rua B", 5, speaker=Speaker.SYSTEM)
    changed, edges = supersede([said, record], lambda f: True)
    assert changed == {ref(said): (FactStatus.EXPIRED, T0 + timedelta(days=5))}
    assert edges == [Relation(ref(record), ref(said), RelationType.SUPERSEDES, origin="rule")]


def test_a_statement_newer_than_the_record_stays_beside_it() -> None:
    record = seen("Rua B", 5, speaker=Speaker.SYSTEM)
    for later in (seen("Rua A", 7), seen("Rua A", 7, speaker=Speaker.HUMAN_AGENT)):
        assert supersede([later, record], lambda f: True) == ({}, [])


def test_the_same_value_other_slots_and_inactive_rows_are_left_alone() -> None:
    record = seen("Rua B", 5, speaker=Speaker.SYSTEM)
    same = seen("rua b", 2)
    other_slot = seen("Rua A", 2, predicate="address")
    expired = replace(seen("Rua C", 1), status=FactStatus.EXPIRED)
    assert supersede([record, same, other_slot, expired], lambda f: True) == ({}, [])
    # Precedence only settles single-valued slots.
    assert supersede([record, seen("Rua A", 2)], lambda f: False) == ({}, [])


def record_turn(cell: Cell, profile: ProfileRecord, days_ago: int) -> EventRecord:
    at = cell.clock.now() - timedelta(days=days_ago)
    return EventRecord(
        uuid7(), EventKind.SYSTEM_EVENT, BILLING_SOURCE, "erp", Speaker.SYSTEM, at, at, profile.handle_ids
    )


async def recorded(s: Services, profile: ProfileRecord, value: str, days_ago: int) -> SessionRecord:
    turn = record_turn(s.cell, profile, days_ago)
    session = s.world.session(profile, [turn], start=s.cell.clock.now() - timedelta(days=days_ago, minutes=1))
    event = s.world.session_events(session)[0]
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(add(value, event),)))
    return session


async def said(s: Services, profile: ProfileRecord, value: str, days_ago: int) -> None:
    session, event = say(s, profile, value, days_ago=days_ago)
    await reconcile(s, session, ExtractionResult(episode=EPISODE, facts=(add(value, event),)))


async def rows(s: Services, profile: ProfileRecord) -> list[Fact]:
    async with s.cell.uow(SPACE_ID) as uow:
        return await uow.memory.facts_for(profile.handle_ids, tuple(FactStatus))


async def state(s: Services, profile: ProfileRecord) -> dict[tuple[str, Speaker], FactStatus]:
    return {(f.value, f.speaker): f.status for f in await rows(s, profile)}


async def test_reconcile_retires_the_loser_with_supersedes_in_any_order(cell: Cell) -> None:
    states = []
    for record_first in (True, False):
        s = setup(Cell())
        profile = s.world.customer()
        if record_first:
            await recorded(s, profile, "Beatriz", days_ago=2)
            await said(s, profile, "Bia", days_ago=5)
        else:
            await said(s, profile, "Bia", days_ago=5)
            await recorded(s, profile, "Beatriz", days_ago=2)
        states.append(await state(s, profile))
        async with s.cell.uow(SPACE_ID) as uow:
            relations = await uow.memory.relations_for(profile.handle_ids)
        found = {f.value: f for f in await rows(s, profile)}
        assert [(r.type, r.a.id, r.b.id) for r in relations] == [
            (RelationType.SUPERSEDES, found["Beatriz"].fact_id, found["Bia"].fact_id)
        ]
    assert (
        states[0]
        == states[1]
        == {
            ("Beatriz", Speaker.SYSTEM): FactStatus.ACTIVE,
            ("Bia", Speaker.CUSTOMER): FactStatus.EXPIRED,
        }
    )


async def test_a_superseded_value_comes_back_when_it_is_stated_after_the_record(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    await said(s, profile, "Bia", days_ago=6)
    await recorded(s, profile, "Beatriz", days_ago=4)
    assert (await state(s, profile))[("Bia", Speaker.CUSTOMER)] is FactStatus.EXPIRED
    # The same value on the same handle folds into the customer's row (5.8), now newer than the record.
    await recorded(s, profile, "Bia", days_ago=1)
    assert await state(s, profile) == {
        ("Bia", Speaker.CUSTOMER): FactStatus.ACTIVE,
        ("Beatriz", Speaker.SYSTEM): FactStatus.ACTIVE,
    }


async def test_a_customer_statement_after_the_record_keeps_both(cell: Cell) -> None:
    s = setup(cell)
    profile = s.world.customer()
    await recorded(s, profile, "Beatriz", days_ago=4)
    await said(s, profile, "Bia", days_ago=1)
    assert await state(s, profile) == {
        ("Beatriz", Speaker.SYSTEM): FactStatus.ACTIVE,
        ("Bia", Speaker.CUSTOMER): FactStatus.ACTIVE,
    }
