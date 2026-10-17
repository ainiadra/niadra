"""Context-use measurement (19): measures each extracted session and serves the aggregates to governance."""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.app.notify import enqueue_event
from niadra.config.models import SpaceConfig
from niadra.contracts.measure import (
    ClaimRates,
    ConstraintRates,
    ContextTrimOut,
    ContextUseBucket,
    ContextUseDetail,
    ContextUseEntry,
    ContextUseReport,
    CostRates,
    EstimateOut,
    GatedTurnsOut,
    GuardTypeUse,
    LearnedViewOut,
    MeasuredConversation,
    MeasuredConversationPage,
    RateOut,
    RetrievalWeightsOut,
    SignalReview,
    SlotChannelUse,
    TrimUnitOut,
    ViewBudgetsOut,
    ViewRunOut,
    WeightRunOut,
)
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal
from niadra.domain.compile.model import slot_budget
from niadra.domain.compile.views import (
    LEARNED_SECTIONS,
    SLOTS,
    default_ceilings,
    learned_ceilings,
    learned_slot_budget,
)
from niadra.domain.extract.minimal import is_agent_task
from niadra.domain.extract.result import ExtractionRun, RunStatus
from niadra.domain.measure.access import can_see, visible_sources
from niadra.domain.measure.agents import ClaimTally, ConstraintTally, CostTally, costs, tallies
from niadra.domain.measure.aggregate import (
    CUSTOMER_INITIATED,
    DIMENSIONS,
    RECONTACT_WINDOW,
    backing_counters,
    group_daily,
    per_thousand,
    recontact_scope,
    row_counters,
    session_counters,
    session_key,
    wilson,
)
from niadra.domain.measure.inputs import (
    actions_in,
    agent_answers,
    agent_turns,
    claims_from,
    context_stamps,
    deliveries_from_receipts,
    episode_category,
    fact_ids,
    first_agent_turns,
    handoffs_in,
    manifest_from_json,
    questions_from,
    reads_by_source,
    slot_deliveries_from_receipts,
)
from niadra.domain.measure.model import (
    MEASURE_RULES,
    SESSION_LEVEL_VIEW,
    AgentAnswer,
    ContextUseRow,
    DailyCounters,
    DailyKey,
    Delivery,
    DeliveryOutcome,
    ItemOutcome,
    Manifest,
    ManifestItem,
    MeasuredSession,
    NotMeasured,
    RecontactScope,
    SessionAction,
    TransferState,
)
from niadra.domain.measure.signals import (
    TRANSFER_WINDOW,
    MeasureRules,
    SessionEvidence,
    measure_deliveries,
    transfer_state,
)
from niadra.domain.measure.slots import (
    GATED,
    VALUE_KIND,
    GuardCounters,
    GuardDailyKey,
    GuardDailyRow,
    MeasuredSlots,
    SlotDailyKey,
    SlotDailyRow,
    SlotDelivery,
    attach,
    group_slot_daily,
    guard_counters,
    measure_slots,
    slot_counters,
    with_guard_violations,
    without_slotted_misses,
)
from niadra.domain.measure.trim import (
    ContextTrim,
    UnitKey,
    active_units,
    derive_units,
    totals,
    unit_counters,
    window,
)
from niadra.domain.measure.views import (
    CONTROL_FRACTION,
    VIEWS_RULES,
    ViewBudgets,
    ViewUseKey,
    learn_views,
    learned_variant,
    target_of,
    view_use_counters,
)
from niadra.domain.measure.views import arm as views_arm
from niadra.domain.measure.views import window_counters as view_window_counters
from niadra.domain.measure.weights import RULES as WEIGHT_RULES
from niadra.domain.measure.weights import (
    RetrievalWeights,
    WeightOutcome,
    control_guard,
    learn,
    window_counters,
)
from niadra.domain.memory.bound import MAX_HANDLES, violates
from niadra.domain.notify.webhooks import WebhookSubject, external_reference
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.records import EventRecord, ObjectKey, SessionRecord
from niadra.domain.review.sampling import UNBACKED_VALUE
from niadra.domain.review.verdicts import SIGNALS, agreement, scoped
from niadra.domain.serve.guards import GUARDS_ARM, short_id
from niadra.domain.serve.slots import DEFAULT_WEIGHTS
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, Feature
from niadra.ports.infra import Clock, ConfigProvider, NoTelemetry, Telemetry
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

RESCHEDULE_AFTER = timedelta(seconds=30)
MAX_WAITS = 40
# The agent may read the context just before the first event of the session is stored.
DELIVERY_SLACK = timedelta(minutes=2)
DEFAULT_PERIOD = timedelta(days=29)
REVIEW_WINDOW = timedelta(days=89)
"""The verdicts beside a report are those of its last 90 days: a 400-day report never loads a year of them."""
WEIGHTS_HOUR = 3
"""Local hour after which the nightly weights run, with the nightly consolidation (5.9)."""
WEIGHTS_EVERY_S = 600.0
WEIGHT_RUNS_SHOWN = 14


