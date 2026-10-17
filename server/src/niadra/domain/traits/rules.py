"""The trait catalog (20.1) as deterministic rules over structured memory.

Every rule reads only structured fields (intent, normalized category, outcome, sentiment, canonical
event types, open-item deadlines); no rule reads free text and no LLM invents a trait (20.2, 20.4).
`expires_at` is the moment the rule stops matching its own evidence, so a trait that nothing
refreshes simply disappears on time (8.7).
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.records import Episode, EventRecord, OpenItem
from niadra.domain.serve.recurrence import RECURRENCE_INTENT, RECURRENCE_WINDOW_DAYS, recurrence
from niadra.domain.traits.barrier import Barrier
from niadra.domain.traits.evidence import EvidenceRef, evidence_handles, evidence_keys
from niadra.domain.vocabulary import Audience, OpenItemStatus, SubjectKind

MAX_ACTIVE_TRAITS = 20  # per profile (22.5)
MAX_WINDOW_DAYS = 365  # never longer than the evidence retention (22.5)

Params = Mapping[str, Any]


class Layer(StrEnum):
    """Stable traits go to B1 without dates; volatile ones to B2 with the last evidence date (5.10)."""

    STABLE = "stable"
    VOLATILE = "volatile"


@dataclass(frozen=True, slots=True)
class TraitRule:
    rule_id: str
    catalog: str
    version: str
    params: Params


@dataclass(frozen=True, slots=True)
class TraitInputs:
    now: datetime
    subject_kind: SubjectKind
    handle_ids: frozenset[UUID]
    episodes: Sequence[Episode] = ()
    open_items: Sequence[OpenItem] = ()
    system_events: Sequence[EventRecord] = ()


@dataclass(frozen=True, slots=True)
class DerivedTrait:
    name: str
    catalog: str
    value: str
    layer: Layer
    rule_version: str
    evidence: tuple[EvidenceRef, ...]
    expires_at: datetime
    confidence: float

    @property
    def last_evidence_at(self) -> datetime:
        return max(e.at for e in self.evidence if e.at is not None)


@dataclass(frozen=True, slots=True)
class _Match:
    value: str
    evidence: tuple[EvidenceRef, ...]
    expires_at: datetime


Evaluator = Callable[[TraitInputs, Params, Barrier], _Match | None]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    evaluate: Evaluator
    layer: Layer
    defaults: Params
    single_evidence: bool = False
    """The single-evidence exception (20.4): structured and unambiguous evidence only."""
    confidence: float = 0.9
    extra_params: frozenset[str] = frozenset()
    purposes: frozenset[str] = frozenset()
    """Who the trait is released to when the tenant's rule names no purpose (20.3)."""
    audiences: frozenset[Audience] = frozenset()


def _window(params: Params) -> timedelta:
    return timedelta(days=int(params["window_days"]))


def _min_count(params: Params, key: str, single_evidence: bool = False) -> int:
    # A pattern needs repetition: tenant parameters can never lower it to one episode (20.4).
    return max(int(params[key]), 1 if single_evidence else 2)


def _in_window(at: datetime, now: datetime, window: timedelta) -> bool:
    return now - window <= at <= now


def _expiry(times: Iterable[datetime], needed: int, window: timedelta) -> datetime:
    """When the `needed`-th newest evidence leaves the window, the rule stops matching."""
    newest_first = sorted(times, reverse=True)
    return newest_first[needed - 1] + window


def _episode_ref(e: Episode) -> EvidenceRef:
    return EvidenceRef("episode", e.episode_id, e.origin_handle_id, e.started_at)


