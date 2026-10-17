"""Source keys (5.1, 7.0).

A key reads `nia_sk_<live|test>_<region>_<space>_<key_id>_<secret>`. Region and space only route
the SDK to the right address; `key_id` is public and never authenticates. Two hashes are kept:
a fast keyed hash (HMAC with a cell pepper) that is indexable and serves as the cache key, and a
slow scrypt hash checked whenever the cache misses, so a stolen table without the pepper is useless
and a stolen pepper still leaves scrypt to brute-force.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

from niadra.domain.common.errors import Unauthenticated
from niadra.domain.vocabulary import Environment, Scope

_KEY = re.compile(
    r"^nia_sk_(?P<env>live|test)_(?P<region>[a-z0-9-]{2,32})_(?P<space>[a-z0-9-]{1,64})"
    r"_(?P<key_id>[A-Za-z0-9]{8,64})_(?P<secret>[A-Za-z0-9]{24,128})$"
)
_SCRYPT_LOG_N = 14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass(frozen=True, slots=True)
class ParsedKey:
    environment: Environment
    region: str
    space_slug: str
    key_id: str
    secret: str


@dataclass(frozen=True, slots=True)
class SourceKeyRow:
    """A row of `source_keys`, mirrored from the control plane by the snapshot (4.7)."""

    space_id: UUID
    key_id: str
    source_id: UUID
    fast_hash: bytes
    slow_hash: str
    scopes: frozenset[Scope]
    status: str = "active"
    version: int = 1

    @property
    def revoked(self) -> bool:
        return self.status == "revoked"


def parse_source_key(token: str) -> ParsedKey:
    match = _KEY.match(token)
    if match is None:
        raise Unauthenticated("malformed source key")
    return ParsedKey(
        environment=Environment.PRODUCTION if match["env"] == "live" else Environment.SANDBOX,
        region=match["region"],
        space_slug=match["space"],
        key_id=match["key_id"],
        secret=match["secret"],
    )


def fast_hash(pepper: bytes, key_id: str, secret: str) -> bytes:
    # The separator keeps ("ab", "cd") and ("a", "bcd") apart; neither part may contain "_".
    return hmac.new(pepper, f"{key_id}_{secret}".encode(), hashlib.sha256).digest()


def slow_hash(key_id: str, secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = _scrypt(f"{key_id}_{secret}".encode(), salt, _SCRYPT_LOG_N, _SCRYPT_R, _SCRYPT_P)
    return "$".join(("scrypt", str(_SCRYPT_LOG_N), str(_SCRYPT_R), str(_SCRYPT_P), _b64(salt), _b64(digest)))


def verify_slow_hash(stored: str, key_id: str, secret: str) -> bool:
    """Parameters travel inside the stored string, so they can be raised without a migration."""
    try:
        scheme, log_n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(f"{key_id}_{secret}".encode(), base64.b64decode(salt), int(log_n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(expected, actual)


def _scrypt(material: bytes, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(material, salt=salt, n=1 << log_n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
