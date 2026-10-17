"""Rules of the tenant's single sign-on (5.22): which e-mail domains a connection may claim, which roles a
person gets from the identity provider's groups, and how a failed sign-in is told to the Console.

The protocols themselves (OIDC and SAML) sit behind the identity provider port; nothing here does I/O.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from niadra.domain.common.errors import InvalidInput, Unauthenticated
from niadra.domain.control.records import Role, RoleMapping

# The identity provider can grant these. The vendor role names its sources, which no group can say.
SSO_ROLES = frozenset({Role.ADMIN, Role.SECURITY, Role.INTEGRATION, Role.REVIEW, Role.ANALYSIS})
MAX_DOMAINS = 20
MAX_MAPPINGS = 100
MAX_GROUP_LENGTH = 256

# Mailboxes anyone can open are never proof that a person belongs to a company.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "outlook.com.br",
        "hotmail.com",
        "hotmail.com.br",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.com.br",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "uol.com.br",
        "bol.com.br",
        "terra.com.br",
        "ig.com.br",
    }
)

_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)
_ATTRIBUTE_RE = re.compile(r"^[\w.:/#-]{1,256}$")


class SsoFailure(StrEnum):
    """Why a sign-in through the identity provider did not end in a session. The Console turns each one into
    a sentence; none of them carries personal data, so they can travel in the URL fragment."""

    DENIED = "denied"
    """The person cancelled, or the provider refused them."""
    INVALID = "invalid"
    """The provider's answer failed validation: signature, issuer, audience, time or nonce."""
    DOMAIN = "domain"
    """The e-mail is outside the connection's domains, or the provider says it is not verified."""
    NO_ROLE = "no_role"
    """No group maps to a role and the connection has no default role."""
    OTHER_TENANT = "other_tenant"
    """The e-mail belongs to a person of another tenant."""
    DEACTIVATED = "deactivated"
    UNAVAILABLE = "unavailable"
    """The provider did not answer in time."""
    DISABLED = "disabled"
    """The tenant turned SSO off after the start."""


class SsoRefused(Unauthenticated):
    """A sign-in through the identity provider was refused; `failure` says why, without personal data."""

    def __init__(self, failure: SsoFailure, detail: str = "") -> None:
        super().__init__(detail or f"single sign-on refused: {failure.value}")
        self.failure = failure


@dataclass(frozen=True, slots=True)
class IdpIdentity:
    """What a valid answer of the identity provider proved about the person."""

    email: str
    subject: str
    groups: tuple[str, ...] = ()
    email_verified: bool | None = None
    name: str | None = None


def email_domain(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1].rstrip(".")


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("@").rstrip(".")
    if not _DOMAIN_RE.match(domain):
        raise InvalidInput("invalid e-mail domain")
    if domain in PUBLIC_EMAIL_DOMAINS:
        raise InvalidInput("a public e-mail domain cannot belong to a tenant")
    return domain


def normalize_domains(values: Iterable[str]) -> tuple[str, ...]:
    domains = sorted({normalize_domain(v) for v in values})
    if not 1 <= len(domains) <= MAX_DOMAINS:
        raise InvalidInput(f"a connection needs 1 to {MAX_DOMAINS} e-mail domains")
    return tuple(domains)


def check_mappings(mappings: Sequence[RoleMapping], default_role: Role | None) -> tuple[RoleMapping, ...]:
    if len(mappings) > MAX_MAPPINGS:
        raise InvalidInput(f"at most {MAX_MAPPINGS} group mappings")
    cleaned: list[RoleMapping] = []
    for m in mappings:
        group = m.group.strip()
        if not 1 <= len(group) <= MAX_GROUP_LENGTH:
            raise InvalidInput(f"a group needs 1 to {MAX_GROUP_LENGTH} characters")
        if m.role not in SSO_ROLES:
            raise InvalidInput("the vendor role is given by invitation, never by a group")
        if (group.casefold(), m.role) not in {(c.group.casefold(), c.role) for c in cleaned}:
            cleaned.append(RoleMapping(group, m.role))
    if default_role is not None and default_role not in SSO_ROLES:
        raise InvalidInput("the vendor role cannot be the default role")
    return tuple(cleaned)


def check_attribute_name(name: str) -> str:
    """The claim (OIDC) or attribute (SAML) that carries the groups: `groups`, or a URI for SAML."""
    name = name.strip()
    if not _ATTRIBUTE_RE.match(name):
        raise InvalidInput("invalid groups claim or attribute name")
    return name


def check_https_url(url: str, what: str) -> str:
    """An address the control plane calls or sends people to: https, a host, no credentials or fragment."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise InvalidInput(f"the {what} must be an https address")
    if len(url) > 2048:
        raise InvalidInput(f"the {what} is too long")
    return url


def roles_from_groups(
    groups: Iterable[str], mappings: Sequence[RoleMapping], default_role: Role | None
) -> frozenset[Role]:
    """The roles of every mapped group the person is in; the default role when none maps; empty when there is
    no default. Groups compare without case, since providers differ on it."""
    present = {g.casefold() for g in groups}
    roles = frozenset(m.role for m in mappings if m.group.casefold() in present)
    if roles:
        return roles
    return frozenset({default_role}) if default_role is not None else frozenset()


def admitted_email(identity: IdpIdentity, domains: Sequence[str]) -> str:
    """The person's e-mail when the provider may speak for it: inside the connection's domains and not
    marked unverified. A provider that sends no `email_verified` is trusted for its own domains."""
    email = identity.email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise SsoRefused(SsoFailure.INVALID, "the identity provider sent no e-mail")
    if identity.email_verified is False or email_domain(email) not in domains:
        raise SsoRefused(SsoFailure.DOMAIN)
    return email
