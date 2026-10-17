"""Names of the derived state the read path keeps in the Redis-compatible cache (06, 3).

The hash tag `{...}` holds the profile, never the space, so one large tenant spreads over the
cluster, and a pack shares its slot with the profile's generation for the atomic write (5.11).
The cache epoch sits outside the tag: a failover bumps it and every old key goes invisible.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class CacheEpoch:
    """The current cache epoch; the read process refreshes it from the cell state every 5 s."""

    value: int = 0


def t0_key(epoch: int, space_id: UUID, profile_id: UUID, variant: str) -> str:
    return f"t0:{epoch}:{space_id}:{{{profile_id}}}:{variant}"


def gen_key(epoch: int, space_id: UUID, profile_id: UUID) -> str:
    return f"gen:{epoch}:{space_id}:{{{profile_id}}}"


def pin_key(
    epoch: int, space_id: UUID, profile_id: UUID, conversation: str, view: str, source_id: UUID
) -> str:
    """Per source: two agents in the same conversation and view each keep their own bytes."""
    return f"pin:{epoch}:{space_id}:{{{profile_id}}}:{conversation}:{view}:{source_id}"


def watermark_key(epoch: int, space_id: UUID, profile_id: UUID, source_id: UUID, thread: str = "") -> str:
    """Per conversation or task: two conversations with the same customer must not share a delta base."""
    return f"wm:{epoch}:{space_id}:{{{profile_id}}}:{source_id}:{thread}"


def latest_mark_key(epoch: int, space_id: UUID, profile_id: UUID, source_id: UUID) -> str:
    """The source's latest watermark over all its conversations: the delta base of a new one."""
    return f"wml:{epoch}:{space_id}:{{{profile_id}}}:{source_id}"


def wanted_key(epoch: int, space_id: UUID, profile_id: UUID) -> str:
    """Variants the read path had to compile on demand; the next compilation makes them eager."""
    return f"want:{epoch}:{space_id}:{{{profile_id}}}"


def account_key(epoch: int, space_id: UUID, profile_id: UUID) -> str:
    """The throttle of a person's `about` packs asked to compile again by a read that found them behind."""
    return f"acct:{epoch}:{space_id}:{{{profile_id}}}"


def seed_key(epoch: int, space_id: UUID, profile_id: UUID) -> str:
    return f"seed:{epoch}:{space_id}:{{{profile_id}}}"
