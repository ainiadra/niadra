"""Triggers: evaluate conditions, fire at most once per window, retract after an unmerge (22.1).

A trigger only warns. Evaluation runs per profile as the coalesced `trigger` task (after reconcile,
apply_object_event, derive_traits and measure) and on time in the 5-minute sweep, which asks for
evaluations of profiles whose deadlines came due and fires the conditions about a source itself:
`source.silent` and `context_use.repetition_rate`.

A profile's conditions read its confirmed handles only (`derivation_view`, 5.3): what came through an
unconfirmed link fires nothing and reaches no endpoint, not even as an id, until the link is confirmed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from uuid import UUID

from niadra.app.notify import enqueue_event
from niadra.app.traits import active_profile_ids
from niadra.config.models import SpaceConfig, TriggerRuleConfig
from niadra.contracts.traits import EvidencePointer
from niadra.contracts.triggers import FiringPage, FiringView, TriggerDryRunResponse, TriggerSample
from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.provenance import derivation_view
from niadra.domain.measure.inputs import CONTROL_GROUP
from niadra.domain.measure.model import DailyRow
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.records import ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.traits.rules import released_to
from niadra.domain.triggers.conditions import (
    CONDITIONS,
    FIRINGS_PER_MINUTE,
    NEEDS,
    REPETITION_LOOKBACK_DAYS,
    SOURCE_CONDITIONS,
    ConditionMatch,
    RepetitionParams,
    SubjectState,
    TriggerRule,
    gap_episode,
    gap_of,
    idempotency_key,
    is_late,
    repetition_run,
    repetition_window,
    silence_episode,
    silent_sources,
    unreadable_reason,
    window_key,
)
from niadra.domain.triggers.firing import Firing
from niadra.domain.vocabulary import OpenItemStatus
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

DRY_RUN_DAYS = 30  # 22.1
DRY_RUN_SAMPLE = 10
RETRACTION_LOOKBACK = timedelta(days=30)
IDEMPOTENCY_LOOKBACK = timedelta(days=400)  # firings are kept 13 months (22.5)
EPISODE_LOOKBACK = timedelta(days=90)
SWEEP_ITEMS = 5000
_RATE_WINDOW = timedelta(minutes=1)

# A source quiet for longer than this is a disconnected integration, not a silent one.
SILENT_LOOKBACK = timedelta(days=30)

log = logging.getLogger(__name__)


class _Outcome(Enum):
    FIRED = "fired"
    SKIPPED = "skipped"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class _Subject:
    subject_id: UUID
    webhook: WebhookSubject
    membership_version: int


def _rule(config: TriggerRuleConfig) -> TriggerRule:
    return TriggerRule(
        rule_id=config.rule_id,
        condition=config.condition,
        version=config.version,
        params=config.params,
        endpoint_id=config.endpoint_id,
        window_hours=config.window_hours,
        late_policy=config.late_policy,
    )


def _enabled(space: SpaceConfig) -> list[TriggerRule]:
    return [_rule(r) for r in space.trigger_rules if r.enabled]


def target_problem(space: SpaceConfig, rule: TriggerRule) -> str | None:
    """The target endpoint's source must be able to read the condition's payload (22.1, rev. 10)."""
    endpoint = space.endpoint(rule.endpoint_id)
    source = space.source(endpoint.source_id) if endpoint else None
    trait_rule = next((t for t in space.trait_rules if t.rule_id == rule.params.get("name")), None)
    purposes, audiences = (
        released_to(trait_rule.catalog, trait_rule.purposes) if trait_rule else (frozenset(), frozenset())
    )
    return unreadable_reason(
        rule,
        endpoint_found=endpoint is not None,
        endpoint_enabled=bool(endpoint and endpoint.enabled),
        endpoint_types=endpoint.event_types if endpoint else [],
        audience=source.audience if source else None,
        source_purposes=source.purposes if source else frozenset(),
        trait_purposes=purposes,
        trait_audiences=audiences,
        endpoint_format=endpoint.format if endpoint else "standard",
    )


def _profile_subject(profile: ProfileRecord) -> _Subject:
    return _Subject(
        profile.profile_id, WebhookSubject(profile.pseudonym, profile.kind.value), profile.membership_version
    )


def _source_subject(source_id: UUID) -> _Subject:
    return _Subject(source_id, WebhookSubject(str(source_id), "source"), 0)


@dataclass(frozen=True, slots=True)
class _SourceMatch:
    rule: TriggerRule
    source_id: UUID
    match: ConditionMatch


class TriggersService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def evaluate(self, task: Task) -> None:
        if task.profile_id is None:
            raise InvalidInput("trigger is a per-profile task")
        space = self._config.space(task.space_id)
        now = self._clock.now()
        async with self._uow(task.space_id) as uow:
            seen = await uow.pipeline.begin_work(task.space_id, task.profile_id, TaskName.TRIGGER)
            if await self._evaluate_profile(uow, space, task.profile_id, now) is _Outcome.DEFERRED:
                # Over the tenant cap: try again in a minute; `done` stays behind so nothing is lost (22.5).
                await uow.pipeline.schedule(task, now + _RATE_WINDOW, reason=DelayReason.RATE_LIMITED)
                return
            if await uow.pipeline.finish_work(task.space_id, task.profile_id, TaskName.TRIGGER, seen):
                again = Task(
                    TaskName.TRIGGER, task.space_id, {"reason": "coalesced"}, profile_id=task.profile_id
                )
                await uow.pipeline.enqueue([again])

    async def _evaluate_profile(
        self, uow: UnitOfWork, space: SpaceConfig, profile_id: UUID, now: datetime
    ) -> _Outcome:
        profile = await uow.identity.get_profile(profile_id)
        if profile is None:
            return _Outcome.SKIPPED
        await self._retract_separated(uow, space, profile, now)
        rules = [r for r in _enabled(space) if r.condition not in SOURCE_CONDITIONS]
        if not rules:
            return _Outcome.SKIPPED
        state = await _load_state(uow, profile, rules, now)
        subject = _profile_subject(profile)
        for rule in rules:
            match = CONDITIONS[rule.condition](state, rule.params)
            if match and await self._fire(uow, space, rule, subject, match, now) is _Outcome.DEFERRED:
                return _Outcome.DEFERRED
        return _Outcome.FIRED

    async def _fire(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        rule: TriggerRule,
        subject: _Subject,
        match: ConditionMatch,
        now: datetime,
    ) -> _Outcome:
        if target_problem(space, rule):
            return _Outcome.SKIPPED
        key = idempotency_key(rule.rule_id, rule.version, match.evidence, match.episode)
        earlier = await uow.triggers.firings_for(subject.subject_id, since=now - IDEMPOTENCY_LOOKBACK)
        if any(f.rule_id == rule.rule_id and f.idempotency_key == key for f in earlier):
            return _Outcome.SKIPPED
        late = is_late(match.became_true_at, now, rule.params)
        if late and rule.late_policy == "suppress":
            return _Outcome.SKIPPED
        if await uow.triggers.count_since(now - _RATE_WINDOW) >= FIRINGS_PER_MINUTE:
            return _Outcome.DEFERRED
        firing = Firing(
            firing_id=uuid7(),
            rule_id=rule.rule_id,
            rule_version=rule.version,
            profile_id=subject.subject_id,
            window_key=window_key(now, rule.window_hours),
            idempotency_key=key,
            evidence=match.evidence,
            membership_version=subject.membership_version,
            fired_at=now,
            late=late,
        )
        if not await uow.triggers.add_firing(firing):
            return _Outcome.SKIPPED
        data = {
            "firing_id": str(firing.firing_id),
            "rule_id": rule.rule_id,
            "rule_version": rule.version,
            "condition": rule.condition,
            "window_key": firing.window_key,
            "late": late,
            "membership_version": subject.membership_version,
            "as_of": now.isoformat(),
            "evidence": [{"kind": e.kind, "id": str(e.id)} for e in match.evidence],
        }
        if match.detail:
            data["detail"] = dict(match.detail)
        await enqueue_event(
            uow,
            space,
            "trigger.fired",
            subject.webhook,
            data,
            endpoint_ids=[rule.endpoint_id],
            links=_links(rule, subject),
        )
        return _Outcome.FIRED

    async def _retract_separated(
        self, uow: UnitOfWork, space: SpaceConfig, profile: ProfileRecord, now: datetime
    ) -> None:
        """An unmerge that separates a firing's evidence retracts the firing (22.1, rev. 10)."""
        handles = frozenset(profile.handle_ids)
        rules = {r.rule_id: r for r in space.trigger_rules}
        for firing in await uow.triggers.firings_for(profile.profile_id, since=now - RETRACTION_LOOKBACK):
            if not firing.separated_from(handles):
                continue
            await uow.triggers.retract(firing.firing_id, now)
            rule = rules.get(firing.rule_id)
            data = {
                "firing_id": str(firing.firing_id),
                "rule_id": firing.rule_id,
                "rule_version": firing.rule_version,
                "reason": "evidence_separated",
                "membership_version": profile.membership_version,
            }
            await enqueue_event(
                uow,
                space,
                "trigger.retracted",
                _profile_subject(profile).webhook,
                data,
                endpoint_ids=[rule.endpoint_id] if rule else None,
            )

    async def sweep(self, now: datetime | None = None) -> int:
        """The 5-minute sweep (22.1): due deadlines request an evaluation; the conditions about a source
        (a silent source, a repetition rate above its threshold) fire here."""
        now = now or self._clock.now()
        touched = 0
        for space in self._config.spaces():
            try:
                rules = _enabled(space)
                if not rules:
                    continue
                async with self._uow(space.space_id) as uow:
                    touched += await self._request_due(uow, space, rules, now)
                    touched += await self._fire_source_conditions(uow, space, rules, now)
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return touched

    async def _request_due(
        self, uow: UnitOfWork, space: SpaceConfig, rules: Sequence[TriggerRule], now: datetime
    ) -> int:
        leads = [float(r.params.get("lead_hours", 24)) for r in rules if r.condition == "open_item.due"]
        if not any(r.condition in ("promise.overdue", "open_item.due") for r in rules):
            return 0
        until = now + timedelta(hours=max(leads, default=0))
        profiles: set[UUID] = set()
        for item in await uow.memory.open_items_due(until, limit=SWEEP_ITEMS):
            profile = await uow.identity.profile_of(item.origin_handle_id)
            if profile:
                profiles.add(profile.profile_id)
        for profile_id in sorted(profiles):
            await uow.pipeline.request_work(
                Task(TaskName.TRIGGER, space.space_id, {"reason": "sweep"}, profile_id=profile_id)
            )
        return len(profiles)

    async def _fire_source_conditions(
        self, uow: UnitOfWork, space: SpaceConfig, rules: Sequence[TriggerRule], now: datetime
    ) -> int:
        fired = 0
        for found in await _source_matches(uow, space, rules, now):
            outcome = await self._fire(
                uow, space, found.rule, _source_subject(found.source_id), found.match, now
            )
            fired += outcome is _Outcome.FIRED
        return fired

    async def dry_run(self, space_id: UUID, rule: TriggerRule) -> TriggerDryRunResponse:
        """How many subjects the rule would fire for now, over the population of the last 30 days (22.1)."""
        if rule.condition not in CONDITIONS:
            raise InvalidInput("unknown trigger condition")
        space = self._config.space(space_id)
        now = self._clock.now()
        if rule.condition in SOURCE_CONDITIONS:
            return await self._dry_run_sources(space, rule, now)
        evaluated, firing = 0, 0
        sample: list[TriggerSample] = []
        async with self._uow(space_id) as uow:
            for profile_id in await active_profile_ids(uow, now - timedelta(days=DRY_RUN_DAYS)):
                profile = await uow.identity.get_profile(profile_id)
                if profile is None:
                    continue
                evaluated += 1
                match = CONDITIONS[rule.condition](await _load_state(uow, profile, [rule], now), rule.params)
                if match is None:
                    continue
                firing += 1
                if len(sample) < DRY_RUN_SAMPLE:
                    sample.append(
                        TriggerSample(
                            subject_id=str(profile_id),
                            pseudonym=profile.pseudonym,
                            evidence=_pointers(match.evidence),
                        )
                    )
        return TriggerDryRunResponse(
            rule_id=rule.rule_id,
            window_days=DRY_RUN_DAYS,
            subjects_evaluated=evaluated,
            would_fire=firing,
            target_problem=target_problem(space, rule),
            sample=sample,
        )

    async def _dry_run_sources(
        self, space: SpaceConfig, rule: TriggerRule, now: datetime
    ) -> TriggerDryRunResponse:
        """A condition about a source is tried on the space's sources as the sweep would see them now."""
        async with self._uow(space.space_id) as uow:
            found = await _source_matches(uow, space, [rule], now)
        names = {s.source_id: s.name for s in space.sources}
        return TriggerDryRunResponse(
            rule_id=rule.rule_id,
            window_days=DRY_RUN_DAYS,
            subjects_evaluated=len(space.sources),
            would_fire=len(found),
            target_problem=target_problem(space, rule),
            sample=[
                TriggerSample(
                    subject_id=str(f.source_id),
                    pseudonym=names.get(f.source_id, str(f.source_id)),
                    evidence=_pointers(f.match.evidence),
                )
                for f in found[:DRY_RUN_SAMPLE]
            ],
        )

    async def list_firings(
        self,
        space_id: UUID,
        *,
        rule_id: str | None = None,
        subject_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FiringPage:
        async with self._uow(space_id) as uow:
            rows = await uow.triggers.list_firings(
                rule_id=rule_id, profile_id=subject_id, before=decode_cursor(cursor), limit=limit + 1
            )
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].fired_at, page[-1].firing_id) if len(rows) > limit else None
        return FiringPage(items=[_view(f) for f in page], next_cursor=next_cursor)