class MeasureService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: ConfigProvider,
        *,
        telemetry: Telemetry | None = None,
        defaults: Mapping[str, float] = DEFAULT_WEIGHTS,
        instructions: ObjectInstructions | None = None,
    ) -> None:
        """`defaults` are the slot fusion's default weights of the cell; `instructions`, what an agent's
        messages are checked against."""
        self._uow = uow
        self._clock = clock
        self._config = config
        self._telemetry = telemetry or NoTelemetry()
        self._defaults = defaults
        self.instructions: ObjectInstructions = instructions or NoObjectInstructions()

    async def measure(self, task: Task) -> None:
        session_id = UUID(task.payload["session_id"])
        watermark = await self._watermark()
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            if session is None or session.closed_at is None:
                return
            receipt_gap = False
            if not _stored_until(watermark, session.closed_at):
                if _waits(task) < MAX_WAITS:
                    await _reschedule(
                        uow, task, self._clock.now() + RESCHEDULE_AFTER, DelayReason.RECEIPT_WATERMARK
                    )
                    return
                receipt_gap = True
            run = await uow.extract.latest_run(session_id)
            version = _measure_version(run)
            previous = await uow.measure.get_session(session_id)
            if previous is not None and previous.measure_version == version:
                return
            config = self._config.space(task.space_id)
            measured = await _Measurement(
                uow, config, session, run, version, receipt_gap, self._clock.now(), self.instructions
            ).run(previous)
        if measured and previous is None:
            # A new extractor measures the session again; only the first measurement says how long it took.
            self._telemetry.measurement_ready((self._clock.now() - session.closed_at).total_seconds())

    async def measure_transfer(self, task: Task) -> None:
        session_id = UUID(task.payload["session_id"])
        watermark = await self._watermark()
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            measured = await uow.measure.get_session(session_id)
            if session is None or measured is None or measured.transfer_state is not None:
                return
            handoffs = handoffs_in(await _session_events(uow, session))
            due = max((h.at for h in handoffs), default=measured.ended_at) + TRANSFER_WINDOW
            waiting = handoffs and not _stored_until(watermark, due)
            if waiting and _waits(task) < MAX_WAITS:
                due_at = max(due, self._clock.now()) + RESCHEDULE_AFTER
                await _reschedule(uow, task, due_at, DelayReason.RECEIPT_WATERMARK)
                return
            since = min((h.at for h in handoffs), default=measured.ended_at) - TRANSFER_WINDOW
            receipts = await uow.audit.receipts_for_conversation(_thread_id(session), since=since)
            integrated = frozenset(s.source_id for s in self._config.space(task.space_id).sources)
            state = transfer_state(handoffs, reads_by_source(receipts), integrated)
            if not await uow.measure.set_transfer_state(session_id, state):
                return
            if state in (TransferState.READ, TransferState.UNREAD):
                unread = state is TransferState.UNREAD
                counters = DailyCounters(transfers=1, transfers_unread=int(unread))
                await uow.measure.add_daily(session_key(measured, SESSION_LEVEL_VIEW), counters)
                for source_id in {h.from_source_id for h in handoffs}:
                    await uow.measure.mark_transfer(session_id, source_id, unread)

    async def _watermark(self) -> datetime | None:
        """The cell's receipt watermark, from the cell database, read before the space transaction: it
        only moves forward, so an earlier read can only make a measurement wait, never go wrong."""
        async with self._uow.cell() as uow:
            return await uow.watermarks.watermark()

    async def context_use(
        self,
        principal: Principal,
        *,
        since: date | None = None,
        until: date | None = None,
        group_by: Sequence[str] = ("source_id",),
        source_id: UUID | None = None,
        channel: str | None = None,
        view: str | None = None,
        experiment_group: str | None = None,
    ) -> ContextUseReport:
        until = until or self._clock.now().date()
        since = since or until - DEFAULT_PERIOD
        if since > until or (until - since).days > 400:
            raise InvalidInput("the period must be ordered and at most 400 days")
        if set(group_by) - set(DIMENSIONS):
            raise InvalidInput("group_by accepts " + ", ".join(DIMENSIONS))
        # A report sums conversations and names none: an analyst key reads it across the sources (19.4).
        visible = allowed = visible_sources(principal, aggregate=True)
        if source_id is not None:
            if not can_see(principal, source_id, aggregate=True):
                raise NotFound("source not found")
            allowed = frozenset({source_id})
        features = self._config.space(principal.space.space_id).features.enabled
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.measure.daily(since, until, allowed)
            # The turns' daily totals: what agents claimed and cost, and what their tools honored (24.6.9).
            turn_days = (
                await uow.turns.daily(since, until, source_id)
                if features & {Feature.CLAIMS, Feature.SIGNALS, Feature.TURNS}
                else []
            )
            reviewed_since = max(since, until - REVIEW_WINDOW)
            verdicts = await uow.review.verdicts_since(datetime.combine(reviewed_since, time.min, UTC))
            trims = await uow.measure.trims()
            slot_rows = await uow.measure.slot_daily(since, until, allowed)
            guard_rows = await uow.measure.guard_daily(since, until, allowed)
            runs = await uow.measure.weight_runs(WEIGHT_RUNS_SHOWN)
            view_runs = await uow.measure.view_budget_runs(WEIGHT_RUNS_SHOWN)
        in_period = (
            v for v in verdicts if reviewed_since <= datetime.fromisoformat(str(v["at"])).date() <= until
        )
        filters = {"channel": channel, "view": view, "experiment_group": experiment_group}
        rows = [r for r in rows if all(v is None or getattr(r.key, k) == v for k, v in filters.items())]
        total = DailyCounters()
        for row in rows:
            total.add(row.counters)
        review = _signal_review(scoped(in_period, visible, source_id), total)
        config = self._config.space(principal.space.space_id)
        vendors = {s.source_id: s.vendor for s in config.sources}
        grouped = group_daily(rows, group_by, vendors)
        buckets = [
            _bucket(dict(zip(group_by, key, strict=True)), c)
            for key, c in sorted(grouped.items(), key=_sort_key)
        ]
        shown = [
            _trim_out(config, t)
            for t in trims
            if t.units and (allowed is None or t.source_id in allowed) and (view is None or t.view == view)
        ]
        slot_rows = [
            r for r in slot_rows if all(v is None or getattr(r.key, k) == v for k, v in filters.items())
        ]
        current = runs[0] if runs else None
        claim_tallies, constraint_tallies = tallies(turn_days, allowed)
        return ContextUseReport(
            since=since,
            until=until,
            group_by=list(group_by),
            buckets=buckets,
            review=review,
            trims=shown,
            slot_channels=_slot_channels(slot_rows, current, self._defaults),
            retrieval_weights=_weights_out(runs, self._defaults),
            guards=_guard_types(guard_rows),
            gated_turns=_gated_turns(slot_rows),
            view_budgets=_views_out(view_runs, allowed, source_id),
            claims=[_claims_out(t) for t in claim_tallies] if Feature.CLAIMS in features else None,
            constraints=(
                [_constraints_out(t) for t in constraint_tallies] if Feature.SIGNALS in features else None
            ),
            cost=_costs_out(config, costs(turn_days, allowed)) if Feature.TURNS in features else None,
        )

    async def learn_weights(self, space_id: UUID) -> int:
        """A per-space loop step (11, 3.4): once a day, after 03:00 in the space's time zone, a space moves
        its retrieval weights by the slots' verdicts of the last seven complete days, within the band and
        under the control group's veto. Returns 1 when a weight moved."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        now = self._clock.now()
        local = now.astimezone(ZoneInfo(config.settings.timezone))
        if local.hour < WEIGHTS_HOUR:
            return 0
        today = local.date()
        until = today - timedelta(days=1)
        since = until - timedelta(days=WEIGHT_RULES.window_days - 1)
        async with self._uow(space_id) as uow:
            current = await uow.measure.retrieval_weights()
            if current is not None and current.day >= today:
                return 0
            counters = window_counters(await uow.measure.slot_daily(since, until))
            guard = control_guard(
                await uow.measure.daily(since, until),
                current.baseline_lift if current else None,
                WEIGHT_RULES,
            )
            run = learn(
                current,
                counters,
                guard,
                run_id=uuid7(),
                day=today,
                now=now,
                rules=WEIGHT_RULES,
                defaults=self._defaults,
            )
            await uow.measure.put_retrieval_weights(run)
        if run.outcome is not WeightOutcome.NO_VERDICTS:
            log.info(
                "retrieval weights: %s, version %d, guard %s",
                run.outcome.value,
                run.version,
                guard.state.value,
                extra={"space_id": str(space_id)},
            )
        return int(run.outcome is WeightOutcome.APPLIED)

    async def learn_views(self, space_id: UUID) -> int:
        """A per-space loop step (13, 6.3): once a day, after 03:00 in the space's time zone, a space moves
        each reader's learned view by what its agents used and missed over the last
        seven complete days, within the band and under both control groups. Returns 1 when one moved."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        now = self._clock.now()
        local = now.astimezone(ZoneInfo(config.settings.timezone))
        if local.hour < WEIGHTS_HOUR:
            return 0
        today = local.date()
        until = today - timedelta(days=1)
        since = until - timedelta(days=VIEWS_RULES.window_days - 1)
        async with self._uow(space_id) as uow:
            current = await uow.measure.view_budgets()
            if current is not None and current.day >= today:
                return 0
            counters = view_window_counters(await uow.measure.view_use_daily(since, until))
            guard = control_guard(
                await uow.measure.daily(since, until),
                current.baseline_lift if current else None,
                VIEWS_RULES.steps,
            )
            run = learn_views(current, counters, guard, run_id=uuid7(), day=today, now=now)
            await uow.measure.put_view_budgets(run)
        if run.outcome is not WeightOutcome.NO_VERDICTS:
            log.info(
                "learned views: %s, version %d, guard %s",
                run.outcome.value,
                run.version,
                guard.state.value,
                extra={"space_id": str(space_id)},
            )
        return int(run.outcome is WeightOutcome.APPLIED)

    async def measured_conversations(
        self,
        principal: Principal,
        *,
        source_id: UUID | None,
        since: date,
        until: date,
        cursor: str | None,
        limit: int,
    ) -> MeasuredConversationPage:
        """The measured conversations of a period and an agent, newest first: what the Console
        lists so nobody types a conversation id. Vendor people see only their sources."""
        allowed = visible_sources(principal)
        if source_id is not None:
            if allowed is not None and source_id not in allowed:
                return MeasuredConversationPage(items=[])
            sources: list[UUID] | None = [source_id]
        else:
            sources = sorted(allowed) if allowed is not None else None
        start = datetime.combine(since, time.min, tzinfo=UTC)
        end = datetime.combine(until + timedelta(days=1), time.min, tzinfo=UTC)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.measure.measured_sessions(
                source_ids=sources, since=start, until=end, before=_measured_cursor(cursor), limit=limit + 1
            )
        page = rows[:limit]
        more = len(rows) > limit
        return MeasuredConversationPage(
            items=[
                MeasuredConversation(
                    conversation_id=r.conversation_key,
                    source_id=r.source_id,
                    channel=r.channel,
                    ended_at=r.ended_at,
                    measured_at=r.measured_at,
                    no_context=r.no_context,
                    experiment_group=r.experiment_group,
                )
                for r in page
            ],
            next_cursor=f"{page[-1].ended_at.isoformat()}|{page[-1].session_id}" if more else None,
        )

    async def conversation_detail(self, principal: Principal, conversation_id: str) -> ContextUseDetail:
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.measure.rows_for_conversation(conversation_id)
        entries = [_entry(r) for r in rows if can_see(principal, r.source_id)]
        if not entries:
            raise NotFound("no measurement for this conversation")
        return ContextUseDetail(conversation_id=conversation_id, entries=entries)


