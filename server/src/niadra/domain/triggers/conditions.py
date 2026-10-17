"""The trigger condition catalog (22.1), deduplication and lateness.

A trigger is a condition with parameters, a window and deduplication per subject; it warns, it never
acts. Each condition returns the evidence that makes it true and the moment it became true, which
is what lateness is measured against.
"""

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Any
from uuid import UUID

from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.objects.state import CONFIRMATION_WINDOW
from niadra.domain.records import AgentAction, BusinessObject, Episode, HandleRecord, OpenItem, Trait
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.vocabulary import Audience, HandleStatus, OpenItemStatus

FIRINGS_PER_MINUTE = 100  # per tenant (22.5)
DEFAULT_LATE_AFTER = timedelta(hours=2)

Params = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TriggerRule:
    rule_id: str
    condition: str
    version: str
    params: Params
    endpoint_id: str
    window_hours: int = 24
    late_policy: str = "deliver_marked"


@dataclass(frozen=True, slots=True)
class SubjectState:
    """What the conditions read about one profile; the app loads only what the enabled rules need."""

    now: datetime
    handle_ids: frozenset[UUID]
    open_items: Sequence[OpenItem] = ()
    traits: Sequence[Trait] = ()
    trait_evidence: Mapping[UUID, tuple[EvidenceRef, ...]] = field(default_factory=dict)
    episodes: Sequence[Episode] = ()
    handles: Sequence[HandleRecord] = ()
    divergent_actions: Sequence[tuple[AgentAction, UUID]] = ()
    """Each divergent action with the origin handle of its object."""
    objects: Sequence[BusinessObject] = ()
    """The subject's business objects, with the timers of the declared types (24.3.8)."""


@dataclass(frozen=True, slots=True)
class ConditionMatch:
    evidence: tuple[EvidenceRef, ...]
    became_true_at: datetime
    episode: str = ""
    """Tells two occurrences over the same evidence apart, such as each time a source falls silent:
    it enters the idempotency key, never the webhook."""
    detail: Mapping[str, Any] = field(default_factory=dict)
    """Numbers behind a rate condition (counts and the rate, never content), sent in the webhook."""


Condition = Callable[[SubjectState, Params], ConditionMatch | None]


def _open_by_company(items: Iterable[OpenItem], owner: str | None) -> list[OpenItem]:
    return [
        i
        for i in items
        if i.status in (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)
        and i.closed_by_action_id is None
        and i.due_at is not None
        and (owner is None or i.owner == owner)
    ]


def _item_ref(item: OpenItem) -> EvidenceRef:
    return EvidenceRef("open_item", item.item_id, item.origin_handle_id, item.due_at)


def _episode_ref(e: Episode) -> EvidenceRef:
    return EvidenceRef("episode", e.episode_id, e.origin_handle_id, e.started_at)


def promise_overdue(state: SubjectState, p: Params) -> ConditionMatch | None:
    late_by = timedelta(days=float(p.get("days", 0)))
    items = [
        i
        for i in _open_by_company(state.open_items, p.get("owner", "company"))
        if i.due_at and i.due_at + late_by <= state.now
    ]
    if not items:
        return None
    return ConditionMatch(tuple(map(_item_ref, items)), max(i.due_at + late_by for i in items if i.due_at))


def open_item_due(state: SubjectState, p: Params) -> ConditionMatch | None:
    lead = timedelta(hours=float(p.get("lead_hours", 24)))
    items = [
        i
        for i in _open_by_company(state.open_items, p.get("owner"))
        if i.status is OpenItemStatus.OPEN and i.due_at and i.due_at - lead <= state.now < i.due_at
    ]
    if not items:
        return None
    return ConditionMatch(tuple(map(_item_ref, items)), max(i.due_at - lead for i in items if i.due_at))


