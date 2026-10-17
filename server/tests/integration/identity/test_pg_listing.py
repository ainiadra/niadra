"""The Console's customer list on PostgreSQL: the same rows as in memory, and what a page costs.

The micro database serves every space, so a page of the list runs a fixed number of statements, whatever
the size of the space, and takes its order from `ix_handles_last_seen` without sorting.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.contracts.common import Handle
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.identity.listing import ListOrder
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from tests.integration.identity.test_pg_identity import NOW, World, email, make_world, phone

T = HandleType
PAGE_STATEMENTS = 3
"""The space pin (`set_config`), a window of the index and the handles of the profiles it met."""
MOST_STATEMENTS = 7
"""A customer with many identifiers uses up a window: the page looks at three, then ends short."""


@pytest.fixture
def world(pg_uow: PgUnitOfWorkFactory) -> World:
    return make_world(pg_uow)


async def seen(world: World, minutes: int, *handles: Handle, kind: SubjectKind = SubjectKind.PERSON) -> UUID:
    async with world.uow(world.space_id) as uow:
        result = await world.service.attach(
            uow,
            list(handles),
            subject_kind=kind,
            method=AssertionMethod.SAME_EVENT,
            origin_event_id=uuid4(),
            level=Verification.V0,
            at=NOW + timedelta(minutes=minutes),
        )
    assert result.profile_id is not None
    return result.profile_id


async def listed(world: World, order: ListOrder = ListOrder.LAST_SEEN_DESC, **filters: Any) -> list[UUID]:
    """Every page of the list, two rows at a time."""
    found: list[UUID] = []
    cursor: str | None = None
    while True:
        rows, cursor = await world.service.list_profiles(
            world.space_id, kind=filters.get("kind"), order=order, cursor=cursor, limit=2
        )
        found += [row.profile_id for row in rows]
        if cursor is None:
            return found


@contextmanager
def statements() -> Iterator[list[tuple[str, Any]]]:
    seen_: list[tuple[str, Any]] = []

    def note(conn: Any, cursor: Any, statement: str, parameters: Any, *args: Any) -> None:
        seen_.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", note)
    try:
        yield seen_
    finally:
        event.remove(Engine, "before_cursor_execute", note)


async def test_every_customer_is_listed_once_in_both_orders(world: World) -> None:
    first = await seen(world, 1, phone(1), email(1))
    second = await seen(world, 2, phone(2))
    third = await seen(world, 3, phone(3))
    org = await seen(
        world,
        4,
        Handle(type=T.SYSTEM_ID, value="acct-1", scope="crm", subject_kind=SubjectKind.ACCOUNT),
        kind=SubjectKind.ACCOUNT,
    )
    await seen(world, 5, email(1))  # the first customer again, by the other identifier

    assert await listed(world) == [first, org, third, second]
    assert await listed(world, ListOrder.LAST_SEEN_ASC) == [second, third, org, first]
    assert await listed(world, kind=SubjectKind.ACCOUNT) == [org]
    assert await listed(world, kind=SubjectKind.PERSON) == [first, third, second]
    rows, _ = await world.service.list_profiles(
        world.space_id, kind=None, order=ListOrder.LAST_SEEN_DESC, cursor=None, limit=1
    )
    [row] = rows
    assert (row.handle_count, [h.type for h in row.handles]) == (2, [T.EMAIL, T.PHONE_E164])
    assert row.last_seen == NOW + timedelta(minutes=5)
    assert [h.value for h in row.handles] == ["person1@acme.com.br", phone(1).value]


async def test_an_erased_profile_leaves_the_list_and_keeps_its_pseudonym_for_receipts(world: World) -> None:
    kept = await seen(world, 1, phone(1))
    erased = await seen(world, 2, phone(2))
    async with world.raw(world.space_id) as uow:
        profile = await uow.identity.get_profile(erased)
        assert profile is not None
        scope = ErasureScope(uuid4(), TargetKind.PROFILE, profile.handle_ids, erased, None, NOW)
        await uow.identity.erase_for(scope, limit=50)

    assert await listed(world) == [kept]
    async with world.raw(world.space_id) as uow:
        names = await uow.identity.pseudonyms([kept, erased, uuid4()])
    assert names == {kept: names[kept], erased: profile.pseudonym}


async def test_a_page_costs_the_same_statements_whatever_the_size_of_the_space(world: World) -> None:
    for n in range(1, 4):
        await seen(world, n, phone(n))
    with statements() as small:
        await world.service.list_profiles(
            world.space_id, kind=None, order=ListOrder.LAST_SEEN_DESC, cursor=None, limit=50
        )
    for n in range(4, 64):
        await seen(world, n, phone(n), email(n))
    with statements() as large:
        rows, cursor = await world.service.list_profiles(
            world.space_id, kind=SubjectKind.PERSON, order=ListOrder.LAST_SEEN_DESC, cursor=None, limit=50
        )
    with statements() as next_page:
        await world.service.list_profiles(
            world.space_id, kind=None, order=ListOrder.LAST_SEEN_DESC, cursor=cursor, limit=50
        )

    assert (len(rows), cursor is not None) == (50, True)
    assert len(small) == len(large) == len(next_page) == PAGE_STATEMENTS


async def test_a_customer_with_many_identifiers_ends_the_page_short_and_the_list_goes_on(
    world: World,
) -> None:
    people = [await seen(world, n, phone(n)) for n in range(1, 4)]
    crowded = await seen(world, 10, phone(9))
    async with world.raw(world.space_id) as uow:
        added = []
        for n in range(70):
            key = HandleKey(T.EMAIL, "", f"alias{n}@acme.com.br")
            await uow.identity.add_handles(
                [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], NOW + timedelta(hours=n)
            )
            found = await uow.identity.find_handle(key.type, key.scope, key.value)
            assert found is not None
            added.append(found.handle_id)
        await uow.identity.set_membership(added, crowded, 2)

    with statements() as ran:
        rows, cursor = await world.service.list_profiles(
            world.space_id, kind=None, order=ListOrder.LAST_SEEN_DESC, cursor=None, limit=5
        )
    rest, end = await world.service.list_profiles(
        world.space_id, kind=None, order=ListOrder.LAST_SEEN_DESC, cursor=cursor, limit=5
    )

    assert ([row.profile_id for row in rows], rows[0].handle_count) == ([crowded], 71)
    assert len(ran) == MOST_STATEMENTS
    assert ([row.profile_id for row in rest], end) == (people[::-1], None)


async def test_the_walk_takes_its_order_from_the_index_and_never_sorts(world: World) -> None:
    for n in range(1, 30):
        await seen(world, n, phone(n))
    with statements() as ran:
        _, cursor = await world.service.list_profiles(
            world.space_id, kind=SubjectKind.PERSON, order=ListOrder.LAST_SEEN_DESC, cursor=None, limit=10
        )
        await world.service.list_profiles(
            world.space_id, kind=SubjectKind.PERSON, order=ListOrder.LAST_SEEN_DESC, cursor=cursor, limit=10
        )
    walk, parameters = next((s, p) for s, p in reversed(ran) if "AS near" in s)

    async with world.raw(world.space_id) as uow:
        connection = await uow.session.connection()
        # A table this small is cheaper to scan; the question is whether the index can give the order.
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        await connection.execute(text("SET LOCAL enable_sort = off"))
        plan = "\n".join(row[0] for row in await connection.exec_driver_sql("EXPLAIN " + walk, parameters))

    assert "ix_handles_last_seen" in plan
    assert "Sort" not in plan
    # The cursor and the kind are conditions of the index scan, not filters on fetched rows.
    condition = next(line for line in plan.splitlines() if "Index Cond" in line and "last_seen" in line)
    assert "subject_kind" in condition
