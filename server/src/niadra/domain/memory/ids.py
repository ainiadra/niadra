"""Deterministic ids for rows that a replayed task must not duplicate (L11).

The random part is a hash of the natural key, so the same episode or item always gets the same id and
`ON CONFLICT DO NOTHING` does the dedupe.
"""

import hashlib
from datetime import datetime
from uuid import UUID


def stable_uuid7(at: datetime, *parts: str) -> UUID:
    """A UUIDv7 whose time part is the moment the row is about, so ids still sort by time."""
    ms = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    digest = int.from_bytes(hashlib.blake2b("\x1f".join(parts).encode(), digest_size=10).digest(), "big")
    rand_a, rand_b = digest >> 68, digest & ((1 << 62) - 1)
    return UUID(int=(ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b)


def stable_uuid(*parts: str) -> UUID:
    """A UUIDv8 (RFC 9562) from the natural key alone, for a row whose key holds no moment of its own: the
    same key said at any time is the same row."""
    digest = int.from_bytes(hashlib.blake2b("\x1f".join(parts).encode(), digest_size=16).digest(), "big")
    value = (digest & ~(0xF << 76) & ~(0b11 << 62)) | (0x8 << 76) | (0b10 << 62)
    return UUID(int=value)