def trait_present(state: SubjectState, p: Params) -> ConditionMatch | None:
    traits = [
        t for t in state.traits if t.name == p.get("name") and ("value" not in p or t.value == p["value"])
    ]
    evidence: list[EvidenceRef] = []
    for t in traits:
        refs = state.trait_evidence.get(t.trait_id, ())
        if refs:
            evidence.append(EvidenceRef("trait", t.trait_id, refs[0].origin_handle_id, t.last_evidence_at))
            evidence.extend(refs)
    if not evidence:
        return None
    return ConditionMatch(tuple(evidence), max(max(t.first_seen, t.last_evidence_at) for t in traits))


def sentiment_drop(state: SubjectState, p: Params) -> ConditionMatch | None:
    baseline_size, drop = int(p.get("sessions", 3)), float(p.get("drop", 0.5))
    scored = sorted(
        (e for e in state.episodes if e.sentiment is not None), key=lambda e: e.ended_at, reverse=True
    )
    if len(scored) < baseline_size + 1:
        return None
    latest, baseline = scored[0], scored[1 : baseline_size + 1]
    if latest.sentiment is None or latest.sentiment > fmean(e.sentiment or 0.0 for e in baseline) - drop:
        return None
    return ConditionMatch(tuple(_episode_ref(e) for e in (latest, *baseline)), latest.ended_at)


def identity_ambiguous(state: SubjectState, _p: Params) -> ConditionMatch | None:
    ambiguous = [h for h in state.handles if h.status is HandleStatus.AMBIGUOUS]
    if not ambiguous:
        return None
    refs = tuple(EvidenceRef("handle", h.handle_id, h.handle_id, h.last_seen) for h in ambiguous)
    return ConditionMatch(refs, max(h.last_seen for h in ambiguous))


def fact_contradicted_by_system_event(state: SubjectState, p: Params) -> ConditionMatch | None:
    since = state.now - timedelta(days=float(p.get("within_days", 7)))
    divergent = [(a, origin) for a, origin in state.divergent_actions if a.occurred_at >= since]
    if not divergent:
        return None
    refs = tuple(EvidenceRef("action", a.action_id, origin, a.occurred_at) for a, origin in divergent)
    # Without a confirming event the action turned divergent when its window ran out (18.4).
    became = max(
        a.occurred_at + (CONFIRMATION_WINDOW if a.confirmed_by_event_id is None else timedelta())
        for a, _ in divergent
    )
    return ConditionMatch(refs, became)


def object_timer_due(state: SubjectState, p: Params) -> ConditionMatch | None:
    """A timer of one of the subject's objects fired within `within_hours` (24.3.8): of the object type and
    the timer the rule names, or of any. Each firing is its own occurrence, so a revised deadline that fires
    the timer again warns again."""
    since = state.now - timedelta(hours=float(p.get("within_hours", 24)))
    fired = [
        (obj, mark)
        for obj in state.objects
        if obj.origin_handle_id is not None and p.get("type") in (None, obj.key.type)
        for name, mark in sorted(obj.typed.timers.items())
        if p.get("timer") in (None, name) and mark.fired_at is not None and mark.fired_at >= since
    ]
    if not fired:
        return None
    refs = tuple(
        EvidenceRef("object", obj.object_id, obj.origin_handle_id, mark.fired_at)
        for obj, mark in fired
        if obj.origin_handle_id is not None
    )
    episode = ",".join(sorted(str(mark.timer_id) for _, mark in fired))
    return ConditionMatch(refs, max(mark.fired_at for _, mark in fired if mark.fired_at), episode)


def recontact(state: SubjectState, p: Params) -> ConditionMatch | None:
    within = timedelta(hours=float(p.get("hours", 72)))
    ordered = sorted(state.episodes, key=lambda e: e.started_at, reverse=True)
    if len(ordered) < 2 or ordered[0].initiated_by != "customer":
        return None
    latest = ordered[0]
    same_category = bool(p.get("same_category", True))
    previous = next(
        (e for e in ordered[1:] if not same_category or e.normalized_category == latest.normalized_category),
        None,
    )
    if previous is None or not timedelta() <= latest.started_at - previous.ended_at <= within:
        return None
    return ConditionMatch((_episode_ref(latest), _episode_ref(previous)), latest.started_at)


def _about_a_source(_state: SubjectState, _p: Params) -> ConditionMatch | None:
    """A condition about a source, not a profile: the 5-minute sweep evaluates it."""
    return None


