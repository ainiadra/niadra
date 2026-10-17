"""Requests to review an automated decision (24.5.3; LGPD, art. 20).

The decisions that can affect a person are Niadra's own: a contact denied or deferred, what a tool is told to
show or leave out, memory held back from an agent. Each left a receipt with its rule, versions and inputs by
reference, so the explanation package is built from receipts alone and never carries personal content. The
controller reviews; Niadra records the resolution. A request is kept under a legal obligation: at least
`KEPT_DAYS` after it was resolved, and while it is open.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

KEPT_DAYS = 395
DECISION_SURFACES = frozenset({"constraints", "coordination", "handoff"})
DECISION_KINDS = frozenset({"holdout", "denied"})
EXPLAINED = 20
"""The receipts an explanation holds when the request names no decision: the subject's latest decisions."""
_EXPLAINED_FIELDS = (
    "kind", "surface", "read_at", "source_id", "conversation_id", "task_id", "policy_version", "verification",
    "experiment_group", "pack_hash", "manifest_hash", "withheld", "item_ids", "detail",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class ReviewRequestRecord:
    request_id: UUID
    profile_id: UUID
    reason_ct: bytes
    dek_version: int
    created_at: datetime
    decision_receipt_id: UUID | None = None
    explanation: Mapping[str, Any] = field(default_factory=dict)
    status: str = "open"
    outcome: str | None = None
    resolution_ct: bytes | None = None
    """Sealed with its own key version in front (`pack_envelope`): the row's `dek_version` is the reason's."""
    resolved_at: datetime | None = None
    resolved_by: str | None = None


def request_id_for(space_id: UUID, idempotency_key: str) -> UUID:
    """Derived from the Idempotency-Key, so a retried request lands on the same row (7.0)."""
    digest = bytearray(hashlib.sha256(f"review_request:{space_id}:{idempotency_key}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # RFC 9562 version 8: custom, hash-derived
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


def is_decision(receipt: Mapping[str, Any]) -> bool:
    return str(receipt.get("kind")) in DECISION_KINDS or str(receipt.get("surface")) in DECISION_SURFACES


def explanation(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The package the reviewer reads: each receipt's id, kind, surface, time, rule and versions, and its
    inputs by reference. Receipts already hold ids and numbers only (5.13), so nothing here is content."""
    out = []
    for receipt in receipts:
        entry = {"receipt_id": str(receipt["receipt_id"])}
        for name in _EXPLAINED_FIELDS:
            value = receipt.get(name)
            if value in (None, (), [], {}):
                continue
            entry[name] = _plain(value)
        out.append(entry)
    return {"receipts": out}


def reason_aad(request_id: UUID, column: str) -> bytes:
    return f"review_requests:{request_id}:{column}".encode()


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if hasattr(value, "value"):
        return value.value
    return value
