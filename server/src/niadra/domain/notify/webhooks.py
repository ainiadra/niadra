"""Standard Webhooks signing, body shape and the retry schedule (7.5).

Signature: `v1,` + base64(HMAC-SHA256(key, "{webhook-id}.{webhook-timestamp}.{body}")), one entry
per active secret separated by spaces, so a consumer mid-rotation accepts either secret.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

import orjson

from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.handles import classify
from niadra.domain.privacy.erasure import ErasureScope, TargetKind, origin_step

SECRET_PREFIX = "whsec_"  # noqa: S105 - the format prefix, not a secret
RETRY_HORIZON = timedelta(hours=24)
DEAD_RETENTION = timedelta(days=7)  # 22.5
DISABLE_AFTER = timedelta(hours=24)
_FIRST_DELAY = timedelta(seconds=30)
_MAX_DELAY = timedelta(hours=4)

# Keys that would carry conversation content; the body carries ids, rules, versions and pointers only.
_CONTENT_KEYS = frozenset(
    {"text", "content", "summary", "transcript", "message", "description", "value", "excerpt"}
)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILING = "failing"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class WebhookSubject:
    pseudonym: str
    kind: str
    system_id: str | None = None
    """The customer id in the tenant's own system; never a phone, e-mail or document (7.5)."""

    def to_json(self) -> dict[str, str]:
        body = {"pseudonym": self.pseudonym, "kind": self.kind}
        if self.system_id:
            body["system_id"] = self.system_id
        return body


def secret_key(secret: str) -> bytes:
    """Standard Webhooks secrets are `whsec_` + base64; anything else is used as raw bytes."""
    if secret.startswith(SECRET_PREFIX):
        return base64.b64decode(secret.removeprefix(SECRET_PREFIX))
    return secret.encode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(secret_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def signature_header(secrets: Sequence[str], msg_id: str, timestamp: int, body: bytes) -> str:
    return " ".join(sign(s, msg_id, timestamp, body) for s in secrets)


def encode_body(body: Mapping[str, Any]) -> bytes:
    """Canonical bytes: the same stored body always signs and hashes the same way."""
    return orjson.dumps(body, option=orjson.OPT_SORT_KEYS)


def body_hash(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def build_body(
    *,
    delivery_id: UUID,
    event_type: str,
    created_at: datetime,
    tenant_id: UUID,
    subject: WebhookSubject | None,
    data: Mapping[str, Any],
    links: Mapping[str, str] | None,
) -> dict[str, Any]:
    assert_ids_only(data)
    return {
        "id": str(delivery_id),
        "type": event_type,
        "created_at": created_at.isoformat(),
        "tenant": str(tenant_id),
        "subject": subject.to_json() if subject else None,
        "data": dict(data),
        "links": dict(links or {}),
    }


def external_reference(value: str | None) -> str | None:
    """A reference of the tenant's own system (a conversation id) goes out only when it is opaque: one
    that reads as a phone number, an e-mail or a WhatsApp id stays home (7.5)."""
    if not value or classify(value) is not None:
        return None
    return value


def assert_ids_only(data: Mapping[str, Any]) -> None:
    """A guard, not a proof: rejects the keys that conversation content would travel under (7.5)."""
    for key, value in data.items():
        if key in _CONTENT_KEYS:
            raise InvalidInput(f"webhook data cannot carry `{key}`")
        if isinstance(value, Mapping):
            assert_ids_only(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    assert_ids_only(item)


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff: 30 s, 1 min, 2 min, ... capped at 4 h."""
    delay: timedelta = _FIRST_DELAY * (2 ** max(attempts - 1, 0))
    return min(delay, _MAX_DELAY)


def next_attempt_at(created_at: datetime, attempts: int, now: datetime) -> datetime | None:
    """When to try again after `attempts` failures, or None once the 24 h horizon is spent (7.5)."""
    deadline = created_at + RETRY_HORIZON
    if now >= deadline:
        return None
    return min(now + retry_delay(attempts), deadline)


def is_success(status: int) -> bool:
    return 200 <= status < 300


def endpoint_disabled(
    last_success_at: datetime | None, first_failure_at: datetime | None, now: datetime
) -> bool:
    """An endpoint failing without a single success for 24 h stops receiving deliveries (7.5)."""
    if first_failure_at is None:
        return False
    if last_success_at is not None and last_success_at >= first_failure_at:
        return False
    return now - first_failure_at >= DISABLE_AFTER


@dataclass(frozen=True, slots=True)
class Delivery:
    delivery_id: UUID
    endpoint_id: str
    event_type: str
    body: dict[str, Any]
    body_hash: bytes
    status: DeliveryStatus
    created_at: datetime
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_status_code: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EndpointHealth:
    last_success_at: datetime | None
    first_failure_at: datetime | None
    """The oldest failed attempt after the last success."""


def erasure_needles(scope: ErasureScope) -> tuple[str, ...]:
    """Identifiers of the target as they appear in stored bodies. Bodies carry ids only (7.5), so a
    delivery belongs to the target when it names the target's pseudonym or one of its handles. A
    conversation's erasure leaves the person's deliveries alone."""
    if scope.kind is TargetKind.CONVERSATION or not origin_step(scope):
        return ()
    handles = tuple(str(h) for h in scope.handle_ids)
    return (scope.pseudonym, *handles) if scope.kind is TargetKind.PROFILE and scope.pseudonym else handles


def erased_by(body: Mapping[str, Any], needles: Sequence[str]) -> bool:
    text = encode_body(body).decode()
    return any(needle in text for needle in needles)
