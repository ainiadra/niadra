"""Source keys as the control issues them, Console passwords, TOTP and recovery codes: formats and
hashes, no storage.

A source key reads `nia_sk_<live|test>_<region>_<space_slug>_<key_id>_<secret>` (7.0). The edge owns
parsing and the two key hashes (`niadra.domain.edge.keys`, 5.1); the control mints keys that satisfy it
and stores the same two hashes, so there is one definition of the format and of the hash inputs.
"""

import base64
import hashlib
import hmac
import re
import secrets
import string
import struct
from collections.abc import Sequence
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.edge.keys import parse_source_key
from niadra.domain.vocabulary import Environment

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
_SECRET_ALPHABET = string.ascii_letters + string.digits
_KEY_ID_ALPHABET = string.ascii_lowercase + string.digits
_ENV_TAG = {Environment.PRODUCTION: "live", Environment.SANDBOX: "test"}

# scrypt cost: about 50 ms and 16 MiB per check on a server core.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

RECOVERY_CODES = 10
# Crockford's base32 leaves out I, L, O and U, so no two symbols look alike: ten symbols are 50 bits.
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# What people type for the letters the alphabet leaves out.
_LOOKALIKES = str.maketrans("OIL", "011")

# The password hash of a person who has not chosen a password: invited and not there yet, or created by the
# tenant's SSO. `check_password` never matches it.
NO_PASSWORD = "!"  # noqa: S105 - a marker that no scrypt hash can equal, not a secret


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def new_key_id() -> str:
    return "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(16))


def new_secret() -> str:
    return "".join(secrets.choice(_SECRET_ALPHABET) for _ in range(40))


def format_key(environment: Environment, region: str, space_slug: str, key_id: str, secret: str) -> str:
    if not SLUG_RE.match(space_slug):
        raise InvalidInput("invalid space slug")
    key = f"nia_sk_{_ENV_TAG[environment]}_{region}_{space_slug}_{key_id}_{secret}"
    parse_source_key(key)  # a key the edge cannot parse must never be handed out
    return key


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def has_password(encoded: str) -> bool:
    return encoded.startswith("scrypt$")


def check_password(password: str, encoded: str) -> bool:
    try:
        name, n, r, p, salt, digest = encoded.split("$")
        if name != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, at_epoch_s: float, step_s: int = 30, digits: int = 6) -> str:
    """RFC 6238 with HMAC-SHA1, the variant every authenticator app implements."""
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", int(at_epoch_s // step_s))
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


TOTP_STEP_S = 30


def totp_step(secret_b32: str, code: str, at_epoch_s: float, drift_steps: int = 1) -> int | None:
    """The time step whose code is `code`, within `drift_steps` of now either way, or None. Every step is
    compared, so the time taken says nothing about which one matched. The caller keeps the last step it
    accepted for the person and refuses any step not after it: a code works once (RFC 6238, 5.2)."""
    now = int(at_epoch_s // TOTP_STEP_S)
    matched = [
        step
        for step in range(now - drift_steps, now + drift_steps + 1)
        if hmac.compare_digest(totp_code(secret_b32, step * TOTP_STEP_S), code)
    ]
    return max(matched) if matched else None


def totp_uri(secret_b32: str, account: str, issuer: str = "Niadra") -> str:
    return f"otpauth://totp/{issuer}:{account}?secret={secret_b32}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"


def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """Single-use codes for a lost authenticator, `XXXXX-XXXXX`, all different."""
    codes: list[str] = []
    while len(codes) < count:
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        if (code := f"{raw[:5]}-{raw[5:]}") not in codes:
            codes.append(code)
    return codes


def normalize_recovery_code(typed: str) -> str | None:
    """The code as it was issued, from what a person typed: any case, spaces or dashes anywhere, O for
    zero, I or L for one. None when it cannot be a recovery code."""
    raw = re.sub(r"[\s-]", "", typed).upper().translate(_LOOKALIKES)
    if len(raw) != 10 or any(c not in RECOVERY_ALPHABET for c in raw):
        return None
    return f"{raw[:5]}-{raw[5:]}"


def recovery_code_hash(pepper: bytes, user_id: UUID, code: str) -> bytes:
    # Keyed like a source key's fast hash, so the table is useless without the pepper; the person's id
    # in the input ties each code to its account.
    return hmac.new(pepper, f"recovery:{user_id}:{code}".encode(), hashlib.sha256).digest()


def new_url_token() -> str:
    """256 random bits, URL-safe: invitation tokens, SSO state, nonce, PKCE verifier and sign-in codes."""
    return secrets.token_urlsafe(32)


def invitation_token_hash(pepper: bytes, token: str) -> bytes:
    # Keyed, like the recovery codes: a copy of the table cannot be turned into working links.
    return hmac.new(pepper, f"invitation:{token}".encode(), hashlib.sha256).digest()


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: base64url of the SHA-256 of the verifier, without padding."""
    return _b64(sha256(verifier))


def matching_hash(candidate: bytes, stored: Sequence[bytes]) -> bytes | None:
    """The stored hash equal to `candidate`. Every one is compared in constant time, with no early exit."""
    found = None
    for digest in stored:
        if hmac.compare_digest(candidate, digest):
            found = digest
    return found


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
