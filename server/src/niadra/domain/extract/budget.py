"""A share of a space's daily LLM budget held for one call (5.9)."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SpendHold:
    """Taken before the call and counted as spent at once, so parallel calls see each other; settled to
    the real cost after the call, or given back when it fails. `micros` is 0 for a purpose without a cap."""

    space_id: UUID
    purpose: str
    day: date
    micros: int