# `source.silent` and `context_use.repetition_rate` have a source as subject, not a profile: the sweep
# evaluates them (see silent_sources and repetition_run).
CONDITIONS: dict[str, Condition] = {
    "promise.overdue": promise_overdue,
    "open_item.due": open_item_due,
    "trait.present": trait_present,
    "sentiment.drop": sentiment_drop,
    "identity.ambiguous": identity_ambiguous,
    "fact.contradicted_by_system_event": fact_contradicted_by_system_event,
    "recontact": recontact,
    "object.timer_due": object_timer_due,
    "context_use.repetition_rate": _about_a_source,
    "source.silent": _about_a_source,
    "source.gap": _about_a_source,
}

# Which inputs each condition reads, so evaluation loads nothing it does not need.
NEEDS: dict[str, frozenset[str]] = {
    "promise.overdue": frozenset({"open_items"}),
    "open_item.due": frozenset({"open_items"}),
    "trait.present": frozenset({"traits"}),
    "sentiment.drop": frozenset({"episodes"}),
    "identity.ambiguous": frozenset({"handles"}),
    "fact.contradicted_by_system_event": frozenset({"actions"}),
    "recontact": frozenset({"episodes"}),
    "object.timer_due": frozenset({"objects"}),
    "context_use.repetition_rate": frozenset(),
    "source.silent": frozenset(),
    "source.gap": frozenset(),
}

SOURCE_CONDITIONS = frozenset({"source.silent", "context_use.repetition_rate", "source.gap"})


def silent_sources(last_event_at: Mapping[UUID, datetime], minutes: float, now: datetime) -> list[UUID]:
    """Sources whose last event is older than `minutes` (5.2 coverage); never-seen sources are not silent."""
    limit = timedelta(minutes=minutes)
    return sorted(s for s, last in last_event_at.items() if now - last >= limit)


def silence_episode(last_event_at: datetime) -> str:
    """Each silence is its own occurrence: a source that comes back and falls silent again warns again."""
    return f"silent_since:{last_event_at.isoformat()}"


REPETITION_LOOKBACK_DAYS = 30
"""How far back the start of a run above the threshold is looked for."""


REPETITION_DEFAULTS: Mapping[str, Any] = {"above": 0.2, "window_days": 7, "min_questions": 30}


@dataclass(frozen=True, slots=True)
class RepetitionParams:
    """`context_use.repetition_rate(source) > above` over the last `window_days` days (22.1, 19.4).

    A window with fewer than `min_questions` questions says nothing: a handful of conversations never
    fires the rule. Without `source_id`, every source is watched on its own."""

    above: float
    window_days: int
    min_questions: int
    source_id: UUID | None = None

    @classmethod
    def of(cls, params: Params) -> "RepetitionParams":
        merged = {**REPETITION_DEFAULTS, **params}
        try:
            above, window_days = float(merged["above"]), int(merged["window_days"])
            min_questions = int(merged["min_questions"])
            source = UUID(str(merged["source_id"])) if merged.get("source_id") else None
        except (TypeError, ValueError):
            raise ValueError("unusable repetition parameters") from None
        if not 0 <= above < 1 or not 1 <= window_days <= 90 or min_questions < 1:
            raise ValueError("unusable repetition parameters: above in [0, 1), window_days 1 to 90")
        return cls(above, window_days, min_questions, source)


@dataclass(frozen=True, slots=True)
class RepetitionWindow:
    questions: int
    repeated: int

    @property
    def rate(self) -> float:
        return self.repeated / self.questions if self.questions else 0.0


def repetition_window(
    daily: Mapping[date, tuple[int, int]], last_day: date, window_days: int
) -> RepetitionWindow:
    """Questions and repeated questions of the `window_days` days ending on `last_day`."""
    days = [last_day - timedelta(days=n) for n in range(window_days)]
    return RepetitionWindow(
        sum(daily.get(d, (0, 0))[0] for d in days), sum(daily.get(d, (0, 0))[1] for d in days)
    )


