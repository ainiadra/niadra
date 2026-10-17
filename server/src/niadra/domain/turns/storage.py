"""Where a turn record lives in object storage, and which turns the kept tier takes (24.3.5).

Every turn is written to the short tier, `turns/short/<space>/<conversation>/<turn_id>.bin`. No bucket
rule expires a turn: a recording is kept until an erasure or the retention the customer sets
(`recording.kept_days`), which the retention sweep applies by the object's age. `<conversation>` is the
space's keyed hash of the conversation id (the task id for a turn without one), never the id itself, so a
person's turns are erased by prefix and no key names them. A turn the kept tier takes is copied under
`turns/kept/`, where it stays until its `kept_until` (the index row in `turns_kept` says when; null keeps it
until a retention is set).

The short tier has no database row: `turns/short/<space>/by-id/<turn_id>` holds the conversation's hash,
so a turn is found by its id alone, and its conditional write is how the same turn sent twice stays one.
"""

import hashlib
from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

HMAC_PURPOSE = "turn_conversation"
RECORD_AAD = "turns:{turn_id}:record"
BY_ID = "by-id"


class Tier(StrEnum):
    SHORT = "short"
    KEPT = "kept"
    # A scenario's turns, deleted by the retention sweep when their row goes, and a held conversation's,
    # deleted when the hold is released.
    SCENARIO = "scenario"
    HELD = "held"


class KeptBy(StrEnum):
    FLAG = "flag"
    REQUEST = "request"
    SAMPLE = "sample"
    SCENARIO = "scenario"


# The capture flags that keep a turn (Turn Record 9.2); `truncated` and `guard_budget_exceeded` do not.
KEEP_FLAGS = frozenset(
    {"error", "guard_acted", "handoff", "assertion_failed", "synthetic", "incomplete", "negative_feedback"}
)


def turn_key(tier: Tier, space_id: UUID, conversation: bytes, turn_id: str) -> str:
    return f"turns/{tier.value}/{space_id}/{conversation.hex()}/{turn_id}.bin"


def part_key(space_id: UUID, conversation: bytes, turn_id: str, digest: str) -> str:
    """A later export's part of a turn built from spans, beside the turn until a worker merges it: `+` is no
    turn id's character and `.part` no record's suffix, so listings of turns never take it for one."""
    return f"turns/short/{space_id}/{conversation.hex()}/{turn_id}+{digest}.part"


def by_id_key(space_id: UUID, turn_id: str) -> str:
    return f"turns/short/{space_id}/{BY_ID}/{turn_id}"


def conversation_prefix(tier: Tier, space_id: UUID, conversation: bytes) -> str:
    return f"turns/{tier.value}/{space_id}/{conversation.hex()}/"


def conversation_prefixes(space_id: UUID, conversation: bytes) -> tuple[str, ...]:
    """What an erasure deletes of one conversation's turns: every copy but a legal hold's, which waits for
    the hold's release (24.5)."""
    return tuple(
        conversation_prefix(t, space_id, conversation) for t in (Tier.SHORT, Tier.KEPT, Tier.SCENARIO)
    )


def short_turn_ids(space_id: UUID, conversation: bytes, keys: Iterable[str]) -> list[str]:
    """The turn ids of the short tier's keys of one conversation, as listed under its prefix."""
    prefix = conversation_prefix(Tier.SHORT, space_id, conversation)
    return [k[len(prefix) : -len(".bin")] for k in keys if k.startswith(prefix) and k.endswith(".bin")]


def turn_at(key: str) -> tuple[bytes, str] | None:
    """The conversation and turn id of a turn's key in any tier; None for another object (a part, a by-id
    entry, a day's index)."""
    parts = key.split("/")
    if len(parts) != 5 or parts[0] != "turns" or not parts[4].endswith(".bin"):
        return None
    try:
        return bytes.fromhex(parts[3]), parts[4][: -len(".bin")]
    except ValueError:
        return None


def record_aad(turn_id: str) -> bytes:
    """The same in both tiers, so keeping a turn copies its sealed bytes as they are."""
    return RECORD_AAD.format(turn_id=turn_id).encode()


def keep_flags(flags: Iterable[str]) -> list[str]:
    return sorted(set(flags) & KEEP_FLAGS)


def sampled(space_id: UUID, conversation: bytes, rate: float) -> bool:
    """The deterministic sample, by conversation: a sampled conversation is kept whole, so it can be replayed
    up to its first divergence, and a retried turn gets the same answer."""
    if rate <= 0:
        return False
    digest = hashlib.sha256(space_id.bytes + conversation).digest()
    return int.from_bytes(digest[:8], "big") < rate * 2**64


def kept_until_after(days: int | None) -> timedelta | None:
    """How long a turn the kept tier takes stays: the customer's retention, or None, kept until one is set
    (memory never cuts what it was given)."""
    return timedelta(days=days) if days is not None else None