def _event_ref(ev: EventRecord, handle_ids: frozenset[UUID]) -> EvidenceRef:
    origin = next((h for h in ev.handle_ids if h in handle_ids), ev.handle_ids[0])
    return EvidenceRef("event", ev.event_id, origin, ev.occurred_at)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _recurring_complaint(inputs: TraitInputs, p: Params, barrier: Barrier) -> _Match | None:
    window, needed = _window(p), _min_count(p, "min_count")
    complaints = [e for e in inputs.episodes if e.intent == p["intent"] and e.started_at <= inputs.now]
    wanted = _as_list(p.get("categories") or p.get("category"))
    categories = [
        c
        for c in wanted or sorted({e.normalized_category for e in complaints})
        if barrier.permits_category(c)
    ]
    # The count comes from the same function the history search uses, so both numbers agree (16.2).
    counted = [
        c
        for c in (
            recurrence(complaints, c, window_days=window.days, intent=p["intent"], now=inputs.now)
            for c in categories
        )
        if c
    ]
    best = max(
        (c for c in counted if c.occurrences >= needed),
        key=lambda c: (c.occurrences, c.last_at),
        default=None,
    )
    if best is None:
        return None
    since = inputs.now - window
    episodes = [e for e in complaints if e.normalized_category == best.category and e.started_at >= since]
    return _Match(
        best.category,
        tuple(map(_episode_ref, episodes)),
        _expiry((e.started_at for e in episodes), needed, window),
    )


def _broken_company_promise(inputs: TraitInputs, p: Params, _barrier: Barrier) -> _Match | None:
    window, grace = _window(p), timedelta(hours=int(p["grace_hours"]))
    broken = [
        i
        for i in inputs.open_items
        if i.owner == "company"
        and i.status in (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)
        and i.closed_by_action_id is None
        and i.due_at is not None
        and i.due_at + grace <= inputs.now
        and inputs.now - i.due_at <= window
    ]
    if not broken:
        return None
    refs = tuple(EvidenceRef("open_item", i.item_id, i.origin_handle_id, i.due_at) for i in broken)
    return _Match(str(len(broken)), refs, max(i.due_at for i in broken if i.due_at) + window)


def _episodes_matching(inputs: TraitInputs, p: Params, test: Callable[[Episode], bool]) -> list[Episode]:
    window = _window(p)
    return [e for e in inputs.episodes if test(e) and _in_window(e.started_at, inputs.now, window)]


def _count_rule(field: str, count_key: str, single_evidence: bool = False) -> Evaluator:
    """Rules of the shape "at least N episodes whose `field` equals the parameter, in the window"."""

    def evaluate(inputs: TraitInputs, p: Params, _barrier: Barrier) -> _Match | None:
        needed = _min_count(p, count_key, single_evidence)
        episodes = _episodes_matching(inputs, p, lambda e: getattr(e, field) == p[field])
        if len(episodes) < needed:
            return None
        refs = tuple(map(_episode_ref, episodes))
        return _Match(str(len(episodes)), refs, _expiry((e.started_at for e in episodes), needed, _window(p)))

    return evaluate


def _preferred_channel(inputs: TraitInputs, p: Params, _barrier: Barrier) -> _Match | None:
    needed = _min_count(p, "min_conversations")
    started = _episodes_matching(inputs, p, lambda e: e.initiated_by == p["initiated_by"])
    if len(started) < needed:
        return None
    by_channel: dict[str, list[Episode]] = {}
    for e in started:
        by_channel.setdefault(e.channel, []).append(e)
    channel, episodes = max(by_channel.items(), key=lambda kv: (len(kv[1]), max(e.started_at for e in kv[1])))
    if len(episodes) / len(started) < float(p["min_share"]):
        return None
    refs = tuple(map(_episode_ref, episodes))
    return _Match(channel, refs, _expiry((e.started_at for e in started), needed, _window(p)))


def _system_events(inputs: TraitInputs, p: Params, barrier: Barrier) -> list[EventRecord]:
    window = _window(p)
    types = {t for t in _as_list(p.get("event_types") or p.get("event_type")) if barrier.permits(t)}
    return [
        ev
        for ev in inputs.system_events
        if ev.canonical_type in types and ev.handle_ids and _in_window(ev.occurred_at, inputs.now, window)
    ]


