"""Single sign-on routes of the control plane (5.22, 7.4).

Two kinds of route: the tenant's connection, for admins with a person token (`/v1/sso`), and the sign-in,
public (`/v1/auth/sso/...`). The identity provider talks to two of them with the browser in between: the OIDC
callback and the SAML assertion consumer service, which answer with a redirect back to the Console.
"""

from html import escape
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from niadra.adapters.inbound.http.control import ControlContainer, Person, _container
from niadra.app.sso import ConnectionSpec, ConnectionView, Landing, SsoService
from niadra.contracts.control import SecondFactorOut, TokenOut
from niadra.contracts.sso import (
    CertificateOut,
    OidcOut,
    RoleMappingModel,
    SamlOut,
    ServiceProviderOut,
    SsoCodeRequest,
    SsoConnectionIn,
    SsoConnectionOut,
    SsoDiscoverOut,
    SsoDiscoverRequest,
    SsoExchangeRequest,
    SsoOut,
    SsoRemoveRequest,
    SsoStartOut,
    SsoStartRequest,
    SsoTestOut,
    SsoTestResultOut,
)
from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.domain.control.credentials import totp_uri
from niadra.domain.control.records import RoleMapping, SsoAttempt, SsoAttemptStatus

router = APIRouter()

MAX_ACS_BYTES = 1024 * 1024
# The pages the provider's tab lands on: no script, no framing, no referrer, no cache.
PAGE_HEADERS = {
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def _sso(request: Request) -> SsoService:
    container: ControlContainer = _container(request)
    if container.sso is None:
        raise Unavailable("single sign-on is not set up on this control plane")
    return container.sso


def _base(request: Request) -> str:
    """The control plane's public address: set by the deployment, or read from the request, whose scheme and
    host the proxy headers already corrected."""
    configured = _container(request).public_url
    return (configured or str(request.base_url)).rstrip("/")


Sso = Annotated[SsoService, Depends(_sso)]


# The tenant's connection (admins)


@router.get("/v1/sso", response_model=SsoOut)
async def get_sso(caller: Person, service: Sso, request: Request) -> SsoOut:
    """The tenant's single sign-on and what to paste into the identity provider. Needs the admin role."""
    return _sso_out(await service.view(caller, _base(request)))


@router.put("/v1/sso", response_model=SsoOut)
async def put_sso(body: SsoConnectionIn, caller: Person, service: Sso, request: Request) -> SsoOut:
    """Creates or replaces the tenant's connection, OIDC or SAML. An OIDC issuer is checked against its
    discovery document; a SAML certificate must parse. A domain another tenant uses answers 409. Needs the
    admin role."""
    spec = ConnectionSpec(
        protocol=body.protocol,
        domains=body.domains,
        enabled=body.enabled,
        enforced=body.enforced,
        default_role=body.default_role,
        role_mappings=[RoleMapping(m.group, m.role) for m in body.role_mappings],
        groups_attribute=body.groups_attribute,
        require_second_factor=body.require_second_factor,
        oidc_issuer=body.oidc.issuer if body.oidc else None,
        oidc_client_id=body.oidc.client_id if body.oidc else None,
        oidc_client_secret=body.oidc.client_secret if body.oidc else None,
        oidc_scopes=body.oidc.scopes if body.oidc else (),
        saml_idp_entity_id=body.saml.idp_entity_id if body.saml else None,
        saml_idp_sso_url=body.saml.idp_sso_url if body.saml else None,
        saml_idp_certificate=body.saml.idp_certificate if body.saml else None,
    )
    await service.save(caller, spec, body.reason)
    return _sso_out(await service.view(caller, _base(request)))


@router.delete("/v1/sso", status_code=204)
async def delete_sso(body: SsoRemoveRequest, caller: Person, service: Sso) -> Response:
    """Removes the tenant's connection. People created by it keep their roles and sign in with a password
    only after an admin sends them an invitation."""
    await service.remove(caller, body.reason)
    return Response(status_code=204)


@router.post("/v1/sso/test", response_model=SsoTestOut, status_code=201)
async def start_test(caller: Person, service: Sso, request: Request) -> SsoTestOut:
    """A test sign-in through the provider: open `authorize_url` in another tab and sign in; then
    GET /v1/sso/tests/{test_id} says what a real sign-in would do (e-mail, groups, roles). Nobody is created
    and no role changes. Works on a connection that is still off."""
    started = await service.start_test(caller, _base(request))
    return SsoTestOut(
        test_id=started.attempt_id, authorize_url=started.authorize_url, expires_at=started.expires_at
    )


@router.get("/v1/sso/tests/{test_id}", response_model=SsoTestResultOut)
async def test_result(test_id: UUID, caller: Person, service: Sso) -> SsoTestResultOut:
    """The outcome of a test sign-in; `pending` until the provider answers (ten minutes at most)."""
    return _test_out(await service.test_result(caller, test_id))


# The sign-in (public)


@router.post("/v1/auth/sso/discover", response_model=SsoDiscoverOut)
async def discover(body: SsoDiscoverRequest, service: Sso) -> SsoDiscoverOut:
    """Whether the e-mail's domain signs in through SSO. It answers by domain, so it never says whether a
    person exists."""
    found = await service.discover(body.email)
    return SsoDiscoverOut(sso=found.sso, protocol=found.protocol, enforced=found.enforced)


@router.post("/v1/auth/sso/start", response_model=SsoStartOut)
async def start(body: SsoStartRequest, service: Sso, request: Request) -> SsoStartOut:
    """The identity provider's address for this e-mail, with a fresh state, nonce and PKCE challenge. Only
    the Console's origins may start (403 otherwise): the provider's answer comes back to that origin."""
    started = await service.start(body.email, body.binding, request.headers.get("origin"), _base(request))
    return SsoStartOut(authorize_url=started.authorize_url, expires_at=started.expires_at)


@router.get("/v1/auth/sso/oidc/callback", response_class=HTMLResponse, include_in_schema=True)
async def oidc_callback(
    service: Sso,
    state: Annotated[str, Query(max_length=256)] = "",
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    """The OIDC redirect URI. Answers 303 to the Console with a one-time sign-in code in the URL fragment, or
    with the reason of a refusal; a test run lands on a short page instead."""
    return _landing(await service.oidc_callback(state, code, error))


@router.get("/v1/auth/sso/saml/{tenant_id}/metadata", response_class=Response)
async def saml_metadata(tenant_id: UUID, service: Sso, request: Request) -> Response:
    """The service provider's SAML metadata: entity id, assertion consumer service and name id format."""
    xml = await service.saml_metadata(tenant_id, _base(request))
    return Response(xml, media_type="application/samlmetadata+xml", headers={"cache-control": "no-store"})


@router.post(
    "/v1/auth/sso/saml/{tenant_id}/acs",
    response_class=HTMLResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "required": ["SAMLResponse", "RelayState"],
                        "properties": {
                            "SAMLResponse": {"type": "string", "description": "The base64 SAML Response."},
                            "RelayState": {
                                "type": "string",
                                "description": "Sent back as Niadra put it in the request.",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def saml_acs(tenant_id: UUID, service: Sso, request: Request) -> Response:
    """The assertion consumer service (HTTP-POST binding): `SAMLResponse` and `RelayState` as a form. The
    assertion must be signed by the configured certificate and answer the request Niadra sent."""
    if int(request.headers.get("content-length") or 0) > MAX_ACS_BYTES:
        raise InvalidInput("the SAML response is too large")
    body = (await request.body())[: MAX_ACS_BYTES + 1]
    if len(body) > MAX_ACS_BYTES:
        raise InvalidInput("the SAML response is too large")
    form = parse_qs(body.decode("ascii", errors="replace"), max_num_fields=10)
    response = (form.get("SAMLResponse") or [""])[0]
    relay_state = (form.get("RelayState") or [""])[0]
    return _landing(await service.saml_acs(tenant_id, response, relay_state))


@router.post("/v1/auth/sso/exchange", response_model=TokenOut)
async def exchange(body: SsoExchangeRequest, service: Sso) -> TokenOut:
    """The session for a one-time sign-in code and the value the Console kept for the tab. When the tenant
    asks for the Niadra second factor, the answers are those of the password login: 401 "second factor
    enrollment required" (then POST /v1/auth/sso/second-factor) or "invalid second factor"; the code stays
    valid for five wrong codes and ten minutes."""
    issued = await service.exchange(body.code, body.verifier, body.totp, body.recovery_code, body.space_id)
    return TokenOut(
        access_token=issued.token,
        expires_in=issued.expires_in,
        space_id=issued.space_id,
        recovery_codes=issued.recovery_codes,
        recovery_codes_left=issued.recovery_codes_left,
    )


@router.post("/v1/auth/sso/second-factor", response_model=SecondFactorOut)
async def sso_second_factor(body: SsoCodeRequest, service: Sso) -> SecondFactorOut:
    """The authenticator enrollment of a person who signed in through SSO, when the tenant asks for the Niadra
    second factor: the sign-in code and the kept value stand in for the password."""
    new = await service.second_factor_setup(body.code, body.verifier)
    return SecondFactorOut(secret=new.secret, totp_uri=totp_uri(new.secret, new.account))


def _landing(landing: Landing) -> Response:
    if landing.redirect:
        return RedirectResponse(
            landing.redirect,
            status_code=303,
            headers={"referrer-policy": "no-referrer", "cache-control": "no-store"},
        )
    if landing.page == "test_done":
        return _page(
            "Teste de SSO concluído",
            "O provedor respondeu. Volte à aba do Console para ver o resultado do teste; esta aba pode ser"
            " fechada.",
        )
    return _page(
        "O login pelo SSO expirou",
        "Esta resposta do provedor já foi usada ou chegou depois de dez minutos. Volte ao Console e entre de"
        " novo.",
        status=400,
    )


def _page(title: str, text: str, status: int = 200) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · Niadra</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:48px 16px;background:#f7f7f2;"
        "color:#181c12}main{max-width:32rem;margin:0 auto}h1{font-size:1.4rem;margin:0 0 .5rem}</style>"
        f"</head><body><main><h1>{escape(title)}</h1><p>{escape(text)}</p></main></body></html>"
    )
    return HTMLResponse(html, status_code=status, headers=PAGE_HEADERS)


def _sso_out(view: ConnectionView) -> SsoOut:
    sp = view.service_provider
    c = view.connection
    connection = None
    if c is not None:
        connection = SsoConnectionOut(
            connection_id=c.connection_id,
            protocol=c.protocol,
            domains=list(c.domains),
            enabled=c.enabled,
            enforced=c.enforced,
            default_role=c.default_role,
            role_mappings=[RoleMappingModel(group=m.group, role=m.role) for m in c.role_mappings],
            groups_attribute=c.groups_attribute,
            require_second_factor=c.require_second_factor,
            oidc=OidcOut(
                issuer=c.oidc.issuer,
                client_id=c.oidc.client_id,
                scopes=list(c.oidc.scopes),
                client_secret_set=True,
            )
            if c.oidc
            else None,
            saml=SamlOut(
                idp_entity_id=c.saml.idp_entity_id,
                idp_sso_url=c.saml.idp_sso_url,
                certificate=CertificateOut(
                    subject=view.certificate.subject,
                    not_after=view.certificate.not_after,
                    sha256_fingerprint=view.certificate.sha256_fingerprint,
                    pem=c.saml.idp_certificate,
                ),
            )
            if c.saml and view.certificate
            else None,
            created_at=c.created_at,
            updated_at=c.updated_at,
            updated_by=c.updated_by,
        )
    return SsoOut(
        connection=connection,
        service_provider=ServiceProviderOut(
            oidc_redirect_uri=sp.oidc_redirect_uri,
            saml_entity_id=sp.saml_entity_id,
            saml_acs_url=sp.saml_acs_url,
            saml_metadata_url=sp.saml_metadata_url,
        ),
    )


def _test_out(attempt: SsoAttempt) -> SsoTestResultOut:
    result = attempt.result or {}
    status = {
        SsoAttemptStatus.COMPLETED: "completed",
        SsoAttemptStatus.FAILED: "failed",
    }.get(attempt.status, "pending")
    return SsoTestResultOut(
        test_id=attempt.attempt_id,
        status=status,
        created_at=attempt.created_at,
        completed_at=attempt.completed_at,
        ok=result.get("ok"),
        failure=result.get("failure"),
        detail=result.get("detail"),
        email=result.get("email"),
        groups=result.get("groups", []),
        roles=result.get("roles", []),
        person_exists=result.get("person_exists"),
    )
