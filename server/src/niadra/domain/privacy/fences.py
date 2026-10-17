"""The one rule every erasure fence applies (5.5; 24.5.5): what a record says of a target erased at or after
the record was made never reaches the durable record again; what was made later is new activity, and stays."""

from collections.abc import Mapping
from datetime import datetime


def erased_before(made_at: datetime, mark: bytes | None, fenced: Mapping[bytes, datetime]) -> bool:
    """Whether a record made at `made_at` about `mark` predates the erasure that fenced the mark."""
    at = fenced.get(mark) if mark is not None else None
    return at is not None and made_at <= at
