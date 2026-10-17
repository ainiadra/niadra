"""Deleting a space from the control plane, and the receipt the cell sends back (9)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.app.control import BindingSpec
from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, NotFound
from niadra.domain.control.records import KeyStatus, Role
from niadra.domain.spaces.deletion import DeletionReceipt, receipt_hash
from niadra.domain.spaces.directory import db_name_for
from tests.unit.control.conftest import PASSWORD, World

CELL = {"x-niadra-cell-token": "cell-secret"}


def receipt_for(world: World) -> dict[str, Any]:
    space = world.boot.space
    now = world.clock.now()
    return DeletionReceipt(
        space_id=space.space_id,
        cell_id=space.cell_id,
        db_name=db_name_for(space.space_id),
        dropped_at=now,
        dek_versions_destroyed=[1],
        objects_deleted=4,
        backups_deleted=2,
        secrets_deleted=1,
        instance_snapshot_expiry=now + timedelta(days=1),
    ).document()


async def test_deleting_a_space_cuts_its_keys_and_lists_it_as_removed(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space
    before = await world.service.snapshot(space.cell_id)
    assert str(space.space_id) in before.manifest["spaces"]

    deleted, receipt = await world.service.delete_space(admin, space.space_id)

    assert deleted.deleted_at == world.clock.now()
    assert receipt is None
    after = await world.service.snapshot(space.cell_id)
    assert after.version == before.version + 1
    assert str(space.space_id) not in after.manifest["spaces"]
    assert after.manifest["removed"] == {str(space.space_id): world.clock.now().isoformat()}
    assert str(space.space_id) not in after.keys
    # The revocation feed still carries the cut keys to the cell.
    feed = await world.service.revocations(space.cell_id, 0)
    cut = {k["key_id"] for k in feed.body["keys"]}
    assert {issued.key.key_id for issued in world.boot.keys.values()} <= cut
    async with world.uow() as uow:
        keys = await uow.access.keys_of_spaces([space.space_id])
    assert {k.status for k in keys} == {KeyStatus.REVOKED}
    # A deleted space looks missing to everything but its receipt.
    with pytest.raises(NotFound):
        await world.service.get_space(admin, space.space_id)
    projects = await world.service.projects(admin)
    assert space.space_id not in {s.space_id for p in projects for s in p.spaces}


async def test_deleting_again_answers_the_same_and_only_admins_delete(world: World) -> None:
    admin = await world.admin()
    security = await world.service.create_user(admin, "sec@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)])
    token = await world.service.login("sec@acme.com", PASSWORD, world.totp(security.totp_secret), None)
    person = world.service.authenticate(token.token)
    with pytest.raises(Forbidden):
        await world.service.delete_space(person, world.boot.space.space_id)
    first, _ = await world.service.delete_space(admin, world.boot.space.space_id)
    world.clock.advance(minutes=5)
    again, _ = await world.service.delete_space(admin, world.boot.space.space_id)
    assert again.deleted_at == first.deleted_at
    with pytest.raises(NotFound):
        await world.service.delete_space(admin, uuid4())


async def test_the_cell_receipt_is_checked_stored_once_and_ends_the_removal(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space
    document = receipt_for(world)
    digest = receipt_hash(document).hex()
    with pytest.raises(Conflict, match="not deleted"):
        await world.service.store_deletion_receipt(space.cell_id, space.space_id, document, digest)
    await world.service.delete_space(admin, space.space_id)
    with pytest.raises(InvalidInput, match="does not match"):
        await world.service.store_deletion_receipt(space.cell_id, space.space_id, document, "00" * 32)
    with pytest.raises(InvalidInput, match="another space or cell"):
        await world.service.store_deletion_receipt("another-cell", space.space_id, document, digest)
    elsewhere = {**document, "cell_id": "another-cell"}
    with pytest.raises(NotFound):
        await world.service.store_deletion_receipt(
            "another-cell", space.space_id, elsewhere, receipt_hash(elsewhere).hex()
        )

    stored = await world.service.store_deletion_receipt(space.cell_id, space.space_id, document, digest)
    again = await world.service.store_deletion_receipt(space.cell_id, space.space_id, document, digest)
    assert again == stored
    changed = {**document, "objects_deleted": 99}
    with pytest.raises(Conflict, match="another receipt"):
        await world.service.store_deletion_receipt(
            space.cell_id, space.space_id, changed, receipt_hash(changed).hex()
        )
    # Confirmed: the space leaves the snapshot's removed list.
    snapshot = await world.service.snapshot(space.cell_id)
    assert snapshot.manifest["removed"] == {}
    deleted, receipt = await world.service.deletion(admin, space.space_id)
    assert receipt == stored
    assert deleted.deleted_at is not None


@pytest.fixture
async def client(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        "niadra-control", ControlContainer(world.service, "cell-secret", "operator-secret"), [router]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://control.test"
    ) as c:
        yield c


async def test_the_routes_delete_report_and_show_the_receipt(client: httpx.AsyncClient, world: World) -> None:
    login = await client.post(
        "/v1/auth/login", json={"email": "founder@acme.com", "password": PASSWORD, "totp": world.totp()}
    )
    auth = {"authorization": f"Bearer {login.json()['access_token']}"}
    space = world.boot.space
    path = f"/v1/spaces/{space.space_id}"

    assert (await client.get(f"{path}/deletion-receipt", headers=auth)).status_code == 404
    deleting = await client.delete(path, headers=auth)
    assert deleting.status_code == 202
    assert deleting.json()["status"] == "deleting"
    assert deleting.json()["receipt"] is None

    document = receipt_for(world)
    body = {"cell_id": space.cell_id, "receipt": document, "receipt_hash": receipt_hash(document).hex()}
    assert (await client.post(f"{path}/deletion-receipt", json=body)).status_code == 401
    posted = await client.post(f"{path}/deletion-receipt", json=body, headers=CELL)
    assert posted.status_code == 200, posted.text
    assert posted.json()["receipt_hash"] == body["receipt_hash"]

    shown = (await client.get(f"{path}/deletion-receipt", headers=auth)).json()
    assert shown["status"] == "deleted"
    assert shown["receipt"]["db_name"] == db_name_for(space.space_id)
    assert shown["receipt"]["dek_versions_destroyed"] == [1]
    assert (await client.delete(path, headers=auth)).json()["status"] == "deleted"