class _Measurement:
    """One pass of `measure` over a closed session, inside the caller's transaction."""

    def __init__(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        session: SessionRecord,
        run: ExtractionRun | None,
        version: str,
        receipt_gap: bool,
        now: datetime,
        instructions: ObjectInstructions | None = None,
    ) -> None:
        assert session.closed_at is not None
        self._instructions = instructions or NoObjectInstructions()
        self._seen: dict[str, Manifest] = {}
        self.uow, self.config, self.session, self.extraction = uow, config, session, run
        self.version, self.receipt_gap, self.now = version, receipt_gap, now
        self.ended_at = session.closed_at
        self.result: Mapping[str, Any] = run.result.to_dict() if run and run.result else {}
        self.subject = session.profile_id
        self._variants: dict[str, str | None] = {}

    async def run(self, previous: MeasuredSession | None) -> bool:
        """Measures the session; False when it is no conversation to measure."""
        since = self.session.opened_at - DELIVERY_SLACK
        receipts = await self.uow.audit.receipts_for_conversation(_thread_id(self.session), since=since)
        deliveries = deliveries_from_receipts(receipts, since=since, until=self.ended_at)
        events = await _session_events(self.uow, self.session)
        if previous is None and not deliveries and is_agent_task(self.session.kind, events):
            # An internal agent's task that only acted and read nothing: no conversation (as extraction
            # says, B2) and no read, so no session without context. Counted, it read as a coverage gap of
            # an agent that by design does not read.
            return False
        if self.subject is None:
            # Nobody identified in the session's own events: the customer the agent read about is the one
            # its words are measured against, when the reads name a single one.
            named = {d.profile_id for d in deliveries if d.profile_id is not None}
            self.subject = named.pop() if len(named) == 1 else None
        answers = agent_answers(events, self.config.settings.stt_confidence_floor)
        outcomes, slotted = await self._outcomes(
            deliveries,
            events,
            slot_deliveries_from_receipts(receipts, since=since, until=self.ended_at),
            answers,
        )
        outcomes = await self._against_instructions(outcomes, answers)
        rows = [self._row(o, slotted.get(i, ())) for i, o in enumerate(outcomes) if self.subject is not None]
        measured = self._measured_session(deliveries)
        touched: set[tuple[UUID, str]] = set()
        if previous is not None:
            touched |= await self._retire(previous)
        inserted = await self.uow.measure.insert_rows(rows)
        for row in inserted:
            await self.uow.measure.add_daily(self._row_key(row), row_counters(row))
            await self._add_slots(row, self.ended_at.date())
            await self._add_guards(row, self.ended_at.date())
            await self._add_views(row, self.ended_at.date())
            touched |= await self._add_units(row, self.ended_at.date())
        await self._derive_trims(touched)
        await self.uow.measure.put_session(measured)
        if previous is None:
            await self.uow.measure.add_daily(
                session_key(measured, SESSION_LEVEL_VIEW), session_counters(measured)
            )
            await self._add_backing(measured, answers)
            await self._announce_violations(inserted)
        await self._mark_recontacts(measured)
        await self._follow_up(events, rows, answers)
        return True

    async def _outcomes(
        self,
        deliveries: Sequence[Delivery],
        events: Sequence[EventRecord],
        slot_deliveries: Sequence[SlotDelivery] = (),
        answers: Sequence[AgentAnswer] = (),
    ) -> tuple[list[DeliveryOutcome], dict[int, list[MeasuredSlots]]]:
        """The verdicts of every pack delivery and, joined to each, those of the per-turn slots its reads
        carried (11, 3.4). Slots are measured only where the pack is."""
        manifests = await self._manifests(deliveries)
        missing = [
            DeliveryOutcome(d, view="unknown", not_measured=NotMeasured.MANIFEST_MISSING)
            for d in deliveries
            if d.manifest_hash not in manifests
        ]
        pairs = [(d, manifests[d.manifest_hash]) for d in deliveries if d.manifest_hash in manifests]
        reason = self._session_reason(events)
        if reason is not None:
            return missing + [_unmeasured(d, m, reason) for d, m in pairs], {}
        turns = agent_turns(events, self.config.settings.stt_confidence_floor)
        evidence = SessionEvidence(
            questions=questions_from(self.result, turns),
            claims=claims_from(self.result, turns),
            actions=await self._actions(),
            first_agent_turn_at=first_agent_turns(events),
            stamps=context_stamps(events),
            answers=tuple(answers),
        )
        rules = MeasureRules(
            single_valued=frozenset(p.name for p in self.config.extraction.predicates if p.single_valued),
            confirms_before_acting=frozenset(
                s.source_id for s in self.config.sources if s.confirms_before_acting
            ),
            task_session=self.session.kind == "task",
        )
        outcomes = missing + measure_deliveries(pairs, evidence, rules)
        live = [d for d in slot_deliveries if d.items or d.channels or d.guards or d.gated]
        if not live:
            return outcomes, {}
        known = await self._slot_items(manifests.values(), live)
        measured = [i for i, o in enumerate(outcomes) if o.not_measured is None and not o.delivery.holdout]
        joined = attach([outcomes[i] for i in measured], measure_slots(live, known, evidence, rules))
        slotted: dict[int, list[MeasuredSlots]] = {}
        for index, slots in joined.items():
            position = measured[index]
            outcomes[position] = with_guard_violations(
                without_slotted_misses(outcomes[position], slots), slots
            )
            slotted[position] = slots
        return outcomes, slotted

    async def _against_instructions(
        self, outcomes: list[DeliveryOutcome], answers: Sequence[AgentAnswer]
    ) -> list[DeliveryOutcome]:
        """Each delivery's agent messages after it that did what an instruction about an object its pack
        carried said not to do: the number a legal director reads. Statements only when a
        pack carried an instruction."""
        carried = {
            h: {(i.object_ref, row_id) for i in m.items for kind, row_id in i.bound if kind == "instruction"}
            for h, m in self._seen.items()
        }
        refs = sorted({ref for pairs in carried.values() for ref, _ in pairs if ref})
        if not refs:
            return outcomes
        keys = [ObjectKey(*_split_ref(ref)) for ref in refs]
        objects = await self.uow.objects.get_objects(keys)
        parts = await self.uow.objects.participants_of([o.object_id for o in objects], per_object=MAX_HANDLES)
        handles = {o.origin_handle_id for o in objects if o.origin_handle_id is not None}
        handles |= {h for pairs in parts.values() for h, _ in pairs}
        held = await self._instructions.held(self.uow, self.config.space_id, sorted(handles, key=str), refs)
        said = {(h.object, h.key): h for h in held}
        out = []
        for outcome in outcomes:
            mine = [said[pair] for pair in carried.get(outcome.delivery.manifest_hash, ()) if pair in said]
            after = [
                a
                for a in answers
                if a.source_id == outcome.delivery.source_id and a.at > outcome.delivery.delivered_at
            ]
            count = sum(1 for a in after if any(violates(h.text, h.strength, a.text) for h in mine))
            out.append(replace(outcome, instruction_violations=count) if count else outcome)
        return out

    async def _slot_items(
        self, manifests: Iterable[Manifest], deliveries: Sequence[SlotDelivery]
    ) -> dict[str, ManifestItem]:
        """What measurement knows of each slotted item: the pack manifest's entry (a held-back item is
        among its exclusions, with its predicate and object), and a settled value's raw text, read only
        to compare with what the agent said and never stored."""
        known: dict[str, ManifestItem] = {}
        for manifest in manifests:
            for item in (*manifest.withheld, *manifest.items):
                known[item.item_id] = item
        wanted = {e.public_id for d in deliveries for e in d.items if e.kind == VALUE_KIND}
        wanted |= {g.public_id for d in deliveries for g in d.guards}
        handles = await self._handles() if wanted else ()
        if handles:
            for value in await self.uow.memory.values_for(handles):
                if str(value.value_id) in wanted:
                    known[str(value.value_id)] = ManifestItem(
                        str(value.value_id), VALUE_KIND, value=value.raw, value_type=value.type.value
                    )
        return known

    def _session_reason(self, events: Sequence[EventRecord]) -> NotMeasured | None:
        if self.receipt_gap:
            return NotMeasured.RECEIPT_GAP
        if self.extraction is None or self.extraction.status is not RunStatus.OK:
            return NotMeasured.NOT_EXTRACTED
        agent_text = any(e.text for e in agent_turns(events, 0.0).values())
        if self.session.channel == "voice" and not agent_text:
            return NotMeasured.PARTIAL_TRANSCRIPT
        return None

    async def _manifests(self, deliveries: Sequence[Delivery]) -> dict[str, Manifest]:
        docs = {}
        for manifest_hash in dict.fromkeys(d.manifest_hash for d in deliveries):
            if (doc := await self.uow.compile.get_manifest(manifest_hash)) is not None:
                docs[manifest_hash] = doc
        values = await self._fact_values({i for doc in docs.values() for i in fact_ids(doc)})
        self._seen = {h: manifest_from_json(h, doc, values) for h, doc in docs.items()}
        return dict(self._seen)

    async def _fact_values(self, wanted: set[str]) -> dict[str, str]:
        handles = await self._handles()
        if not wanted or not handles:
            return {}
        facts = await self.uow.memory.facts_for(handles, statuses=tuple(FactStatus))
        return {str(f.fact_id): f.value for f in facts if str(f.fact_id) in wanted}

    async def _actions(self) -> tuple[SessionAction, ...]:
        handles = await self._handles()
        if not handles:
            return ()
        actions = await self.uow.objects.actions_for(handles, since=self.session.opened_at)
        return actions_in(actions, since=self.session.opened_at, until=self.ended_at)

    async def _handles(self) -> tuple[UUID, ...]:
        if self.subject is None:
            return ()
        profile = await self.uow.identity.get_profile(self.subject)
        return profile.handle_ids if profile else ()

    async def _add_units(self, row: ContextUseRow, day: date, sign: int = 1) -> set[tuple[UUID, str]]:
        counted = unit_counters(row)
        for unit, counters in counted.items():
            key = UnitKey(day, row.source_id, row.view, unit)
            await self.uow.measure.add_units(key, counters if sign > 0 else counters.negated())
        return {(row.source_id, row.view)} if counted else set()

    async def _add_slots(self, row: ContextUseRow, day: date, sign: int = 1) -> None:
        for name, counters in sorted(slot_counters(row).items()):
            key = SlotDailyKey(day, row.source_id, row.channel, row.view, row.experiment_group, name)
            await self.uow.measure.add_slot_daily(key, counters if sign > 0 else counters.negated())

    async def _add_guards(self, row: ContextUseRow, day: date, sign: int = 1) -> None:
        """Per value type and arm, the values slotted or guarded and the contradictions (13, 6.1): what the
        read path's guard types and the control comparison are read from."""
        for (value_type, arm), counters in sorted(guard_counters(row).items()):
            key = GuardDailyKey(day, row.source_id, value_type, arm)
            await self.uow.measure.add_guard_daily(key, counters if sign > 0 else counters.negated())

    async def _add_views(self, row: ContextUseRow, day: date, sign: int = 1) -> None:
        """Per learned section, what this reader (source, target model, view) used and missed, under the
        customer's arm (13, 6.3). Only deliveries of a skeleton count; the target model is the
        one the pack was compiled for, read from its manifest."""
        counted = view_use_counters(row)
        if not counted:
            return
        variant = await self._variant_of(row.manifest_hash)
        if not learned_variant(variant):
            return
        target, group = target_of(variant), views_arm(self.config.space_id, row.profile_id)
        for section, counters in sorted(counted.items()):
            key = ViewUseKey(day, row.source_id, target, row.view, group, section)
            await self.uow.measure.add_view_use(key, counters if sign > 0 else counters.negated())

    async def _variant_of(self, manifest_hash: str) -> str | None:
        if manifest_hash not in self._variants:
            doc = await self.uow.compile.get_manifest(manifest_hash)
            self._variants[manifest_hash] = str(doc["variant"]) if doc and doc.get("variant") else None
        return self._variants[manifest_hash]

    async def _add_backing(self, measured: MeasuredSession, answers: Sequence[AgentAnswer]) -> None:
        """Per agent source, the answers the SDK checked and the values with no source (13, 6.2), counted
        once per session with its session-level counters."""
        for source_id, counters in sorted(backing_counters(answers).items(), key=lambda p: str(p[0])):
            key = DailyKey(
                measured.day, source_id, measured.channel, SESSION_LEVEL_VIEW, measured.experiment_group
            )
            await self.uow.measure.add_daily(key, counters)

    async def _announce_violations(self, rows: Sequence[ContextUseRow]) -> None:
        """`guard.violated` for the guards the rule found broken and the SDK did not flag (an agent
        without the SDK's check); a flagged one already left when its turn arrived."""
        found = [
            (row, guard)
            for row in rows
            for turn in row.slots
            for guard in turn.get("guards") or ()
            if guard.get("violated") and not guard.get("flagged") and guard.get("arm") == GUARDS_ARM
        ]
        if not found:
            return
        profile = await self.uow.identity.get_profile(self.subject) if self.subject is not None else None
        subject = WebhookSubject(profile.pseudonym, profile.kind.value) if profile is not None else None
        for row, guard in found:
            data = {
                "event_id": (guard.get("evidence") or [None])[0],
                "conversation_id": external_reference(self.session.conversation_id),
                "task_id": external_reference(self.session.task_id),
                "source_id": str(row.source_id),
                "guards": [short_id(str(guard["id"]))],
                "value_type": guard.get("value_type"),
                "detected_by": "measurement",
            }
            await enqueue_event(self.uow, self.config, "guard.violated", subject, data)

    async def _derive_trims(self, pairs: set[tuple[UUID, str]]) -> None:
        """Per source and view this measurement moved: the units of the window it never used (15.8)."""
        settings = self.config.settings.context_trim
        since, until = window(self.now.date(), settings.window_days)
        for source_id, view in sorted(pairs, key=lambda p: (str(p[0]), p[1])):
            counted = totals(await self.uow.measure.unit_totals(source_id, view, since, until))
            await self.uow.measure.put_trim(
                ContextTrim(
                    source_id=source_id,
                    view=view,
                    units=derive_units(counted, settings.min_deliveries),
                    derived_at=self.now,
                    window_days=settings.window_days,
                    min_deliveries=settings.min_deliveries,
                )
            )

    def _row(self, outcome: DeliveryOutcome, slots: Sequence[MeasuredSlots] = ()) -> ContextUseRow:
        assert self.subject is not None
        return ContextUseRow(
            conversation_key=_conversation_key(self.session),
            manifest_hash=outcome.delivery.manifest_hash,
            source_id=outcome.delivery.source_id,
            measure_version=self.version,
            session_id=self.session.session_id,
            profile_id=self.subject,
            channel=self.session.channel,
            view=outcome.view,
            experiment_group=outcome.delivery.experiment_group,
            items=tuple(i.to_json() for i in outcome.items),
            questions=outcome.questions,
            repeated=outcome.repeated,
            used=outcome.used,
            contradicted=outcome.contradicted,
            late=outcome.late,
            measured_at=self.now,
            not_measured_reason=outcome.not_measured.value if outcome.not_measured else None,
            tokens_saved=outcome.tokens_saved,
            slots=tuple(m.to_json() for m in slots),
            object_rows=outcome.object_rows,
            object_rows_used=outcome.object_rows_used,
            instruction_violations=outcome.instruction_violations,
        )

    def _row_key(self, row: ContextUseRow) -> DailyKey:
        return DailyKey(self.ended_at.date(), row.source_id, row.channel, row.view, row.experiment_group)

    def _measured_session(self, deliveries: Sequence[Delivery]) -> MeasuredSession:
        category = episode_category(self.result)
        return MeasuredSession(
            session_id=self.session.session_id,
            measure_version=self.version,
            conversation_key=_conversation_key(self.session),
            source_id=self.session.source_id,
            channel=self.session.channel,
            profile_id=self.subject,
            experiment_group=deliveries[0].experiment_group if deliveries else "",
            day=self.ended_at.date(),
            ended_at=self.ended_at,
            measured_at=self.now,
            no_context=not deliveries,
            normalized_category=self.config.extraction.normalize_category(category) if category else None,
            initiated_by=self.session.initiated_by,
        )

    async def _retire(self, previous: MeasuredSession) -> set[tuple[UUID, str]]:
        """A new extractor version re-measures the session (L13): the old rows leave the aggregates."""
        old = [
            r
            for r in await self.uow.measure.rows_for_session(previous.session_id)
            if r.measure_version != self.version
        ]
        touched: set[tuple[UUID, str]] = set()
        for row in old:
            await self.uow.measure.add_daily(
                DailyKey(previous.day, row.source_id, row.channel, row.view, row.experiment_group),
                _negated(row_counters(row)),
            )
            await self._add_slots(row, previous.day, sign=-1)
            await self._add_guards(row, previous.day, sign=-1)
            await self._add_views(row, previous.day, sign=-1)
            touched |= await self._add_units(row, previous.day, sign=-1)
        await self.uow.measure.delete_rows(old)
        return touched

    async def _mark_recontacts(self, measured: MeasuredSession) -> None:
        if measured.profile_id is None or measured.initiated_by != CUSTOMER_INITIATED:
            return
        opened_at = self.session.opened_at
        earlier = await self.uow.measure.sessions_ended_between(
            measured.profile_id, opened_at - RECONTACT_WINDOW, opened_at
        )
        for previous in earlier:
            if previous.session_id == measured.session_id:
                continue
            scope = recontact_scope(previous, category=measured.normalized_category, opened_at=opened_at)
            if scope is None or not await self.uow.measure.mark_recontact(previous.session_id, scope):
                continue
            if scope is RecontactScope.CATEGORY:
                await self.uow.measure.add_daily(
                    session_key(previous, SESSION_LEVEL_VIEW), DailyCounters(recontacts=1)
                )

    async def _follow_up(
        self,
        events: Sequence[EventRecord],
        rows: Sequence[ContextUseRow],
        answers: Sequence[AgentAnswer] = (),
    ) -> None:
        space_id = self.config.space_id
        payload = {"session_id": str(self.session.session_id)}
        if handoffs := handoffs_in(events):
            due = max(h.at for h in handoffs) + TRANSFER_WINDOW
            await self.uow.pipeline.schedule(
                Task(TaskName.MEASURE_TRANSFER, space_id, payload), due, DelayReason.TRANSFER_WINDOW
            )
        signal = any(r.repeated or r.contradicted for r in rows)
        # An answer stating a value nothing backs goes to review boosted, beside the other signals (13, 6.2).
        conditions = [UNBACKED_VALUE] if any(a.unbacked for a in answers) else []
        await self.uow.pipeline.enqueue(
            [
                Task(
                    TaskName.SAMPLE_REVIEW,
                    space_id,
                    {
                        **payload,
                        "measure_signal": signal,
                        **({"conditions": conditions} if conditions else {}),
                    },
                )
            ]
        )


