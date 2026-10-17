"""A contact window (the suppression-list spec, 6.1): the local hours, in an IANA time zone and on some
weekdays, during which a suppression applies. "Do not call before 9 am" is a window from 00:00 to 09:00 on
the voice channel; outside it the subject may be called. A window that ends before it starts crosses
midnight, and belongs to the day it starts on: a Friday window from 22:00 to 06:00 still applies at 01:00 on
Saturday. The end is open: at exactly 09:00 the window from 00:00 to 09:00 is over.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from niadra.domain.common.errors import InvalidInput

_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_ZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$")
MIDNIGHT = time(0, 0)
CONTACT_WINDOW_TRAIT = "contact_window."
"""A person's contact window kept in their signals, as a trait `contact_window.<channel or any>` whose value
is `Window.encode` (an empty value lifts it)."""
CONTACT_TIME_FACT = "preferred_contact_time"
"""The starter's fact for when a person wants to be reached: the hours they said, in their memory, so the
default block carries them as a contact preference beside the window the coordination holds."""


class InvalidWindow(InvalidInput):
    """A window with a bad clock time, a start equal to its end, a zone that is not an IANA name, or a day
    that is not an ISO weekday."""


@dataclass(frozen=True, slots=True)
class Window:
    start: time
    end: time
    tz: str
    days: tuple[int, ...] = ()
    """ISO weekdays (1 is Monday) the window starts on; empty for every day."""

    @classmethod
    def parse(cls, start: str, end: str, tz: str, days: Iterable[int] | None = None) -> "Window":
        found = sorted(set(days or ()))
        if any(not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 7 for d in found):
            raise InvalidWindow("days are ISO weekdays, 1 (Monday) to 7 (Sunday)")
        if not _ZONE.match(tz):
            raise InvalidWindow(f"{tz} is not an IANA time zone")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise InvalidWindow(f"{tz} is not an IANA time zone") from None
        window = cls(_clock(start), _clock(end), tz, tuple(found))
        if window.start == window.end:
            raise InvalidWindow("a window starts before it ends")
        return window

    @classmethod
    def said(
        cls, not_before: str | None, not_after: str | None, tz: str, days: Iterable[int] | None = None
    ) -> "Window":
        """The window a person's hours give: "not before 09:00" forbids 00:00 to 09:00, "not after 20:00"
        forbids 20:00 to midnight, and both forbid the night between (20:00 to 09:00)."""
        if not_before is None and not_after is None:
            raise InvalidWindow("a window needs a time not to contact before or after")
        start = not_after if not_after is not None else "00:00"
        end = not_before if not_before is not None else "00:00"
        if not_before is not None and not_after is not None and _clock(not_before) >= _clock(not_after):
            raise InvalidWindow("the hours to contact in start before they end")
        return cls.parse(start, end, tz, days)

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    def to_json(self) -> dict[str, Any]:
        return {
            "from": self.start.strftime("%H:%M"),
            "to": self.end.strftime("%H:%M"),
            "tz": self.tz,
            "days": list(self.days) or None,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Window":
        return cls.parse(str(data["from"]), str(data["to"]), str(data["tz"]), data.get("days"))

    def encode(self) -> str:
        """The window in the state store's value of an entry, after its `until` and its id."""
        days = ",".join(map(str, self.days))
        return f"{self.start:%H:%M};{self.end:%H:%M};{days};{self.tz}"

    @classmethod
    def decode(cls, text: str) -> "Window":
        start, end, days, tz = text.split(";", 3)
        return cls.parse(start, end, tz, [int(d) for d in days.split(",") if d])


