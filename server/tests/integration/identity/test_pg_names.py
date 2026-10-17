"""The names and contacts the Console shows customers by, on PostgreSQL: the same as in memory, sealed rows
opened only for the name predicates, and a fixed number of statements for any page."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.contracts.common import Handle
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.memory.facts import normalize_value
from niadra.domain.records import BusinessObject, Fact, ObjectKey, ProfileAttr
from niadra.domain.vocabulary import (
    AssertionMethod,
    Environment,
    FactStatus,
    HandleType,
    Sensitivity,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import BILLING_SOURCE, TENANT_ID, WHATSAPP_SOURCE
from tests.integration.graph.test_pg_graph import statements
from tests.integration.identity.test_pg_identity import NOW, World, make_world

T = HandleType
SHOWN_STATEMENTS = 6
"""The space pin, the profiles, their handles, the name facts, the name slots and the own records."""


@pytest.fixture
def world(pg_uow: PgUnitOfWorkFactory) -> World:
    return make_world(pg_uow)


def reader(world: World, *roles: str) -> PersonPrincipal:
    return PersonPrincipal(
        SpaceRef(world.space_id, TENANT_ID, "us-east-2", Environment.SANDBOX), uuid4(), frozenset(roles)
    )


async def seen(world: World, *handles: Handle, kind: SubjectKind = SubjectKind.PERSON) -> UUID:
    async with world.uow(world.space_id) as uow:
        result = await world.service.attach(
            uow, list(handles), subject_kind=kind, method=AssertionMethod.SAME_EVENT, origin_event_id=uuid4(),
            level=Verification.V0, at=NOW,
        )  # fmt: skip
    assert result.profile_id is not None
    return result.profile_id


async def handle_id(world: World, profile_id: UUID) -> UUID:
    async with world.raw(world.space_id) as uow:
        profile = await uow.identity.get_profile(profile_id)
    assert profile is not None
    return profile.handle_ids[0]


async def said(world: World, profile_id: UUID, predicate: str, value: str) -> None:
    fact = Fact(
        uuid7(), await handle_id(world, profile_id), predicate, value, "contact", Sensitivity.NORMAL,
        Speaker.CUSTOMER, 0.9, NOW, NOW, FactStatus.ACTIVE, NOW,
    )  # fmt: skip
    async with world.raw(world.space_id) as uow:
        await uow.memory.insert_fact(fact, normalize_value(value))


async def test_names_and_contacts_as_each_role_sees_them(world: World) -> None:
    marina = await seen(world, Handle(type=T.PHONE_E164, value="+5511981172201"))
    company = await seen(
        world, Handle(type=T.SYSTEM_ID, value="ACC-2201", scope="crm", subject_kind=SubjectKind.ACCOUNT),
        kind=SubjectKind.ACCOUNT,
    )  # fmt: skip
    slot = await seen(world, Handle(type=T.EMAIL, value="jose.silva@acme.com.br"))
    await said(world, marina, "preferred_name", "Marina")
    await said(world, marina, "order_problem", "atraso")
    async with world.raw(world.space_id) as uow:
        await uow.objects.add_object(
            BusinessObject(
                uuid7(), ObjectKey("account", "crm", "ACC-2201"), {"nome_fantasia": "Horizonte Hotéis"}, NOW,
                BILLING_SOURCE, await handle_id(world, company),
            )
        )  # fmt: skip
        await uow.objects.add_object(
            BusinessObject(
                uuid7(), ObjectKey("order", "crm", "PED-1"), {"name": "Toalha"}, NOW + timedelta(hours=1),
                BILLING_SOURCE, await handle_id(world, company),
            )
        )  # fmt: skip
        await uow.memory.put_attr(
            ProfileAttr(await handle_id(world, slot), "name", "José da Silva", WHATSAPP_SOURCE, NOW)
        )

    security = await world.service.shown(reader(world, "security"), [marina, company, slot])
    integration = await world.service.shown(reader(world, "integration"), [marina, company, slot])

    assert (security[marina].name, security[marina].contact) == ("Marina", "+55 11 98117-2201")
    assert (security[company].name, security[company].contact) == ("Horizonte Hotéis", None)
    assert (security[slot].name, security[slot].contact) == ("José da Silva", "jose.silva@acme.com.br")
    assert (integration[marina].name, integration[marina].contact) == (None, "+55 ** *****-2201")
    assert integration[company].name == "Horizonte Hotéis"
    assert (integration[slot].name, integration[slot].contact) == (None, "j***@acme.com.br")


async def test_a_page_of_names_costs_the_same_statements_whatever_it_holds(world: World) -> None:
    few = [await seen(world, Handle(type=T.PHONE_E164, value=f"+55119810000{n:02d}")) for n in range(2)]
    for profile_id in few:
        await said(world, profile_id, "name", "Ana")
    await world.service.shown(reader(world, "security"), few)  # warms the key vault
    many = few + [
        await seen(world, Handle(type=T.PHONE_E164, value=f"+55119820000{n:02d}")) for n in range(30)
    ]

    with statements() as small:
        await world.service.shown(reader(world, "security"), few)
    with statements() as large:
        await world.service.shown(reader(world, "security"), many)

    def space(seen: list[str]) -> list[str]:
        return [s for s in seen if not s.startswith(("BEGIN", "COMMIT", "ROLLBACK")) and "data_keys" not in s]

    assert len(space(small)) == len(space(large)) == SHOWN_STATEMENTS


async def test_the_search_finds_a_name_by_its_keyed_hash_for_a_role_that_may_see_it(world: World) -> None:
    marina = await seen(world, Handle(type=T.PHONE_E164, value="+5511981172201"))
    other = await seen(world, Handle(type=T.PHONE_E164, value="+5511981172202"))
    await said(world, marina, "name", "Marina Souza Lima")
    await said(world, other, "important_date", "Marina Souza Lima")

    found = await world.service.search_profiles(
        world.space_id, "marina  souza LIMA", viewer=reader(world, "security")
    )

    assert [s.profile.profile_id for s in found] == [marina]
    assert (
        await world.service.search_profiles(world.space_id, "Marina", viewer=reader(world, "security")) == []
    )