def _does_not_answer_calls(inputs: TraitInputs, p: Params, barrier: Barrier) -> _Match | None:
    window, needed = _window(p), _min_count(p, "min_attempts")
    attempts = _system_events(inputs, p, barrier)
    voice = set(_as_list(p["voice_channels"]))
    replies = _episodes_matching(inputs, p, lambda e: e.channel not in voice and e.initiated_by == "customer")
    if len(attempts) < needed or not replies:
        return None
    latest_reply = max(replies, key=lambda e: e.started_at)
    refs = (*(_event_ref(ev, inputs.handle_ids) for ev in attempts), _episode_ref(latest_reply))
    expires = min(
        _expiry((ev.occurred_at for ev in attempts), needed, window), latest_reply.started_at + window
    )
    return _Match(str(len(attempts)), refs, expires)


def _declining_sentiment(inputs: TraitInputs, p: Params, _barrier: Barrier) -> _Match | None:
    needed, threshold, window = _min_count(p, "sessions"), float(p["threshold"]), _window(p)
    scored = sorted(
        (
            e
            for e in inputs.episodes
            if e.sentiment is not None and _in_window(e.ended_at, inputs.now, window)
        ),
        key=lambda e: e.ended_at,
        reverse=True,
    )[:needed]
    if len(scored) < needed or any(e.sentiment is None or e.sentiment >= threshold for e in scored):
        return None
    return _Match(str(needed), tuple(map(_episode_ref, scored)), scored[0].ended_at + window)


def _overdue_invoices(inputs: TraitInputs, p: Params, barrier: Barrier) -> _Match | None:
    needed = _min_count(p, "min_count")
    events = _system_events(inputs, p, barrier)
    if len(events) < needed:
        return None
    refs = tuple(_event_ref(ev, inputs.handle_ids) for ev in events)
    return _Match(str(len(events)), refs, _expiry((ev.occurred_at for ev in events), needed, _window(p)))


CATALOG: dict[str, CatalogEntry] = {
    "recurring_complaint": CatalogEntry(
        _recurring_complaint,
        Layer.VOLATILE,
        {"intent": RECURRENCE_INTENT, "min_count": 3, "window_days": RECURRENCE_WINDOW_DAYS},
        extra_params=frozenset({"category", "categories"}),
    ),
    "broken_company_promise": CatalogEntry(
        _broken_company_promise,
        Layer.VOLATILE,
        {"window_days": 60, "grace_hours": 0},
        single_evidence=True,
        confidence=1.0,
    ),
    "asked_to_cancel": CatalogEntry(
        _count_rule("intent", "min_count", single_evidence=True),
        Layer.STABLE,
        {"intent": "cancellation", "min_count": 1, "window_days": 180},
        single_evidence=True,
    ),
    "preferred_channel": CatalogEntry(
        _preferred_channel,
        Layer.STABLE,
        {"min_share": 0.7, "min_conversations": 5, "window_days": 180, "initiated_by": "customer"},
    ),
    "does_not_answer_calls": CatalogEntry(
        _does_not_answer_calls,
        Layer.STABLE,
        {
            "min_attempts": 3,
            "window_days": 30,
            "event_types": ["call.unanswered"],
            "voice_channels": ["voice"],
        },
    ),
    "escalated_to_human": CatalogEntry(
        _count_rule("outcome", "min_count"),
        Layer.VOLATILE,
        {"outcome": "escalated", "min_count": 2, "window_days": 90},
    ),
    "declining_sentiment": CatalogEntry(
        _declining_sentiment,
        Layer.VOLATILE,
        {"sessions": 3, "threshold": -0.3, "window_days": 30},
        confidence=0.8,
        # How the customer feels guides whoever talks to them, not a back-office task.
        audiences=frozenset({Audience.CUSTOMER_AGENT}),
    ),
    "overdue_invoices": CatalogEntry(
        _overdue_invoices,
        Layer.VOLATILE,
        # The system events a rule counts stay in the live database for the space's hot window, 90 days
        # at most, so the default window is that.
        {"event_type": "invoice.overdue", "min_count": 2, "window_days": 90},
        confidence=1.0,
        purposes=frozenset({"billing"}),
    ),
}


