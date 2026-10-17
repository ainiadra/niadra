"""The pure parts of the coordination decision (24.3.4, 24.4.2): what a check answers when Niadra cannot take
it (L23), the channel's state at the check, and the quiet hours."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from niadra.domain.coordinate.model import Decision, FailDirection

UNCHECKED = "unchecked"
REBUILD_CLOSED_S = 60
"""After the state store comes back empty, how long the purposes that fail closed wait for the writer to
drain the queue into the durable record the store is reloaded from (24.3.4)."""


class Cause(StrEnum):
    """Why the producer answers a check itself: a dependency is down, or the state store is being reloaded."""

    UNAVAILABLE = "unavailable"
    REBUILDING = "rebuilding"


def fallback(*, inbound: bool, fails: FailDirection, effect_key: bool, cause: Cause) -> tuple[Decision, str]:
    """The declared default (L23; the coordination spec, 10): a message the customer sent is always answered;
    an effect's key fails closed, since what may have gone out is never sent again on a guess; otherwise the
    purpose's own direction."""
    if inbound:
        return Decision.ALLOW, UNCHECKED
    if effect_key or fails is FailDirection.CLOSED:
        return Decision.DEFER, cause.value
    return Decision.ALLOW, UNCHECKED


@dataclass(frozen=True, slots=True)
class ChannelView:
    free_form_until: datetime | None
    template_required: bool
    quiet_until: datetime | None


def channel_view(
    *,
    free_form_hours: int | None,
    template_outside_window: bool,
    last_inbound: datetime | None,
    quiet_until: datetime | None,
    now: datetime,
) -> ChannelView:
    """A channel with a service window is free-form until `free_form_hours` after the customer's last message;
    outside it, or when no message is known, a paid template is required where the channel says so."""
    if free_form_hours is None:
        return ChannelView(None, False, quiet_until)
    until = last_inbound + timedelta(hours=free_form_hours) if last_inbound is not None else None
    outside = until is None or until <= now
    return ChannelView(until if not outside else None, template_outside_window and outside, quiet_until)


def quiet_until(start: time, end: time, zone: ZoneInfo, now: datetime) -> datetime | None:
    """The end of the quiet hours `now` falls in, in UTC-aware time, or None outside them. Hours that cross
    midnight (22:00 to 08:00) end on the next local day."""
    local = now.astimezone(zone)
    at = local.time().replace(tzinfo=None)
    if start < end:
        inside = start <= at < end
        day = local.date()
    else:
        inside = at >= start or at < end
        day = local.date() + timedelta(days=1) if at >= start else local.date()
    if not inside:
        return None
    return datetime.combine(day, end, tzinfo=zone)
