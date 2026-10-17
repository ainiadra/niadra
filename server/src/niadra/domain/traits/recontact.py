"""The cross-channel recontact pattern (13, 6.4): the channel a customer comes back on after a contact.

Measurement already marks a recontact, a customer-initiated contact within 72 hours of the end of another
(19.7). This rule reads the customer's own history for the channel of those returns: each contact whose
next contact started on another channel within the window is one return to that channel; the channel
with at least `min_returns` returns in the lookback, the most frequent, the most recent on a tie, is the
customer's recontact channel. A customer who wrote on WhatsApp and called within three days, twice in
six months, has `voice`.

Only channels and start times are read, never a text, so the rule runs on a pack's episode index as well
as on the episodes themselves. Same history, same answer; nothing here reads a clock.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Protocol

RECONTACT_WINDOW = timedelta(hours=72)
LOOKBACK = timedelta(days=180)
MIN_RETURNS = 2


class Contact(Protocol):
    @property
    def channel(self) -> str: ...

    @property
    def started_at(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class RecontactPattern:
    channel: str
    """Where the customer comes back: `voice`, `whatsapp`..."""
    returns: int
    """Returns to that channel in the lookback."""
    last_at: datetime
    """When the latest of them started."""


def recontact_pattern(
    contacts: Iterable[Contact],
    now: datetime,
    *,
    window: timedelta = RECONTACT_WINDOW,
    lookback: timedelta = LOOKBACK,
    min_returns: int = MIN_RETURNS,
) -> RecontactPattern | None:
    """The customer's recontact channel as of `now`, or None without a pattern."""
    since = now - lookback
    ordered = sorted(
        ((c.started_at, c.channel) for c in contacts if since <= c.started_at <= now and c.channel),
        key=lambda pair: pair,
    )
    returns: dict[str, list[datetime]] = {}
    for (at, channel), (next_at, next_channel) in pairwise(ordered):
        if next_channel != channel and next_at - at <= window:
            returns.setdefault(next_channel, []).append(next_at)
    if not returns:
        return None
    channel, times = max(returns.items(), key=lambda kv: (len(kv[1]), max(kv[1]), kv[0]))
    if len(times) < min_returns:
        return None
    return RecontactPattern(channel, len(times), max(times))