EVENT_RULES = frozenset({"does_not_answer_calls", "overdue_invoices"})
"""Catalog rules that count system events, which leave the live database after the space's
`events_hot_days`: their window never reaches further (20.1)."""


def event_window(rule: TraitRule, hot_days: int) -> int | None:
    """The window a rule over events asks for, when it reaches past the hot window; None otherwise."""
    if rule.catalog not in EVENT_RULES:
        return None
    window = int(rule.params.get("window_days", CATALOG[rule.catalog].defaults["window_days"]))
    return window if window > hot_days else None


def within_hot_window(rule: TraitRule, hot_days: int) -> TraitRule:
    """The rule as derivation runs it: a window over events is cut to the events that are still there,
    so a pattern never outlives the evidence it could count again."""
    if event_window(rule, hot_days) is None:
        return rule
    return replace(rule, params={**rule.params, "window_days": hot_days})


def released_to(catalog: str, purposes: frozenset[str]) -> tuple[frozenset[str], frozenset[Audience]]:
    """Who a trait reaches (20.3): the rule's own purposes or else the catalog's, and the catalog's
    audience classes. Overdue invoices reach billing only, even when the tenant names no purpose."""
    entry = CATALOG.get(catalog)
    if entry is None:
        return purposes, frozenset()
    return purposes or entry.purposes, entry.audiences


def validate_rule(rule: TraitRule, barrier: Barrier) -> Params:
    """The rule's effective parameters; raises InvalidInput for anything outside the catalog (20.5)."""
    entry = CATALOG.get(rule.catalog)
    if entry is None:
        raise InvalidInput("unknown trait catalog entry")
    unknown = set(rule.params) - set(entry.defaults) - entry.extra_params
    if unknown:
        raise InvalidInput("unknown rule parameters: " + ", ".join(sorted(unknown)))
    params = {**entry.defaults, **rule.params}
    if not 1 <= int(params["window_days"]) <= MAX_WINDOW_DAYS:
        raise InvalidInput(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")
    barrier.check_params(params)
    return params


def derive(rules: Iterable[TraitRule], inputs: TraitInputs, barrier: Barrier) -> list[DerivedTrait]:
    # Evidence from a sensitive category never feeds any rule, not even a channel preference (20.4).
    safe = replace(
        inputs, episodes=[e for e in inputs.episodes if not barrier.is_sensitive(e.normalized_category)]
    )
    derived = []
    for rule in rules:
        try:
            params = validate_rule(rule, barrier)
        except InvalidInput:
            continue  # a rule the allow-list no longer admits stops producing traits, nothing else breaks
        entry = CATALOG[rule.catalog]
        match = entry.evaluate(safe, params, barrier)
        if match is None or not _enough_evidence(match, entry, inputs.subject_kind):
            continue
        derived.append(
            DerivedTrait(
                name=rule.rule_id,
                catalog=rule.catalog,
                value=match.value,
                layer=entry.layer,
                rule_version=rule.version,
                evidence=match.evidence,
                expires_at=match.expires_at,
                confidence=entry.confidence,
            )
        )
    derived.sort(key=lambda t: (t.confidence, t.last_evidence_at), reverse=True)
    return derived[:MAX_ACTIVE_TRAITS]


def _enough_evidence(match: _Match, entry: CatalogEntry, kind: SubjectKind) -> bool:
    if not entry.single_evidence and len(evidence_keys(match.evidence)) < 2:
        return False
    # An account trait sums at least two different contacts (21.4).
    return not kind.is_organization or len(evidence_handles(match.evidence)) >= 2
