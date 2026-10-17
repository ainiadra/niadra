"""People and single sign-on against PostgreSQL: invitations kept as keyed hashes, deactivation, the access
history, the tenant's connection with its domains, and a whole OIDC and SAML sign-in on the real tables."""

import base64
import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Any

import psycopg
import pytest

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.control_secrets import AesGcmSealer
from niadra.adapters.outbound.postgres.control import PgControlUnitOfWorkFactory
from niadra.adapters.outbound.sso.oidc import HttpOidcClient
from niadra.adapters.outbound.sso.saml import SignXmlSamlService
from niadra.app.control import BindingSpec, BootstrapResult, ControlService
from niadra.app.sso import ConnectionSpec, Landing, SsoService, SsoSettings
from niadra.domain.common.errors import Conflict, Unauthenticated
from niadra.domain.control.credentials import new_url_token, totp_code
from niadra.domain.control.records import AccessChangeKind, Caller, Role, RoleMapping, SsoProtocol
from tests.unit.control.idp import (
    CLIENT_ID,
    CLIENT_SECRET,
    IDP_ENTITY,
    IDP_SSO_URL,
    ISSUER,
    FakeOidcProvider,
    FakeSamlProvider,
)

PASSWORD = "correct horse battery staple"
PEPPER = b"p" * 32
CONSOLE = "https://niadra.test"
BASE = "https://control.niadra.test"