def _unmeasured(delivery: Delivery, manifest: Manifest, reason: NotMeasured) -> DeliveryOutcome:
    items = tuple(ItemOutcome(i.item_id, i.kind) for i in manifest.items)
    return DeliveryOutcome(
        delivery,
        view=manifest.view,
        items=items,
        not_measured=reason,
        tokens_saved=0 if delivery.holdout else manifest.tokens_saved,
    )


def _trim_out(config: SpaceConfig, trim: ContextTrim) -> ContextTrimOut:
    settings = config.settings.context_trim
    source = config.source(trim.source_id)
    reason = (
        "disabled"
        if not settings.enabled
        else "opt_out"
        if trim.source_id in settings.opt_out_sources
        else "critical"
        if source is not None and source.critical
        else None
    )
    applied = active_units(
        trim,
        enabled=settings.enabled,
        opted_out=trim.source_id in settings.opt_out_sources,
        critical=source is not None and source.critical,
    )
    return ContextTrimOut(
        source_id=trim.source_id,
        view=trim.view,
        active=bool(applied),
        inactive_reason=reason,
        units=[TrimUnitOut.of(u.unit, u.deliveries) for u in trim.units],
        derived_at=trim.derived_at,
        window_days=trim.window_days,
        min_deliveries=trim.min_deliveries,
    )


