"""A dedicated cell's own credential at the control plane's HTTP edge, and the operator's commands that
register it and place a project on it."""

import hashlib
from collections.abc import AsyncIterator

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.entrypoints import control_cli
from tests.unit.control.conftest import World
from tests.unit.control.test_dedicated_cells import DEDICATED, REGION, TOKEN, TOKEN_SHA256, dedicate

# The cell's own credential at the control plane's HTTP edge


@pytest.fixture
async def client(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        "niadra-control", ControlContainer(world.service, "cell-secret", "operator-secret"), [router]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://control.test"
    ) as c:
        yield c


async def test_a_dedicated_cell_is_reached_only_with_its_own_credential(
    client: httpx.AsyncClient, world: World
) -> None:
    await dedicate(world)
    shared_cell = world.boot.space.cell_id
    own = {"x-niadra-cell-token": TOKEN}
    shared = {"x-niadra-cell-token": "cell-secret"}

    assert (await client.get(f"/v1/snapshot/{DEDICATED}", headers=own)).status_code == 200
    # The credential the shared cells use does not open the dedicated cell's snapshot or feeds...
    assert (await client.get(f"/v1/snapshot/{DEDICATED}", headers=shared)).status_code == 401
    feed = await client.get("/v1/revocations", params={"cell_id": DEDICATED}, headers=shared)
    assert feed.status_code == 401
    # ...and the dedicated cell's credential acts for no other cell.
    assert (await client.get(f"/v1/snapshot/{shared_cell}", headers=own)).status_code == 401
    pushed = await client.post("/v1/usage", json={"cell_id": shared_cell, "items": []}, headers=own)
    assert pushed.status_code == 401
    changes = await client.get("/v1/access-changes", params={"cell_id": shared_cell}, headers=own)
    assert changes.status_code == 401
    # Each works for its own cell.
    assert (await client.get(f"/v1/snapshot/{shared_cell}", headers=shared)).status_code == 200
    ok = await client.post("/v1/usage", json={"cell_id": DEDICATED, "items": []}, headers=own)
    assert ok.status_code == 202

    # A new credential replaces the old one at once.
    await world.service.rekey_cell(DEDICATED, hashlib.sha256(b"next").digest())
    assert (await client.get(f"/v1/snapshot/{DEDICATED}", headers=own)).status_code == 401
    rekeyed = await client.get(f"/v1/snapshot/{DEDICATED}", headers={"x-niadra-cell-token": "next"})
    assert rekeyed.status_code == 200


# The operator's commands (scripts/cell.sh runs them in the control pod)


class _Engine:
    async def dispose(self) -> None:
        return None


def test_the_operator_commands_register_place_route_and_refuse_bad_input(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(control_cli, "control_engine", lambda pool_size: _Engine())
    monkeypatch.setattr(control_cli, "build_service", lambda engine: world.service)
    digest = TOKEN_SHA256.hex()
    register = ["register-cell", "--cell", DEDICATED, "--address", "https://x", "--region", REGION]
    control_cli.main([*register, "--tenant", "acme", "--token-sha256", digest])
    place = ["place-project", "--tenant", "acme", "--name", "Reg", "--slug", "acme-reg", "--region", REGION]
    control_cli.main([*place, "--cell", DEDICATED])
    control_cli.main(["routes", "--cell", DEDICATED, "--domain", "niadra.test"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"cell {DEDICATED} {REGION} https://x dedicated to acme"
    assert all(line.endswith(f"cell {DEDICATED} instance primary tier t2") for line in out[1:3])
    assert sorted(out[3:]) == [
        f"acme-reg-sandbox.{REGION}.api.niadra.test",
        f"acme-reg.{REGION}.api.niadra.test",
    ]

    with pytest.raises(SystemExit, match="SHA-256"):
        control_cli.main(["rekey-cell", "--cell", DEDICATED, "--token-sha256", "abcd"])
    with pytest.raises(SystemExit, match="close-cell: the cell still serves spaces"):
        control_cli.main(["close-cell", "--cell", DEDICATED])
