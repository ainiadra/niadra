"""Traits: derive, expire, retract, dry-run and opt out (20).

Derivation is coalesced per profile (`derive_traits`, 5.5): a burst of reconciliations and object
events recomputes the profile's traits once, from the evidence pinned to its current handles, so a
merge or unmerge only needs a new derivation for each profile involved (20.2). Only the confirmed
handles count (`derivation_view`, 5.3): the rows of an unconfirmed link support no pattern until the
link is confirmed, which derives again.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from niadra.app.compile import account_contacts
from niadra.app.notify import enqueue_event
from niadra.config.models import SpaceConfig
from niadra.contracts.traits import (
    EvidencePointer,
    OptOutResponse,
    TraitDryRunResponse,
    TraitSample,
    TraitView,
)
from niadra.domain.common.errors import InvalidInput, NotFound, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.provenance import derivation_view
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.policy import ItemAttrs
from niadra.domain.records import ProfileRecord, Trait
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.barrier import Barrier
from niadra.domain.traits.evidence import EvidenceRef, evidence_keys
from niadra.domain.traits.rules import (
    CATALOG,
    EVENT_RULES,
    DerivedTrait,
    TraitInputs,
    TraitRule,
    derive,
    released_to,
    validate_rule,
    within_hot_window,
)
from niadra.domain.vocabulary import Audience, EventKind, OpenItemStatus, Sensitivity
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

DRY_RUN_DAYS = 90  # 20.5
REDERIVE_PROFILES = 50_000
"""Profiles one rule change re-derives; far above a space's active profiles in a hot window."""
DRY_RUN_PROFILES = 5000
DRY_RUN_SAMPLE = 10
_ITEM_RULES = frozenset({"broken_company_promise"})


def barrier_for(space: SpaceConfig) -> Barrier:
    categories = [*space.policy.categories, *space.extraction.categories]
    episodes = space.extraction.episode_categories
    declared = (
        {*(c.name for c in categories), *(e.name for e in episodes), *(e.normalized_as for e in episodes)}
        if episodes
        else None
    )
    return Barrier.build(
        (c.name for c in categories if c.sensitivity is Sensitivity.SENSITIVE),
        space.settings.trait_allowed_categories,
        declared,
    )


