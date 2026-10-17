"""Public models of the Console's single sign-on (5.22, 7.4): the tenant's connection and the sign-in."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Model
from niadra.domain.control.records import Role, SsoProtocol

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
TotpCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class RoleMappingModel(Model):
    group: str = Field(
        min_length=1,
        max_length=256,
        description="A value of the groups claim (OIDC) or attribute (SAML): a group name, or an id such as"
        " the object id Microsoft Entra ID sends. Compared without case.",
    )
    role: Role = Field(description="Given on the whole tenant. The vendor role is never given by a group.")


class OidcIn(Model):
    issuer: str = Field(
        min_length=9,
        max_length=2048,
        description="The provider's issuer, https. Niadra reads `<issuer>/.well-known/openid-configuration`.",
    )
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str | None = Field(
        default=None,
        max_length=2048,
        description="Required when the connection is created or moves to OIDC; null keeps the one stored."
        " Never returned: it is stored sealed with the control plane's key.",
    )
    scopes: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Scopes beyond `openid email profile`, which always go, such as `groups`.",
    )


class SamlIn(Model):
    idp_entity_id: str = Field(
        min_length=1, max_length=1024, description="The provider's entity id (issuer)."
    )
    idp_sso_url: str = Field(
        min_length=9,
        max_length=2048,
        description="The provider's single sign-on address for the HTTP-Redirect binding, https.",
    )
    idp_certificate: str = Field(
        min_length=100,
        max_length=20_000,
        description="The certificate that signs the assertions, PEM or the base64 body from the metadata.",
    )


class SsoConnectionIn(Model):
    protocol: SsoProtocol
    domains: list[str] = Field(
        min_length=1,
        max_length=20,
        description="The e-mail domains whose people sign in through this connection. Each domain belongs to"
        " one tenant; public mailboxes (gmail.com and the like) are refused.",
    )
    enabled: bool = True
    enforced: bool = Field(
        default=False,
        description="People of the domains sign in only through SSO. A tenant-wide admin keeps the password,"
        " as the way in when the provider fails.",
    )
    default_role: Role | None = Field(
        default=None,
        description="For a person in no mapped group; null lets in only people of mapped groups.",
    )
    role_mappings: list[RoleMappingModel] = Field(default_factory=list, max_length=100)
    groups_attribute: str = Field(
        default="groups",
        min_length=1,
        max_length=256,
        description="The claim (OIDC) or attribute (SAML) that carries the groups.",
    )
    require_second_factor: bool = Field(
        default=False,
        description="Ask for the Niadra authenticator code after the provider, on top of the provider's MFA.",
    )
    oidc: OidcIn | None = None
    saml: SamlIn | None = None
    reason: Reason = Field(description="Why, kept in the tenant's access history.")

    @model_validator(mode="after")
    def _settings_of_the_protocol(self) -> "SsoConnectionIn":
        if self.protocol is SsoProtocol.OIDC and self.oidc is None:
            raise ValueError("`oidc` is required for an OIDC connection")
        if self.protocol is SsoProtocol.SAML and self.saml is None:
            raise ValueError("`saml` is required for a SAML connection")
        return self


class OidcOut(Model):
    issuer: str
    client_id: str
    scopes: list[str]
    client_secret_set: bool = Field(description="Always true: the secret itself is never returned.")


class CertificateOut(Model):
    subject: str
    not_after: datetime
    sha256_fingerprint: str
    pem: str = Field(
        description="The certificate as stored: public, so the Console shows it for the next save."
    )


class SamlOut(Model):
    idp_entity_id: str
    idp_sso_url: str
    certificate: CertificateOut


class SsoConnectionOut(Model):
    connection_id: UUID
    protocol: SsoProtocol
    domains: list[str]
    enabled: bool
    enforced: bool
    default_role: Role | None
    role_mappings: list[RoleMappingModel]
    groups_attribute: str
    require_second_factor: bool
    oidc: OidcOut | None
    saml: SamlOut | None
    created_at: datetime
    updated_at: datetime
    updated_by: UUID


class ServiceProviderOut(Model):
    """What to paste into the identity provider."""

    oidc_redirect_uri: str = Field(description="OIDC: the redirect (callback) URI of the application.")
    saml_entity_id: str = Field(description="SAML: the service provider's entity id (audience).")
    saml_acs_url: str = Field(description="SAML: the assertion consumer service, HTTP-POST binding.")
    saml_metadata_url: str = Field(
        description="SAML: the service provider's metadata, once the connection exists."
    )


class SsoOut(Model):
    connection: SsoConnectionOut | None = Field(description="Null while the tenant has no connection.")
    service_provider: ServiceProviderOut


class SsoRemoveRequest(Model):
    reason: Reason = Field(description="Why, kept in the tenant's access history.")


class SsoTestOut(Model):
    test_id: UUID
    authorize_url: str = Field(description="Open it in another tab: the provider's sign-in, in test mode.")
    expires_at: datetime


class SsoTestResultOut(Model):
    test_id: UUID
    status: Literal["pending", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
    ok: bool | None = Field(description="Null while the test waits for the provider.")
    failure: str | None = Field(
        description="Why a real sign-in would be refused: denied, expired, invalid, domain, no_role,"
        " other_tenant, deactivated, unavailable or disabled."
    )
    detail: str | None
    email: str | None = Field(description="The e-mail the provider sent for the account used in the test.")
    groups: list[str]
    roles: list[Role] = Field(description="The roles that account would get.")
    person_exists: bool | None


class SsoDiscoverRequest(Model):
    email: str = Field(min_length=3, max_length=320)


class SsoDiscoverOut(Model):
    sso: bool = Field(
        description="The e-mail's domain signs in through a tenant's SSO. Says nothing about the person."
    )
    protocol: SsoProtocol | None
    enforced: bool = Field(description="Only the tenant-wide admins of the domain may still use a password.")


class SsoStartRequest(Model):
    email: str = Field(min_length=3, max_length=320)
    binding: str = Field(
        pattern=r"^[A-Za-z0-9_-]{43}$",
        description="base64url, without padding, of the SHA-256 of a random value the Console keeps for this"
        " tab and sends again at the exchange.",
    )


class SsoStartOut(Model):
    authorize_url: str = Field(description="Where to send the browser: the provider's sign-in.")
    expires_at: datetime


class SsoExchangeRequest(Model):
    code: str = Field(
        min_length=20,
        max_length=128,
        description="The one-time sign-in code the control plane put in the fragment of the Console address.",
    )
    verifier: str = Field(
        min_length=20, max_length=128, description="The value whose SHA-256 went as `binding`."
    )
    totp: TotpCode | None = Field(
        default=None, description="When the tenant asks for the Niadra second factor."
    )
    recovery_code: str | None = Field(default=None, min_length=10, max_length=32)
    space_id: UUID | None = None

    @model_validator(mode="after")
    def _one_second_factor(self) -> "SsoExchangeRequest":
        if self.totp is not None and self.recovery_code is not None:
            raise ValueError("send `totp` or `recovery_code`, not both")
        return self


class SsoCodeRequest(Model):
    code: str = Field(min_length=20, max_length=128)
    verifier: str = Field(min_length=20, max_length=128)
