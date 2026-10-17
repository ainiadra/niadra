import base64
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.app.control_config import DOC_TYPES
from niadra.domain.control.rules import canonical_json
from tests.unit.control.conftest import PASSWORD, World

CELL = {"x-niadra-cell-token": "cell-secret"}
OPERATOR = {"x-niadra-operator-token": "operator-secret"}


@pytest.fixture
async def client(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        "niadra-control", ControlContainer(world.service, "cell-secret", "operator-secret"), [router]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://control.test"
    ) as c:
        yield c


async def _bearer(client: httpx.AsyncClient, world: World) -> dict[str, str]:
    response = await client.post(
        "/v1/auth/login", json={"email": "founder@acme.com", "password": PASSWORD, "totp": world.totp()}
    )
    assert response.status_code == 200, response.text
    return {"authorization": f"Bearer {response.json()['access_token']}"}


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


async def test_login_then_list_projects(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    projects = (await client.get("/v1/projects", headers=auth)).json()
    assert [p["slug"] for p in projects] == ["acme"]
    assert {s["environment"] for s in projects[0]["spaces"]} == {"sandbox", "production"}


async def test_routes_without_a_token_answer_401_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/projects")
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "unauthenticated"


async def test_bad_login_does_not_echo_the_e_mail(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/auth/login", json={"email": "marina@example.com", "password": "nope"})
    assert response.status_code == 401
    assert "marina" not in response.text


async def test_snapshot_needs_the_cell_token_and_verifies_against_the_jwks(
    client: httpx.AsyncClient, world: World
) -> None:
    cell = world.boot.space.cell_id
    assert (await client.get(f"/v1/snapshot/{cell}")).status_code == 401
    assert (
        await client.get(f"/v1/snapshot/{cell}", headers={"x-niadra-cell-token": "wrong"})
    ).status_code == 401

    body = (await client.get(f"/v1/snapshot/{cell}", headers=CELL)).json()
    jwks = (await client.get("/.well-known/jwks.json")).json()
    jwk = next(k for k in jwks["keys"] if k["kid"] == body["kid"])
    public = Ed25519PublicKey.from_public_bytes(_unb64(jwk["x"]))
    public.verify(_unb64(body["signature"]), canonical_json(body["manifest"]))
    public.verify(_unb64(body["keys_signature"]), canonical_json(body["keys"]))

    version = body["manifest"]["version"]
    unchanged = await client.get(f"/v1/snapshot/{cell}", params={"after": version, "wait": 0}, headers=CELL)
    assert unchanged.status_code == 304


async def test_diff_flow_over_http(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    space = str(world.boot.space.space_id)
    created = await client.post(
        "/v1/config/diffs",
        headers=auth,
        json={
            "space_id": space,
            "type": "review-sampling",
            "document": {"base_rate": 0.02},
            "reason": "more samples",
        },
    )
    assert created.status_code == 201, created.text
    diff_id = created.json()["diff_id"]
    # A diff names who proposed it and who decided it, for any role that reads it.
    assert (created.json()["author_label"], created.json()["decided_by_label"]) == ("founder@acme.com", None)
    [listed] = (await client.get("/v1/config/diffs", params={"space_id": space}, headers=auth)).json()
    assert (listed["status"], listed["author_label"]) == ("pending", "founder@acme.com")
    approved = await client.post(f"/v1/config/diffs/{diff_id}/approve", headers=auth)
    assert approved.json()["status"] == "applied"
    assert approved.json()["decided_by_label"] == "founder@acme.com"

    current = (
        await client.get("/v1/config/review-sampling", params={"space_id": space}, headers=auth)
    ).json()
    assert current["document"]["base_rate"] == 0.02
    assert current["version"] == 1
    history = (await client.get("/v1/config/history", params={"space_id": space}, headers=auth)).json()
    assert [h["type"] for h in history] == ["review-sampling"]
    rolled = await client.post(
        f"/v1/config/diffs/{diff_id}/rollback", headers=auth, json={"reason": "too many samples"}
    )
    assert rolled.json()["rollback_of"] == diff_id


async def test_keys_are_shown_once_and_revocations_reach_the_feed(
    client: httpx.AsyncClient, world: World
) -> None:
    auth = await _bearer(client, world)
    source = str(world.boot.keys["voice"].key.source_id)
    issued = (await client.post(f"/v1/sources/{source}/keys", headers=auth, json={})).json()
    assert issued["secret"].startswith("nia_sk_test_sa-east-1_acme-sandbox_")
    listed = (await client.get(f"/v1/sources/{source}/keys", headers=auth)).json()
    assert all("secret" not in k for k in listed)

    key_id = issued["key"]["key_id"]
    revoked = await client.post(
        f"/v1/sources/{source}/keys/{key_id}/revoke", headers=auth, json={"reason": "leaked"}
    )
    assert revoked.status_code == 200
    feed = (
        await client.get("/v1/revocations", params={"cell_id": world.boot.space.cell_id}, headers=CELL)
    ).json()
    assert [k["key_id"] for k in feed["body"]["keys"]] == [key_id]


async def test_operators_manage_cells_and_cells_push_usage(client: httpx.AsyncClient, world: World) -> None:
    created = await client.post(
        "/v1/cells",
        headers=OPERATOR,
        json={
            "cell_id": "sa-east-1-b",
            "region": "sa-east-1",
            "address": "https://cell-b.test",
            "capacity": 10,
        },
    )
    assert created.status_code == 201
    assert (await client.post("/v1/cells", headers=CELL, json={})).status_code == 401
    assert [c["cell_id"] for c in (await client.get("/v1/cells", headers=CELL)).json()] == [
        "sa-east-1-a",
        "sa-east-1-b",
    ]

    pushed = await client.post(
        "/v1/usage",
        headers=CELL,
        json={
            "cell_id": world.boot.space.cell_id,
            "items": [
                {
                    "space_id": str(world.boot.space.space_id),
                    "period_start": "2026-09-22T16:00:00Z",
                    "metric": "tasks",
                    "quantity": 7,
                }
            ],
        },
    )
    assert pushed.json() == {"accepted": 1}
    auth = await _bearer(client, world)
    usage = await client.get(
        "/v1/usage", params={"since": "2026-09-22T00:00:00Z", "until": "2026-09-23T00:00:00Z"}, headers=auth
    )
    assert [i["quantity"] for i in usage.json()["items"]] == [7]

    # The month per space, to invoice from, in JSON and as CSV; every space is listed.
    await client.post(
        "/v1/usage",
        headers=CELL,
        json={
            "cell_id": world.boot.space.cell_id,
            "items": [
                {
                    "space_id": str(world.boot.space.space_id),
                    "period_start": "2026-09-23T10:00:00Z",
                    "metric": "session_billable",
                    "quantity": 120,
                    "cost_micros": 5000,
                }
            ],
        },
    )
    statement = await client.get("/v1/usage/statement", params={"month": "2026-09"}, headers=auth)
    lines = {line["space_id"]: line for line in statement.json()["lines"]}
    boot = lines[str(world.boot.space.space_id)]
    assert (boot["billable"], boot["ai_cost_micros"], boot["metrics"]) == (
        120,
        5000,
        {"session_billable": 120, "tasks": 7},
    )
    assert len(lines) == 2  # the other environment, without usage, is listed too
    csv = await client.get("/v1/usage/statement", params={"month": "2026-09", "format": "csv"}, headers=auth)
    assert csv.headers["content-type"].startswith("text/csv")
    header, *rows = csv.text.strip().split("\n")
    assert header.endswith("billable,ai_cost_micros,session_billable,tasks")
    assert len(rows) == 2
    october = await client.get("/v1/usage/statement", params={"month": "2026-10"}, headers=auth)
    assert all(line["billable"] == 0 for line in october.json()["lines"])
    bad = await client.get("/v1/usage/statement", params={"month": "september"}, headers=auth)
    assert bad.status_code == 422


async def test_people_and_roles(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    created = await client.post(
        "/v1/users",
        headers=auth,
        json={"email": "sec@acme.com", "password": PASSWORD, "roles": [{"role": "security"}]},
    )
    assert created.status_code == 201
    assert created.json()["totp_uri"].startswith("otpauth://totp/Niadra:")
    users = (await client.get("/v1/users", headers=auth)).json()
    assert {u["email"] for u in users} == {"founder@acme.com", "sec@acme.com"}
    assert all(u["totp_uri"] is None for u in users)
    roles = (await client.get("/v1/roles", headers=auth)).json()
    assert {r["role"] for r in roles} == {"admin", "security", "integration", "review", "analysis", "vendor"}


async def test_me_lists_the_spaces_and_roles_of_the_caller(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    me = (await client.get("/v1/users/me", headers=auth)).json()
    assert me["email"] == "founder@acme.com"
    assert me["user_id"] == str(world.boot.admin.user.user_id)
    assert me["name"] is None
    assert {s["environment"] for s in me["spaces"]} == {"sandbox", "production"}
    assert all(s["roles"] == ["admin"] for s in me["spaces"])


async def test_me_shows_only_the_spaces_a_scoped_role_opens(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    sandbox = str(world.boot.space.space_id)
    body = {
        "email": "analyst@acme.com",
        "password": PASSWORD,
        "roles": [{"role": "analysis", "space_id": sandbox}],
    }
    created = await client.post("/v1/users", headers=auth, json=body)
    secret = created.json()["totp_uri"].split("secret=")[1].split("&")[0]
    login = await client.post(
        "/v1/auth/login", json={"email": "analyst@acme.com", "password": PASSWORD, "totp": world.totp(secret)}
    )
    token = {"authorization": f"Bearer {login.json()['access_token']}"}
    me = (await client.get("/v1/users/me", headers=token)).json()
    assert [(s["space_id"], s["roles"]) for s in me["spaces"]] == [(sandbox, ["analysis"])]


async def test_config_types_come_from_the_same_table_diffs_validate_against(
    client: httpx.AsyncClient, world: World
) -> None:
    auth = await _bearer(client, world)
    types = (await client.get("/v1/config/types", headers=auth)).json()
    names = [t["type"] for t in types]
    assert names == list(DOC_TYPES)
    assert all(t["description"] for t in types)
    assert next(t for t in types if t["type"] == "policies")["roles"] == ["security"]
    assert (await client.get("/v1/config/types")).status_code == 401
    space = str(world.boot.space.space_id)
    for name in names:
        assert (
            await client.get(f"/v1/config/{name}", params={"space_id": space}, headers=auth)
        ).status_code == 200


async def test_recovery_codes_come_once_and_sign_in_once(client: httpx.AsyncClient, world: World) -> None:
    founder = {"email": "founder@acme.com", "password": PASSWORD}
    first = (await client.post("/v1/auth/login", json={**founder, "totp": world.totp()})).json()
    codes = first["recovery_codes"]
    assert len(codes) == 10
    assert first["recovery_codes_left"] is None
    again = (await client.post("/v1/auth/login", json={**founder, "totp": world.totp()})).json()
    assert again["recovery_codes"] is None

    both = await client.post(
        "/v1/auth/login", json={**founder, "totp": world.totp(), "recovery_code": codes[0]}
    )
    assert both.status_code == 422
    used = await client.post("/v1/auth/login", json={**founder, "recovery_code": codes[0]})
    assert used.status_code == 200, used.text
    assert (used.json()["recovery_codes"], used.json()["recovery_codes_left"]) == (None, 9)
    spent = await client.post("/v1/auth/login", json={**founder, "recovery_code": codes[0]})
    assert spent.status_code == 401
    assert spent.json()["detail"] == "invalid second factor"
    assert codes[0] not in spent.text


async def test_the_account_manages_its_own_second_factor(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    status = (await client.get("/v1/users/me/second-factor", headers=auth)).json()
    assert status == {"confirmed_at": "2026-09-22T17:00:00Z", "recovery_codes_left": 10}
    assert (await client.get("/v1/users/me/second-factor")).status_code == 401

    # A wrong password is 403, never 401: the Console ends the session on a 401.
    wrong = await client.post(
        "/v1/users/me/recovery-codes", headers=auth, json={"password": "not the password"}
    )
    assert wrong.status_code == 403
    fresh = await client.post("/v1/users/me/recovery-codes", headers=auth, json={"password": PASSWORD})
    assert len(fresh.json()["recovery_codes"]) == 10

    started = await client.post("/v1/users/me/second-factor", headers=auth, json={"password": PASSWORD})
    secret = started.json()["secret"]
    assert started.json()["totp_uri"].startswith(f"otpauth://totp/Niadra:founder@acme.com?secret={secret}&")
    other = "111111" if world.totp(secret) != "111111" else "222222"
    bad = await client.post("/v1/users/me/second-factor/confirm", headers=auth, json={"totp": other})
    assert bad.status_code == 403
    confirmed = await client.post(
        "/v1/users/me/second-factor/confirm", headers=auth, json={"totp": world.totp(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert not set(confirmed.json()["recovery_codes"]) & set(fresh.json()["recovery_codes"])
    later = await client.post(
        "/v1/users/me/second-factor/confirm", headers=auth, json={"totp": world.totp(secret)}
    )
    assert later.status_code == 409


async def test_an_admin_resets_a_second_factor(client: httpx.AsyncClient, world: World) -> None:
    auth = await _bearer(client, world)
    created = await client.post(
        "/v1/users",
        headers=auth,
        json={"email": "sec@acme.com", "password": PASSWORD, "roles": [{"role": "security"}]},
    )
    user_id = created.json()["user_id"]
    secret = created.json()["totp_uri"].split("secret=")[1].split("&")[0]
    login = await client.post(
        "/v1/auth/login", json={"email": "sec@acme.com", "password": PASSWORD, "totp": world.totp(secret)}
    )
    security = {"authorization": f"Bearer {login.json()['access_token']}"}
    founder = str(world.boot.admin.user.user_id)
    refused = await client.post(
        f"/v1/users/{founder}/second-factor/reset", headers=security, json={"reason": "trying my luck"}
    )
    assert refused.status_code == 403

    reset = await client.post(
        f"/v1/users/{user_id}/second-factor/reset", headers=auth, json={"reason": "lost the phone"}
    )
    assert reset.status_code == 200, reset.text
    assert {k: reset.json()[k] for k in ("user_id", "reset_by", "reason")} == {
        "user_id": user_id,
        "reset_by": founder,
        "reason": "lost the phone",
    }
    enroll = await client.post("/v1/auth/login", json={"email": "sec@acme.com", "password": PASSWORD})
    assert enroll.json()["detail"] == "second factor enrollment required"
    missing = await client.post(
        f"/v1/users/{uuid4()}/second-factor/reset", headers=auth, json={"reason": "nobody"}
    )
    assert missing.status_code == 404
    short = await client.post(f"/v1/users/{user_id}/second-factor/reset", headers=auth, json={"reason": "x"})
    assert short.status_code == 422