def _negated(counters: DailyCounters) -> DailyCounters:
    negated = DailyCounters()
    negated.add(counters, sign=-1)
    return negated


def _measure_version(run: ExtractionRun | None) -> str:
    versions = run.versions_key if run and run.status is RunStatus.OK else "none"
    return f"{MEASURE_RULES}/{versions}"


def _thread_id(session: SessionRecord) -> str:
    return session.conversation_id or session.task_id or str(session.session_id)


def _measured_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        at, _, session = cursor.partition("|")
        return datetime.fromisoformat(at), UUID(session)
    except ValueError:
        raise InvalidInput("unknown cursor") from None


def _conversation_key(session: SessionRecord) -> str:
    return session.conversation_id or f"task:{session.task_id}"


async def _session_events(uow: UnitOfWork, session: SessionRecord) -> list[EventRecord]:
    return await uow.ingest.events_between(
        conversation_id=session.conversation_id,
        task_id=session.task_id,
        since=session.opened_at,
        until=session.closed_at or session.last_event_at,
    )


def _stored_until(watermark: datetime | None, at: datetime) -> bool:
    return watermark is not None and watermark >= at


async def _reschedule(uow: UnitOfWork, task: Task, due_at: datetime, reason: DelayReason) -> None:
    payload = {**task.payload, "waits": _waits(task) + 1}
    await uow.pipeline.schedule(Task(task.name, task.space_id, payload, task.profile_id), due_at, reason)