def rules_fingerprint(space: SpaceConfig) -> int:
    """What decides a space's patterns, as a signed 63-bit number for `space_state`: the enabled rules with
    their window, and the categories the barrier keeps out."""
    rules = [(r.rule_id, r.catalog, r.version, json.dumps(r.params, sort_keys=True)) for r in rules_of(space)]
    barrier = sorted(
        c.name
        for c in [*space.policy.categories, *space.extraction.categories]
        if c.sensitivity is Sensitivity.SENSITIVE
    )
    allowed = sorted(space.settings.trait_allowed_categories or [])
    declared = barrier_for(space).declared
    # The declared categories are the default allow-list: declaring one changes what may derive.
    digest = hashlib.sha256(
        json.dumps([rules, barrier, allowed, sorted(declared) if declared is not None else None]).encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def rules_of(space: SpaceConfig) -> list[TraitRule]:
    hot = space.settings.events_hot_days
    return [
        within_hot_window(TraitRule(r.rule_id, r.catalog, r.version, r.params), hot)
        for r in space.trait_rules
        if r.enabled
    ]


class TraitsService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def derive(self, task: Task) -> None:
        if task.profile_id is None:
            raise InvalidInput("derive_traits is a per-profile task")
        space = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            seen = await uow.pipeline.begin_work(task.space_id, task.profile_id, TaskName.DERIVE_TRAITS)
            await self._derive_profile(uow, space, task.profile_id, self._clock.now())
            if await uow.pipeline.finish_work(task.space_id, task.profile_id, TaskName.DERIVE_TRAITS, seen):
                again = Task(
                    TaskName.DERIVE_TRAITS, task.space_id, {"reason": "coalesced"}, profile_id=task.profile_id
                )
                await uow.pipeline.enqueue([again])

    async def watch_rules(self, space_id: UUID) -> int:
        """A per-space loop step: when the space's pattern rules, their window or the categories
        they may read changed since the last derivation, every profile they reach is derived again, not
        only the next one whose memory moves. The first pass only records the rules."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        current = rules_fingerprint(space)
        async with self._uow(space_id) as uow:
            stored = await uow.traits.rules_fingerprint()
            if stored == current:
                return 0
            await uow.traits.set_rules_fingerprint(current)
            if stored is None:
                return 0
            await uow.pipeline.enqueue(
                [Task(TaskName.REDERIVE_TRAITS, space_id, {"reason": "rules_changed"})]
            )
        return 1

    async def rederive_space(self, task: Task) -> None:
        """Asks a coalesced derivation of every profile active within the rules' window and of every
        profile that holds a pattern (one a rule turned off must lose it)."""
        space = self._config.space(task.space_id)
        since = self._clock.now() - timedelta(days=space.settings.events_hot_days)
        async with self._uow(task.space_id) as uow:
            active = await uow.sessions.profiles_active_since(since, limit=REDERIVE_PROFILES)
            holding = await uow.traits.profiles_with_traits(limit=REDERIVE_PROFILES)
            for profile_id in sorted({*active, *holding}):
                await uow.pipeline.request_work(
                    Task(
                        TaskName.DERIVE_TRAITS,
                        task.space_id,
                        {"reason": "rules_changed"},
                        profile_id=profile_id,
                    )
                )

    async def _derive_profile(
        self, uow: UnitOfWork, space: SpaceConfig, profile_id: UUID, now: datetime
    ) -> None:
        existing = await uow.traits.traits_of(profile_id)
        profile = await uow.identity.get_profile(profile_id)
        if profile is None:
            # Merged away: the surviving profile re-derives from the same evidence (20.2).
            for trait in existing:
                await uow.traits.delete_trait(trait.trait_id)
            return
        await self._drop_traits_of_absorbed_profiles(uow, profile)
        rules = rules_of(space)
        derived: list[DerivedTrait] = []
        if rules and not await uow.traits.opted_out(profile.handle_ids):
            inputs = await _inputs(uow, derivation_view(profile), rules, now)
            derived = derive(rules, inputs, barrier_for(space))
        if await self._store(uow, space, profile, existing, derived, now):
            for name in (TaskName.COMPILE, TaskName.TRIGGER):
                await uow.pipeline.request_work(
                    Task(name, space.space_id, {"reason": "traits"}, profile_id=profile_id)
                )
            # A pattern saved again lost its vector with its old value; history search needs a new one.
            await uow.pipeline.enqueue([Task(TaskName.EMBED, space.space_id, profile_id=profile_id)])

    async def _drop_traits_of_absorbed_profiles(self, uow: UnitOfWork, profile: ProfileRecord) -> None:
        for trait in await uow.traits.traits_with_evidence_from(profile.handle_ids):
            if (
                trait.profile_id != profile.profile_id
                and await uow.identity.get_profile(trait.profile_id) is None
            ):
                await uow.traits.delete_trait(trait.trait_id)

    async def _store(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        profile: ProfileRecord,
        existing: Sequence[Trait],
        derived: Sequence[DerivedTrait],
        now: datetime,
    ) -> bool:
        current = {t.name: t for t in existing}
        releases = {r.rule_id: released_to(r.catalog, r.purposes) for r in space.trait_rules}
        subject = WebhookSubject(profile.pseudonym, profile.kind.value)
        changed = False
        for d in derived:
            before = current.get(d.name)
            if before and before.retracted_at and await self._retraction_holds(uow, before, d):
                continue
            trait = _trait_row(profile, d, before, now)
            if before is None or _differs(before, trait, d):
                await uow.traits.save_trait(trait, d.evidence)
                changed = True
            if before is None or before.retracted_at is not None or before.expires_at <= now:
                await _announce(uow, space, "trait.added", subject, trait, d.evidence, releases.get(d.name))
        for name, before in current.items():
            if name not in {d.name for d in derived} and before.retracted_at is None:
                await uow.traits.delete_trait(before.trait_id)
                await _announce(uow, space, "trait.expired", subject, before, (), releases.get(name))
                changed = True
        return changed

    async def _retraction_holds(self, uow: UnitOfWork, retracted: Trait, derived: DerivedTrait) -> bool:
        """A retracted trait stays retracted until evidence it never had shows up (20.4)."""
        old = (await uow.traits.evidence_of([retracted.trait_id])).get(retracted.trait_id, ())
        return evidence_keys(derived.evidence) <= evidence_keys(old)

    async def expire_due(self, now: datetime | None = None) -> int:
        """The 5-minute sweep: profiles with an expired trait are re-derived (20.2)."""
        now = now or self._clock.now()
        requested = 0
        for space in self._config.spaces():
            try:
                async with self._uow(space.space_id) as uow:
                    profiles = sorted({t.profile_id for t in await uow.traits.expiring(now)})
                    for profile_id in profiles:
                        task = Task(
                            TaskName.DERIVE_TRAITS,
                            space.space_id,
                            {"reason": "expiry"},
                            profile_id=profile_id,
                        )
                        await uow.pipeline.request_work(task)
                    requested += len(profiles)
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return requested

    async def retract(self, space_id: UUID, trait_id: UUID) -> TraitView:
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            trait = await uow.traits.get_trait(trait_id)
            if trait is None:
                raise NotFound("trait not found")
            await uow.traits.retract(trait_id, now)
            for name in (TaskName.COMPILE, TaskName.TRIGGER):
                await uow.pipeline.request_work(
                    Task(name, space_id, {"reason": "retraction"}, profile_id=trait.profile_id)
                )
            return _view(trait, retracted_at=now)

    async def opt_out(self, space_id: UUID, profile_id: UUID) -> OptOutResponse:
        """The data subject objects to profiling: no trait is derived from its handles again."""
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            profile = await uow.identity.get_profile(profile_id)
            if profile is None:
                raise NotFound("profile not found")
            await uow.traits.opt_out(profile.handle_ids, now)
            task = Task(TaskName.DERIVE_TRAITS, space_id, {"reason": "opt_out"}, profile_id=profile_id)
            await uow.pipeline.request_work(task)
        return OptOutResponse(profile_id=str(profile_id), opted_out_at=now)

    async def dry_run(self, space_id: UUID, rule: TraitRule) -> TraitDryRunResponse:
        """How many profiles active in the last 90 days would get the trait, with a sample (20.5)."""
        space = self._config.space(space_id)
        barrier = barrier_for(space)
        validate_rule(rule, barrier)
        run = within_hot_window(rule, space.settings.events_hot_days)
        now = self._clock.now()
        evaluated, matching = 0, 0
        sample: list[TraitSample] = []
        async with self._uow(space_id) as uow:
            for profile_id in await active_profile_ids(uow, now - timedelta(days=DRY_RUN_DAYS)):
                profile = await uow.identity.get_profile(profile_id)
                # Who objected to profiling never gets a pattern, so is neither counted nor shown (20.6).
                if profile is None or await uow.traits.opted_out(profile.handle_ids):
                    continue
                evaluated += 1
                found = derive([run], await _inputs(uow, derivation_view(profile), [run], now), barrier)
                if not found:
                    continue
                matching += 1
                if len(sample) < DRY_RUN_SAMPLE:
                    sample.append(
                        TraitSample(
                            profile_id=str(profile_id),
                            pseudonym=profile.pseudonym,
                            value=found[0].value,
                            expires_at=found[0].expires_at,
                            evidence=_pointers(found[0].evidence),
                        )
                    )
        return TraitDryRunResponse(
            rule_id=rule.rule_id,
            window_days=DRY_RUN_DAYS,
            profiles_evaluated=evaluated,
            profiles_matching=matching,
            sample=sample,
        )


async def active_profile_ids(uow: UnitOfWork, since: datetime) -> list[UUID]:
    """The dry-run population: profiles with a session since `since` (20.5, 22.1)."""
    return await uow.sessions.profiles_active_since(since, limit=DRY_RUN_PROFILES)


async def _inputs(
    uow: UnitOfWork, profile: ProfileRecord, rules: Iterable[TraitRule], now: datetime
) -> TraitInputs:
    rules = list(rules)
    catalogs = {r.catalog for r in rules}
    since = now - timedelta(
        days=max(int({**CATALOG[r.catalog].defaults, **r.params}["window_days"]) for r in rules)
    )
    handles = list(profile.handle_ids)
    if profile.kind.is_organization:
        # An account's patterns sum what its contacts said, two contacts at least (21.4), each
        # contact's confirmed handles only (5.3).
        contacts = await account_contacts(uow, profile)
        handles += [h for person, _ in contacts for h in derivation_view(person).handle_ids]
    episodes = await uow.memory.episodes_for(handles, since=since, limit=1000)
    items = (
        await uow.memory.open_items_for(handles, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE))
        if catalogs & _ITEM_RULES
        else []
    )
    events = (
        [
            e
            for e in await uow.ingest.events_for_handles(handles, since, 2000)
            if e.kind is EventKind.SYSTEM_EVENT
        ]
        if catalogs & EVENT_RULES
        else []
    )
    return TraitInputs(now, profile.kind, frozenset(handles), episodes, items, events)


def _trait_row(profile: ProfileRecord, d: DerivedTrait, before: Trait | None, now: datetime) -> Trait:
    alive = before is not None and before.retracted_at is None and before.expires_at > now
    return Trait(
        trait_id=before.trait_id if before else uuid7(),
        profile_id=profile.profile_id,
        membership_version=profile.membership_version,
        name=d.name,
        value=d.value,
        layer=d.layer.value,
        rule_version=d.rule_version,
        first_seen=before.first_seen if alive and before else now,
        last_evidence_at=d.last_evidence_at,
        expires_at=d.expires_at,
        confidence=d.confidence,
        evidence=tuple((e.kind, e.id) for e in d.evidence),
    )


def _differs(before: Trait, after: Trait, d: DerivedTrait) -> bool:
    fields = (
        "value",
        "layer",
        "rule_version",
        "membership_version",
        "expires_at",
        "last_evidence_at",
        "retracted_at",
    )
    return any(getattr(before, f) != getattr(after, f) for f in fields) or set(
        before.evidence
    ) != evidence_keys(d.evidence)


async def _announce(
    uow: UnitOfWork,
    space: SpaceConfig,
    event_type: str,
    subject: WebhookSubject,
    trait: Trait,
    evidence: Sequence[EvidenceRef],
    release: tuple[frozenset[str], frozenset[Audience]] | None,
) -> None:
    data = {
        "trait_id": str(trait.trait_id),
        "name": trait.name,
        "rule_version": trait.rule_version,
        "layer": trait.layer,
        "expires_at": trait.expires_at.isoformat(),
        "evidence": [{"kind": e.kind, "id": str(e.id)} for e in evidence],
    }
    # A trait released to some purposes or audiences (overdue invoices: billing) reaches only the
    # endpoints whose source the policy lets read it, exactly as in a context read (20.3).
    purposes, audiences = release or (frozenset(), frozenset())
    item = ItemAttrs("trait", "trait", purposes=purposes, audiences=audiences)
    links = {"open": f"/v1/profiles/{trait.profile_id}/memory"}
    await enqueue_event(uow, space, event_type, subject, data, links=links, item=item)


def _pointers(evidence: Iterable[EvidenceRef]) -> list[EvidencePointer]:
    return [EvidencePointer(kind=e.kind, id=str(e.id)) for e in evidence]


def _view(trait: Trait, *, retracted_at: datetime | None = None) -> TraitView:
    return TraitView(
        trait_id=str(trait.trait_id),
        profile_id=str(trait.profile_id),
        name=trait.name,
        layer=trait.layer,
        rule_version=trait.rule_version,
        first_seen=trait.first_seen,
        last_evidence_at=trait.last_evidence_at,
        expires_at=trait.expires_at,
        confidence=trait.confidence,
        retracted_at=retracted_at or trait.retracted_at,
        evidence=[EvidencePointer(kind=k, id=str(i)) for k, i in trait.evidence],
    )
