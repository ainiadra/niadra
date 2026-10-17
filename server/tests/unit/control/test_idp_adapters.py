"""The identity provider adapters on their own: each refusal of a SAML or OIDC answer names the check that
failed, so a broken check cannot hide behind another one."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from niadra.adapters.outbound.sso.oidc import HttpOidcClient
from niadra.adapters.outbound.sso.saml import SignXmlSamlService, load_certificate
from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.sso import SsoRefused

from .idp import (
    CLIENT_ID,
    CLIENT_SECRET,
    IDP_ENTITY,
    IDP_SSO_URL,
    ISSUER,
    FakeOidcProvider,
    FakeSamlProvider,
    SamlRequest,
    new_key,
    self_signed,
)

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
ENTITY = "urn:niadra:sso:0192a000-0000-7000-8000-000000000001"
ACS = "https://control.niadra.test/v1/auth/sso/saml/0192a000-0000-7000-8000-000000000001/acs"


def _request() -> SamlRequest:
    url = SignXmlSamlService().login_url(
        idp_sso_url=IDP_SSO_URL,
        entity_id=ENTITY,
        acs_url=ACS,
        request_id="_r1",
        relay_state="state-1",
        now=NOW,
    )
    return FakeSamlProvider.read_request(url)


def _identity(provider: FakeSamlProvider, posted: str, *, certificate: str | None = None) -> object:
    return SignXmlSamlService().identity(
        saml_response=posted,
        idp_entity_id=IDP_ENTITY,
        idp_certificate=certificate or provider.certificate,
        entity_id=ENTITY,
        acs_url=ACS,
        request_id="_r1",
        groups_attribute="groups",
        now=NOW,
    )


def test_the_authn_request_is_deflated_and_names_the_acs() -> None:
    request = _request()
    assert (request.request_id, request.acs_url, request.relay_state, request.issuer) == (
        "_r1",
        ACS,
        "state-1",
        ENTITY,
    )


def test_a_valid_saml_answer_gives_the_email_and_the_groups() -> None:
    provider = FakeSamlProvider.create()
    posted = provider.response(_request(), NOW, email="Mia@Acme.com", groups=("a", "b"))
    identity = _identity(provider, posted)
    assert identity == type(identity)(email="Mia@Acme.com", subject="Mia@Acme.com", groups=("a", "b"))


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"sign": False}, "not signed by the configured certificate"),
        ({"signer": "other"}, "not signed by the configured certificate"),
        ({"audience": "urn:someone-else"}, "another service provider"),
        ({"recipient": "https://evil.test/acs"}, "does not confirm this sign-in"),
        ({"in_response_to": "_r2"}, "does not confirm this sign-in"),
        ({"issuer": "https://another-idp.test"}, "another identity provider"),
        ({"expired": True}, "expired"),
        ({"extra_assertion": True}, "exactly one assertion"),
        ({"encrypted": True}, "encrypted assertions are not supported"),
        ({"status": "urn:oasis:names:tc:SAML:2.0:status:Responder"}, "did not sign the person in"),
    ],
)
def test_each_saml_check_refuses_on_its_own(options: dict[str, object], message: str) -> None:
    provider = FakeSamlProvider.create()
    kwargs = {k: v for k, v in options.items() if k not in ("signer", "expired")}
    if options.get("signer") == "other":
        kwargs["signer"] = self_signed("impostor.test")
    if options.get("expired"):
        kwargs["not_on_or_after"] = NOW - timedelta(minutes=5)
    posted = provider.response(_request(), NOW, email="mia@acme.com", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(SsoRefused, match=message):
        _identity(provider, posted)


def test_a_response_to_another_request_or_a_dtd_is_refused() -> None:
    provider = FakeSamlProvider.create()
    posted = provider.response(_request(), NOW, email="mia@acme.com")
    with pytest.raises(SsoRefused, match="answers another sign-in"):
        SignXmlSamlService().identity(
            saml_response=posted,
            idp_entity_id=IDP_ENTITY,
            idp_certificate=provider.certificate,
            entity_id=ENTITY,
            acs_url=ACS,
            request_id="_other",
            groups_attribute="groups",
            now=NOW,
        )
    entity = base64.b64encode(b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>').decode()
    with pytest.raises(SsoRefused):
        _identity(provider, entity)
    with pytest.raises(SsoRefused, match="not base64"):
        _identity(provider, "%%%")


def test_an_expired_provider_certificate_is_refused_with_a_clear_reason() -> None:
    key, certificate = self_signed("old.idp.test", not_after=datetime(2021, 1, 1, tzinfo=UTC))
    provider = FakeSamlProvider(key, certificate)
    posted = provider.response(_request(), NOW, email="mia@acme.com")
    with pytest.raises(SsoRefused, match="certificate expired"):
        _identity(provider, posted)


def test_certificates_load_as_pem_or_as_the_bare_base64_of_the_metadata() -> None:
    _, pem = self_signed("idp.test")
    body = "".join(line for line in pem.splitlines() if "CERTIFICATE" not in line)
    assert load_certificate(pem) == load_certificate(body)
    info = SignXmlSamlService().certificate(body)
    assert info.subject == "CN=idp.test"
    assert len(info.sha256_fingerprint.split(":")) == 32
    with pytest.raises(InvalidInput):
        load_certificate("MIIB" + "A" * 200)


def test_the_metadata_asks_for_signed_assertions() -> None:
    xml = SignXmlSamlService().metadata(entity_id=ENTITY, acs_url=ACS).decode()
    assert f'entityID="{ENTITY}"' in xml
    assert 'WantAssertionsSigned="true"' in xml
    assert "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" in xml


async def _grant(
    provider: FakeOidcProvider, **overrides: object
) -> tuple[HttpOidcClient, str, dict[str, str]]:
    client = HttpOidcClient(provider)
    url = (
        f"{ISSUER}/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri=https%3A%2F%2Fc%2Fcb"
        "&scope=openid+email&state=s&nonce=n1&code_challenge="
        + base64.urlsafe_b64encode(hashlib.sha256(b"verifier-value").digest()).decode().rstrip("=")
        + "&code_challenge_method=S256"
    )
    _, code = provider.authorize(url, email="rita@acme.com", groups=("g",), **overrides)  # type: ignore[arg-type]
    args = {"code": code, "redirect_uri": "https://c/cb", "code_verifier": "verifier-value", "nonce": "n1"}
    return client, code, args


async def _redeem(client: HttpOidcClient, provider: FakeOidcProvider, args: dict[str, str]) -> object:
    discovered = await client.discover(ISSUER)
    return await client.redeem(
        discovered, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, groups_claim="groups", now=NOW, **args
    )


async def test_oidc_redeems_a_code_with_pkce_and_reads_the_claims() -> None:
    provider = FakeOidcProvider(now=lambda: NOW)
    client, _, args = await _grant(provider)
    identity = await _redeem(client, provider, args)
    assert identity == type(identity)(
        email="rita@acme.com", subject="idp-rita@acme.com", groups=("g",), name="Pessoa de Teste"
    )


async def test_a_wrong_pkce_verifier_is_refused_at_the_token_endpoint() -> None:
    provider = FakeOidcProvider(now=lambda: NOW)
    client, _, args = await _grant(provider)
    with pytest.raises(SsoRefused, match="token endpoint answered 400"):
        await _redeem(client, provider, args | {"code_verifier": "another-verifier"})


async def test_a_rotated_provider_key_is_found_by_reading_the_keys_again() -> None:
    provider = FakeOidcProvider(now=lambda: NOW)
    client, _, args = await _grant(provider)
    await _redeem(client, provider, args)
    # The provider rotates its key; the cached key set no longer has the new kid.
    provider.key, provider.kid = new_key(), "key-2"
    _, _, args = await _grant(provider)
    assert await _redeem(client, provider, args)
    assert provider.calls.count(f"GET {ISSUER}/keys") == 2


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_symmetric_and_unsigned_id_tokens_are_refused(algorithm: str) -> None:
    provider = FakeOidcProvider(now=lambda: NOW)
    client, code, args = await _grant(provider)
    body = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "x",
        "iat": 1,
        "exp": 2**31,
        "nonce": "n1",
        "email": "r@acme.com",
    }
    forged = jwt.encode(body, CLIENT_SECRET if algorithm == "HS256" else None, algorithm=algorithm)  # type: ignore[arg-type]
    provider.codes[code]["id_token"] = forged
    with pytest.raises(SsoRefused, match="algorithm"):
        await _redeem(client, provider, args)


async def test_a_token_for_two_audiences_needs_the_client_as_authorized_party() -> None:
    provider = FakeOidcProvider(now=lambda: NOW)
    client, _, args = await _grant(provider, claims={"aud": [CLIENT_ID, "other"], "azp": "other"})
    with pytest.raises(SsoRefused, match="another client"):
        await _redeem(client, provider, args)


async def test_discovery_must_name_the_same_issuer_and_https_endpoints() -> None:
    provider = FakeOidcProvider(now=lambda: NOW, discovery_issuer="https://impostor.test")
    with pytest.raises(InvalidInput, match="another issuer"):
        await HttpOidcClient(provider).discover(ISSUER)
    assert json.loads((await FakeOidcProvider(now=lambda: NOW).get(f"{ISSUER}/keys", {}, 1)).body)["keys"]
