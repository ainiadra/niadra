"""Deprecation and sunset of the public contract: what leaves `/v1` is announced on every response
of its route, at least twelve months ahead, in the headers the SDKs read and log.

`Deprecation: @<unix time>` (RFC 9745) says since when; `Sunset: <HTTP date>` (RFC 8594) says when it stops
answering; `Link: <...>; rel="deprecation"` points at the migration notes. Nothing is deprecated today.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime


@dataclass(frozen=True, slots=True)
class Deprecation:
    method: str
    route: str
    """The route template the router matches, `/v1/objects/{object_id}`, never a raw path."""
    since: datetime
    sunset: datetime
    link: str
    field: str | None = None
    """A field of the route's request or response that goes away; None when the whole route does."""


DEPRECATIONS: tuple[Deprecation, ...] = ()
"""Every deprecated route and field. A test keeps each sunset at least a year after `since`, each link on
the docs site and never shared by two routes (the SDKs warn once per note), and each route one that the cell
or the control plane serves."""


def deprecation_headers(method: str, route: str) -> dict[str, str]:
    """The headers of one response. When the route and some of its fields are deprecated at different dates,
    the earliest of each date wins and every migration note is linked."""
    found = [d for d in DEPRECATIONS if d.method == method and d.route == route]
    if not found:
        return {}
    links = dict.fromkeys(d.link for d in found)
    return {
        "deprecation": f"@{int(min(d.since for d in found).timestamp())}",
        "sunset": format_datetime(min(d.sunset for d in found).astimezone(UTC), usegmt=True),
        "link": ", ".join(f'<{link}>; rel="deprecation"' for link in links),
    }
