"""The contact token (the contact-token spec): `nct1.<payload>.<signature>`, signed with the space's Ed25519
key, which a gateway checks offline before it lets one outbound contact out.

The issuer writes the payload as compact JSON with its members in the spec's order, so a token is the same
bytes in every implementation, and a verifier checks the signature over the segments as received, never over
a payload it decoded and encoded again. `verify` runs the thirteen steps of the spec's section 6 in order and
refuses with the first code that applies.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from niadra.domain.coordinate.destination import b64url

VERSION = "nct1"
MAX_TTL_S = 120
SKEW_S = 5
MAX_LENGTH = 1024

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_KID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RCPT = re.compile(r"^[A-Za-z0-9_-]{43}$")


class TokenRefused(Exception):
    """A gateway refuses the token with `code`, one of the spec's section 6."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The payload, members in the spec's order (section 4)."""

    kid: str
    space: str
    jti: str
    purpose: str
    channel: str
    rcpt: str
    gateway: str
    iat: int
    exp: int


@dataclass(frozen=True, slots=True)
class PublicKey:
    """One key of a space's key set, as `/.well-known/niadra-contact-keys.json` publishes it."""

    kid: str
    x: str
    space: str
    status: str = "active"
    not_after: datetime | None = None


MEMBERS = tuple(TokenClaims.__dataclass_fields__)


def signing_key(seed: bytes) -> Ed25519PrivateKey:
    """The space's key from its 32-byte seed (RFC 8032)."""
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_x(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url(raw)


def key_id(x: str) -> str:
    """The RFC 7638 thumbprint of the public key: the same key always has the same id, on every replica."""
    members = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"))
    return b64url(hashlib.sha256(members.encode()).digest())


def recipient(gateway_key: bytes, canonical: str) -> str:
    """`rcpt`: the destination's canonical form keyed with the key the gateway shares with the producer."""
    return b64url(hmac.new(gateway_key, canonical.encode(), hashlib.sha256).digest())


def issue(claims: TokenClaims, key: Ed25519PrivateKey) -> str:
    if not 0 < claims.exp - claims.iat <= MAX_TTL_S:
        raise ValueError(f"a contact token lives more than 0 and at most {MAX_TTL_S} seconds")
    payload = json.dumps(asdict(claims), separators=(",", ":"), ensure_ascii=False).encode()
    signed = f"{VERSION}.{b64url(payload)}"
    return f"{signed}.{b64url(key.sign(signed.encode()))}"


def verify(
    token: str,
    *,
    keys: Sequence[PublicKey],
    gateway_id: str,
    space: str,
    gateway_key: bytes,
    destination: str,
    channel: str,
    now: int,
    seen: Collection[str] = (),
) -> TokenClaims:
    """The gateway's check before it sends a message of `channel` to `destination` (the canonical form),
    with its clock at `now`, in seconds. `seen` holds the `jti` values it let through and still remembers."""
    segments = token.split(".")
    if len(token) > MAX_LENGTH or len(segments) != 3 or not all(segments):
        raise TokenRefused("malformed")
    version, payload_text, signature_text = segments
    if version != VERSION:
        raise TokenRefused("unsupported_version")
    payload, signature = _decode(payload_text), _decode(signature_text)
    if len(signature) != 64:
        raise TokenRefused("malformed")
    claims = _claims(payload)
    key = next((k for k in keys if k.kid == claims.kid and k.space == claims.space), None)
    if key is None or (key.not_after is not None and now > key.not_after.timestamp()):
        raise TokenRefused("unknown_key")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(key.x)).verify(
            signature, f"{version}.{payload_text}".encode()
        )
    except (InvalidSignature, ValueError, TokenRefused):
        raise TokenRefused("bad_signature") from None
    if claims.space != space:
        raise TokenRefused("wrong_space")
    if claims.gateway != gateway_id:
        raise TokenRefused("wrong_gateway")
    if not 0 < claims.exp - claims.iat <= MAX_TTL_S:
        raise TokenRefused("lifetime_too_long")
    if claims.iat - SKEW_S > now:
        raise TokenRefused("not_yet_valid")
    if now >= claims.exp + SKEW_S:
        raise TokenRefused("expired")
    if claims.channel != channel:
        raise TokenRefused("wrong_channel")
    if not hmac.compare_digest(claims.rcpt, recipient(gateway_key, destination)):
        raise TokenRefused("wrong_recipient")
    if claims.jti in seen:
        raise TokenRefused("replayed")
    return claims


def _decode(text: str) -> bytes:
    if not _B64URL.match(text):
        raise TokenRefused("malformed")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise TokenRefused("malformed") from None


def _claims(payload: bytes) -> TokenClaims:
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        raise TokenRefused("malformed") from None
    if not isinstance(data, dict) or set(data) != set(MEMBERS):
        raise TokenRefused("malformed")
    texts = {name: data[name] for name in MEMBERS if name not in ("iat", "exp")}
    numbers = (data["iat"], data["exp"])
    if not all(isinstance(v, str) for v in texts.values()) or not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in numbers
    ):
        raise TokenRefused("malformed")
    if not (
        _KID.match(texts["kid"])
        and _uuid(texts["space"])
        and _uuid(texts["jti"])
        and all(_NAME.match(texts[name]) for name in ("purpose", "channel", "gateway"))
        and _RCPT.match(texts["rcpt"])
    ):
        raise TokenRefused("malformed")
    return TokenClaims(**data)


def _uuid(text: str) -> bool:
    try:
        return str(UUID(text)) == text
    except ValueError:
        return False
