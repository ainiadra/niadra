"""Time-ordered identifiers.

Every id we mint is a UUIDv7 (RFC 9562): sortable by creation time, which keeps
B-tree inserts append-mostly and lets `events` and `receipts` partition by id range.
"""

import os
import time
import uuid

_last_ms = 0
_counter = 0


def uuid7(at_ms: int | None = None) -> uuid.UUID:
    """UUIDv7 with a 12-bit monotonic counter inside the same millisecond."""
    global _last_ms, _counter
    ms = int(time.time() * 1000) if at_ms is None else at_ms
    if ms == _last_ms:
        _counter = (_counter + 1) & 0xFFF
        if _counter == 0:  # counter wrapped: borrow the next millisecond
            ms += 1
    else:
        _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF
    _last_ms = ms
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= _counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def uuid7_ms(value: uuid.UUID) -> int:
    """Creation time, in epoch milliseconds, of a UUIDv7."""
    return value.int >> 80


def uuid7_floor(at_ms: int) -> uuid.UUID:
    """Smallest UUIDv7 for a millisecond; used as a partition or range bound."""
    return uuid.UUID(int=((at_ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (0b10 << 62))