def _waits(task: Task) -> int:
    return int(task.payload.get("waits", 0))


def _bucket(key: dict[str, Any], c: DailyCounters) -> ContextUseBucket:
    return ContextUseBucket(
        **key,
        **c.as_dict(),
        unbacked_per_1000=per_thousand(c.unbacked_values, c.answers_checked),
        usage_rate=RateOut.of(wilson(c.deliveries_used, c.deliveries - c.not_measured)),
        repetition_rate=RateOut.of(wilson(c.repeated, c.questions)),
        transfer_unread_rate=RateOut.of(wilson(c.transfers_unread, c.transfers)),
        recontact_rate=RateOut.of(wilson(c.recontacts, c.sessions)),
    )


CONTRACT_AGREEMENT = 0.90


# The count each signal's marks add to in the report's buckets.
_SIGNAL_COUNTS = {
    "used": "deliveries_used",
    "repeated": "repeated",
    "contradicted": "contradicted",
    "selection_miss": "selection_misses",
}


def _signal_review(
    verdicts: Iterable[Mapping[str, object]], total: DailyCounters | None = None
) -> list[SignalReview]:
    """What sample review says of each signal: the share of its marks reviewers confirmed (22.2), and the
    count corrected by that share, so the verdicts calibrate the number they judge."""
    found = agreement(verdicts)
    review = []
    for signal in SIGNALS:
        judged = found.get(f"signal:{signal}")
        rate = wilson(judged.agreed, judged.judged) if judged else None
        measured = getattr(total, _SIGNAL_COUNTS[signal]) if total else 0
        review.append(
            SignalReview(
                signal=signal,
                judged=judged.judged if judged else 0,
                confirmed=judged.agreed if judged else 0,
                agreement=RateOut.of(rate),
                # The contract counts a signal only once review agrees with 90% or more.
                contract_ready=rate is not None and rate.low >= CONTRACT_AGREEMENT,
                measured=measured,
                calibrated=EstimateOut(
                    value=round(measured * rate.value, 1),
                    low=round(measured * rate.low, 1),
                    high=round(measured * rate.high, 1),
                )
                if rate is not None
                else None,
            )
        )
    return review


