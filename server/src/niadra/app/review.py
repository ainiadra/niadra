"""Review by sample (22.2): sampling, the reviewer's queue, verdicts and the agreement metric."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import UUID

from niadra.app.extract import extractor_key
from niadra.app.profile_view import ConsoleView
from niadra.config.models import SpaceConfig
from niadra.config.starter import DEFAULT_PURPOSE
from niadra.contracts.measure import RateOut
from niadra.contracts.review import (
    AgreementOut,
    AgreementReport,
    CaseTally,
    ReviewCase,
    ReviewCases,
    ReviewContradiction,
    ReviewFact,
    ReviewOpenItem,
    ReviewQueue,
    ReviewSampleOut,
    ReviewSignal,
    ReviewTrait,
    VerdictsRequest,
    VerdictsResponse,
)
from niadra.contracts.traits import EvidencePointer
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal
from niadra.domain.compile import MemorySnapshot
from niadra.domain.extract.result import ExtractionRun
from niadra.domain.measure.access import can_see, visible_sources
from niadra.domain.measure.aggregate import wilson
from niadra.domain.measure.model import ContextUseRow
from niadra.domain.memory.facts import Relation
from niadra.domain.memory.items import evidence_event_ids
from niadra.domain.policy import Effect
from niadra.domain.records import Fact, OpenItem, ProfileRecord, SessionRecord, Trait
from niadra.domain.review.sampling import (
    MEASURE_SIGNAL,
    NEW_EXTRACTOR,
    NEW_SOURCE,
    NEW_TRAIT,
    SENSITIVE,
    ReviewSampleRecord,
    SamplingPolicy,
    draw,
    sampling_condition,
    should_sample,
    wants_second_review,
)
from niadra.domain.review.verdicts import (
    SIGNALS,
    CaseRun,
    TargetKind,
    agreement,
    contradiction_target,
    is_valid,
    item_case,
    kinds_of,
    scoped,
    signal_case,
    visible_to,
)
from niadra.domain.tasks import Task
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, RelationType, Sensitivity
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

SECOND_REVIEW_WITHIN = timedelta(days=30)
CASE_SAMPLES = 200
"""The newest reviewed samples a run of the cases reads, in one short transaction."""
CASES_SHOWN = 50
NEW_SOURCE_LOOKBACK = timedelta(days=30)
NEW_SOURCE_WITHIN = timedelta(days=14)
NEW_EXTRACTOR_WITHIN = timedelta(days=7)
NEW_TRAIT_WITHIN = timedelta(days=7)
SECOND_REVIEW_CANDIDATES = 200


class ReviewService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def sample_review(self, task: Task) -> None:
        session_id = UUID(task.payload["session_id"])
        config = self._config.space(task.space_id)
        policy = SamplingPolicy(**config.review_sampling.model_dump())
        now = self._clock.now()
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            if session is None or await uow.review.has_sample(session_id):
                return
            met = set(task.payload.get("conditions") or ())
            if task.payload.get(MEASURE_SIGNAL):
                met.add(MEASURE_SIGNAL)
            run = await uow.extract.latest_run(session_id)
            if _touches_sensitive(run, config):
                met.add(SENSITIVE)
            met |= await _novelties(uow, config, session, run, now)
            condition = sampling_condition(met, policy)
            taken = await uow.review.count_since(datetime.combine(now.date(), time.min, now.tzinfo))
            if not should_sample(condition, draw(task.space_id, session_id), policy, taken):
                return
            rows = await uow.measure.rows_for_session(session_id)
            if not _judgeable(await _left_by(uow, session), rows):
                return  # nothing a reviewer could judge: it would wait in the queue for ever
            source = config.source(session.source_id)
            # The source's own purpose first: the default one (zero touch) a source that declares none reads
            # for says nothing about what this source does.
            own = (source.purposes - {DEFAULT_PURPOSE}) or source.purposes if source else frozenset()
            purpose = min(own) if own else None
            sample = ReviewSampleRecord(
                uuid7(),
                session_id,
                condition,
                purpose,
                now,
                profile_id=session.profile_id,
                conversation_id=session.conversation_id,
            )
            await uow.review.add_sample(sample)

    async def queue(self, principal: Principal, limit: int = 10) -> ReviewQueue:
        config = self._config.space(principal.space.space_id)
        async with self._uow(principal.space.space_id) as uow:
            items = []
            # A few slots go to samples another person judged, so agreement between reviewers has pairs.
            for sample in await self._second_reviews(uow, principal, max(1, limit // 10)):
                if (shown := await self._sample_view(uow, config, principal, sample)) is not None:
                    items.append(shown)
            for sample in await uow.review.pending(limit * 4):
                if len(items) == limit:
                    break
                shown = await self._sample_view(uow, config, principal, sample)
                if shown is not None:
                    items.append(shown)
        return ReviewQueue(items=items)

    async def _second_reviews(
        self, uow: UnitOfWork, principal: Principal, slots: int
    ) -> list[ReviewSampleRecord]:
        if not isinstance(principal, PersonPrincipal):
            return []  # only a person records verdicts
        since = self._clock.now() - SECOND_REVIEW_WITHIN
        candidates = await uow.review.reviewed_since(since, SECOND_REVIEW_CANDIDATES)
        return [s for s in candidates if wants_second_review(s, principal.user_id)][:slots]

    async def record_verdicts(
        self, reviewer: PersonPrincipal, sample_id: UUID, request: VerdictsRequest
    ) -> VerdictsResponse:
        for v in request.verdicts:
            if not is_valid(v.target, v.verdict):
                raise InvalidInput(f"verdict `{v.verdict}` is not valid for a {v.target.value}")
        now = self._clock.now()
        async with self._uow(reviewer.space.space_id) as uow:
            sample = await uow.review.get_sample(sample_id)
            session = await uow.sessions.get_session(sample.session_id) if sample else None
            if sample is None or session is None or not can_see(reviewer, session.source_id):
                raise NotFound("sample not found")
            entries = [
                {
                    "target": v.target.value,
                    "target_id": v.target_id,
                    "verdict": v.verdict,
                    "reviewer_id": str(reviewer.user_id),
                    # The agreement a vendor reads is the agreement on its own agents (19.4).
                    "source_id": str(session.source_id),
                    "at": now.isoformat(),
                }
                for v in request.verdicts
            ]
            # Only the verdicts are stored: memory changes go through feedback or retract (22.2).
            await uow.review.add_verdicts(sample_id, entries, reviewer.user_id, now)
        return VerdictsResponse(sample_id=sample_id, recorded=len(entries), reviewed_at=now)

    async def agreement(
        self, principal: Principal, *, days: int = 90, source_id: UUID | None = None
    ) -> AgreementReport:
        if source_id is not None and not can_see(principal, source_id):
            raise NotFound("source not found")
        since = self._clock.now() - timedelta(days=days)
        async with self._uow(principal.space.space_id) as uow:
            found = await uow.review.verdicts_since(since)
        verdicts = scoped(found, visible_sources(principal), source_id)
        targets = [
            AgreementOut(
                target=kind,
                judged=a.judged,
                agreed=a.agreed,
                system_agreement=RateOut.of(wilson(a.agreed, a.judged)),
                reviewer_pairs=a.reviewer_pairs,
                reviewer_agreement=RateOut.of(wilson(a.reviewer_pairs_agreeing, a.reviewer_pairs)),
            )
            for kind, a in sorted(agreement(verdicts).items())
        ]
        return AgreementReport(since=since, targets=targets)

    async def cases(self, principal: Principal, *, days: int = 90) -> ReviewCases:
        """Every verdict of the period run as a test against today's memory and measurement: a
        new extractor or new measurement rules show at once which judged cases they now answer otherwise."""
        since = self._clock.now() - timedelta(days=days)
        visible = visible_sources(principal)
        runs: list[tuple[ReviewSampleRecord, dict[str, object], CaseRun]] = []
        async with self._uow(principal.space.space_id) as uow:
            for sample in await uow.review.reviewed_since(since, CASE_SAMPLES):
                verdicts = list(scoped(sample.verdicts, visible))
                if not verdicts:
                    continue
                kinds = {str(v["target"]) for v in verdicts}
                marks = (
                    {
                        (row.manifest_hash, str(item["id"]), signal): bool(item.get(signal))
                        for row in await uow.measure.rows_for_session(sample.session_id)
                        for item in row.items
                        for signal in SIGNALS
                    }
                    if TargetKind.SIGNAL in kinds
                    else {}
                )
                standing = (
                    await _standing(uow, sample) if kinds & {TargetKind.FACT, TargetKind.OPEN_ITEM} else {}
                )
                for verdict in verdicts:
                    target = str(verdict["target"])
                    if target == TargetKind.SIGNAL:
                        run = signal_case(verdict, marks)
                    elif target in (TargetKind.FACT, TargetKind.OPEN_ITEM):
                        run = item_case(verdict, standing.get(str(verdict["target_id"])))
                    else:
                        continue
                    runs.append((sample, dict(verdict), run))
        tallies: dict[str, list[bool]] = {}
        for _, verdict, run in runs:
            if run.passes is not None:
                tallies.setdefault(kinds_of(verdict)[-1], []).append(run.passes)
        failing = [
            ReviewCase(
                sample_id=sample.sample_id,
                target=str(verdict["target"]),
                target_id=str(verdict["target_id"]),
                verdict=str(verdict["verdict"]),
                now=run.now,
                passes=run.passes,
            )
            for sample, verdict, run in runs
            if run.passes is False
        ]
        return ReviewCases(
            since=since,
            tallies=[
                CaseTally(target=t, cases=len(r), passing=sum(r), failing=len(r) - sum(r))
                for t, r in sorted(tallies.items())
            ],
            failing=failing[:CASES_SHOWN],
        )

    async def _sample_view(
        self, uow: UnitOfWork, config: SpaceConfig, principal: Principal, sample: ReviewSampleRecord
    ) -> ReviewSampleOut | None:
        session = await uow.sessions.get_session(sample.session_id)
        if session is None or not can_see(principal, session.source_id):
            return None
        rows = await uow.measure.rows_for_session(session.session_id)
        left = await _left_by(uow, session)
        if not _judgeable(left, rows):
            # Its memory was erased or retired since it was sampled: no verdict can be recorded on it, so it
            # leaves the queue instead of holding a place in it.
            if sample.reviewed_at is None:
                await uow.review.drop_sample(sample.sample_id)
            return None
        facts, open_items, traits, contradictions, withheld = await _session_items(
            uow, config, principal, left, sample.purpose
        )
        signals = [
            ReviewSignal(
                target_id=f"{row.manifest_hash}:{item['id']}:{signal}",
                manifest_hash=row.manifest_hash,
                item_id=str(item["id"]),
                signal=signal,
            )
            for row in rows
            if can_see(principal, row.source_id)
            for item in row.items
            for signal in SIGNALS
            if item.get(signal)
        ]
        return ReviewSampleOut(
            sample_id=sample.sample_id,
            session_id=session.session_id,
            source_id=session.source_id,
            channel=session.channel,
            condition=sample.condition,
            purpose=sample.purpose,
            created_at=sample.created_at,
            facts=facts,
            open_items=open_items,
            traits=traits,
            signals=signals,
            contradictions=contradictions,
            withheld=withheld,
        )


@dataclass(frozen=True, slots=True)
class _Left:
    """What a session left in its customer's memory, before any reviewer's policy: the facts whose evidence
    lies in it, the pending items and promises it opened and the patterns that rest on it."""

    profile: ProfileRecord
    every: Sequence[Fact]
    facts: Sequence[Fact]
    items: Sequence[OpenItem]
    traits: Sequence[Trait]
    in_session: frozenset[UUID]
    here: frozenset[UUID]


async def _left_by(uow: UnitOfWork, session: SessionRecord) -> _Left | None:
    """None for a session of no customer, or of one who is gone."""
    if session.profile_id is None:
        return None
    profile = await uow.identity.get_profile(session.profile_id)
    if profile is None:
        return None
    events = await uow.ingest.events_between(
        conversation_id=session.conversation_id,
        task_id=session.task_id,
        since=session.opened_at,
        until=session.closed_at or session.last_event_at,
    )
    in_session = frozenset(e.event_id for e in events)
    every = await uow.memory.facts_for(profile.handle_ids, statuses=tuple(FactStatus))
    items = [
        i
        for i in await uow.memory.open_items_for(profile.handle_ids, statuses=tuple(OpenItemStatus))
        if i.origin.get("session_id") == str(session.session_id)
    ]
    episodes = {
        e.episode_id
        for e in await uow.memory.episodes_for(profile.handle_ids, since=session.opened_at, limit=50)
        if e.session_id == session.session_id
    }
    here = frozenset({*episodes, *in_session, *(i.item_id for i in items)})
    return _Left(
        profile=profile,
        every=every,
        facts=[f for f in every if in_session.intersection(f.evidence_event_ids)],
        items=items,
        traits=[
            t for t in await uow.traits.traits_of(profile.profile_id) if any(r in here for _, r in t.evidence)
        ],
        in_session=in_session,
        here=here,
    )


def _judgeable(left: _Left | None, rows: Sequence[ContextUseRow]) -> bool:
    """Whether a sample of the session has anything a verdict can be recorded on, for some reviewer: a
    contradiction always has a fact of the session, so the facts answer for the pairs."""
    if left is not None and (left.facts or left.items or left.traits):
        return True
    return any(item.get(signal) for row in rows for item in row.items for signal in SIGNALS)


async def _session_items(
    uow: UnitOfWork, config: SpaceConfig, principal: Principal, left: _Left | None, purpose: str | None
) -> tuple[list[ReviewFact], list[ReviewOpenItem], list[ReviewTrait], list[ReviewContradiction], int]:
    """What the session left, each item shown only when both the space's policy, for the reviewer as the
    Console reads (5.22), and the sample's purpose let it through whole; the rest count as withheld. A fact
    of a category the policy never declared is released to nobody, reviewers included."""
    if left is None:
        return [], [], [], [], 0
    profile, every, facts, items, traits = left.profile, left.every, left.facts, left.items, left.traits
    in_session, here = left.in_session, left.here
    purposes = {c.name: c.purposes for c in config.policy.categories}
    sensitive = _sensitive_categories(config)
    pairs = _live_pairs(await uow.memory.relations_for(profile.handle_ids), every, {f.fact_id for f in facts})
    paired = {f.fact_id for newer, older, _ in pairs for f in (newer, older)}
    others = [f for f in every if f.fact_id in paired and not in_session.intersection(f.evidence_event_ids)]
    snapshot = MemorySnapshot(
        profile=profile, facts=(*facts, *others), open_items=tuple(items), traits=tuple(traits)
    )
    view = ConsoleView.of(principal, config, snapshot)
    shown, withheld = [], 0
    for fact in facts:
        whole = view.effect(f"fact:{fact.fact_id}") is Effect.ALLOW
        if not whole or not visible_to(purpose, fact.category, purposes):
            withheld += 1
            continue
        shown.append(
            ReviewFact(
                fact_id=fact.fact_id,
                predicate=fact.predicate,
                value=fact.value,
                category=fact.category,
                evidence_event_ids=[e for e in fact.evidence_event_ids if e in in_session],
                sensitive=fact.sensitivity is Sensitivity.SENSITIVE or fact.category in sensitive,
            )
        )
    open_items = []
    for item in items:
        if view.effect(f"open_item:{item.item_id}") is not Effect.ALLOW:
            withheld += 1
            continue
        open_items.append(
            ReviewOpenItem(
                item_id=item.item_id,
                description=item.description,
                owner="company" if item.owner == "company" else "customer",
                promise=item.owner == "company",
                status=item.status.value,
                due_at=item.due_at,
                evidence_event_ids=[e for e in evidence_event_ids(item) if e in in_session],
            )
        )
    patterns = []
    for trait in traits:
        if view.effect(f"trait:{trait.trait_id}") is not Effect.ALLOW:
            withheld += 1
            continue
        patterns.append(
            ReviewTrait(
                trait_id=trait.trait_id,
                name=trait.name,
                value=trait.value,
                first_seen=trait.first_seen,
                confidence=trait.confidence,
                evidence=[EvidencePointer(kind=k, id=str(ref)) for k, ref in trait.evidence if ref in here],
            )
        )
    conflicts = []
    for newer, older, origin in pairs:
        if not all(
            view.effect(f"fact:{f.fact_id}") is Effect.ALLOW and visible_to(purpose, f.category, purposes)
            for f in (newer, older)
        ):
            withheld += 1
            continue
        conflicts.append(
            ReviewContradiction(
                target_id=contradiction_target(newer.fact_id, older.fact_id, origin),
                fact_id=newer.fact_id,
                other_fact_id=older.fact_id,
                origin=origin,
                predicate=newer.predicate,
                value=newer.value,
                other_value=older.value,
            )
        )
    return shown, open_items, patterns, conflicts, withheld


def _live_pairs(
    edges: Sequence[Relation], facts: Sequence[Fact], here: set[UUID]
) -> list[tuple[Fact, Fact, str]]:
    """The `CONTRADICTS` pairs with a fact of this conversation whose two facts are still active: a pair a
    person settled through feedback leaves the queue."""
    active = {f.fact_id: f for f in facts if f.status is FactStatus.ACTIVE}
    found: dict[tuple[UUID, UUID, str], tuple[Fact, Fact, str]] = {}
    for edge in edges:
        a, b = active.get(edge.a.id), active.get(edge.b.id)
        if edge.type is RelationType.CONTRADICTS and a and b and {a.fact_id, b.fact_id} & here:
            found.setdefault((a.fact_id, b.fact_id, edge.origin), (a, b, edge.origin))
    return [found[k] for k in sorted(found, key=lambda k: (str(k[0]), str(k[1]), k[2]))]


async def _novelties(
    uow: UnitOfWork, config: SpaceConfig, session: SessionRecord, run: ExtractionRun | None, now: datetime
) -> set[str]:
    """The boosts of 22.2 that come from something new: a source that started sending in the last two
    weeks, an extractor that changed in the last week (the session was extracted by it), and a pattern of
    the customer first derived in the last week."""
    met: set[str] = set()
    days = (await uow.ingest.coverage(now - NEW_SOURCE_LOOKBACK)).get(session.source_id, [])
    if days and min(d.day for d in days) >= (now - NEW_SOURCE_WITHIN).date():
        met.add(NEW_SOURCE)
    _, changed_at = await uow.extract.extractor_state()
    extracted_now = run is not None and run.versions_key.split("#", 1)[0] == extractor_key(config)
    if changed_at is not None and now - changed_at <= NEW_EXTRACTOR_WITHIN and extracted_now:
        met.add(NEW_EXTRACTOR)
    if session.profile_id is not None and any(
        t.retracted_at is None and now - t.first_seen <= NEW_TRAIT_WITHIN
        for t in await uow.traits.traits_of(session.profile_id)
    ):
        met.add(NEW_TRAIT)
    return met


async def _standing(uow: UnitOfWork, sample: ReviewSampleRecord) -> dict[str, bool]:
    """Whether each fact and open item of the sample's customer still stands: active or open, not gone."""
    if sample.profile_id is None:
        return {}
    profile = await uow.identity.get_profile(sample.profile_id)
    if profile is None:
        return {}
    facts = await uow.memory.facts_for(profile.handle_ids, statuses=tuple(FactStatus))
    items = await uow.memory.open_items_for(profile.handle_ids, statuses=tuple(OpenItemStatus))
    return {
        **{str(f.fact_id): f.status is FactStatus.ACTIVE for f in facts},
        # An open item that was resolved still happened; one taken back leaves memory.
        **{str(i.item_id): True for i in items},
    }


def _touches_sensitive(run: ExtractionRun | None, config: SpaceConfig) -> bool:
    facts = run.result.facts if run and run.result else ()
    sensitive = _sensitive_categories(config)
    return any(f.sensitivity is Sensitivity.SENSITIVE or f.category in sensitive for f in facts)


def _sensitive_categories(config: SpaceConfig) -> frozenset[str]:
    rules = [*config.policy.categories, *config.extraction.categories]
    return frozenset(c.name for c in rules if c.sensitivity is Sensitivity.SENSITIVE)
