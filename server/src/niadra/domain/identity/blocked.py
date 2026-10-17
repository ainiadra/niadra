"""Values that must never join two subjects (5.3, protection 2).

QA placeholders are the classic cause of mass merges: the fixed rules below catch the common
ones, each tenant extends them in `TenantSettings.blocked_values`, and the Console adds more
through `POST /v1/identity/handles/block` (stored as HMAC).
"""

import re
from collections.abc import Mapping, Sequence

from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.handles import HandleKey, normalize_handle
from niadra.domain.vocabulary import HandleType

WEBMAIL_DOMAINS = frozenset(
    [
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.com.br",
        "outlook.com",
        "outlook.com.br",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.com.br",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "uol.com.br",
        "bol.com.br",
        "terra.com.br",
        "ig.com.br",
        "globo.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "zoho.com",
        "yandex.com",
    ]
)
"""A webmail domain says nothing about an organization; it is never an `email_domain` handle (21.3)."""

_PLACEHOLDER_LOCALS = frozenset(
    [
        "test",
        "teste",
        "testing",
        "noreply",
        "no-reply",
        "donotreply",
        "naoresponda",
        "nao-responda",
        "null",
        "none",
        "nenhum",
        "sememail",
        "sem-email",
        "naotem",
        "nao-tem",
        "fake",
        "asdf",
        "qwerty",
    ]
)
_PLACEHOLDER_DOMAINS = frozenset(
    ["example.com", "example.org", "example.net", "test.com", "teste.com", "mailinator.com"]
)
_RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost", ".local")
_PLACEHOLDER_IDS = frozenset({"0", "-", "null", "none", "undefined", "n/a", "na", "test", "teste", "unknown"})


def is_fixed_blocked(key: HandleKey) -> bool:
    match key.type:
        case HandleType.PHONE_E164 | HandleType.WA_ID | HandleType.WA_JID:
            subscriber = re.sub(r"\D", "", key.value)[-8:]
            return _repeated_or_sequence(subscriber)
        case HandleType.GOV_ID_HMAC | HandleType.ORG_REGISTRY_HMAC:
            return _repeated_or_sequence(key.value) or key.value == "12345678909"
        case HandleType.EMAIL:
            local, _, domain = key.value.partition("@")
            return (
                local in _PLACEHOLDER_LOCALS
                or domain in _PLACEHOLDER_DOMAINS
                or domain.endswith(_RESERVED_TLDS)
            )
        case HandleType.EMAIL_DOMAIN:
            return key.value in WEBMAIL_DOMAINS or key.value in _PLACEHOLDER_DOMAINS
        case HandleType.SYSTEM_ID | HandleType.APP_USER_ID:
            return key.value.strip().lower() in _PLACEHOLDER_IDS or set(key.value) == {"0"}
        case _:
            return False


def is_tenant_blocked(key: HandleKey, blocked_values: Mapping[str, Sequence[str]]) -> bool:
    """Tenant lists hold raw values; each is normalized like the handle before comparing."""
    for raw in blocked_values.get(key.type.value, ()):
        try:
            candidate = normalize_handle(key.type, raw, key.scope or None).key.value
        except InvalidInput:
            candidate = raw.strip()
        if candidate == key.value:
            return True
    return False


def _repeated_or_sequence(digits: str) -> bool:
    """`99999999` and `12345678...` are what people type when a form insists."""
    return len(set(digits)) == 1 or digits.startswith("1234567")