def _slot_channels(
    rows: Iterable[SlotDailyRow], current: RetrievalWeights | None, defaults: Mapping[str, float]
) -> list[SlotChannelUse]:
    found = []
    for (view, name), c in sorted(group_slot_daily(rows).items()):
        if name == GATED:
            continue  # the intent gate's turns, reported apart (`gated_turns`)
        weights = current.for_view(view, defaults) if current is not None else dict(defaults)
        found.append(
            SlotChannelUse(
                view=view,
                slot_channel=name,
                **c.as_dict(),
                usage_rate=RateOut.of(wilson(c.used, c.slots)),
                weight=weights.get(name, 1.0),
                default_weight=defaults.get(name, 1.0),
            )
        )
    return found


def _gated_turns(rows: Iterable[SlotDailyRow]) -> list[GatedTurnsOut]:
    return [
        GatedTurnsOut(
            view=view,
            turns=c.turns,
            asked_again=c.repeated,
            asked_again_rate=RateOut.of(wilson(c.repeated, c.turns)),
        )
        for (view, name), c in sorted(group_slot_daily(rows).items())
        if name == GATED and c.turns > 0
    ]


def _guard_types(rows: Iterable[GuardDailyRow]) -> list[GuardTypeUse]:
    totals: dict[tuple[str, str], GuardCounters] = {}
    for row in rows:
        totals.setdefault((row.key.value_type, row.key.arm), GuardCounters()).add(row.counters)
    return [
        GuardTypeUse(
            value_type=value_type,
            arm=arm,
            slots=c.slots,
            contradicted=c.contradicted,
            contradiction_rate=RateOut.of(wilson(c.contradicted, c.slots)),
        )
        for (value_type, arm), c in sorted(totals.items())
        if c.slots > 0
    ]


