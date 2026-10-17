"""Projections the receipt writer keeps for the read path (5.11): pins and agent watermarks."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

PIN_TTL = timedelta(hours=24)
"""How long a conversation keeps the bytes it was first served (5.11)."""


@dataclass(frozen=True, slots=True)
class ConversationPin:
    conversation_id: str
    view: str
    pack_key: str
    pack_hash: str
    generation: int
    pinned_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AgentWatermark:
    """What a source has seen of a profile in one conversation or task (`thread`, "" outside one), the
    base of its deltas (2.4). It moves only to a pack of the same or a later generation, so a read of
    the older pack a conversation is pinned to never takes it back."""

    source_id: UUID
    profile_id: UUID
    manifest_hash: str
    seen_at: datetime
    thread: str = ""
    generation: int = 0


def encode_mark(generation: int, manifest_hash: str) -> bytes:
    """The cached form of a watermark: the generation travels with it so a write can compare."""
    return json.dumps({"g": generation, "m": manifest_hash}, separators=(",", ":")).encode()


def decode_mark(raw: bytes) -> tuple[int, str]:
    doc = json.loads(raw)
    return int(doc["g"]), str(doc["m"])
