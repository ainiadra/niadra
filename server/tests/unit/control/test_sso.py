"""Single sign-on for the Console (5.22): OIDC with PKCE and SAML 2.0, the person created just in time with
roles from the provider's groups, the one-time sign-in code bound to the browser tab, and the Niadra second
factor on top of the provider's when the tenant asks for it."""

import base64
import hashlib
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from niadra.adapters.outbound.keys.control_secrets import AesGcmSealer
from niadra.adapters.outbound.sso.oidc import HttpOidcClient
from niadra.adapters.outbound.sso.saml import SignXmlSamlService
from niadra.app.control import BindingSpec
from niadra.app.sso import ConnectionSpec, Landing, SsoService, SsoSettings
from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, NotFound, Unauthenticated
from niadra.domain.control.credentials import new_url_token, totp_code
from niadra.domain.control.records import (
    AccessChange,
    AccessChangeKind,
    Caller,
    Role,
    RoleMapping,
    SsoAttemptStatus,
    SsoProtocol,
)

from .conftest import PASSWORD, PEPPER, World
from .idp import (
    CLIENT_ID,
    CLIENT_SECRET,
    IDP_ENTITY,
    IDP_SSO_URL,
    ISSUER,
    FakeOidcProvider,
    FakeSamlProvider,
    new_key,
    self_signed,
)

BASE = "https://control.niadra.test"
CONSOLE = "https://niadra.test"
MAPPINGS = (RoleMapping("niadra-admins", Role.ADMIN), RoleMapping("CX-Review", Role.REVIEW))


@dataclass
class Sso:
    world: World
    service: SsoService
    oidc: FakeOidcProvider
    saml: FakeSamlProvider

    async def admin(self) -> Caller:
        return await self.world.admin()

    async def configure_oidc(self, **changes: object) -> None:
        spec = ConnectionSpec(
            protocol=SsoProtocol.OIDC,
            domains=["acme.com"],
            default_role=None,
            role_mappings=MAPPINGS,
            oidc_issuer=ISSUER,
            oidc_client_id=CLIENT_ID,
            oidc_client_secret=CLIENT_SECRET,
        )
        await self.service.save(await self.admin(), _replace(spec, **changes), "company SSO")

    async def configure_saml(self, **changes: object) -> None:
        spec = ConnectionSpec(
            protocol=SsoProtocol.SAML,
            domains=["acme.com"],
            role_mappings=MAPPINGS,
            default_role=Role.ANALYSIS,
            saml_idp_entity_id=IDP_ENTITY,
            saml_idp_sso_url=IDP_SSO_URL,
            saml_idp_certificate=self.saml.certificate,
        )
        await self.service.save(await self.admin(), _replace(spec, **changes), "company SSO")

    async def oidc_login(
        self, email: str, groups: tuple[str, ...] = (), asserted: str | None = None, **claims: object
    ) -> tuple[Landing, str]:
        """Types `email` in the Console; the provider answers for `asserted` (the same person by default)."""
        verifier = new_url_token()
        started = await self.service.start(email, binding(verifier), CONSOLE, BASE)
        state, code = self.oidc.authorize(
            started.authorize_url, email=asserted or email, groups=groups, claims=claims
        )
        return await self.service.oidc_callback(state, code, None), verifier


def _replace(spec: ConnectionSpec, **changes: object) -> ConnectionSpec:
    return replace(spec, **changes)  # type: ignore[arg-type]