def _views_out(
    runs: Sequence[ViewBudgets], allowed: frozenset[UUID] | None, source_id: UUID | None
) -> ViewBudgetsOut:
    """Each reader's learned view beside the view's own, in tokens, for the sources the caller sees."""
    current = runs[0] if runs else None
    readers = []
    for key, factors in sorted((current.factors if current else {}).items()):
        source, target, view = key.split("|", 2)
        reader = UUID(source)
        if (allowed is not None and reader not in allowed) or (source_id is not None and reader != source_id):
            continue
        defaults = {**default_ceilings(view), SLOTS: slot_budget(view)}
        learned = {**defaults, **_kinds(dict(learned_ceilings(view, factors)))}
        learned[SLOTS] = learned_slot_budget(view, factors.get(SLOTS))
        readers.append(
            LearnedViewOut(
                source_id=reader,
                target=target,
                view=view,
                factors={k: round(v, 6) for k, v in sorted(factors.items())},
                budgets=learned,
                defaults=defaults,
                tokens_delta=sum(learned.values()) - sum(defaults.values()),
            )
        )
    return ViewBudgetsOut(
        version=current.version if current else 0,
        control_fraction=CONTROL_FRACTION,
        readers=readers,
        runs=[
            ViewRunOut(
                day=r.day,
                computed_at=r.computed_at,
                version=r.version,
                outcome=r.outcome.value,
                guard=(r.evidence.get("guard") or {}).get("state"),
            )
            for r in runs
        ],
    )


def _kinds(by_section: Mapping[str, int]) -> dict[str, int]:
    """Ceilings by section name back to the item kinds the report names them by."""
    names = {section.value: kind for kind, section in LEARNED_SECTIONS.items()}
    return {names[name]: tokens for name, tokens in by_section.items() if name in names}


def _weights_out(runs: Sequence[RetrievalWeights], defaults: Mapping[str, float]) -> RetrievalWeightsOut:
    current = runs[0] if runs else None
    return RetrievalWeightsOut(
        version=current.version if current else 0,
        weights={view: dict(w) for view, w in current.weights.items()} if current else {},
        # A channel at 0 would be switched off: it does not fuse, so it is not listed.
        defaults={name: weight for name, weight in defaults.items() if weight > 0},
        runs=[
            WeightRunOut(
                day=r.day,
                computed_at=r.computed_at,
                version=r.version,
                outcome=r.outcome.value,
                guard=(r.evidence.get("guard") or {}).get("state"),
            )
            for r in runs
        ],
    )


def _sort_key(item: tuple[tuple[object, ...], DailyCounters]) -> tuple[str, ...]:
    return tuple(str(part) for part in item[0])


def _entry(row: ContextUseRow) -> ContextUseEntry:
    return ContextUseEntry(
        manifest_hash=row.manifest_hash,
        source_id=row.source_id,
        measure_version=row.measure_version,
        channel=row.channel,
        view=row.view,
        experiment_group=row.experiment_group,
        items=list(row.items),
        questions=row.questions,
        repeated=row.repeated,
        used=row.used,
        contradicted=row.contradicted,
        late=row.late,
        transfer_unread=row.transfer_unread,
        not_measured_reason=row.not_measured_reason,
        tokens_saved=row.tokens_saved,
        measured_at=row.measured_at,
        slots=[dict(turn) for turn in row.slots],
    )


def _claims_out(t: ClaimTally) -> ClaimRates:
    return ClaimRates(
        source_id=t.source_id,
        agent=t.agent,
        turns=t.turns,
        claims=t.claims,
        evidenced=t.evidenced,
        categories={c: dict(sorted(v.items())) for c, v in sorted(t.by_category.items())},
    )


def _constraints_out(t: ConstraintTally) -> ConstraintRates:
    return ConstraintRates(
        source_id=t.source_id,
        agent=t.agent,
        turns=t.turns,
        sent=t.sent,
        checked=t.checked,
        verifiable=t.verifiable,
        violated=t.violated,
        unverifiable=t.unverifiable,
        relaxed_declared=t.relaxed_declared,
        relaxed_suspected=t.relaxed_suspected,
        items_presented=t.items_presented,
        violations_per_1000_items=t.violations_per_1000_items,
    )


def _costs_out(config: SpaceConfig, found: Iterable[CostTally]) -> list[CostRates]:
    baselines = {b.agent: b.cost_per_turn_usd for b in config.measurement.memory_off_costs}
    out = []
    for t in found:
        cost = round(t.cost_micros / t.turns / 1_000_000, 6) if t.turns else None
        baseline = baselines.get(t.agent)
        difference = round(cost - baseline, 6) if cost is not None and baseline is not None else None
        out.append(
            CostRates(
                source_id=t.source_id, agent=t.agent, turns=t.turns, cost_per_turn_usd=cost,
                model_calls_per_turn=t.per_turn(t.model_calls),
                tool_calls_per_turn=t.per_turn(t.tool_calls),
                tokens_in_per_turn=t.per_turn(t.tokens_in),
                tokens_cached_per_turn=t.per_turn(t.tokens_cached),
                tokens_out_per_turn=t.per_turn(t.tokens_out),
                memory_off_cost_per_turn_usd=baseline,
                difference_usd=difference,
            )
        )  # fmt: skip
    return out


def _split_ref(ref: str) -> tuple[str, str, str]:
    kind, _, rest = ref.partition(":")
    namespace, _, external_id = rest.partition(":")
    return kind, namespace, external_id
