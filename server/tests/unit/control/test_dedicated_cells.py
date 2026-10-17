"""A cell dedicated to one tenant, plan Regulado: placement, the tier and instance in the
snapshot, and the routes."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import Conflict, InvalidInput, NotFound, Unavailable
from niadra.domain.control.records import Cell, CellStatus
from niadra.domain.control.rules import check_placement, choose_cell, tier_for
from niadra.domain.spaces.directory import PRIMARY_INSTANCE, Tier
from tests.unit.control.conftest import World

T0 = datetime(2026, 9, 1, tzinfo=UTC)
REGION = "sa-east-1"
DEDICATED = "niadra-globex"
TOKEN = "globex-cell-secret"
TOKEN_SHA256 = hashlib.sha256(TOKEN.encode()).digest()


def _cell(cell_id: str, capacity: int = 10, *, tenant: object = None, age: int = 0) -> Cell:
    return Cell(
        cell_id,
        REGION,
        f"https://{cell_id}",
        capacity,
        CellStatus.ACTIVE,
        T0 - timedelta(days=age),
        tenant_id=tenant,  # type: ignore[arg-type]
    )


# Rules


def test_a_dedicated_cell_takes_only_its_tenants_spaces_and_all_of_them() -> None:
    mine, other = uuid4(), uuid4()
    cells = [_cell("shared", 100, age=30), _cell("mine", 5, tenant=mine), _cell("theirs", 500, tenant=other)]
    # The shared cell has the most room, but the tenant with a cell of its own goes there.
    assert choose_cell(cells, {}, REGION, mine).cell_id == "mine"
    # Everyone else stays on the shared cells, whatever room a dedicated cell has.
    assert choose_cell(cells, {}, REGION, uuid4()).cell_id == "shared"
    assert choose_cell(cells, {}, REGION).cell_id == "shared"
    # A full dedicated cell never spills onto a shared one.
    with pytest.raises(Unavailable):
        choose_cell(cells, {"mine": 5}, REGION, mine)


def test_an_operator_places_only_on_an_active_cell_of_the_region_and_of_the_tenant() -> None:
    tenant = uuid4()
    check_placement(_cell("mine", tenant=tenant), tenant, REGION)
    check_placement(_cell("shared"), tenant, REGION)
    with pytest.raises(Conflict):
        check_placement(_cell("theirs", tenant=uuid4()), tenant, REGION)
    with pytest.raises(InvalidInput):
        check_placement(_cell("mine", tenant=tenant), tenant, "us-east-2")


def test_the_tier_follows_the_cell_and_the_instance() -> None:
    assert tier_for(_cell("mine", tenant=uuid4())) is Tier.T2
    assert tier_for(_cell("shared"), "big-customer") is Tier.T1
    assert tier_for(_cell("shared")) is Tier.T0


# Service


async def dedicate(world: World, tenant_slug: str = "acme") -> Cell:
    tenant = await world.service.tenant_by_slug(tenant_slug)
    return await world.service.register_cell(
        DEDICATED,
        REGION,
        "https://niadra-globex.cells.niadra.test",
        10,
        tenant_id=tenant.tenant_id,
        token_sha256=TOKEN_SHA256,
    )


async def test_a_dedicated_cell_needs_a_credential_of_its_own(world: World) -> None:
    tenant = await world.service.tenant_by_slug("acme")
    with pytest.raises(InvalidInput):
        await world.service.register_cell(DEDICATED, REGION, "https://x", 10, tenant_id=tenant.tenant_id)
    with pytest.raises(InvalidInput):
        await world.service.register_cell(
            DEDICATED, REGION, "https://x", 10, tenant_id=tenant.tenant_id, token_sha256=b"short"
        )
    with pytest.raises(NotFound):
        await world.service.register_cell(
            DEDICATED, REGION, "https://x", 10, tenant_id=uuid4(), token_sha256=TOKEN_SHA256
        )
    cell = await dedicate(world)
    assert (cell.tenant_id, cell.dedicated) == (tenant.tenant_id, True)
    assert await world.service.cell_credential(DEDICATED) == TOKEN_SHA256
    assert await world.service.cell_credential(world.boot.space.cell_id) is None


async def test_the_tenants_new_project_lands_on_its_cell_as_t2_and_the_snapshot_says_so(world: World) -> None:
    await dedicate(world)
    view = await world.service.create_project(await world.admin(), "Acme Regulated", "acme-reg", REGION)
    assert {(s.cell_id, s.tier, s.instance) for s in view.spaces} == {(DEDICATED, Tier.T2, PRIMARY_INSTANCE)}

    snapshot = await world.service.snapshot(DEDICATED)
    assert set(snapshot.manifest["spaces"]) == {str(s.space_id) for s in view.spaces}
    for space in view.spaces:
        document = snapshot.documents[snapshot.manifest["spaces"][str(space.space_id)]]
        config = SpaceConfig.model_validate(document)
        assert (config.tier, config.instance) == ("t2", None)
    # The shared cell's snapshot does not carry them.
    shared = await world.service.snapshot(world.boot.space.cell_id)
    assert not set(shared.manifest["spaces"]) & {str(s.space_id) for s in view.spaces}
    for document in shared.documents.values():
        assert document.get("tier") is None
        assert document.get("instance") is None


async def test_another_tenant_never_lands_on_the_dedicated_cell(world: World) -> None:
    await world.service.create_tenant("Globex", "globex")
    await dedicate(world, "globex")
    view = await world.service.create_project(await world.admin(), "Acme Two", "acme-two", REGION)
    assert {s.cell_id for s in view.spaces} == {world.boot.space.cell_id}
    assert {s.tier for s in view.spaces} == {Tier.T0}
    with pytest.raises(Conflict):
        await world.service.place_project("acme", "Acme Three", "acme-three", REGION, DEDICATED)
    with pytest.raises(Conflict):
        await world.service.request_move(view.spaces[0].space_id, DEDICATED, "not theirs")


async def test_an_operator_places_a_project_on_a_named_cell_and_instance(world: World) -> None:
    await dedicate(world)
    placed = await world.service.place_project(
        "acme", "Acme Health", "acme-health", REGION, DEDICATED, instance="acme-health"
    )
    assert {(s.cell_id, s.tier, s.instance) for s in placed.spaces} == {(DEDICATED, Tier.T2, "acme-health")}
    snapshot = await world.service.snapshot(DEDICATED)
    documents = [snapshot.documents[snapshot.manifest["spaces"][str(s.space_id)]] for s in placed.spaces]
    assert {(d["tier"], d["instance"]) for d in documents} == {("t2", "acme-health")}

    # On the shared cell, an instance of its own is T1.
    shared = await world.service.place_project(
        "acme", "Acme Big", "acme-big", REGION, world.boot.space.cell_id, instance="acme-big"
    )
    assert {s.tier for s in shared.spaces} == {Tier.T1}
    with pytest.raises(InvalidInput):
        await world.service.place_project("acme", "Bad", "acme-bad", REGION, DEDICATED, instance="Not Valid")
    with pytest.raises(NotFound):
        await world.service.place_project("nobody", "X", "x-project", REGION, DEDICATED)


async def test_routes_list_the_space_addresses_and_a_cell_with_spaces_stays_open(world: World) -> None:
    await dedicate(world)
    view = await world.service.place_project("acme", "Acme Reg", "acme-reg", REGION, DEDICATED)
    assert sorted(s.slug for s in await world.service.routes(DEDICATED)) == ["acme-reg", "acme-reg-sandbox"]
    assert {s.space_id for s in await world.service.routes(DEDICATED)} == {s.space_id for s in view.spaces}
    with pytest.raises(Conflict):
        await world.service.close_cell(DEDICATED)
    with pytest.raises(NotFound):
        await world.service.routes("no-such-cell")

    empty = await world.service.register_cell("sa-east-1-z", REGION, "https://z", 10)
    closed = await world.service.close_cell(empty.cell_id)
    assert closed.status is CellStatus.CLOSED
    # A closed cell takes nothing new.
    with pytest.raises(Conflict):
        await world.service.place_project("acme", "Late", "acme-late", REGION, empty.cell_id)
