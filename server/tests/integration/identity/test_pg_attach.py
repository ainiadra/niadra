"""`attach` on PostgreSQL: concurrent attaches of the same new handles, a split that the next event does not
undo, and the statements an attach costs the micro database (5.3)."""

import asyncio
from itertools import permutations
from uuid import uuid4

from niadra.app.collaborators import AttachResult
from niadra.contracts.common import Handle
from niadra.domain.identity.model import AssertionState
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleType, SubjectKind, Verification
from tests.integration.identity.test_pg_identity import NOW, World, make_world
from tests.integration.identity.test_pg_listing import statements

T = HandleType


async def attach(world: World, *handles: Handle) -> AttachResult:
    async with world.uow(world.space_id) as uow:
        return await world.service.attach(
            uow,
            list(handles),
            subject_kind=SubjectKind.PERSON,
            method=AssertionMethod.SAME_EVENT,
            origin_event_id=uuid4(),
            level=Verification.V0,
            at=NOW,
        )


async def test_concurrent_attaches_of_the_same_new_handles_never_deadlock(pg_uow: object) -> None:
    """A CRM sync and a chat name one new customer at once, each in its own order: before the handles were
    inserted in key order, the two transactions locked them in opposite orders and one died."""
    world = make_world(pg_uow)  # type: ignore[arg-type]
    for n in range(25):
        handles = [
            Handle(type=T.PHONE_E164, value=f"+55119{n + 3100:04d}{n + 6200:04d}"),
            Handle(type=T.EMAIL, value=f"race{n}@acme.com.br"),
            Handle(type=T.SYSTEM_ID, value=f"c-{n}", scope="crm"),
        ]
        orders = list(permutations(handles))[:4]
        results = await asyncio.gather(*(attach(world, *order) for order in orders), return_exceptions=True)
        assert [r for r in results if isinstance(r, BaseException)] == [], f"round {n}"
        assert len({await world.profile_id(h) for h in handles}) == 1


async def test_a_split_survives_the_record_that_joined_them(pg_uow: object) -> None:
    world = make_world(pg_uow)  # type: ignore[arg-type]
    phone, email = (
        Handle(type=T.PHONE_E164, value="+5511987650001"),
        Handle(type=T.EMAIL, value="bia@acme.com.br"),
    )
    await attach(world, phone)
    await attach(world, email)
    await attach(world, phone, email)
    for task in world.recorder.take(TaskName.IDENTITY_APPLY):
        await world.run(task)
    joined = await world.profile_id(phone)
    await world.service.unmerge(
        world.space_id,
        joined,
        handle_ids=[await world.handle_id(phone)],
        actor="user:ops",
        reason="two people",
    )
    for task in world.recorder.take(TaskName.IDENTITY_APPLY):
        await world.run(task)
    assert await world.profile_id(phone) != await world.profile_id(email)

    again = await attach(world, phone, email)
    assert again.merge is None
    assert world.recorder.take(TaskName.IDENTITY_APPLY) == []
    assert await world.profile_id(phone) != await world.profile_id(email)
    async with world.raw(world.space_id) as uow:
        [suggestion] = await uow.identity.open_suggestions(after=None, limit=10)
        assert suggestion.features["after_split"] is True
        pending = await uow.identity.list_assertions(None, AssertionState.PENDING, None, 10)
    assert pending == []


async def test_what_an_attach_costs_the_database(pg_uow: object) -> None:
    """A new customer named by three handles, the same event again (every turn of a conversation), and a
    new handle for a known customer. Each statement holds one of the space's two pooled connections."""
    world = make_world(pg_uow)  # type: ignore[arg-type]
    await attach(world, Handle(type=T.EMAIL, value="warm@acme.com.br"))  # data key and space lookups
    phone = Handle(type=T.PHONE_E164, value="+5511987654321")
    customer = [
        phone,
        Handle(type=T.EMAIL, value="ana@acme.com.br"),
        Handle(type=T.SYSTEM_ID, value="c-1", scope="crm"),
    ]

    with statements() as new_customer:
        await attach(world, *customer)
    with statements() as same_again:
        await attach(world, *customer)
    with statements() as one_more:
        await attach(world, phone, Handle(type=T.APP_USER_ID, value="u-1", scope="app"))

    # The transaction's space pin, then: find, blocked values, insert, retired numbers, profile, assertions,
    # membership.
    assert len(new_customer) == 8, [s for s, _ in new_customer]
    # Find, membership, retired numbers, touch.
    assert len(same_again) == 5, [s for s, _ in same_again]
    # Find, blocked, insert, membership, retired numbers, the phone's partners this month, lock, assertion,
    # version, membership, touch.
    assert len(one_more) == 12, [s for s, _ in one_more]