def _links(rule: TriggerRule, subject: _Subject) -> dict[str, str] | None:
    if subject.webhook.kind != "source":
        return {"open": f"/v1/profiles/{subject.subject_id}"}
    if rule.condition == "context_use.repetition_rate":
        return {"open": f"/v1/context-use?source_id={subject.subject_id}"}
    return None


async def _source_matches(
    uow: UnitOfWork, space: SpaceConfig, rules: Sequence[TriggerRule], now: datetime
) -> list[_SourceMatch]:
    """The conditions about a source that hold now: each silent source, each source above its
    repetition threshold. Only sources of the space count; a removed source never warns."""
    known = {s.source_id for s in space.sources}
    found: list[_SourceMatch] = []
    silent = [r for r in rules if r.condition == "source.silent"]
    if silent:
        last_seen = await uow.ingest.source_last_event_at(now - SILENT_LOOKBACK)
        for rule in silent:
            minutes = float(rule.params.get("minutes", 30))
            for source_id in silent_sources(last_seen, minutes, now):
                last = last_seen[source_id]
                evidence = (EvidenceRef("source", source_id, source_id, last),)
                match = ConditionMatch(evidence, last + timedelta(minutes=minutes), silence_episode(last))
                found.append(_SourceMatch(rule, source_id, match))
    gaps = [r for r in rules if r.condition == "source.gap"]
    if gaps:
        # What each source's SDK says it sent, against what arrived, yesterday and today.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days = await uow.ingest.coverage(midnight - timedelta(days=1))
        for rule in gaps:
            for source_id in sorted(days.keys() & known):
                for day in days[source_id]:
                    missing = gap_of(day, rule.params)
                    if missing is None:
                        continue
                    evidence = (EvidenceRef("source", source_id, source_id, now),)
                    detail = {
                        "day": day.day.isoformat(),
                        "sent": day.sent,
                        "received": day.received,
                        "rejected": day.rejected,
                        "missing": missing,
                    }
                    match = ConditionMatch(evidence, now, gap_episode(day.day), detail)
                    found.append(_SourceMatch(rule, source_id, match))
    rated = list(_repetition_rules(rules))
    if not rated:
        return found
    today = now.date()
    widest = max(params.window_days for _, params in rated)
    rows = await uow.measure.daily(today - timedelta(days=widest + REPETITION_LOOKBACK_DAYS), today)
    daily = _questions_by_source(rows)
    for rule, params in rated:
        for source_id in sorted(daily.keys() & known):
            if params.source_id is not None and source_id != params.source_id:
                continue
            start = repetition_run(daily[source_id], today, params)
            if start is None:
                continue
            window = repetition_window(daily[source_id], today, params.window_days)
            detail = {
                "questions": window.questions,
                "repeated": window.repeated,
                "rate": round(window.rate, 4),
                "above": params.above,
                "window_days": params.window_days,
                "above_since": start.isoformat(),
            }
            evidence = (EvidenceRef("source", source_id, source_id, now),)
            # One warning per run above the threshold: the run's first day is the occurrence.
            match = ConditionMatch(evidence, now, f"above_since:{start.isoformat()}", detail)
            found.append(_SourceMatch(rule, source_id, match))
    return found