def window_until(window: Window, at: datetime) -> datetime | None:
    """When the window `at` falls in ends, in UTC, or None when `at` is outside it."""
    zone = ZoneInfo(window.tz)
    local = at.astimezone(zone)
    now = local.time().replace(tzinfo=None)
    if not window.crosses_midnight:
        inside, first = window.start <= now < window.end, local.date()
    elif now >= window.start:
        inside, first = True, local.date()
    elif now < window.end:
        inside, first = True, local.date() - timedelta(days=1)
    else:
        inside, first = False, local.date()
    if not inside or (window.days and first.isoweekday() not in window.days):
        return None
    last = first + timedelta(days=1) if window.crosses_midnight else first
    return datetime.combine(last, window.end, tzinfo=zone).astimezone(UTC)


WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "before": "não antes das {end}",
        "after": "não depois das {start}",
        "only": "só entre {end} e {start}",
        "not": "não entre {start} e {end}",
        "days": "{line} ({days})",
        "zone": "{line}, horário de {tz}",
        "every": "{text}",
        "any": "a qualquer hora",
    },
    "en": {
        "before": "not before {end}",
        "after": "not after {start}",
        "only": "only between {end} and {start}",
        "not": "not between {start} and {end}",
        "days": "{line} ({days})",
        "zone": "{line}, {tz} time",
        "every": "{text}",
        "any": "any time",
    },
}
DAY_NAMES = {
    "pt": ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
CHANNEL_NAMES = {
    "pt": {"voice": "ligação", "whatsapp": "WhatsApp", "sms": "SMS", "email": "e-mail", None: "contato"},
    "en": {"voice": "calls", "whatsapp": "WhatsApp", "sms": "SMS", "email": "e-mail", None: "contact"},
}


def window_words(window: Window, channel: str | None, lang: str) -> str:
    """The window as a person says it: "ligação: não antes das 09:00, horário de America/Sao_Paulo"."""
    words = WORDS.get(lang, WORDS["en"])
    start, end = window.start.strftime("%H:%M"), window.end.strftime("%H:%M")
    if window.start == MIDNIGHT:
        said = words["before"].format(end=end)
    elif window.end == MIDNIGHT:
        said = words["after"].format(start=start)
    elif window.crosses_midnight:
        said = words["only"].format(start=start, end=end)
    else:
        said = words["not"].format(start=start, end=end)
    if window.days:
        names = DAY_NAMES.get(lang, DAY_NAMES["en"])
        said = words["days"].format(line=said, days=", ".join(names[d - 1] for d in window.days))
    said = words["zone"].format(line=said, tz=window.tz)
    named = CHANNEL_NAMES.get(lang, CHANNEL_NAMES["en"])
    return f"{named.get(channel, channel)}: {said}"


def hours_words(window: Window | None, channel: str | None, lang: str) -> str:
    """Hours a person said, as their block says them: the window in words, or, for hours they took back
    (`window` None), that they may be reached at any time on that channel ("ligação: a qualquer hora")."""
    if window is not None:
        return window_words(window, channel, lang)
    named = CHANNEL_NAMES.get(lang, CHANNEL_NAMES["en"])
    return f"{named.get(channel, channel)}: {WORDS.get(lang, WORDS['en'])['any']}"


def _clock(text: str) -> time:
    found = _CLOCK.match(str(text))
    if found is None:
        raise InvalidWindow(f"{text} is not a time as HH:MM")
    return time(int(found.group(1)), int(found.group(2)))


@dataclass(frozen=True, slots=True)
class WindowedSuppression:
    """A subject's suppression that holds only in its window: of `purpose` (`any` for every purpose), on
    `channel` (None for every channel)."""

    purpose: str
    channel: str | None
    window: Window

    def holds(self, purpose: str, channel: str | None) -> bool:
        return self.purpose in (purpose, "any") and (
            self.channel is None or not channel or self.channel == channel
        )


def windows_until(
    found: Iterable[WindowedSuppression], purpose: str, channel: str | None, at: datetime
) -> datetime | None:
    """The latest end among the windows `at` falls in for the purpose and channel, or None when none does."""
    ends = [
        end for w in found if w.holds(purpose, channel) and (end := window_until(w.window, at)) is not None
    ]
    return max(ends) if ends else None
