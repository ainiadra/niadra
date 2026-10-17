"""The temporal channel of the per-turn slots (11, 3.3): what the question's time words select.

Two readings, both without a model. An anchor ("depois da vistoria", "before the delivery", "desde el cambio
de tarjeta") resolved against the customer's own system event gives a window that filters and boosts. A
calendar expression ("mês passado", "last week") gives a window that only boosts: "o CEP que vocês têm hoje"
is not a question about today, and a boost never hides an item another channel found.

A question without time words returns `TemporalResult()`: no window, no hits, nothing outside, so the other
channels decide alone.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.serve.relative_time import (
    AnchorLexicon,
    AnchorPhrase,
    Relation,
    ResolvedAnchor,
    SystemMark,
    Window,
    find_when,
    read_anchor,
    resolve_anchor,
)

_DAY = timedelta(days=1)
_PROXIMITY_DAYS = 30.0
"""The scale of the closeness score: an item 30 days from the anchor scores half of one on the same day."""
_MENTION = 1.0
"""An item that itself names the anchor's event ("depois da entrega, a nota veio errada") ranks first."""


class TimedItem(Protocol):
    """What the channel reads of an item; the compiler's `Candidate` has all of it."""

    @property
    def item_id(self) -> str: ...
    @property
    def at(self) -> datetime: ...
    @property
    def text(self) -> str: ...
    @property
    def object_id(self) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class TemporalHit:
    item_id: str
    score: float


@dataclass(frozen=True, slots=True)
class TemporalResult:
    window: Window | None = None
    anchor: AnchorPhrase | None = None
    resolved: ResolvedAnchor | None = None
    hits: tuple[TemporalHit, ...] = ()
    """Items inside the window, best first; ties broken by item id, so the same input ranks the same (L5)."""
    outside: frozenset[str] = field(default_factory=frozenset)
    """Items an anchored window excludes. Empty for a calendar window and for an anchor with no event: the
    caller drops these from the slots of the kinds it filters (episodes, values, objects, actions)."""

    def analysis(self) -> dict[str, Any]:
        """What the manifest records of the reading: the relation and the event type, never the words."""
        if self.window is None and self.anchor is None:
            return {}
        out: dict[str, Any] = {}
        if self.anchor is not None:
            out["anchor"] = {
                "relation": self.anchor.relation.value,
                "event": self.anchor.name,
                "resolved": self.resolved is not None,
            }
            if self.resolved is not None:
                out["anchor"]["event_type"] = self.resolved.mark.canonical_type
                out["anchor"]["event_at"] = self.resolved.mark.at.isoformat()
        if self.window is not None:
            out["window"] = {"since": self.window.since.isoformat(), "until": self.window.until.isoformat()}
        return out


def _inside(window: Window, at: datetime) -> bool:
    return window.since <= at < window.until


def _intersect(a: Window, b: Window) -> Window | None:
    since, until = max(a.since, b.since), min(a.until, b.until)
    return Window(since, until) if since < until else None


def _closeness(at: datetime, to: datetime) -> float:
    return 1.0 / (1.0 + abs((at - to) / _DAY) / _PROXIMITY_DAYS)


def temporal_channel(
    question: str | None,
    items: Sequence[TimedItem],
    *,
    lexicon: AnchorLexicon,
    marks: Iterable[SystemMark],
    reference: datetime,
    timezone: str,
) -> TemporalResult:
    """Rank `items` by the question's time words; `marks` are the customer's system events.

    `reference` is the moment the question is read against: the last event of the conversation, so a
    replay reads the same words the same way."""
    if not question:
        return TemporalResult()
    phrase = read_anchor(question, lexicon)
    resolved = resolve_anchor(phrase, marks, reference) if phrase is not None else None
    calendar = find_when(question, reference, timezone)
    if resolved is None:
        if calendar is None:
            return TemporalResult(anchor=phrase)
        return TemporalResult(calendar, phrase, None, _rank(items, calendar, reference, None, lexicon))
    window = resolved.window
    if calendar is not None:
        window = _intersect(window, calendar) or window
    hits = _rank(items, window, reference, resolved, lexicon)
    outside = frozenset(i.item_id for i in items if not _inside(window, i.at))
    return TemporalResult(window, phrase, resolved, hits, outside)


def _rank(
    items: Sequence[TimedItem],
    window: Window,
    reference: datetime,
    resolved: ResolvedAnchor | None,
    lexicon: AnchorLexicon,
) -> tuple[TemporalHit, ...]:
    hits: list[TemporalHit] = []
    for item in items:
        if not _inside(window, item.at):
            continue
        if resolved is None:
            score = _closeness(item.at, reference)
        else:
            # "After" and "before" read the nearest items to the event first; "since" the most recent.
            to = reference if resolved.phrase.relation is Relation.SINCE else resolved.mark.at
            score = _closeness(item.at, to)
            if lexicon.mentions(resolved.phrase.positions, item.text):
                score += _MENTION
        hits.append(TemporalHit(item.item_id, round(score, 6)))
    hits.sort(key=lambda h: (-h.score, h.item_id))
    return tuple(hits)
