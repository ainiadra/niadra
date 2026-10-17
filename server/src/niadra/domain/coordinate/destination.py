"""The canonical destination and the suppression key (the suppression-list spec, 3 and 4).

A contact goes to a phone number or an e-mail address, whatever handle carried it: `phone:+<E.164>`, with
the Brazilian ninth digit, or `email:<address>` trimmed and lowercased. The same form feeds the contact
token's `rcpt` and each reader's key of the suppression list, so a reader matches the destination it is
about to contact and learns nothing about anyone else.
"""

import base64
import hashlib
import hmac
import re

from niadra.domain.common.errors import InvalidHandle, InvalidInput, UnsupportedHandleType
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.identity.phones import normalize_phone
from niadra.domain.vocabulary import HandleType, SubjectKind

PHONE_TYPES = frozenset({"phone_e164", "wa_id", "wa_jid"})
EMAIL_TYPES = frozenset({"email"})
SUBJECT_HMAC = "coordination_subject"
"""The keyed hash coordination names a subject by, over `subject_canonical`: the state store's keys, the
durable record's rows, and what an erasure finds them by."""
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def canonical_destination(handle_type: str, value: str) -> str:
    if handle_type in PHONE_TYPES:
        return "phone:" + _phone(handle_type, value)
    if handle_type in EMAIL_TYPES:
        address = value.strip().lower()
        if not _EMAIL.match(address):
            raise InvalidHandle("not an e-mail address")
        return "email:" + address
    raise UnsupportedHandleType("only phone numbers and e-mail addresses are contact destinations")


def suppression_key(salt: bytes, canonical: str) -> str:
    """The reader's key of one destination: base64url of HMAC-SHA256 under its salt, 43 characters."""
    digest = hmac.new(salt, canonical.encode(), hashlib.sha256).digest()
    return b64url(digest)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _phone(handle_type: str, value: str) -> str:
    text = value.strip()
    if handle_type == "wa_jid":
        text = text.partition("@")[0].partition(":")[0]
    if handle_type != "phone_e164" and not text.startswith("+"):
        text = "+" + text  # a WhatsApp id is an international number without its plus sign
    try:
        return normalize_phone(text)
    except InvalidInput:
        raise InvalidHandle("not a phone number") from None


def subject_canonical(
    handle_type: HandleType, value: str, scope: str | None = None, subject_kind: SubjectKind | None = None
) -> tuple[str, str]:
    """A subject's canonical form and its kind: a destination for a phone number or an e-mail address, so a
    number reached through WhatsApp is the same subject, and the normalized handle otherwise."""
    if handle_type.value in PHONE_TYPES | EMAIL_TYPES:
        canonical = canonical_destination(handle_type.value, value)
        return canonical, canonical.partition(":")[0]
    normalized = normalize_handle(handle_type, value, scope, subject_kind)
    key = normalized.key
    return f"{key.type.value}:{key.scope}:{key.value}", key.type.value