def _repetition_rules(rules: Iterable[TriggerRule]) -> Iterable[tuple[TriggerRule, RepetitionParams]]:
    for rule in rules:
        if rule.condition != "context_use.repetition_rate":
            continue
        try:
            yield rule, RepetitionParams.of(rule.params)
        except (TypeError, ValueError):
            log.warning("trigger rule %s has unusable parameters; it is not evaluated", rule.rule_id)


def _questions_by_source(rows: Iterable[DailyRow]) -> dict[UUID, dict[date, tuple[int, int]]]:
    """(questions, repeated) per source and day. The control group is left out: it never received
    the context, so what it asks again says nothing about the agent (19.7)."""
    found: dict[UUID, dict[date, tuple[int, int]]] = defaultdict(dict)
    for row in rows:
        if row.key.experiment_group == CONTROL_GROUP:
            continue
        questions, repeated = found[row.key.source_id].get(row.key.day, (0, 0))
        found[row.key.source_id][row.key.day] = (
            questions + row.counters.questions,
            repeated + row.counters.repeated,
        )
    return found


async def _load_state(
    uow: UnitOfWork, profile: ProfileRecord, rules: Sequence[TriggerRule], now: datetime
) -> SubjectState:
    needs = frozenset().union(*(NEEDS[r.condition] for r in rules))
    view = derivation_view(profile)
    handles = list(view.handle_ids)
    traits = await uow.traits.active_for(profile.profile_id) if "traits" in needs else []
    evidence = await uow.traits.evidence_of([t.trait_id for t in traits]) if traits else {}
    if hidden := set(view.hidden_handle_ids):
        # A pattern derived before its evidence was kept apart never fires while the link is unconfirmed.
        traits = [
            t for t in traits if not any(e.origin_handle_id in hidden for e in evidence.get(t.trait_id, ()))
        ]
    return SubjectState(
        now=now,
        handle_ids=frozenset(handles),
        open_items=(
            await uow.memory.open_items_for(handles, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE))
            if "open_items" in needs
            else ()
        ),
        traits=traits,
        trait_evidence=evidence,
        episodes=await uow.memory.episodes_for(handles, since=now - EPISODE_LOOKBACK)
        if "episodes" in needs
        else (),
        handles=await uow.identity.get_handles(handles) if "handles" in needs else (),
        divergent_actions=(
            await uow.objects.divergent_actions_for(handles, since=now - timedelta(days=30))
            if "actions" in needs
            else ()
        ),
        objects=await uow.objects.objects_for(handles) if "objects" in needs else (),
    )


def _pointers(evidence: Sequence[EvidenceRef]) -> list[EvidencePointer]:
    return [EvidencePointer(kind=e.kind, id=str(e.id)) for e in evidence]


def _view(f: Firing) -> FiringView:
    return FiringView(
        firing_id=str(f.firing_id),
        rule_id=f.rule_id,
        rule_version=f.rule_version,
        subject_id=str(f.profile_id),
        window_key=f.window_key,
        fired_at=f.fired_at,
        late=f.late,
        retracted_at=f.retracted_at,
        evidence=_pointers(f.evidence),
    )