def binding(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def fragment(landing: Landing) -> dict[str, str]:
    assert landing.redirect is not None
    assert landing.redirect.startswith(f"{CONSOLE}/console/sso#")
    return {k: v[0] for k, v in parse_qs(urlsplit(landing.redirect).fragment).items()}


@pytest.fixture
def sso(world: World) -> Sso:
    oidc = FakeOidcProvider(now=world.clock.now)
    service = SsoService(
        world.uow,
        world.clock,
        AesGcmSealer.from_pepper(PEPPER),
        HttpOidcClient(oidc),
        SignXmlSamlService(),
        world.service,
        SsoSettings(console_origins=(CONSOLE,)),
    )
    return Sso(world, service, oidc, FakeSamlProvider.create())


# OIDC


async def test_an_oidc_sign_in_creates_the_person_with_the_roles_of_the_groups(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("Rita@Acme.com", ("cx-review", "unrelated"))
    code = fragment(landing)["code"]

    issued = await sso.service.exchange(code, verifier, None, None, None)
    caller = sso.world.service.authenticate(issued.token)
    assert caller.roles == frozenset({Role.REVIEW})
    people = {p.user.email: p for p in await sso.world.service.list_users(await sso.admin())}
    rita = people["rita@acme.com"]
    assert rita.sso
    assert rita.user.last_sso_login_at == sso.world.clock.now()
    # Created by the provider: no password, so the password login never works for her.
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await sso.world.service.login("rita@acme.com", "!", None, None)
    history = await sso.world.service.access_history(await sso.admin(), rita.user.user_id)
    assert [(c.kind, c.actor_id) for c in history] == [(AccessChangeKind.SSO_PERSON_CREATED, None)]


async def test_the_authorization_request_carries_state_nonce_and_a_pkce_challenge(sso: Sso) -> None:
    await sso.configure_oidc()
    started = await sso.service.start("rita@acme.com", binding(new_url_token()), CONSOLE, BASE)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started.authorize_url).query).items()}
    assert query["redirect_uri"] == f"{BASE}/v1/auth/sso/oidc/callback"
    assert query["login_hint"] == "rita@acme.com"
    assert len(query["state"]) >= 40
    assert len(query["nonce"]) >= 40
    assert len(query["code_challenge"]) == 43
    # Only hashes of what the browser carries are stored: never the state itself.
    assert query["state"] not in repr(sso.world.uow.store.data)


