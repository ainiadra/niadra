"""Handle normalization: one canonical key per identifier, whatever field it came in (5.3).

Values are classified by their format, not by the name of the field that carried them: a CRM
"phone" column holding an e-mail becomes an e-mail handle. Opaque identifiers (system and app
ids, BSUIDs, documents) keep the declared type, because any format is legal for them.
"""

import re
from dataclasses import dataclass

from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.documents import (
    looks_like_cpf,
    normalize_country,
    normalize_org_registry,
    normalize_person_document,
)
from niadra.domain.identity.phones import clean_text, normalize_phone
from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind

_DOMAIN_PART = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+"
_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@" + _DOMAIN_PART + "$")
_DOMAIN = re.compile("^" + _DOMAIN_PART + "$")
_PHONE_LIKE = re.compile(r"^\+?[\d\s().\-/]{8,24}$")
_WA_USER_SERVERS = ("@s.whatsapp.net", "@c.us")
_WA_OTHER_SERVERS = ("@g.us", "@broadcast", "@newsletter", "@call")
"""Groups, broadcast lists, channels and calls: a WhatsApp id that names no person."""
_SCHEME = re.compile(r"^(?:mailto|tel):", re.IGNORECASE)
_ANGLE = re.compile(r"^[^<>]*<([^<>]+)>$")
_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

PHONE_FAMILY = frozenset({HandleType.PHONE_E164, HandleType.WA_ID})
CONTACT_TYPES = PHONE_FAMILY | {HandleType.EMAIL, HandleType.WA_JID, HandleType.WA_LID}
ORG_ONLY_TYPES = frozenset({HandleType.ORG_REGISTRY_HMAC, HandleType.EMAIL_DOMAIN})
PERSON_ONLY_TYPES = frozenset({HandleType.GOV_ID_HMAC})
DOCUMENT_TYPES = frozenset({HandleType.GOV_ID_HMAC, HandleType.ORG_REGISTRY_HMAC})
SCOPED_TYPES = frozenset({HandleType.WA_BSUID, HandleType.SYSTEM_ID})
WEAK_TYPES = frozenset({HandleType.EMAIL_DOMAIN, HandleType.ANON_ID})
"""Hints only: never enough to join handles in the synchronous path (5.3, 21.3)."""
PARTIAL_TYPES = frozenset({HandleType.WA_LID, HandleType.WA_BSUID, HandleType.ANON_ID})
"""Identifiers that say nothing about how to reach the person (5.3, "partial")."""


@dataclass(frozen=True, slots=True)
class HandleKey:
    type: HandleType
    scope: str
    value: str


@dataclass(frozen=True, slots=True)
class NewHandle:
    """A handle to insert, with the status it is born with."""

    key: HandleKey
    subject_kind: SubjectKind
    status: HandleStatus


@dataclass(frozen=True, slots=True)
class NormalizedHandle:
    key: HandleKey
    subject_kind: SubjectKind
    derived: tuple["NormalizedHandle", ...] = ()
    """Handles implied by the format, such as the phone behind a legacy `wa_id`."""

    @property
    def type(self) -> HandleType:
        return self.key.type


def classify(value: str) -> HandleType | None:
    """The contact type a value's format reveals, or None for opaque values."""
    text = contact_text(value).lower()
    if text.endswith(_WA_OTHER_SERVERS):
        return None
    if text.endswith("@lid"):
        return HandleType.WA_LID
    if text.endswith(_WA_USER_SERVERS):
        return HandleType.WA_JID
    if _EMAIL.match(text):
        return HandleType.EMAIL
    if _PHONE_LIKE.match(text) and 8 <= sum(c.isdigit() for c in text) <= 15:
        return HandleType.PHONE_E164
    return None


def normalize_handle(
    type: HandleType,
    value: str,
    scope: str | None = None,
    subject_kind: SubjectKind | None = None,
    *,
    default_kind: SubjectKind = SubjectKind.PERSON,
) -> NormalizedHandle:
    if type in CONTACT_TYPES or type in DOCUMENT_TYPES:
        value = contact_text(value)
    type = _reclassify(type, value)
    kind = _subject_kind(type, subject_kind, default_kind)
    return _normalize(type, value.strip(), (scope or "").strip(), kind)


