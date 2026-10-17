"""Signed configuration from the control plane (4.7).

The manifest is signed with Ed25519 over its canonical JSON (sorted keys, no whitespace,
UTF-8), so the bytes on the wire may be re-encoded without breaking the signature. Each
document is addressed by the sha256 of its own canonical JSON.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from niadra.domain.common.errors import InvalidInput


@dataclass(frozen=True, slots=True)
class SignedSnapshot:
    manifest: bytes
    signature: bytes
    documents: dict[str, bytes]
    """sha256 hex -> the document's JSON bytes."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(document: Any) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def parse_json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput(f"{what} is not valid JSON") from None


def verify_signed(body: bytes, signature: bytes, keys: list[Ed25519PublicKey]) -> Any:
    """Parses `body` and checks the signature over its canonical form against any trusted key.

    More than one key is accepted so the control plane can rotate its signing key.
    """
    value = parse_json(body, "the signed body")
    payload = canonical_json(value)
    for key in keys:
        try:
            key.verify(signature, payload)
        except InvalidSignature:
            continue
        return value
    raise InvalidInput("bad signature")


def sign(value: Any, key: Ed25519PrivateKey) -> bytes:
    """What the control plane does; here for tests."""
    return key.sign(canonical_json(value))


def version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        raise InvalidInput("bad version string") from None
