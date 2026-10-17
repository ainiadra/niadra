"""Legal holds (24.5.1; LGPD, art. 16): what a legal obligation keeps from purging while the hold is active.

A hold names one handle, one object or one conversation, the last by the keyed hash turn records use. The
retention sweep skips what is held, and an erasure over it takes it out of every read at once and leaves its
physical removal for the release; the erasure's receipt says what stayed and why.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LegalHoldRecord:
    hold_id: UUID
    scope: str
    created_at: datetime
    created_by: str
    reason_ct: bytes
    dek_version: int
    handle_id: UUID | None = None
    object_id: UUID | None = None
    conversation_hmac: bytes | None = None
    status: str = "active"
    released_at: datetime | None = None
    released_by: str | None = None
    release_reason_ct: bytes | None = None
    """Sealed with its own key version in front (`pack_envelope`): the row's `dek_version` is the reason's."""


@dataclass(frozen=True, slots=True)
class Held:
    """The active holds of a space, by what they hold: each to the ids of the holds on it."""

    conversations: Mapping[bytes, tuple[UUID, ...]] = field(default_factory=dict)
    """By keyed hash: the conversations held themselves and those of held handles."""
    handles: Mapping[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    objects: Mapping[UUID, tuple[UUID, ...]] = field(default_factory=dict)

    def holds_on(self, conversations: tuple[bytes, ...]) -> list[UUID]:
        return sorted({h for c in conversations for h in self.conversations.get(c, ())}, key=str)


def hold_id_for(space_id: UUID, idempotency_key: str) -> UUID:
    """Derived from the Idempotency-Key, so a retried request lands on the same hold (7.0)."""
    digest = bytearray(hashlib.sha256(f"legal_hold:{space_id}:{idempotency_key}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # RFC 9562 version 8: custom, hash-derived
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


def reason_aad(hold_id: UUID, column: str) -> bytes:
    return f"legal_holds:{hold_id}:{column}".encode()
