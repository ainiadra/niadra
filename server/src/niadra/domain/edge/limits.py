"""Rate windows and request idempotency (5.1, 7.0)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from niadra.domain.common.errors import Conflict

IDEMPOTENCY_TTL = timedelta(hours=24)
_WRITE_PREFIXES = ("/v1/batch", "/v1/ingest", "/v1/otel", "/v1/media", "/v1/feedback")


def route_group(path: str) -> str:
    """Writes and reads are counted apart, so a flood of writes never cuts reads first (5.1)."""
    return "write" if path.startswith(_WRITE_PREFIXES) else "read"


def rate_window_key(key_id: str, path: str, now: datetime) -> str:
    return f"rate:{key_id}:{route_group(path)}:{int(now.timestamp())}"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    idem_key: str
    body_hash: bytes
    status: int
    response: dict[str, Any]
    created_at: datetime


def replayable(record: IdempotencyRecord | None, body_hash: bytes, now: datetime) -> bool:
    """True when the stored answer must be replayed; the same key with another body is a 409 (7.0)."""
    if record is None or now - record.created_at >= IDEMPOTENCY_TTL:
        return False
    if record.body_hash != body_hash:
        raise Conflict("the Idempotency-Key was used with another body")
    return True
