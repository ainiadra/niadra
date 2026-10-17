"""Person tokens: short-lived EdDSA JWTs issued by the control plane, verified offline (5.1).

Expiry is checked against the injected clock rather than the library's wall clock, so tests and
replays are deterministic.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niadra.domain.common.errors import Unauthenticated

AUDIENCE = "niadra-cell"
LEEWAY = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class PersonClaims:
    user_id: UUID
    space_id: UUID
    roles: frozenset[str]
    sources: frozenset[UUID] | None
    expires_at: datetime
    label: str | None = None
    """The person's e-mail, when the token carries it: the name their receipts show."""


def verify_person_token(token: str, jwks: Mapping[str, bytes], now: datetime) -> PersonClaims:
    try:
        header = jwt.get_unverified_header(token)
        public = jwks.get(str(header.get("kid")))
        if header.get("alg") != "EdDSA" or public is None:
            raise Unauthenticated("unknown signing key")
        claims: dict[str, Any] = jwt.decode(
            token,
            Ed25519PublicKey.from_public_bytes(public),
            algorithms=["EdDSA"],
            audience=AUDIENCE,
            options={"require": ["exp", "sub", "aud"], "verify_exp": False},
        )
        expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
        sources = claims.get("sources")
        parsed = PersonClaims(
            user_id=UUID(str(claims["sub"])),
            space_id=UUID(str(claims["space"])),
            roles=frozenset(str(r) for r in claims.get("roles") or ()),
            sources=None if sources is None else frozenset(UUID(str(s)) for s in sources),
            expires_at=expires_at,
            label=str(claims["email"]) if claims.get("email") else None,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise Unauthenticated("invalid person token") from exc
    if now > expires_at + LEEWAY:
        raise Unauthenticated("expired person token")
    return parsed
