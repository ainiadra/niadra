"""The `subject_token` that binds an MCP connection or a tool kit to one customer (5.17, 16.3).

The developer's backend mints it with its source key; the model never sees or chooses the
customer, nor the organization the customer acts for (`about`, 21.4) when the backend binds one.
The token is signed with HMAC-SHA256 under a server secret and lives 15 minutes. The handles
inside are personal data, so they travel sealed (AES-GCM), never in clear text.
"""

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from niadra.domain.common.errors import Unauthenticated
from niadra.domain.vocabulary import HandleType, SubjectKind, Verification

TOKEN_TTL = timedelta(minutes=15)
_PREFIX = "nst1"
_NONCE = 12


@dataclass(frozen=True, slots=True)
class SubjectRef:
    type: HandleType
    value: str
    scope: str | None = None
    subject_kind: SubjectKind | None = None


@dataclass(frozen=True, slots=True)
class SubjectClaims:
    space_id: UUID
    source_id: UUID
    subject: SubjectRef
    level: Verification
    expires_at: datetime
    conversation_id: str | None = None
    task_id: str | None = None
    about: SubjectRef | None = None


class SubjectTokens:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("the subject-token secret must have at least 32 bytes")
        self._mac_key = hmac.new(secret, b"subject-token:mac", hashlib.sha256).digest()
        self._seal = AESGCM(hmac.new(secret, b"subject-token:seal", hashlib.sha256).digest())

    def mint(self, claims: SubjectClaims) -> str:
        body = {
            "sp": str(claims.space_id),
            "src": str(claims.source_id),
            "sub": self._sealed(claims.subject, claims.space_id),
            "lvl": claims.level.value,
            "exp": int(claims.expires_at.timestamp()),
            "conv": claims.conversation_id,
            "task": claims.task_id,
        }
        if claims.about is not None:
            body["ab"] = self._sealed(claims.about, claims.space_id)
        payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
        return f"{_PREFIX}.{payload}.{self._sign(payload)}"

    def verify(self, token: str, *, now: datetime) -> SubjectClaims:
        prefix, _, rest = token.partition(".")
        payload, _, signature = rest.partition(".")
        if prefix != _PREFIX or not payload or not hmac.compare_digest(signature, self._sign(payload)):
            raise Unauthenticated("invalid subject token")
        try:
            body = json.loads(_unb64(payload))
            space_id = UUID(body["sp"])
            claims = SubjectClaims(
                space_id=space_id,
                source_id=UUID(body["src"]),
                subject=self._opened(body["sub"], space_id),
                level=Verification(body["lvl"]),
                expires_at=datetime.fromtimestamp(body["exp"], UTC),
                conversation_id=body.get("conv"),
                task_id=body.get("task"),
                about=self._opened(body["ab"], space_id) if body.get("ab") else None,
            )
        except (KeyError, ValueError, TypeError, InvalidTag) as exc:
            raise Unauthenticated("invalid subject token") from exc
        if claims.expires_at <= now:
            raise Unauthenticated("expired subject token")
        return claims

    def _sealed(self, ref: SubjectRef, space_id: UUID) -> str:
        plain = json.dumps(
            [ref.type.value, ref.value, ref.scope, ref.subject_kind.value if ref.subject_kind else None],
            separators=(",", ":"),
        ).encode()
        nonce = os.urandom(_NONCE)
        return _b64(nonce + self._seal.encrypt(nonce, plain, space_id.bytes))

    def _opened(self, sealed_text: str, space_id: UUID) -> SubjectRef:
        sealed = _unb64(sealed_text)
        kind, value, scope, subject_kind = json.loads(
            self._seal.decrypt(sealed[:_NONCE], sealed[_NONCE:], space_id.bytes)
        )
        return SubjectRef(HandleType(kind), value, scope, SubjectKind(subject_kind) if subject_kind else None)

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._mac_key, f"{_PREFIX}.{payload}".encode(), hashlib.sha256).digest())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