def _sql(dsn: str, query: str, *args: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(query, args).fetchall()


def _binding(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


async def _founder(control: ControlService, clock: FixedClock) -> tuple[BootstrapResult, Caller]:
    await control.ensure_signing_key()
    boot = await control.bootstrap(
        tenant_name="Acme",
        tenant_slug="acme",
        email="founder@acme.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.test",
        password=PASSWORD,
    )
    assert boot.admin.totp_secret
    token = await control.login(
        "founder@acme.com", PASSWORD, totp_code(boot.admin.totp_secret, clock.now().timestamp()), None
    )
    return boot, control.authenticate(token.token)


@pytest.fixture
def sso(
    service: tuple[ControlService, FixedClock], control_uow: PgControlUnitOfWorkFactory
) -> tuple[SsoService, FakeOidcProvider]:
    control, clock = service
    provider = FakeOidcProvider(now=clock.now)
    return (
        SsoService(
            control_uow,
            clock,
            AesGcmSealer.from_pepper(PEPPER),
            HttpOidcClient(provider),
            SignXmlSamlService(),
            control,
            SsoSettings(console_origins=(CONSOLE,)),
        ),
        provider,
    )


async def test_invitations_deactivation_and_history_on_postgres(
    service: tuple[ControlService, FixedClock], control_dsn: str
) -> None:
    control, clock = service
    boot, admin = await _founder(control, clock)
    created = await control.create_user(admin, "nina@acme.com", None, [BindingSpec(Role.REVIEW)])
    assert created.invitation is not None
    token = created.invitation.token
    stored = _sql(
        control_dsn, "SELECT token_hash, used_at FROM invitations WHERE user_id = %s", created.user.user_id
    )
    assert len(stored) == 1
    assert token.encode() not in bytes(stored[0][0])
    assert stored[0][1] is None
    assert _sql(control_dsn, "SELECT password_hash FROM users WHERE user_id = %s", created.user.user_id) == [
        ("!",)
    ]

    people = {p.user.email: p for p in await control.list_users(admin)}
    assert people["nina@acme.com"].invitation is not None
    assert people["founder@acme.com"].recovery_codes_left == 10

    await control.accept_invitation(token, "a long enough passphrase")
    with pytest.raises(Unauthenticated):
        await control.accept_invitation(token, "a long enough passphrase")
    assert _sql(control_dsn, "SELECT count(*) FROM invitations WHERE used_at IS NULL") == [(0,)]

    clock.advance(minutes=1)
    await control.deactivate_user(admin, created.user.user_id, "left the company")
    assert _sql(
        control_dsn, "SELECT active, deactivated_at FROM users WHERE user_id = %s", created.user.user_id
    ) == [(False, clock.now())]
    clock.advance(minutes=1)
    await control.reactivate_user(admin, created.user.user_id, "came back")
    history = await control.access_history(admin, created.user.user_id)
    assert [c.kind for c in history] == [
        AccessChangeKind.REACTIVATED,
        AccessChangeKind.DEACTIVATED,
        AccessChangeKind.INVITATION_ACCEPTED,
        AccessChangeKind.INVITED,
    ]
    assert history[1].reason == "left the company"
    assert history[3].detail == {
        "roles": [{"role": "review", "space_id": None, "source_ids": []}],
        "password": False,
    }
    assert boot.tenant.tenant_id == history[0].tenant_id


async def test_a_connection_keeps_its_id_and_a_domain_belongs_to_one_tenant(
    service: tuple[ControlService, FixedClock],
    sso: tuple[SsoService, FakeOidcProvider],
    control_dsn: str,
) -> None:
    control, clock = service
    sso_service, _ = sso
    _, admin = await _founder(control, clock)
    spec = ConnectionSpec(
        protocol=SsoProtocol.OIDC,
        domains=["acme.com"],
        role_mappings=[RoleMapping("admins", Role.ADMIN)],
        default_role=Role.REVIEW,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
    )
    first = await sso_service.save(admin, spec, "company SSO")
    second = await sso_service.save(
        admin, replace(spec, domains=["acme.com.br", "acme.com"], oidc_client_secret=None), "second domain"
    )
    assert second.connection_id == first.connection_id
    assert _sql(control_dsn, "SELECT domain FROM sso_domains ORDER BY domain") == [
        ("acme.com",),
        ("acme.com.br",),
    ]
    sealed = _sql(control_dsn, "SELECT oidc_client_secret FROM sso_connections")[0][0]
    assert CLIENT_SECRET.encode() not in bytes(sealed)

    other = await control.bootstrap(
        tenant_name="Beta",
        tenant_slug="beta",
        email="owner@beta.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.test",
        password=PASSWORD,
        with_totp=False,
    )
    owner = control.authenticate((await control.login(other.admin.user.email, PASSWORD, None, None)).token)
    with pytest.raises(Conflict):
        await sso_service.save(owner, replace(spec, domains=["beta.com", "acme.com.br"]), "take a domain")
    # The refused save left nothing behind, for either tenant.
    assert _sql(control_dsn, "SELECT count(*) FROM sso_connections") == [(1,)]
    assert _sql(control_dsn, "SELECT domain FROM sso_domains ORDER BY domain") == [
        ("acme.com",),
        ("acme.com.br",),
    ]


async def test_an_oidc_sign_in_on_postgres_is_used_once(
    service: tuple[ControlService, FixedClock],
    sso: tuple[SsoService, FakeOidcProvider],
    control_dsn: str,
) -> None:
    control, clock = service
    sso_service, provider = sso
    _, admin = await _founder(control, clock)
    await sso_service.save(
        admin,
        ConnectionSpec(
            protocol=SsoProtocol.OIDC,
            domains=["acme.com"],
            role_mappings=[RoleMapping("cx", Role.REVIEW)],
            oidc_issuer=ISSUER,
            oidc_client_id=CLIENT_ID,
            oidc_client_secret=CLIENT_SECRET,
        ),
        "company SSO",
    )
    verifier = new_url_token()
    started = await sso_service.start("rita@acme.com", _binding(verifier), CONSOLE, BASE)
    state, code = provider.authorize(started.authorize_url, email="rita@acme.com", groups=("cx",))
    landing = await sso_service.oidc_callback(state, code, None)
    assert landing.redirect is not None
    login_code = landing.redirect.split("#code=")[1]
    assert _sql(control_dsn, "SELECT status, purpose FROM sso_attempts") == [("signed_in", "login")]
    assert await sso_service.oidc_callback(state, code, None) == Landing(page="expired")

    issued = await sso_service.exchange(login_code, verifier, None, None, None)
    assert control.authenticate(issued.token).roles == frozenset({Role.REVIEW})
    assert _sql(control_dsn, "SELECT status FROM sso_attempts") == [("completed",)]
    with pytest.raises(Unauthenticated):
        await sso_service.exchange(login_code, verifier, None, None, None)
    row = _sql(
        control_dsn, "SELECT password_hash, last_sso_login_at FROM users WHERE email = 'rita@acme.com'"
    )
    assert row == [("!", clock.now())]
    kinds = _sql(control_dsn, "SELECT kind, actor_id FROM access_changes WHERE kind LIKE 'sso%%' ORDER BY at")
    assert kinds == [("sso_saved", admin.user_id), ("sso_person_created", None)]

    # Old attempts are cleared by the next start.
    clock.advance(days=2)
    await sso_service.start("rita@acme.com", _binding(new_url_token()), CONSOLE, BASE)
    assert _sql(control_dsn, "SELECT count(*) FROM sso_attempts") == [(1,)]


async def test_a_saml_sign_in_and_the_removal_on_postgres(
    service: tuple[ControlService, FixedClock],
    sso: tuple[SsoService, FakeOidcProvider],
    control_dsn: str,
) -> None:
    control, clock = service
    sso_service, _ = sso
    boot, admin = await _founder(control, clock)
    idp = FakeSamlProvider.create()
    await sso_service.save(
        admin,
        ConnectionSpec(
            protocol=SsoProtocol.SAML,
            domains=["acme.com"],
            default_role=Role.ANALYSIS,
            saml_idp_entity_id=IDP_ENTITY,
            saml_idp_sso_url=IDP_SSO_URL,
            saml_idp_certificate=idp.certificate,
        ),
        "company SSO",
    )
    verifier = new_url_token()
    started = await sso_service.start("mia@acme.com", _binding(verifier), CONSOLE, BASE)
    request = idp.read_request(started.authorize_url)
    posted = idp.response(request, clock.now(), email="mia@acme.com")
    landing = await sso_service.saml_acs(boot.tenant.tenant_id, posted, request.relay_state)
    assert landing.redirect is not None
    issued = await sso_service.exchange(landing.redirect.split("#code=")[1], verifier, None, None, None)
    assert control.authenticate(issued.token).roles == frozenset({Role.ANALYSIS})

    await sso_service.remove(admin, "moving to OIDC")
    assert _sql(control_dsn, "SELECT count(*) FROM sso_connections") == [(0,)]
    assert _sql(control_dsn, "SELECT count(*) FROM sso_domains") == [(0,)]
    assert _sql(control_dsn, "SELECT count(*) FROM sso_attempts") == [(0,)]
    # The person the provider created stays, with the roles it had.
    assert _sql(control_dsn, "SELECT active FROM users WHERE email = 'mia@acme.com'") == [(True,)]


async def test_the_access_feed_of_a_cell_on_postgres(service: tuple[ControlService, FixedClock]) -> None:
    # The cell pulls, oldest first, the access changes of the tenants it serves, after a cursor.
    control, clock = service
    boot, admin = await _founder(control, clock)
    start = await control.access_feed("sa-east-1-a", None)
    assert start.body["changes"] == []
    clock.advance(seconds=1)
    nina = await control.create_user(admin, "nina@acme.com", None, [BindingSpec(Role.REVIEW)])
    clock.advance(seconds=1)
    await control.set_roles(admin, nina.user.user_id, [BindingSpec(Role.SECURITY)], "privacy duties")
    feed = await control.access_feed("sa-east-1-a", datetime.fromisoformat(start.body["after"]))
    assert [c["kind"] for c in feed.body["changes"]] == ["invited", "roles_changed"]
    assert {c["tenant_id"] for c in feed.body["changes"]} == {str(boot.tenant.tenant_id)}
    assert all("reason" not in c for c in feed.body["changes"])
    later = await control.access_feed("sa-east-1-a", datetime.fromisoformat(feed.body["after"]))
    assert later.body["changes"] == []
