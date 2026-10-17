"""The control plane's Ed25519 signer: snapshots, the revocation feed and person JWTs (4.7, 5.22).

The private key comes from a PEM file or an environment variable. `generate()` exists for tests and
local development only; production refuses to start without a key instead of minting one silently.
"""

import base64
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from niadra.domain.common.errors import Unauthenticated

ALGORITHM = "EdDSA"


class Ed25519Signer:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private = private_key
        self._public = {_kid(private_key.public_key()): private_key.public_key()}
        self._kid = _kid(private_key.public_key())

    @classmethod
    def from_pem(cls, pem: bytes) -> "Ed25519Signer":
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("the signing key must be Ed25519")
        return cls(key)

    @classmethod
    def from_file(cls, path: str | Path) -> "Ed25519Signer":
        return cls.from_pem(Path(path).read_bytes())

    @classmethod
    def from_env(
        cls, file_var: str = "NIADRA_CONTROL_SIGNING_KEY_FILE", pem_var: str = "NIADRA_CONTROL_SIGNING_KEY"
    ) -> "Ed25519Signer":
        if path := os.environ.get(file_var):
            return cls.from_file(path)
        if pem := os.environ.get(pem_var):
            return cls.from_pem(pem.encode())
        raise RuntimeError(f"no signing key: set {file_var} or {pem_var}")

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def algorithm(self) -> str:
        return ALGORITHM

    def sign(self, payload: bytes) -> bytes:
        return self._private.sign(payload)

    def public_jwks(self) -> list[dict[str, str]]:
        ordered = [self._kid, *(k for k in self._public if k != self._kid)]
        return [_jwk(kid, self._public[kid]) for kid in ordered]

    def encode_jwt(self, claims: Mapping[str, Any]) -> str:
        return jwt.encode(dict(claims), self._private, algorithm=ALGORITHM, headers={"kid": self._kid})

    def decode_jwt(self, token: str, audience: str, issuer: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._public.get(kid) if isinstance(kid, str) else None
            if key is None:
                raise Unauthenticated("unknown signing key")
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=audience,
                issuer=issuer,
                # The caller checks expiry against the Clock port, so tests and replicas agree on "now".
                options={
                    "require": ["exp", "iat", "sub", "aud", "iss"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError:
            raise Unauthenticated("invalid token") from None
        return claims


def _raw(public: Ed25519PublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _kid(public: Ed25519PublicKey) -> str:
    """RFC 7638 thumbprint: the same key always gets the same id, on every replica."""
    members = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": _b64(_raw(public))}, separators=(",", ":"))
    return _b64(hashlib.sha256(members.encode()).digest())


def _jwk(kid: str, public: Ed25519PublicKey) -> dict[str, str]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64(_raw(public)),
        "kid": kid,
        "alg": ALGORITHM,
        "use": "sig",
    }