async def test_the_sign_in_code_needs_the_value_the_tab_kept_and_works_once(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    code = fragment(landing)["code"]
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await sso.service.exchange(code, new_url_token(), None, None, None)
    await sso.service.exchange(code, verifier, None, None, None)
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await sso.service.exchange(code, verifier, None, None, None)


async def test_the_sign_in_code_expires_after_ten_minutes(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    sso.world.clock.advance(minutes=10, seconds=1)
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)


async def test_one_answer_of_the_provider_is_used_once_and_only_for_ten_minutes(sso: Sso) -> None:
    await sso.configure_oidc()
    verifier = new_url_token()
    started = await sso.service.start("rita@acme.com", binding(verifier), CONSOLE, BASE)
    state, code = sso.oidc.authorize(started.authorize_url, email="rita@acme.com", groups=("CX-Review",))
    assert fragment(await sso.service.oidc_callback(state, code, None))["code"]
    assert await sso.service.oidc_callback(state, code, None) == Landing(page="expired")

    late = await sso.service.start("rita@acme.com", binding(verifier), CONSOLE, BASE)
    state, code = sso.oidc.authorize(late.authorize_url, email="rita@acme.com", groups=("CX-Review",))
    sso.world.clock.advance(minutes=11)
    assert await sso.service.oidc_callback(state, code, None) == Landing(page="expired")
    assert await sso.service.oidc_callback("unknown-state", "x", None) == Landing(page="expired")


@pytest.mark.parametrize(
    ("email", "groups", "claims", "failure"),
    [
        ("rita@other.com", ("CX-Review",), {}, "domain"),
        ("rita@acme.com", ("CX-Review",), {"email_verified": False}, "domain"),
        ("rita@acme.com", ("nobody-maps-this",), {}, "no_role"),
        ("rita@acme.com", ("CX-Review",), {"nonce": "another sign-in"}, "invalid"),
        ("rita@acme.com", ("CX-Review",), {"aud": "another-client"}, "invalid"),
        ("rita@acme.com", ("CX-Review",), {"iss": "https://evil.test"}, "invalid"),
    ],
)
async def test_a_refused_sign_in_goes_back_with_the_reason_and_no_code(
    sso: Sso, email: str, groups: tuple[str, ...], claims: dict[str, object], failure: str
) -> None:
    await sso.configure_oidc()
    landing, _ = await sso.oidc_login("rita@acme.com", groups, asserted=email, **claims)
    assert fragment(landing) == {"error": failure}
    assert all(p.user.email != "rita@acme.com" for p in await sso.world.service.list_users(await sso.admin()))


async def test_an_id_token_signed_by_another_key_or_expired_is_refused(sso: Sso) -> None:
    await sso.configure_oidc()
    verifier = new_url_token()
    started = await sso.service.start("rita@acme.com", binding(verifier), CONSOLE, BASE)
    state, code = sso.oidc.authorize(
        started.authorize_url, email="rita@acme.com", groups=("CX-Review",), key=new_key()
    )
    assert fragment(await sso.service.oidc_callback(state, code, None)) == {"error": "invalid"}

    old = await sso.service.start("rita@acme.com", binding(verifier), CONSOLE, BASE)
    at = int(sso.world.clock.now().timestamp())
    state, code = sso.oidc.authorize(
        old.authorize_url,
        email="rita@acme.com",
        groups=("CX-Review",),
        claims={"iat": at - 3600, "exp": at - 600},
    )
    assert fragment(await sso.service.oidc_callback(state, code, None)) == {"error": "invalid"}


async def test_a_person_who_cancels_at_the_provider_goes_back_with_denied(sso: Sso) -> None:
    await sso.configure_oidc()
    started = await sso.service.start("rita@acme.com", binding(new_url_token()), CONSOLE, BASE)
    state = parse_qs(urlsplit(started.authorize_url).query)["state"][0]
    assert fragment(await sso.service.oidc_callback(state, None, "access_denied")) == {"error": "denied"}


async def test_the_default_role_lets_in_people_of_no_mapped_group(sso: Sso) -> None:
    await sso.configure_oidc(default_role=Role.ANALYSIS)
    landing, verifier = await sso.oidc_login("nuno@acme.com", ())
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    assert sso.world.service.authenticate(issued.token).roles == frozenset({Role.ANALYSIS})


async def test_roles_follow_the_groups_at_every_sign_in(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    landing, verifier = await sso.oidc_login("rita@acme.com", ("niadra-admins", "cx-review"))
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    assert sso.world.service.authenticate(issued.token).roles == frozenset({Role.ADMIN, Role.REVIEW})

    rita = next(
        p for p in await sso.world.service.list_users(await sso.admin()) if p.user.email == "rita@acme.com"
    )
    synced = (await sso.world.service.access_history(await sso.admin(), rita.user.user_id))[0]
    assert synced.kind is AccessChangeKind.SSO_ROLES_SYNCED
    assert synced.detail == {"before": ["review"], "after": ["admin", "review"], "kept_admin": False}


async def test_the_groups_never_take_the_last_admin_away(sso: Sso) -> None:
    await sso.configure_oidc()
    # The founder signs in through SSO while no group maps to admin: the tenant would have no admin left.
    landing, verifier = await sso.oidc_login("founder@acme.com", ("CX-Review",))
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    assert sso.world.service.authenticate(issued.token).roles == frozenset({Role.ADMIN, Role.REVIEW})


async def test_other_tenants_and_deactivated_people_are_refused(sso: Sso) -> None:
    await sso.configure_oidc()
    admin = await sso.admin()
    created = await sso.world.service.create_user(
        admin, "gone@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)], with_totp=False
    )
    await sso.world.service.deactivate_user(admin, created.user.user_id, "left")
    landing, _ = await sso.oidc_login("gone@acme.com", ("CX-Review",))
    assert fragment(landing) == {"error": "deactivated"}

    await sso.world.service.bootstrap(
        tenant_name="Beta",
        tenant_slug="beta",
        email="owner@acme-beta.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.sa-east-1.niadra.test",
        password=PASSWORD,
    )
    # A person of another tenant whose e-mail sits in Acme's domain list: never moved across tenants.
    beta_owner = sso.world.service.authenticate(
        (
            await sso.world.service.login(
                "owner@acme-beta.com",
                PASSWORD,
                totp_code(
                    await sso.world.service.second_factor_setup("owner@acme-beta.com", PASSWORD),
                    sso.world.clock.now().timestamp(),
                ),
                None,
            )
        ).token
    )
    await sso.world.service.create_user(
        beta_owner, "shared@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)], with_totp=False
    )
    landing, _ = await sso.oidc_login("shared@acme.com", ("CX-Review",))
    assert fragment(landing) == {"error": "other_tenant"}


async def test_the_tenant_can_ask_for_the_niadra_second_factor_on_top_of_the_provider(sso: Sso) -> None:
    await sso.configure_oidc(require_second_factor=True)
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    code = fragment(landing)["code"]
    with pytest.raises(Unauthenticated, match="enrollment required"):
        await sso.service.exchange(code, verifier, None, None, None)
    new = await sso.service.second_factor_setup(code, verifier)
    assert new.account == "rita@acme.com"
    now = sso.world.clock.now().timestamp()
    first = await sso.service.exchange(code, verifier, totp_code(new.secret, now), None, None)
    assert first.recovery_codes is not None

    # The next sign-in asks for the code; five wrong ones spend the sign-in code.
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    code = fragment(landing)["code"]
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await sso.service.exchange(code, verifier, None, None, None)
    for _ in range(5):
        with pytest.raises(Unauthenticated, match="invalid second factor"):
            await sso.service.exchange(code, verifier, "000000", None, None)
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await sso.service.exchange(code, verifier, totp_code(new.secret, now), None, None)


async def test_without_the_niadra_second_factor_the_provider_is_enough(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("founder@acme.com", ("niadra-admins",))
    # The founder has a Niadra authenticator, but the tenant relies on the provider's MFA.
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    assert issued.recovery_codes is None


async def test_an_enforced_connection_leaves_the_password_only_to_tenant_admins(sso: Sso) -> None:
    admin = await sso.admin()
    await sso.world.service.create_user(
        admin, "rev@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)], with_totp=False
    )
    await sso.configure_oidc(enforced=True)
    with pytest.raises(Unauthenticated, match="single sign-on required"):
        await sso.world.service.login("rev@acme.com", PASSWORD, None, None)
    # The founder is a tenant-wide admin: the way in when the provider fails.
    assert await sso.world.service.login("founder@acme.com", PASSWORD, sso.world.totp(), None)
    found = await sso.service.discover("Anyone@ACME.com")
    assert (found.sso, found.enforced) == (True, True)


async def test_discovery_answers_by_domain_only(sso: Sso) -> None:
    assert not (await sso.service.discover("rita@acme.com")).sso
    await sso.configure_oidc()
    found = await sso.service.discover("nobody-with-this-name@acme.com")
    assert (found.sso, found.protocol, found.enforced) == (True, SsoProtocol.OIDC, False)
    assert not (await sso.service.discover("rita@elsewhere.com")).sso
    await sso.configure_oidc(enabled=False)
    assert not (await sso.service.discover("rita@acme.com")).sso


async def test_only_the_console_origins_may_start_and_the_binding_is_a_sha256(sso: Sso) -> None:
    await sso.configure_oidc()
    with pytest.raises(Forbidden):
        await sso.service.start("rita@acme.com", binding("v" * 43), "https://evil.test", BASE)
    with pytest.raises(Forbidden):
        await sso.service.start("rita@acme.com", binding("v" * 43), None, BASE)
    with pytest.raises(InvalidInput):
        await sso.service.start("rita@acme.com", "not-a-hash", CONSOLE, BASE)
    with pytest.raises(NotFound):
        await sso.service.start("rita@nosso.com", binding("v" * 43), CONSOLE, BASE)


# The connection


async def test_the_client_secret_is_sealed_and_kept_when_not_sent_again(sso: Sso) -> None:
    await sso.configure_oidc()
    assert CLIENT_SECRET not in repr(sso.world.uow.store.data)
    await sso.configure_oidc(oidc_client_secret=None, domains=["acme.com", "acme.com.br"])
    view = await sso.service.view(await sso.admin(), BASE)
    assert view.connection is not None
    assert view.connection.domains == ("acme.com", "acme.com.br")
    # The kept secret still works at the provider's token endpoint.
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    assert await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)


async def test_the_connection_is_validated_before_it_is_stored(sso: Sso) -> None:
    for changes, error in [
        ({"domains": ["gmail.com"]}, InvalidInput),
        ({"domains": ["not a domain"]}, InvalidInput),
        ({"role_mappings": (RoleMapping("vendors", Role.VENDOR),)}, InvalidInput),
        ({"role_mappings": (), "default_role": None}, InvalidInput),
        ({"oidc_issuer": "http://login.acme-idp.test/tenant"}, InvalidInput),
        ({"oidc_client_secret": None}, InvalidInput),
        ({"oidc_issuer": "https://login.acme-idp.test/other"}, InvalidInput),
    ]:
        with pytest.raises(error):
            await sso.configure_oidc(**changes)
    sso.oidc.discovery_issuer = "https://login.acme-idp.test/impostor"
    with pytest.raises(InvalidInput, match="another issuer"):
        await sso.configure_oidc()
    assert (await sso.service.view(await sso.admin(), BASE)).connection is None


async def test_a_domain_belongs_to_one_tenant(sso: Sso) -> None:
    await sso.configure_oidc()
    boot = await sso.world.service.bootstrap(
        tenant_name="Beta",
        tenant_slug="beta",
        email="owner@beta.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.sa-east-1.niadra.test",
        password=PASSWORD,
        with_totp=False,
    )
    owner = sso.world.service.authenticate(
        (await sso.world.service.login(boot.admin.user.email, PASSWORD, None, None)).token
    )
    spec = ConnectionSpec(
        protocol=SsoProtocol.OIDC,
        domains=["beta.com", "acme.com"],
        default_role=Role.REVIEW,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
    )
    with pytest.raises(Conflict):
        await sso.service.save(owner, spec, "take the domain")


async def test_only_admins_see_and_change_the_connection(sso: Sso) -> None:
    admin = await sso.admin()
    await sso.world.service.create_user(
        admin, "sec@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)], with_totp=False
    )
    secret = await sso.world.service.second_factor_setup("sec@acme.com", PASSWORD)
    security = sso.world.service.authenticate(
        (await sso.world.service.login("sec@acme.com", PASSWORD, sso.world.totp(secret), None)).token
    )
    for call in (sso.service.view(security, BASE), sso.service.start_test(security, BASE)):
        with pytest.raises(Forbidden):
            await call


async def test_removing_the_connection_is_recorded_with_the_reason(sso: Sso) -> None:
    await sso.configure_oidc()
    admin = await sso.admin()
    await sso.service.remove(admin, "moving to another provider")
    assert (await sso.service.view(admin, BASE)).connection is None
    assert not (await sso.service.discover("rita@acme.com")).sso
    kinds = [c.kind for c in await _tenant_history(sso)]
    assert kinds[:2] == [AccessChangeKind.SSO_REMOVED, AccessChangeKind.SSO_SAVED]


async def _tenant_history(sso: Sso) -> list[AccessChange]:
    async with sso.world.uow() as uow:
        return await uow.access.access_changes(sso.world.boot.tenant.tenant_id, None, 50)


# The test run from the Console


async def test_a_test_sign_in_reports_what_would_happen_without_doing_it(sso: Sso) -> None:
    await sso.configure_oidc(enabled=False)
    admin = await sso.admin()
    started = await sso.service.start_test(admin, BASE)
    state, code = sso.oidc.authorize(started.authorize_url, email="rita@acme.com", groups=("CX-Review", "x"))
    pending = await sso.service.test_result(admin, started.attempt_id)
    assert pending.status is SsoAttemptStatus.PENDING

    assert await sso.service.oidc_callback(state, code, None) == Landing(page="test_done")
    result = await sso.service.test_result(admin, started.attempt_id)
    assert result.status is SsoAttemptStatus.COMPLETED
    assert result.result == {
        "ok": True,
        "failure": None,
        "email": "rita@acme.com",
        "groups": ["CX-Review", "x"],
        "name": "Pessoa de Teste",
        "roles": ["review"],
        "person_exists": False,
    }
    # Nobody was created.
    assert all(p.user.email != "rita@acme.com" for p in await sso.world.service.list_users(admin))


async def test_a_failed_test_says_why(sso: Sso) -> None:
    await sso.configure_oidc()
    admin = await sso.admin()
    started = await sso.service.start_test(admin, BASE)
    state, code = sso.oidc.authorize(started.authorize_url, email="rita@elsewhere.com", groups=("CX-Review",))
    await sso.service.oidc_callback(state, code, None)
    result = await sso.service.test_result(admin, started.attempt_id)
    assert result.status is SsoAttemptStatus.FAILED
    assert result.result is not None
    assert (result.result["ok"], result.result["failure"]) == (False, "domain")
    assert result.result["email"] == "rita@elsewhere.com"


async def test_an_unreachable_provider_is_reported(sso: Sso) -> None:
    await sso.configure_oidc()
    verifier = new_url_token()
    started = await sso.service.start("rita@acme.com", binding(verifier), CONSOLE, BASE)
    state, code = sso.oidc.authorize(started.authorize_url, email="rita@acme.com", groups=("CX-Review",))
    sso.oidc.down = True
    assert fragment(await sso.service.oidc_callback(state, code, None)) == {"error": "unavailable"}


# SAML


async def test_saml_metadata_and_request_name_the_service_provider(sso: Sso) -> None:
    await sso.configure_saml()
    tenant = sso.world.boot.tenant.tenant_id
    metadata = (await sso.service.saml_metadata(tenant, BASE)).decode()
    assert f'entityID="urn:niadra:sso:{tenant}"' in metadata
    assert f'Location="{BASE}/v1/auth/sso/saml/{tenant}/acs"' in metadata
    assert 'WantAssertionsSigned="true"' in metadata

    started = await sso.service.start("rita@acme.com", binding(new_url_token()), CONSOLE, BASE)
    request = sso.saml.read_request(started.authorize_url)
    assert request.acs_url == f"{BASE}/v1/auth/sso/saml/{tenant}/acs"
    assert request.issuer == f"urn:niadra:sso:{tenant}"
    assert request.request_id.startswith("_")


async def test_a_signed_saml_assertion_signs_the_person_in(sso: Sso) -> None:
    await sso.configure_saml()
    verifier = new_url_token()
    started = await sso.service.start("mia@acme.com", binding(verifier), CONSOLE, BASE)
    request = sso.saml.read_request(started.authorize_url)
    posted = sso.saml.response(
        request, sso.world.clock.now(), email="Mia@Acme.com", groups=("niadra-admins",)
    )
    landing = await sso.service.saml_acs(sso.world.boot.tenant.tenant_id, posted, request.relay_state)
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    assert sso.world.service.authenticate(issued.token).roles == frozenset({Role.ADMIN})
    # The same answer posted again is refused.
    again = await sso.service.saml_acs(sso.world.boot.tenant.tenant_id, posted, request.relay_state)
    assert again == Landing(page="expired")


@pytest.mark.parametrize(
    "tamper",
    [
        {"sign": False},
        {"signer": "other"},
        {"audience": "urn:niadra:sso:someone-else"},
        {"recipient": "https://evil.test/acs"},
        {"in_response_to": "_another-request"},
        {"issuer": "https://another-idp.test"},
        {"expired": True},
        {"extra_assertion": True},
        {"encrypted": True},
        {"status": "urn:oasis:names:tc:SAML:2.0:status:Requester"},
    ],
)
async def test_a_saml_answer_that_fails_any_check_signs_nobody_in(
    sso: Sso, tamper: dict[str, object]
) -> None:
    await sso.configure_saml()
    verifier = new_url_token()
    started = await sso.service.start("mia@acme.com", binding(verifier), CONSOLE, BASE)
    request = sso.saml.read_request(started.authorize_url)
    now = sso.world.clock.now()
    options: dict[str, object] = {k: v for k, v in tamper.items() if k not in ("signer", "expired")}
    if tamper.get("signer") == "other":
        options["signer"] = self_signed("impostor.test")
    if tamper.get("expired"):
        options["not_on_or_after"] = now - timedelta(minutes=10)
    posted = sso.saml.response(request, now, email="mia@acme.com", groups=("niadra-admins",), **options)  # type: ignore[arg-type]
    landing = await sso.service.saml_acs(sso.world.boot.tenant.tenant_id, posted, request.relay_state)
    expected = "denied" if "status" in tamper else "invalid"
    assert fragment(landing) == {"error": expected}


async def test_a_saml_answer_for_another_tenant_or_garbage_is_refused(sso: Sso) -> None:
    await sso.configure_saml()
    started = await sso.service.start("mia@acme.com", binding(new_url_token()), CONSOLE, BASE)
    request = sso.saml.read_request(started.authorize_url)
    posted = sso.saml.response(request, sso.world.clock.now(), email="mia@acme.com")
    assert await sso.service.saml_acs(uuid4(), posted, request.relay_state) == Landing(page="expired")
    garbage = base64.b64encode(b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>").decode()
    landing = await sso.service.saml_acs(sso.world.boot.tenant.tenant_id, garbage, request.relay_state)
    assert fragment(landing) == {"error": "invalid"}


async def test_a_saml_certificate_that_does_not_parse_is_refused(sso: Sso) -> None:
    with pytest.raises(InvalidInput, match="certificate"):
        await sso.configure_saml(saml_idp_certificate="not a certificate at all" * 10)
    await sso.configure_saml()
    view = await sso.service.view(await sso.admin(), BASE)
    assert view.certificate is not None
    assert view.certificate.subject == "CN=idp.acme-saml.test"
    assert view.service_provider.saml_metadata_url.endswith("/metadata")


async def test_a_person_without_a_password_manages_the_authenticator_with_its_current_code(sso: Sso) -> None:
    await sso.configure_oidc(require_second_factor=True)
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    code = fragment(landing)["code"]
    new = await sso.service.second_factor_setup(code, verifier)
    issued = await sso.service.exchange(code, verifier, sso.world.totp(new.secret), None, None)
    rita = sso.world.service.authenticate(issued.token)
    me = await sso.world.service.me(rita)
    assert (me.sso, me.user.password_hash) == (True, "!")

    # She has no password: typing one proves nothing.
    with pytest.raises(Forbidden, match="invalid second factor"):
        await sso.world.service.regenerate_recovery_codes(rita, "any password")
    with pytest.raises(Forbidden, match="invalid second factor"):
        await sso.world.service.regenerate_recovery_codes(rita, None, "000000")
    proof = sso.world.totp(new.secret)
    codes = await sso.world.service.regenerate_recovery_codes(rita, None, proof)
    assert len(codes) == 10
    # The code of the proof works once, like at the login.
    with pytest.raises(Forbidden, match="invalid second factor"):
        await sso.world.service.start_second_factor_change(rita, None, proof)
    change = await sso.world.service.start_second_factor_change(rita, None, sso.world.totp(new.secret))
    assert change.account == "rita@acme.com"


async def test_a_person_without_a_password_or_an_authenticator_relies_on_the_provider(sso: Sso) -> None:
    await sso.configure_oidc()
    landing, verifier = await sso.oidc_login("rita@acme.com", ("CX-Review",))
    issued = await sso.service.exchange(fragment(landing)["code"], verifier, None, None, None)
    rita = sso.world.service.authenticate(issued.token)
    with pytest.raises(Conflict, match="without a password"):
        await sso.world.service.start_second_factor_change(rita, None, "123456")
