"""How a handle value appears in the Console, by the viewer's role (5.22, 7.1 "with the role's mask")."""

from niadra.domain.common.space import PersonPrincipal, Principal
from niadra.domain.vocabulary import HandleType

CONTACT_REVEAL_ROLES = frozenset({"security", "admin"})
"""Security and privacy handle erasure and incidents, so they see contact values in full."""


def reveals_contacts(principal: Principal) -> bool:
    """Whether the reader sees contact values in full: a person with one of `CONTACT_REVEAL_ROLES`, never a
    source key, whatever its scopes."""
    return isinstance(principal, PersonPrincipal) and bool(principal.roles & CONTACT_REVEAL_ROLES)


_CONTACTS = frozenset({HandleType.PHONE_E164, HandleType.WA_ID, HandleType.WA_JID, HandleType.EMAIL})
_DOCUMENTS = frozenset({HandleType.GOV_ID_HMAC, HandleType.ORG_REGISTRY_HMAC})


def mask_value(type: HandleType, value: str, *, reveal_contacts: bool) -> str:
    """Documents are always masked; opaque ids stay visible, since integrators debug with them."""
    if type in _DOCUMENTS:
        return "*" * max(0, len(value) - 2) + value[-2:]
    if type not in _CONTACTS or reveal_contacts:
        return value
    if type is HandleType.EMAIL:
        local, _, domain = value.partition("@")
        return local[:1] + "***@" + domain
    user, at, server = value.partition("@")
    return user[:3] + "*" * max(0, len(user) - 7) + user[-4:] + at + server


def mask_listed(type: HandleType, value: str) -> str:
    """The customer list shows no identifier in full, whatever the role: contacts and documents as above,
    and an opaque id by its two first and two last characters."""
    if type in _CONTACTS or type in _DOCUMENTS:
        return mask_value(type, value, reveal_contacts=False)
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]
