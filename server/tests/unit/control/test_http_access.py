"""People and single sign-on over HTTP (7.4): the Console's routes, the redirects the identity provider
drives, and what reaches the browser at each step."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.control import ControlContainer, router
from niadra.adapters.inbound.http.sso import router as sso_router
from niadra.adapters.outbound.keys.control_secrets import AesGcmSealer
from niadra.adapters.outbound.sso.oidc import HttpOidcClient
from niadra.adapters.outbound.sso.saml import SignXmlSamlService
from niadra.app.sso import SsoService, SsoSettings
from niadra.domain.control.credentials import new_url_token

from .conftest import PASSWORD, PEPPER, World
from .idp import CLIENT_ID, CLIENT_SECRET, IDP_ENTITY, IDP_SSO_URL, ISSUER, FakeOidcProvider, FakeSamlProvider
from .test_sso import binding

CONSOLE = "https://niadra.test"
CONTROL = "https://control.test"


@dataclass
class Http:
    client: httpx.AsyncClient
    world: World
    oidc: FakeOidcProvider
    saml: FakeSamlProvider

    async def admin(self) -> dict[str, str]:
        response = await self.client.post(
            "/v1/auth/login",
            json={"email": "founder@acme.com", "password": PASSWORD, "totp": self.world.totp()},
        )
        assert response.status_code == 200, response.text
        return {"authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def http(world: World) -> AsyncIterator[Http]:
    oidc = FakeOidcProvider(now=world.clock.now)
    sso = SsoService(
        world.uow,
        world.clock,
        AesGcmSealer.from_pepper(PEPPER),
        HttpOidcClient(oidc),
        SignXmlSamlService(),
        world.service,
        SsoSettings(console_origins=(CONSOLE,)),
    )
    container = ControlContainer(world.service, "cell-secret", "operator-secret", sso=sso)
    app = create_app("niadra-control", container, [router, sso_router], [CONSOLE])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=CONTROL) as client:
        yield Http(client, world, oidc, FakeSamlProvider.create())


# People


async def test_an_invitation_over_http_from_the_admin_to_the_first_sign_in(http: Http) -> None:
    auth = await http.admin()
    created = await http.client.post(
        "/v1/users", headers=auth, json={"email": "nina@acme.com", "roles": [{"role": "review"}]}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["invitation"]["token"]
    assert (body["password_set"], body["sso"], body["totp_uri"]) == (False, False, None)

    listed = {p["email"]: p for p in (await http.client.get("/v1/users", headers=auth)).json()}
    assert listed["nina@acme.com"]["invitation"] is None
    assert listed["nina@acme.com"]["invitation_expires_at"] == body["invitation"]["expires_at"]
    assert listed["founder@acme.com"]["second_factor_confirmed_at"] is not None
    assert listed["founder@acme.com"]["recovery_codes_left"] == 10

    preview = await http.client.post("/v1/auth/invitation/preview", json={"token": token})
    assert preview.json()["email"] == "nina@acme.com"
    assert preview.json()["tenant_name"] == "Acme"
    accepted = await http.client.post(
        "/v1/auth/invitation", json={"token": token, "password": "a long enough passphrase"}
    )
    assert accepted.json() == {"email": "nina@acme.com"}
    again = await http.client.post(
        "/v1/auth/invitation", json={"token": token, "password": "a long enough passphrase"}
    )
    assert again.status_code == 401
    assert again.headers["content-type"] == "application/problem+json"

    login = await http.client.post(
        "/v1/auth/login", json={"email": "nina@acme.com", "password": "a long enough passphrase"}
    )
    assert login.status_code == 401
    assert login.json()["detail"] == "second factor enrollment required"


async def test_deactivation_over_http_cuts_the_person_at_the_next_call(http: Http) -> None:
    auth = await http.admin()
    user = (
        await http.client.post(
            "/v1/users",
            headers=auth,
            json={"email": "ana@acme.com", "password": PASSWORD, "roles": [{"role": "security"}]},
        )
    ).json()
    secret = parse_qs(urlsplit(user["totp_uri"]).query)["secret"][0]
    token = (
        await http.client.post(
            "/v1/auth/login",
            json={"email": "ana@acme.com", "password": PASSWORD, "totp": http.world.totp(secret)},
        )
    ).json()["access_token"]
    ana = {"authorization": f"Bearer {token}"}
    assert (await http.client.get("/v1/users/me", headers=ana)).status_code == 200

    off = await http.client.post(
        f"/v1/users/{user['user_id']}/deactivate", headers=auth, json={"reason": "left the company"}
    )
    assert off.status_code == 200
    assert off.json()["active"] is False
    assert off.json()["deactivated_at"] is not None
    assert (await http.client.get("/v1/users/me", headers=ana)).status_code == 401

    on = await http.client.post(
        f"/v1/users/{user['user_id']}/reactivate", headers=auth, json={"reason": "back"}
    )
    assert on.json()["active"] is True
    history = (await http.client.get(f"/v1/users/{user['user_id']}/history", headers=auth)).json()
    assert [h["kind"] for h in history] == ["reactivated", "deactivated", "invited"]
    assert history[1]["reason"] == "left the company"

    self_off = await http.client.post(
        f"/v1/users/{http.world.boot.admin.user.user_id}/deactivate", headers=auth, json={"reason": "oops"}
    )
    assert self_off.status_code == 409


async def test_roles_change_with_a_reason_and_a_new_link_is_shown_once(http: Http) -> None:
    auth = await http.admin()
    user = (
        await http.client.post(
            "/v1/users", headers=auth, json={"email": "ivo@acme.com", "roles": [{"role": "review"}]}
        )
    ).json()
    roles = await http.client.put(
        f"/v1/users/{user['user_id']}/roles",
        headers=auth,
        json={"roles": [{"role": "analysis"}], "reason": "moved to measurement"},
    )
    assert [r["role"] for r in roles.json()] == ["analysis"]
    link = await http.client.post(f"/v1/users/{user['user_id']}/invitation", headers=auth)
    assert link.status_code == 201
    assert link.json()["token"] != user["invitation"]["token"]
    history = (await http.client.get(f"/v1/users/{user['user_id']}/history", headers=auth)).json()
    assert history[1]["reason"] == "moved to measurement"


# Single sign-on


async def _configure_oidc(http: Http, auth: dict[str, str], **changes: object) -> httpx.Response:
    body = {
        "protocol": "oidc",
        "domains": ["acme.com"],
        "role_mappings": [{"group": "CX-Review", "role": "review"}],
        "default_role": None,
        "oidc": {
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scopes": ["groups"],
        },
        "reason": "company single sign-on",
    } | changes
    return await http.client.put("/v1/sso", headers=auth, json=body)


async def test_the_admin_sees_what_to_paste_into_the_provider(http: Http) -> None:
    auth = await http.admin()
    empty = (await http.client.get("/v1/sso", headers=auth)).json()
    tenant = http.world.boot.tenant.tenant_id
    assert empty == {
        "connection": None,
        "service_provider": {
            "oidc_redirect_uri": f"{CONTROL}/v1/auth/sso/oidc/callback",
            "saml_entity_id": f"urn:niadra:sso:{tenant}",
            "saml_acs_url": f"{CONTROL}/v1/auth/sso/saml/{tenant}/acs",
            "saml_metadata_url": f"{CONTROL}/v1/auth/sso/saml/{tenant}/metadata",
        },
    }
    saved = await _configure_oidc(http, auth)
    assert saved.status_code == 200, saved.text
    connection = saved.json()["connection"]
    assert connection["oidc"] == {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "scopes": ["openid", "email", "profile", "groups"],
        "client_secret_set": True,
    }
    assert CLIENT_SECRET not in saved.text

    missing = await http.client.put(
        "/v1/sso", headers=auth, json={"protocol": "saml", "domains": ["acme.com"], "reason": "no settings"}
    )
    assert missing.status_code == 422


async def test_an_oidc_sign_in_over_http_never_puts_a_token_in_a_url(http: Http) -> None:
    auth = await http.admin()
    await _configure_oidc(http, auth)
    found = await http.client.post("/v1/auth/sso/discover", json={"email": "rita@acme.com"})
    assert found.json() == {"sso": True, "protocol": "oidc", "enforced": False}

    verifier = new_url_token()
    start = {"email": "rita@acme.com", "binding": binding(verifier)}
    assert (await http.client.post("/v1/auth/sso/start", json=start)).status_code == 403
    started = await http.client.post("/v1/auth/sso/start", json=start, headers={"origin": CONSOLE})
    assert started.status_code == 200, started.text
    state, code = http.oidc.authorize(
        started.json()["authorize_url"], email="rita@acme.com", groups=("CX-Review",)
    )

    back = await http.client.get("/v1/auth/sso/oidc/callback", params={"state": state, "code": code})
    assert back.status_code == 303
    assert back.headers["referrer-policy"] == "no-referrer"
    location = back.headers["location"]
    assert location.startswith(f"{CONSOLE}/console/sso#code=")
    assert "?" not in location

    exchanged = await http.client.post(
        "/v1/auth/sso/exchange", json={"code": location.split("#code=")[1], "verifier": verifier}
    )
    assert exchanged.status_code == 200, exchanged.text
    person = {"authorization": f"Bearer {exchanged.json()['access_token']}"}
    assert (await http.client.get("/v1/users/me", headers=person)).json()["email"] == "rita@acme.com"

    replay = await http.client.get("/v1/auth/sso/oidc/callback", params={"state": state, "code": code})
    assert replay.status_code == 400
    assert replay.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in replay.headers["content-security-policy"]
    assert "O login pelo SSO expirou" in replay.text


async def test_a_saml_sign_in_over_http_posts_the_form_to_the_acs(http: Http) -> None:
    auth = await http.admin()
    saved = await http.client.put(
        "/v1/sso",
        headers=auth,
        json={
            "protocol": "saml",
            "domains": ["acme.com"],
            "default_role": "review",
            "saml": {
                "idp_entity_id": IDP_ENTITY,
                "idp_sso_url": IDP_SSO_URL,
                "idp_certificate": http.saml.certificate,
            },
            "reason": "company single sign-on",
        },
    )
    assert saved.status_code == 200, saved.text
    certificate = saved.json()["connection"]["saml"]["certificate"]
    assert certificate["subject"] == "CN=idp.acme-saml.test"
    # Public, and returned as stored, so the Console's next save does not ask to paste it again.
    assert certificate["pem"] == http.saml.certificate.strip()
    tenant = http.world.boot.tenant.tenant_id
    metadata = await http.client.get(f"/v1/auth/sso/saml/{tenant}/metadata")
    assert metadata.headers["content-type"] == "application/samlmetadata+xml"

    verifier = new_url_token()
    started = await http.client.post(
        "/v1/auth/sso/start",
        json={"email": "mia@acme.com", "binding": binding(verifier)},
        headers={"origin": CONSOLE},
    )
    request = http.saml.read_request(started.json()["authorize_url"])
    posted = http.saml.response(request, http.world.clock.now(), email="mia@acme.com")
    back = await http.client.post(
        f"/v1/auth/sso/saml/{tenant}/acs",
        content=urlencode({"SAMLResponse": posted, "RelayState": request.relay_state}),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert back.status_code == 303
    code = back.headers["location"].split("#code=")[1]
    exchanged = await http.client.post("/v1/auth/sso/exchange", json={"code": code, "verifier": verifier})
    assert exchanged.status_code == 200


async def test_a_test_run_lands_on_a_page_and_the_console_reads_the_result(http: Http) -> None:
    auth = await http.admin()
    await _configure_oidc(http, auth, enabled=False)
    started = (await http.client.post("/v1/sso/test", headers=auth)).json()
    pending = (await http.client.get(f"/v1/sso/tests/{started['test_id']}", headers=auth)).json()
    assert (pending["status"], pending["ok"]) == ("pending", None)

    state, code = http.oidc.authorize(started["authorize_url"], email="rita@acme.com", groups=("CX-Review",))
    page = await http.client.get("/v1/auth/sso/oidc/callback", params={"state": state, "code": code})
    assert page.status_code == 200
    assert "Teste de SSO concluído" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    result = (await http.client.get(f"/v1/sso/tests/{started['test_id']}", headers=auth)).json()
    assert (result["status"], result["ok"], result["roles"], result["email"]) == (
        "completed",
        True,
        ["review"],
        "rita@acme.com",
    )


async def test_the_sso_second_factor_enrollment_uses_the_sign_in_code(http: Http) -> None:
    auth = await http.admin()
    await _configure_oidc(http, auth, require_second_factor=True)
    verifier = new_url_token()
    started = await http.client.post(
        "/v1/auth/sso/start",
        json={"email": "rita@acme.com", "binding": binding(verifier)},
        headers={"origin": CONSOLE},
    )
    state, code = http.oidc.authorize(
        started.json()["authorize_url"], email="rita@acme.com", groups=("CX-Review",)
    )
    location = (
        await http.client.get("/v1/auth/sso/oidc/callback", params={"state": state, "code": code})
    ).headers["location"]
    sign_in = {"code": location.split("#code=")[1], "verifier": verifier}
    first = await http.client.post("/v1/auth/sso/exchange", json=sign_in)
    assert (first.status_code, first.json()["detail"]) == (401, "second factor enrollment required")
    setup = await http.client.post("/v1/auth/sso/second-factor", json=sign_in)
    secret = setup.json()["secret"]
    assert setup.json()["totp_uri"].startswith("otpauth://totp/Niadra:rita@acme.com?")
    done = await http.client.post("/v1/auth/sso/exchange", json=sign_in | {"totp": http.world.totp(secret)})
    assert done.status_code == 200
    assert len(done.json()["recovery_codes"]) == 10


async def test_removing_the_connection_needs_a_reason(http: Http) -> None:
    auth = await http.admin()
    await _configure_oidc(http, auth)
    gone = await http.client.request("DELETE", "/v1/sso", headers=auth, json={"reason": "new provider"})
    assert gone.status_code == 204
    assert (await http.client.get("/v1/sso", headers=auth)).json()["connection"] is None
    assert (await http.client.post("/v1/auth/sso/discover", json={"email": "r@acme.com"})).json()[
        "sso"
    ] is False


async def test_a_person_keeps_console_preferences_of_their_own(http: Http) -> None:
    admin = await http.admin()
    progress = {"done": {"tour": "2026-10-01T12:00:00.000Z"}}

    empty = await http.client.get("/v1/users/me/preferences", headers=admin)
    await http.client.put("/v1/users/me/preferences/tutorials", json={"value": progress}, headers=admin)
    written = await http.client.put(
        "/v1/users/me/preferences/welcome_seen", json={"value": True}, headers=admin
    )
    replaced = await http.client.put(
        "/v1/users/me/preferences/tutorials", json={"value": {"done": {}}}, headers=admin
    )
    read = await http.client.get("/v1/users/me/preferences", headers=admin)

    assert empty.json() == {"preferences": {}}
    assert written.json() == {"preferences": {"tutorials": progress, "welcome_seen": True}}
    assert (
        read.json() == replaced.json() == {"preferences": {"tutorials": {"done": {}}, "welcome_seen": True}}
    )
    assert (await http.client.get("/v1/users/me/preferences")).status_code == 401


async def test_preferences_are_capped_in_key_size_and_count(http: Http) -> None:
    admin = await http.admin()

    async def put(key: str, value: object) -> int:
        url = f"/v1/users/me/preferences/{key}"
        return (await http.client.put(url, json={"value": value}, headers=admin)).status_code

    assert await put("Tutorials", True) == 422
    assert await put("k" * 65, True) == 422
    assert await put("empty", None) == 422
    assert await put("large", "x" * 4095) == 422
    assert await put("fits", "x" * 4094) == 200
    for n in range(15):
        assert await put(f"key-{n}", n) == 200
    assert await put("one-too-many", 1) == 422
    assert await put("key-3", "an existing key is still written") == 200
    kept = (await http.client.get("/v1/users/me/preferences", headers=admin)).json()["preferences"]
    assert (len(kept), kept["key-3"]) == (16, "an existing key is still written")