def repetition_run(
    daily: Mapping[date, tuple[int, int]],
    today: date,
    params: RepetitionParams,
    lookback_days: int = REPETITION_LOOKBACK_DAYS,
) -> date | None:
    """The first day of the run of days, ending today, whose trailing window was above the threshold;
    None when today's window is not. The run is the occurrence: one warning each time the source goes
    above, however many sweeps see it there. `daily` maps a day to (questions, repeated)."""

    def above(day: date) -> bool:
        window = repetition_window(daily, day, params.window_days)
        return window.questions >= params.min_questions and window.rate > params.above

    if not above(today):
        return None
    start = today
    for back in range(1, lookback_days + 1):
        day = today - timedelta(days=back)
        if not above(day):
            break
        start = day
    return start


def window_key(now: datetime, window_hours: int) -> str:
    """The window a firing belongs to: at most one firing per rule, subject and window (22.1)."""
    size = window_hours * 3600
    return f"{window_hours}h:{int(now.timestamp()) // size}"


def idempotency_key(
    rule_id: str, rule_version: str, evidence: Iterable[EvidenceRef], episode: str = ""
) -> bytes:
    """hash(rule, evidence handles, evidence): the same evidence never fires the same rule twice (22.1).
    `episode` keeps two occurrences over the same evidence apart (a source silent twice)."""
    refs = list(evidence)
    handles = sorted({str(e.origin_handle_id) for e in refs})
    items = sorted(f"{e.kind}:{e.id}" for e in refs)
    parts = [rule_id, rule_version, ",".join(handles), ",".join(items)]
    material = "|".join([*parts, episode] if episode else parts)
    return hashlib.sha256(material.encode()).digest()


def is_late(became_true_at: datetime, now: datetime, params: Params) -> bool:
    late_after = timedelta(
        minutes=float(params.get("late_after_minutes", DEFAULT_LATE_AFTER.total_seconds() / 60))
    )
    return now - became_true_at > late_after


def unreadable_reason(
    rule: TriggerRule,
    *,
    endpoint_found: bool,
    endpoint_enabled: bool,
    endpoint_types: Sequence[str],
    audience: Audience | None,
    source_purposes: frozenset[str],
    trait_purposes: frozenset[str],
    trait_audiences: frozenset[Audience] = frozenset(),
    endpoint_format: str = "standard",
) -> str | None:
    """Why the target cannot receive this rule's payload, or None when it can (22.1, rev. 10)."""
    if not endpoint_found or audience is None:
        return "endpoint_unknown"
    if endpoint_format != "standard":
        # A SIEM stream carries receipts in OCSF or HEC; a warning is never one of them.
        return "endpoint_is_siem_stream"
    if not endpoint_enabled:
        return "endpoint_disabled"
    if "trigger.fired" not in endpoint_types and "*" not in endpoint_types:
        return "endpoint_not_subscribed"
    if audience in (Audience.ANALYST, Audience.EXPORT):
        return "audience_cannot_receive_triggers"
    if rule.condition == "identity.ambiguous" and audience is Audience.CUSTOMER_AGENT:
        return "audience_cannot_read_identity"
    if rule.condition == "trait.present" and trait_purposes and not trait_purposes & source_purposes:
        return "purpose_not_allowed"
    if rule.condition == "trait.present" and trait_audiences and audience not in trait_audiences:
        return "audience_not_allowed"
    return None


GAP_DEFAULTS: Mapping[str, Any] = {"min_missing": 10, "share": 0.05}


def gap_of(day: SourceDay, params: Mapping[str, Any]) -> int | None:
    """What a source's SDK says it sent on a day and never arrived, when it is more than the rule allows:
    at least `min_missing` events and more than `share` of what was sent. None otherwise."""
    p = {**GAP_DEFAULTS, **params}
    missing = day.sent - day.received - day.rejected
    if missing >= int(p["min_missing"]) and missing > float(p["share"]) * day.sent:
        return missing
    return None


def gap_episode(day: date) -> str:
    """One warning per source and day: the counts only grow during the day."""
    return f"gap:{day.isoformat()}"