def contact_text(value: str) -> str:
    """A contact value as typed or copied: invisible marks, compatibility forms, a `mailto:` or `tel:`
    scheme and the name of `Name <address>` left out. Opaque ids are never touched."""
    text = clean_text(value)
    if angle := _ANGLE.match(text):
        text = angle.group(1).strip()
    return _SCHEME.sub("", text).strip()


def gmail_canonical(email: str) -> str | None:
    """Gmail ignores dots and `+tags` in the local part; only a hint, one step less trusted (5.3)."""
    local, _, domain = email.partition("@")
    if domain not in _GMAIL_DOMAINS:
        return None
    return local.split("+", 1)[0].replace(".", "") + "@gmail.com"


def is_partial(types: set[HandleType]) -> bool:
    """A profile known only by channel-private ids, with no way to reach the person (5.3)."""
    return bool(types) and types <= PARTIAL_TYPES


def _reclassify(declared: HandleType, value: str) -> HandleType:
    if declared not in CONTACT_TYPES:
        return declared
    if value.lower().endswith(_WA_OTHER_SERVERS):
        raise InvalidInput("a WhatsApp group, list or channel id names no person")
    detected = classify(value)
    if detected is None:
        raise InvalidInput(f"the value does not look like a {declared.value}")
    if detected is HandleType.PHONE_E164 and declared in PHONE_FAMILY:
        return declared
    return detected


def _subject_kind(type: HandleType, given: SubjectKind | None, default: SubjectKind) -> SubjectKind:
    if given is SubjectKind.STAFF and type is not HandleType.SYSTEM_ID:
        # Staff is who the company's own systems name: an identifier a customer gives (a phone, an e-mail,
        # a document) never makes its conversation internal.
        raise InvalidInput("staff is named by a system id of the company's own systems")
    if type in ORG_ONLY_TYPES:
        if given is SubjectKind.PERSON:
            raise InvalidInput(f"a {type.value} handle belongs to an organization")
        return given or (default if default.is_organization else SubjectKind.ACCOUNT)
    if type in PERSON_ONLY_TYPES:
        if given is not None and given.is_organization:
            raise InvalidInput(f"a {type.value} handle belongs to a person")
        return SubjectKind.PERSON
    return given or default


def _normalize(type: HandleType, value: str, scope: str, kind: SubjectKind) -> NormalizedHandle:
    match type:
        case HandleType.PHONE_E164:
            return NormalizedHandle(HandleKey(type, "", normalize_phone(value)), kind)
        case HandleType.WA_ID:
            phone = normalize_phone(value if value.startswith("+") else "+" + value)
            return _with_phone(HandleKey(type, "", phone[1:]), phone, kind)
        case HandleType.WA_JID:
            user, _, _server = value.lower().partition("@")
            phone = normalize_phone("+" + user.split(":", 1)[0])
            return _with_phone(HandleKey(type, "", phone[1:] + "@s.whatsapp.net"), phone, kind)
        case HandleType.WA_LID:
            user = value.lower().removesuffix("@lid").split(":", 1)[0]  # the device, as in a JID
            if not user.isdigit():
                raise InvalidInput("the value does not look like a wa_lid")
            return NormalizedHandle(HandleKey(type, "", user + "@lid"), kind)
        case HandleType.EMAIL:
            return NormalizedHandle(HandleKey(type, "", value.lower()), kind)
        case HandleType.EMAIL_DOMAIN:
            domain = value.lower().rsplit("@", 1)[-1]
            if not _DOMAIN.match(domain):
                raise InvalidInput("the value does not look like a domain")
            return NormalizedHandle(HandleKey(type, "", domain), kind)
        case HandleType.GOV_ID_HMAC:
            country = normalize_country(scope or ("BR" if looks_like_cpf(value) else None))
            return NormalizedHandle(HandleKey(type, country, normalize_person_document(country, value)), kind)
        case HandleType.ORG_REGISTRY_HMAC:
            country = normalize_country(scope or "BR")
            return NormalizedHandle(HandleKey(type, country, normalize_org_registry(country, value)), kind)
        case _ if type in SCOPED_TYPES and not scope:
            raise InvalidInput(f"a {type.value} handle needs its scope")
        case _:
            return NormalizedHandle(HandleKey(type, scope, value), kind)


def _with_phone(key: HandleKey, phone: str, kind: SubjectKind) -> NormalizedHandle:
    derived = NormalizedHandle(HandleKey(HandleType.PHONE_E164, "", phone), kind)
    return NormalizedHandle(key, kind, (derived,))
