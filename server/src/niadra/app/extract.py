"""Extraction (5.7): a customer's closed sessions, one decision call and at most one LLM call per batch.

`gate` runs under the profile lease, calls no model, and schedules the session's `extract` after a short
window: the sessions of one customer that close together (an imported history, a customer
who wrote and then called) go to the models together, and only the task of the newest one runs the batch.
`extract` runs without the lease (5.5), so a slow LLM never blocks the profile: it reads what it needs in
one short transaction, calls the models outside any transaction, and writes each session's run and
`reconcile` task in another.

- One decision model call per batch (`DecideService.triage`): which short sessions were answered on the
  spot, and whether any message tries to instruct the system or reveals a special category. An answered
  session keeps its own turns as its episode and never reaches the extraction model; its settled values come
  from the deterministic net, as for every session. Only a batch the screens flag is checked turn by turn.
- One extraction model call for the rest: a compact answer, one entry per session. The effort is `none` when
  every session is short and nothing in it names a document, a card or an account, `low` otherwise.

LLM failures are retried here, with backoff, through `scheduled_tasks`; the last failure writes a template
episode and marks the run `failed` for the nightly job.
"""

import dataclasses
import hashlib
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from niadra.app.decide import DecideService, ModelsPendingError, answered
from niadra.app.notify import enqueue_event
from niadra.app.object_memory import known_objects
from niadra.app.observed_operations import ObservedOperationsService
from niadra.app.pipeline import fence
from niadra.app.withhold import WithholdPass
from niadra.config.models import ExtractionGuidance, PredicateSpec, SourceGuidance, SpaceConfig
from niadra.contracts.ingest import IngestStatus, IngestStatusRequest
from niadra.domain.common.errors import ModelsRefused, NiadraError, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.decide import INJECTION_SCREEN, SENSITIVE_SCREEN, DecisionTask, Triage
from niadra.domain.extract.budget import SpendHold
from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.extract.minimal import (
    answered_result,
    is_agent_task,
    is_trivial,
    minimal_result,
    trivial_result,
)
from niadra.domain.extract.prompt import (
    BatchMaterial,
    Prompt,
    SessionMaterial,
    build_prompt,
    is_short,
    redactable,
    select_candidates,
    turns,
)
from niadra.domain.extract.redaction import WITHHELD_KINDS
from niadra.domain.extract.result import ExtractionResult, ExtractionRun, QuestionType, RunStatus
from niadra.domain.extract.rules import (
    EXTRACTOR_VERSION,
    OBJECT_CATEGORY,
    OBSERVATION,
    PROMPT_VERSION,
    ExtractionRules,
    Guidance,
    PredicateRule,
    SlotRule,
)
from niadra.domain.extract.validate import InvalidExtractionError, validate
from niadra.domain.extract.windows import merged, windows
from niadra.domain.extract.withhold import (
    held_numbers,
    label_topics,
    no_topics,
    sensitive_candidates,
    withhold_result,
)
from niadra.domain.identity.provenance import session_view
from niadra.domain.ingest.masking import language
from niadra.domain.memory.bound import OBJECTS_CEILING, staff
from niadra.domain.memory.values import fold, label_word
from niadra.domain.objects.operations import (
    MAX_OPERATIONS,
    canonical_operation,
    canonical_operations,
    catalog_operations,
)
from niadra.domain.objects.state import operation_of
from niadra.domain.pipeline.delay import DelayReason, spread_offset
from niadra.domain.policy.audience import conversation_audience
from niadra.domain.policy.sensitive import is_product_sensitive
from niadra.domain.records import Episode, EventRecord, LinkRecord, ProfileRecord, SessionRecord
from niadra.domain.tasks import CATALOG, Task, TaskName
from niadra.domain.vocabulary import EventKind, OpenItemStatus, Sensitivity, SubjectKind
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import (
    Clock,
    ConfigProvider,
    LlmClient,
    LlmResult,
    NoTelemetry,
    PiiSpan,
    Reasoning,
    Telemetry,
)
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

PURPOSE = "extraction"
REASONING: Reasoning = "low"
"""The extraction model's effort for extraction, measured on 26/09: at `low`, 100% valid answers, 99.2% of the
session's values kept, no document number in the answers and 10.6 s at p95.
`none` wrote 52 of 60 document numbers into its answers, so it is only for `QUIET_REASONING`'s batches."""
QUIET_REASONING: Reasoning = "none"
"""The effort for a batch of short sessions in which neither the PII model nor the words of the turns found
a document, a card or a bank account: there is no such number for the model to copy, and the validation
withholds one anyway."""
QUIET_TURNS = 4
"""The most message turns a session of a `QUIET_REASONING` batch may have."""
BATCH_MAX_SESSIONS = 8
"""Sessions per extraction call: the answer stays well under `max_output_tokens`."""
OUTPUT_TOKENS_PER_SESSION = 500
"""A batch's answer may take this many output tokens per session, above `max_output_tokens`: a rich session
wrote about 250 on 28/09, so a batch of eight never hits the ceiling."""
BATCH_HORIZON = timedelta(hours=1)
"""How far back a batch looks for the customer's other closed sessions still waiting for their extraction."""
REEXTRACT_DAYS = 30
REEXTRACT_DAILY = 1_000
"""Sessions a pass sends to extraction in one UTC day: past it, the pass goes on the next day, so a change of
the extractor reaches every session of the window and its AI spend is spread over days. The old cap stopped
the pass at 1,000 sessions, and the older ones stayed on the old extraction with no trace."""
REEXTRACT_PAGE = 100
REEXTRACT_PAUSE = timedelta(minutes=2)
ATTEMPT_KEY = "llm_attempt"
RECENT_CATEGORIES_DAYS = 90
RECORDED_OPERATIONS_AGE = timedelta(days=30)
"""How far back the operations the customer's agents and systems recorded name what an open item may wait
for."""
RECORDED_SYSTEM_EVENTS = 200
RECENT_CATEGORIES_MAX = 6
RECENT_MATTERS_MAX = 8
DEFAULT_DAILY_CAPS_MICROS: Mapping[str, int] = {"extraction": 5_000_000, "assistant": 200_000}
"""Each purpose's daily AI spend floor for a space whose contract sets none. `extraction` is the write path's
(the extraction and decision models count in it together); a floor too low stopped a customer's memory a
day behind. Past the first week the ceiling follows the space's own traffic (`traffic_ceiling`): it is a
guard against a runaway, never the pace of memory."""
TRAFFIC_MULTIPLE = 3
"""How far above its usual day a space may spend before the guard stops it: three times the median of
`TRAFFIC_DAYS` days, so a busy Monday or a campaign passes and a loop does not."""
TRAFFIC_DAYS = 7
# What one call reserves before it runs: above the usual cost, so the cap holds with calls in flight; the
# real cost replaces it after the answer.
DEFAULT_CALL_ESTIMATE_MICROS = 2_000
DEFAULT_CALL_ESTIMATES_MICROS: Mapping[str, int] = {"extraction": 2_000, "assistant": 5_000}


def daily_ceiling(
    config: SpaceConfig | None,
    purpose: str,
    defaults: Mapping[str, int] = DEFAULT_DAILY_CAPS_MICROS,
    history: Sequence[int] = (),
) -> tuple[int | None, bool]:
    """A purpose's daily AI spend ceiling for the space, and whether its contract set it: the
    contract's when it sets one; else the floor, or the space's own traffic when that is higher
    (`traffic_ceiling`, over `history`, the spend of its last `TRAFFIC_DAYS` days)."""
    limits = config.limits if config is not None else None
    if limits is not None and purpose in limits.llm_daily_micros:
        return limits.llm_daily_micros[purpose], True
    floor = defaults.get(purpose)
    return (None if floor is None else traffic_ceiling(floor, history)), False


def traffic_ceiling(floor: int, history: Sequence[int]) -> int:
    """`floor`, or `TRAFFIC_MULTIPLE` times the median spend of the days in `history` that spent anything,
    when that is higher: the ceiling grows with the space's volume, which the price per conversation
    already covers."""
    spent = sorted(h for h in history if h > 0)
    if not spent:
        return floor
    half = len(spent) // 2
    median = spent[half] if len(spent) % 2 else (spent[half - 1] + spent[half]) // 2
    return max(floor, TRAFFIC_MULTIPLE * median)


async def spend_history(uow: UnitOfWork, purpose: str, today: date) -> list[int]:
    """What the space spent on `purpose` on each of the `TRAFFIC_DAYS` days before `today`."""
    return [await uow.extract.spent(today - timedelta(days=n), purpose) for n in range(1, TRAFFIC_DAYS + 1)]


@dataclass(frozen=True, slots=True)
class ExtractSettings:
    max_output_tokens: int = 2000
    max_attempts: int = CATALOG[TaskName.EXTRACT].max_attempts
    retry_base_s: float = 5.0
    retry_factor: float = 4.0
    """A failed extraction call comes back 5, 20, 80 and 320 s later: the first retry soon enough for memory's
    minute when the provider only stumbled (a rate limit upstream), the last ones about as far out as
    before, so an outage of a few minutes still ends in an extraction, not the template."""
    budget_spread_s: float = 7200.0
    """How far after the budget reopens a deferred extraction may come back (`spread_offset`)."""
    batch_window_s: float = 8.0
    """How long a closed session waits for the customer's other sessions before its extraction (C4), when
    another of them closed within this time: an imported history arrives a session every second or two,
    and a live customer rarely closes two sessions together, so a lone session never waits. 0 extracts at
    once."""


def rules_for(config: SpaceConfig, source_id: UUID, *, staff_said: bool | None = None) -> ExtractionRules:
    """The tenant schema plus the predicates the source adds, keyed by source id or name (rev. 9).
    `staff_said`: whether the session's person is the company's staff (`said_by_staff`); the source's own
    audience decides when the session is not known."""
    schema = config.extraction
    source = config.source(source_id)
    keys = {str(source_id), *([source.name] if source else [])}
    specs = {p.name: p for p in schema.predicates}
    for key, extra in schema.source_overrides.items():
        if key in keys:
            specs.update({p.name: p for p in extra})
    sensitivity = {c.name: c.sensitivity for c in [*config.policy.categories, *schema.categories]}
    patterns = tuple(config.object_types.key_patterns())
    if any(t.ownership == "subject" for t in config.object_types.types) and OBSERVATION not in specs:
        # What was said about an object that no predicate of the space holds.
        specs[OBSERVATION] = PredicateSpec(name=OBSERVATION, category=OBJECT_CATEGORY)
    return ExtractionRules(
        schema_version=schema.version,
        predicates=tuple(
            PredicateRule(
                name=p.name,
                category=p.category,
                namespace=p.namespace,
                single_valued=p.single_valued,
                enum=tuple(p.enum) if p.enum is not None else None,
                sensitivity=Sensitivity.SENSITIVE
                if is_product_sensitive(p.category)
                else sensitivity.get(p.category, Sensitivity.NORMAL),
                organization=p.about == "organization",
                observes=p.observes,
            )
            for p in specs.values()
        ),
        intents=tuple(schema.intents),
        outcomes=tuple(schema.outcomes),
        episode_categories=tuple(c.name for c in schema.episode_categories),
        slots=tuple(SlotRule(s.name, s.type) for s in schema.slots),
        language=schema.language,
        timezone=config.settings.timezone,
        stt_floor=config.settings.stt_confidence_floor,
        operations=tuple(sorted(_operations(config))),
        operation_meanings=_meanings(config),
        guidance=_guidance(config, keys) or None,
        staff=staff_said if staff_said is not None else staff(source.audience if source else None),
        key_patterns=patterns,
    )


def _guidance(config: SpaceConfig, keys: set[str]) -> Guidance:
    """The space's guidance, then the source's own, by source id or name (5.7)."""
    space = config.extraction_guidance
    parts: list[ExtractionGuidance | SourceGuidance] = [
        space,
        *(g for key, g in sorted(space.sources.items()) if key in keys),
    ]
    lines = [line.strip() for p in parts if p.instructions for line in p.instructions.splitlines()]
    return Guidance(
        instructions=tuple(line for line in lines if line),
        include=tuple(dict.fromkeys(t for p in parts for t in p.include)),
        exclude=tuple(dict.fromkeys(t for p in parts for t in p.exclude)),
    )


def recorded_operations(recorded: Iterable[str], events: Iterable[EventRecord]) -> tuple[str, ...]:
    """Canonical operations of the customer's recent actions, of its recent system events and of the batch's
    own events: an action's, and the one a system event confirms (`fields.operation`). The most used first,
    ties by name, at most `MAX_OPERATIONS`, the cap the operations the space's traffic shows have too: an
    alphabetical cut at 40 never offered what a customer did most if its name came late."""
    named = [*recorded, *(operation_of(e) or "" for e in events if e.kind is not EventKind.MESSAGE)]
    counts = Counter(op for name in named if name and (op := canonical_operation(name)))
    return tuple(name for name, _ in sorted(counts.items(), key=lambda c: (-c[1], c[0]))[:MAX_OPERATIONS])


def _operations(config: SpaceConfig) -> frozenset[str]:
    """What an open item may wait for: the catalog, the space's declared operations (`extraction.operations`)
    and what its agents and mappings record."""
    recorded = [op for source in config.sources for op in source.trusted_action_ops]
    mapped = [rule.operation for mapping in config.mappings for rule in mapping.rules if rule.operation]
    declared = config.extraction.operation_names()
    return catalog_operations() | canonical_operations([*declared, *recorded, *mapped])


def _meanings(config: SpaceConfig) -> tuple[tuple[str, str], ...]:
    """The declared operations' meanings, by canonical name."""
    named = {
        canonical_operation(name): meaning for name, meaning in config.extraction.operation_meanings().items()
    }
    return tuple(sorted((name, meaning) for name, meaning in named.items() if name))


async def organization_handles(
    uow: UnitOfWork, people: Sequence[UUID], links: Sequence[LinkRecord]
) -> set[UUID]:
    """The handles of the organizations the person's active links name (21.4), none of the person's own."""
    mine = set(people)
    found: set[UUID] = set()
    for link in links:
        if link.person_handle_id not in mine or link.org_handle_id in found:
            continue
        org = await uow.identity.profile_of(link.org_handle_id)
        found |= set(org.handle_ids) if org is not None else {link.org_handle_id}
    return found - mine


async def session_events(uow: UnitOfWork, session: SessionRecord) -> list[EventRecord]:
    return await uow.ingest.events_between(
        conversation_id=session.conversation_id,
        task_id=session.task_id,
        since=session.opened_at,
        until=session.closed_at or session.last_event_at,
    )


@dataclass(frozen=True, slots=True)
class _Spend:
    """A call's usage and model, or the share of it one session's run records."""

    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_micros: int = 0


def _spend(answer: LlmResult) -> _Spend:
    u = answer.usage
    return _Spend(
        answer.model, answer.provider, u.input_tokens, u.output_tokens, u.cached_tokens, u.cost_micros
    )


def _shares(answer: LlmResult, n: int) -> list[_Spend]:
    """The call's usage split over the `n` sessions it extracted, the remainder on the first: the runs add
    up to what the call cost."""
    whole = _spend(answer)

    def split(total: int) -> list[int]:
        base, rest = divmod(total, n)
        return [base + (rest if i == 0 else 0) for i in range(n)]

    columns = [
        split(whole.input_tokens),
        split(whole.output_tokens),
        split(whole.cached_tokens),
        split(whole.cost_micros),
    ]
    return [_Spend(whole.model, whole.provider, *(c[i] for c in columns)) for i in range(n)]


def _summed(shares: Sequence[_Spend]) -> _Spend:
    """What a session's windows cost together: its run records every call it took."""
    last = shares[-1]
    return _Spend(
        last.model,
        last.provider,
        sum(s.input_tokens for s in shares),
        sum(s.output_tokens for s in shares),
        sum(s.cached_tokens for s in shares),
        sum(s.cost_micros for s in shares),
    )


class DecisionsPendingError(Exception):
    """The decision model did not answer a decision whose default would lose memory; the extraction is
    retried with backoff, and its last attempt takes the default. Without the PII spans there is no default
    worth a call: the last attempt gives the batch the template."""


class LlmBudget:
    """Daily LLM spend cap per space and purpose (5.9).

    A call first reserves an estimate of its cost, in one statement that also checks the cap, so calls
    running in parallel count each other's reservations and stop at the cap; before, each checked the
    spend and only added its cost after the answer, and on 25/09 a sandbox spent 1,126,925 micros
    against a cap of 500,000. After the call the reservation is settled to the real cost, and a call
    that fails gives it back. A process that dies between the two leaves its estimate counted.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        caps_micros: Mapping[str, int] | None = None,
        telemetry: Telemetry | None = None,
        config: ConfigProvider | None = None,
        estimates_micros: Mapping[str, int] | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._telemetry = telemetry or NoTelemetry()
        self._caps = dict(DEFAULT_DAILY_CAPS_MICROS if caps_micros is None else caps_micros)
        self._estimates = dict(
            DEFAULT_CALL_ESTIMATES_MICROS if estimates_micros is None else estimates_micros
        )
        self._config = config
        self._history: dict[tuple[UUID, str], tuple[date, tuple[int, ...]]] = {}

    async def cap(self, space_id: UUID, purpose: str) -> int | None:
        """The contract's ceiling for the space when it sets one, else the floor or the space's own
        traffic (`daily_ceiling`). The traffic is read once a day per space and purpose in this process."""
        config: SpaceConfig | None = None
        if self._config is not None:
            try:
                config = self._config.space(space_id)
            except NotFound:
                config = None
        today = _day(self._clock.now())
        known = self._history.get((space_id, purpose))
        if known is None or known[0] != today:
            async with self._uow(space_id) as uow:
                known = (today, tuple(await spend_history(uow, purpose, today)))
            self._history[(space_id, purpose)] = known
        return daily_ceiling(config, purpose, self._caps, known[1])[0]

    async def reserve(self, space_id: UUID, purpose: str, estimate_micros: int | None = None) -> SpendHold:
        """Holds the call's estimated cost (the purpose's, unless the caller knows its own), or raises
        `BudgetExceeded` once the day's spend and the reservations in flight reached the cap."""
        cap = await self.cap(space_id, purpose)
        now = self._clock.now()
        day = _day(now)
        if cap is None:
            return SpendHold(space_id, purpose, day, 0)
        if estimate_micros is None:
            estimate_micros = self._estimates.get(purpose, DEFAULT_CALL_ESTIMATE_MICROS)
        estimate = max(1, estimate_micros)
        held = False
        if cap > 0:
            async with self._uow(space_id) as uow:
                held = await uow.extract.reserve_spend(day, purpose, estimate, cap)
        if not held:
            self._telemetry.llm_budget_exhausted(purpose)
            tomorrow = datetime.combine(day + timedelta(days=1), time(), tzinfo=UTC)
            raise BudgetExceeded(
                int((tomorrow - now).total_seconds()) + 1, f"daily {purpose} budget spent", cap_micros=cap
            )
        return SpendHold(space_id, purpose, day, estimate)

    async def settle(self, hold: SpendHold, cost_micros: int) -> None:
        """Replaces the reservation with the call's real cost, on the day it was reserved."""
        cost = max(0, cost_micros)
        async with self._uow(hold.space_id) as uow:
            await uow.extract.add_spend(hold.day, hold.purpose, cost - hold.micros)
        self._telemetry.llm_spent(hold.purpose, cost)

    async def release(self, hold: SpendHold) -> None:
        """Gives the reservation back: the call failed before it cost anything."""
        if hold.micros:
            async with self._uow(hold.space_id) as uow:
                await uow.extract.add_spend(hold.day, hold.purpose, -hold.micros, calls=0)


class ExtractService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        llm: LlmClient,
        decide: DecideService,
        clock: Clock,
        config: ConfigProvider,
        settings: ExtractSettings | None = None,
        observed: ObservedOperationsService | None = None,
        issues: DataIssues | None = None,
    ) -> None:
        self._uow = uow
        self._llm = llm
        self._decide = decide
        self._clock = clock
        self._config = config
        self._settings = settings or ExtractSettings()
        self._observed = observed
        self._issues = issues
        self._withhold = WithholdPass(uow, config, clock, rules_for, session_events)

    async def watch_withholding(self, space_id: UUID) -> int:
        """A per-space loop step: what memory stored before the withholding rules is scrubbed by them, a
        bounded pass (`app/withhold.py`)."""
        return await self._withhold.watch(space_id)

    async def withhold_stored(self, task: Task) -> None:
        await self._withhold.page(task)

    async def status(self, caller: SourcePrincipal, request: IngestStatusRequest) -> IngestStatus:
        """Whether a conversation or task became memory yet (7.1): the session's state and its latest
        extraction run, never content. Any source of the space may ask, as any may write to it.

        `ready` means a read made now sees the run: memory applied it, and the compilation that covers it
        published its generation, so the packs, the pins' delta and slots and the retrieval cache all read
        at least that generation. Until then, `processing`."""
        thread = {"conversation_id": request.conversation_id, "task_id": request.task_id}
        async with self._uow(caller.space.space_id) as uow:
            current = await uow.sessions.open_session(**thread)
            # Counts by type from each event's `content_meta`, never a value (WP-A).
            masked = await uow.ingest.masked_counts(**thread) or None
            if current is not None:
                return IngestStatus(state="open", last_event_at=current.last_event_at, masked=masked)
            closed = await uow.sessions.last_closed_session(**thread)
            if closed is None:
                return IngestStatus(state="unknown")
            run = await uow.extract.latest_run(closed.session_id)
            state: Literal["processing", "ready", "failed"] = "processing"
            if run is not None and run.status in (RunStatus.OK, RunStatus.MINIMAL):
                state = "ready" if await _read_sees(uow, caller.space.space_id, run) else "processing"
            elif run is not None:
                state = "failed"
        return IngestStatus(
            state=state,
            last_event_at=closed.last_event_at,
            closed_at=closed.closed_at,
            close_reason=closed.close_reason,
            extraction=run.status.value if run is not None else None,
            masked=masked,
        )

    async def gate(self, task: Task) -> None:
        """Sends the closed session's extraction, after the batch window when another session of the customer
        just closed; calls no model."""
        session_id = UUID(task.payload["session_id"])
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            if session is None or session.closed_at is None:
                log.info("gate skipped: session missing or still open")
                return
        profile_id = task.profile_id or session.profile_id
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            kind = await _profile_kind(uow, profile_id)
        rules = rules_for(config, session.source_id, staff_said=said_by_staff(config, session, kind))
        async with self._uow(task.space_id) as uow:
            await fence(uow)
            if _finished(await uow.extract.get_run(session_id, run_key(rules.versions_key, session))):
                return  # a batch that another session's task ran already took this one
            extract = Task(
                TaskName.EXTRACT, task.space_id, {"session_id": str(session_id)}, profile_id=profile_id
            )
            window = timedelta(seconds=self._settings.batch_window_s)
            now = self._clock.now()
            # Only a customer whose other session closed within the window waits for the rest (an import
            # closes one every second or two); a live customer's lone session goes at once.
            together = (
                window
                and profile_id is not None
                and any(
                    s.session_id != session_id
                    for s in await uow.sessions.closed_for_profile(profile_id, now - window, limit=2)
                )
            )
            if together:
                await uow.pipeline.schedule(extract, now + window, DelayReason.BATCH_WINDOW)
            else:
                await uow.pipeline.enqueue([extract])

    async def extract(self, task: Task) -> None:
        space_id = task.space_id
        session_id = UUID(task.payload["session_id"])
        config = self._config.space(space_id)
        async with self._uow(space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            if session is None:
                return
            profile_id = task.profile_id or session.profile_id
            kind = await _profile_kind(uow, profile_id)
            rules = rules_for(config, session.source_id, staff_said=said_by_staff(config, session, kind))
            if _finished(await uow.extract.get_run(session_id, run_key(rules.versions_key, session))):
                return  # the run and its `reconcile` committed together; this is a redelivery
            alone = task.payload.get("reason") == "reextract"
            batch = await self._batch(uow, config, session, rules, profile_id, kind, alone=alone)
            if batch is None:
                return  # a session of the customer that closed later takes this one in its batch
            sessions, leftover = batch
            material, apart = await self._material(uow, config, sessions, profile_id, rules)
        final = int(task.payload.get(ATTEMPT_KEY, 1)) >= self._settings.max_attempts
        try:
            triage = await self._triage(space_id, material, rules)
        except BudgetExceeded as exc:
            await self._wait_for_budget(task, config, exc)
            return
        full = await self._without_model(space_id, material, rules, triage, profile_id)
        if full:
            rest = await self._with_observed(
                space_id, dataclasses.replace(material, sessions=tuple(full)), rules
            )
            done = await self._extract_full(task, config, rules, rest, triage, profile_id, final=final)
            if not done:
                return
        following = leftover or (apart[-1] if apart else None)
        if following is not None:
            # More of the customer's sessions waited than one call takes, or some of them see the customer
            # through another view: the newest of those runs next, and takes the others of its batch.
            async with self._uow(space_id) as uow:
                await uow.pipeline.enqueue(
                    [Task(TaskName.EXTRACT, space_id, {"session_id": str(following)}, profile_id=profile_id)]
                )

    async def _with_observed(
        self, space_id: UUID, material: BatchMaterial, rules: ExtractionRules
    ) -> BatchMaterial:
        """The operations the space's agents record and their meanings, read outside any
        transaction: writing a missing meaning calls the generation model."""
        if self._observed is None:
            return material
        observed = await self._observed.current(space_id)
        declared = [name for name, _ in rules.operation_meanings]
        return dataclasses.replace(
            material, observed_operations=observed.names, observed_meanings=observed.meanings(declared)
        )

    async def _batch(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        session: SessionRecord,
        rules: ExtractionRules,
        profile_id: UUID | None,
        kind: SubjectKind | None,
        *,
        alone: bool,
    ) -> tuple[list[SessionRecord], UUID | None] | None:
        """The sessions this task extracts, oldest first, and the newest one left for a next call; None when
        a session of the customer that closed later is still waiting, since its task takes this one.

        A batch shares the extraction rules (the source's predicates and guidance) and, checked once the
        events are read, the profile view (5.3). A re-extraction goes alone."""
        if alone or profile_id is None or session.closed_at is None:
            return [session], None
        closed = await uow.sessions.closed_for_profile(
            profile_id, self._clock.now() - BATCH_HORIZON, limit=BATCH_MAX_SESSIONS * 4
        )
        runs = await uow.extract.run_keys([s.session_id for s in closed])
        waiting = [
            s
            for s in closed
            if rules_for(config, s.source_id, staff_said=said_by_staff(config, s, kind)) == rules
            and not any(
                key == run_key(rules.versions_key, s) and status in _DONE
                for key, status in runs.get(s.session_id, [])
            )
        ]
        if not any(s.session_id == session.session_id for s in waiting):
            return [session], None
        if any(_order(s) > _order(session) for s in waiting):
            return None
        picked = waiting[-BATCH_MAX_SESSIONS:]
        rest = waiting[:-BATCH_MAX_SESSIONS] if len(waiting) > BATCH_MAX_SESSIONS else []
        return picked, (rest[-1].session_id if rest else None)

    async def _triage(self, space_id: UUID, material: BatchMaterial, rules: ExtractionRules) -> Triage:
        """The decision model's one answer about the batch's sessions that say something; a batch of trivial
        sessions asks nothing."""
        said = [s for s in material.sessions if not is_trivial(s.events)]
        if not said:
            return Triage.conservative(0)
        transcripts = [
            "\n".join(
                f"{e.speaker.value}: {e.text}"
                for e in turns(s.events)
                if e.kind is EventKind.MESSAGE and e.text
            )
            for s in said
        ]
        found = await self._decide.triage(
            space_id,
            transcripts,
            rules.episode_categories,
            [f"session:{s.session.session_id}" for s in said],
        )
        # Back to the batch's order, with the trivial sessions (never light: they get the template).
        by_id = {s.session.session_id: i for i, s in enumerate(said)}
        index = [by_id.get(s.session.session_id) for s in material.sessions]
        return Triage(
            answered=tuple(found.answered[i] if i is not None else 0.0 for i in index),
            categories=tuple(found.categories[i] if i is not None else None for i in index),
            injection=found.injection,
            sensitive=found.sensitive,
            model_version=found.model_version,
        )

    async def _without_model(
        self,
        space_id: UUID,
        material: BatchMaterial,
        rules: ExtractionRules,
        triage: Triage,
        profile_id: UUID | None,
    ) -> list[SessionMaterial]:
        """Writes the runs that need no extraction model (a trivial or an answered short session, by its own
        turns) and returns the sessions that do."""
        written: list[tuple[SessionMaterial, ExtractionResult]] = []
        full: list[SessionMaterial] = []
        for i, s in enumerate(material.sessions):
            if not s.events or is_agent_task(s.session.kind, s.events):
                # Nothing was said (a proof or a read alone) or an internal agent only acted: no episode.
                written.append((s, ExtractionResult(episode=None)))
            elif is_trivial(s.events):
                written.append((s, trivial_result(s.events, rules.language, staff=rules.staff)))
            elif is_short(s.events) and triage.light(i):
                result = answered_result(
                    s.events,
                    rules.language,
                    triage.categories[i],
                    rules.intents,
                    rules.outcomes,
                    staff=rules.staff,
                )
                held = held_numbers(e.text for e in s.events if e.text)
                written.append((s, withhold_result(result, rules, held, language(rules.language), no_topics)))
            else:
                full.append(s)
        if written:
            async with self._uow(space_id) as uow:
                for s, result in written:
                    await self._finish(uow, space_id, s.session, rules, RunStatus.MINIMAL, result, profile_id)
        return full

    async def _extract_full(
        self,
        task: Task,
        config: SpaceConfig,
        rules: ExtractionRules,
        material: BatchMaterial,
        triage: Triage,
        profile_id: UUID | None,
        *,
        final: bool,
    ) -> bool:
        """The extraction model's call for the sessions that need it; False when the task was rescheduled
        (a retry, a spent budget) instead of finishing."""
        space_id = task.space_id
        try:
            flagged = await self._screen(space_id, material, triage, final=final)
        except DecisionsPendingError:
            await self._retry_or_fallback(task, material, rules, profile_id, None, frozenset())
            return False
        except ModelsRefused:
            await self._retry_or_fallback(task, material, rules, profile_id, None, frozenset(), settle=True)
            return False
        except BudgetExceeded as exc:
            await self._wait_for_budget(task, config, exc)
            return False
        parts = windows(material)
        if len(parts) > 1:
            # Every turn is read whole: a batch past one call's budget takes one call per window.
            log.info("extraction reads %d sessions in %d windows", len(material.sessions), len(parts))
        read: dict[UUID, list[ExtractionResult]] = {}
        spent: dict[UUID, list[_Spend]] = {}
        for window in parts:
            try:
                prompt, spans = await self._prompt(space_id, window, rules, flagged)
            except DecisionsPendingError:
                await self._retry_or_fallback(task, material, rules, profile_id, None, flagged)
                return False
            except ModelsRefused:
                # The pod refused the texts, and it would refuse them again: the batch settles now.
                await self._retry_or_fallback(task, material, rules, profile_id, None, flagged, settle=True)
                return False
            except BudgetExceeded as exc:
                await self._wait_for_budget(task, config, exc)
                return False
            try:
                answer = await self._llm.complete_json(
                    system=prompt.system,
                    user=prompt.user,
                    json_schema=prompt.schema,
                    reasoning=effort(window, spans),
                    max_output_tokens=max(
                        self._settings.max_output_tokens, OUTPUT_TOKENS_PER_SESSION * len(window.sessions)
                    ),
                    space_id=space_id,
                    purpose=PURPOSE,
                )
            except BudgetExceeded as exc:
                await self._wait_for_budget(task, config, exc)
                return False
            except NiadraError as exc:
                log.warning("extraction call failed: %s", exc.code)
                await self._retry_or_fallback(task, material, rules, profile_id, None, flagged)
                return False
            try:
                # The sensitive categories are the decision model's, asked next (`_decided`).
                found = validate(answer.content, prompt, window, rules, no_topics)
            except InvalidExtractionError:
                log.warning("extraction answer rejected by validation")
                await self._retry_or_fallback(task, material, rules, profile_id, answer, flagged)
                return False
            for s, share in zip(window.sessions, _shares(answer, len(window.sessions)), strict=True):
                sid = s.session.session_id
                read.setdefault(sid, []).append(found[sid])
                spent.setdefault(sid, []).append(share)
        results = {sid: merged(found) for sid, found in read.items()}
        try:
            results = await self._decided(space_id, material, rules, results, triage, final=final)
        except DecisionsPendingError:
            await self._retry_or_fallback(task, material, rules, profile_id, None, prompt.flagged)
            return False
        except BudgetExceeded as exc:
            await self._wait_for_budget(task, config, exc)
            return False
        async with self._uow(space_id) as uow:
            for s in material.sessions:
                share = _summed(spent[s.session.session_id])
                result = dataclasses.replace(
                    results[s.session.session_id],
                    flagged_event_ids=tuple(sorted(e.event_id for e in s.events if e.event_id in flagged)),
                )
                if result.withheld:
                    # Counts only: what was withheld is exactly what must never reach a log.
                    log.info(
                        "extraction withheld %d numbers and dropped %d items", result.withheld, result.dropped
                    )
                await self._finish(uow, space_id, s.session, rules, RunStatus.OK, result, profile_id, share)
        return True

    async def watch_extractor(self, space_id: UUID) -> int:
        """A per-space loop step: when the extractor, its prompt or the space's extraction schema
        changed, the recent sessions are extracted again, a bounded pass, so memory and the measurement
        follow the new version. The first pass only records the version."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        key = extractor_key(config)
        current = extractor_fingerprint(key)
        async with self._uow(space_id) as uow:
            stored, _ = await uow.extract.extractor_state()
            if stored == current:
                return 0
            if stored is None:
                await uow.extract.set_extractor_state(current, None)
                return 0
            now = self._clock.now()
            await uow.extract.set_extractor_state(current, now)
            since = now - reextract_window(config)
            payload = {"versions_key": key, "since": since.isoformat(), "sent": 0}
            await uow.pipeline.enqueue([Task(TaskName.REEXTRACT, space_id, payload)])
        return 1

    async def reextract(self, task: Task) -> None:
        """One page of the pass: sessions closed in the window whose last good run came from an older
        extractor get a new `extract`, `REEXTRACT_DAILY` a day until the window is done; the AI budget of the
        space still applies to each call."""
        space_id = task.space_id
        config = self._config.space(space_id)
        key = str(task.payload["versions_key"])
        if key != extractor_key(config):
            return  # a newer change started its own pass
        since = datetime.fromisoformat(task.payload["since"])
        after = UUID(task.payload["after"]) if task.payload.get("after") else None
        sent = int(task.payload.get("sent", 0))
        today = _day(self._clock.now()).isoformat()
        sent_today = int(task.payload.get("sent_today", 0)) if task.payload.get("day") == today else 0
        async with self._uow(space_id) as uow:
            sessions = await uow.sessions.closed_since(since, after_id=after, limit=REEXTRACT_PAGE)
            runs = await uow.extract.run_keys([s.session_id for s in sessions])
            wanted = [s for s in sessions if needs_reextraction(runs.get(s.session_id, []), key)]
            await uow.pipeline.enqueue(
                [
                    Task(
                        TaskName.EXTRACT,
                        space_id,
                        {"session_id": str(s.session_id), "reason": "reextract"},
                        profile_id=s.profile_id,
                    )
                    for s in wanted
                ]
            )
            sent += len(wanted)
            sent_today += len(wanted)
            if len(sessions) < REEXTRACT_PAGE:
                log.info("reextraction pass done: %d sessions", sent)
                return
            following = {
                **task.payload,
                "after": str(sessions[-1].session_id),
                "sent": sent,
                "day": today,
                "sent_today": sent_today,
            }
            # A pause between pages keeps the extraction queue open to live sessions; past the day's share,
            # the pass goes on after midnight UTC.
            at = self._clock.now() + REEXTRACT_PAUSE
            if sent_today >= REEXTRACT_DAILY:
                at = datetime.combine(_day(self._clock.now()) + timedelta(days=1), time(), tzinfo=UTC)
                log.info("reextraction pass sent %d sessions so far; it goes on tomorrow", sent)
            again = Task(TaskName.REEXTRACT, space_id, following)
            await uow.pipeline.schedule(again, at, DelayReason.REEXTRACT)

    async def _wait_for_budget(self, task: Task, config: SpaceConfig, exc: BudgetExceeded) -> None:
        # Every deferred extraction of the day waits for 00:00 UTC; each comes back at its own offset into
        # the spread, so the cell does not take them all at midnight.
        spread = spread_offset(task.id, timedelta(seconds=self._settings.budget_spread_s))
        await self._delay(task, timedelta(seconds=exc.retry_after_s) + spread, DelayReason.LLM_BUDGET)
        await self._budget_reached(task.space_id, config, exc)

    async def _budget_reached(self, space_id: UUID, config: SpaceConfig, exc: BudgetExceeded) -> None:
        """The company hears, once a day, that memory waits for the next day's budget: by webhook,
        and as a `budget_reached` issue the Console shows next to the others."""
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            if not await uow.extract.mark_budget_notice(_day(now), PURPOSE):
                return
            if self._issues is not None:
                await self._issues.open(uow, space_id, "budget_reached", source=PURPOSE)
            data = {
                "purpose": PURPOSE,
                "day": _day(now).isoformat(),
                "cap_micros": exc.cap_micros,
                "resumes_at": (now + timedelta(seconds=exc.retry_after_s)).isoformat(),
            }
            await enqueue_event(uow, config, "usage.budget_reached", None, data)

    async def _material(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        sessions: Sequence[SessionRecord],
        profile_id: UUID | None,
        rules: ExtractionRules,
    ) -> tuple[BatchMaterial, list[UUID]]:
        """The batch's turns, and what memory holds for the customer as the batch's sessions see it; and the
        sessions left out, oldest first, because their view of the profile differs from the newest one's
        (5.3: an unconfirmed link), for a call of their own."""
        events = {s.session_id: await session_events(uow, s) for s in sessions}
        newest = sessions[-1]
        profile = await uow.identity.get_profile(profile_id) if profile_id else None
        view: ProfileRecord | None = None
        if profile is not None:
            # What the model sees of the customer is what this conversation's writes see (5.3): never the
            # rows of an unconfirmed link from another conversation, nor the confirmed rows from one that
            # speaks only through an unconfirmed link.
            view = session_view(profile, _handles(events[newest.session_id]))
            same = [
                s
                for s in sessions
                if s is newest or _same_view(session_view(profile, _handles(events[s.session_id])), view)
            ]
            apart = [s.session_id for s in sessions if s not in same]
            sessions = same
        else:
            apart = []
        handle_ids = (
            list(view.handle_ids)
            if view
            else sorted({h for s in sessions for h in _handles(events[s.session_id])})
        )
        facts = await uow.memory.facts_for(handle_ids) if handle_ids else []
        items = await uow.memory.open_items_for(handle_ids, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE))
        links = await uow.identity.active_links(handle_ids) if handle_ids else []
        delivered: set[str] = set()
        for s in sessions:
            thread = s.conversation_id or s.task_id
            if thread:
                receipts = await uow.audit.receipts_for_conversation(thread, since=s.opened_at)
                delivered |= {str(i) for r in receipts for i in r.get("item_ids") or ()}
        transcript = " ".join(e.text for s in sessions for e in events[s.session_id] if e.text)
        oldest = min(s.last_event_at for s in sessions)
        since = oldest - timedelta(days=RECENT_CATEGORIES_DAYS)
        episodes = await uow.memory.episodes_for(handle_ids, since=since, limit=50) if handle_ids else []
        # A person who acts for an organization (21.4) asks for what is done there: its agents' actions and
        # its system events name the operations the person's items may wait for.
        named_by = sorted({*handle_ids, *await organization_handles(uow, handle_ids, links)})
        recorded = (
            await uow.objects.actions_for(named_by, since=oldest - RECORDED_OPERATIONS_AGE, limit=200)
            if named_by
            else []
        )
        # What the systems of record confirmed for the customer (`fields.operation`): an item may wait for
        # an operation no agent records, such as a filing the court system reports.
        confirmed = (
            await uow.ingest.system_events_for_handles(
                named_by, oldest - RECORDED_OPERATIONS_AGE, RECORDED_SYSTEM_EVENTS
            )
            if named_by
            else []
        )
        # What a natural key or "the order" in the turns may name: the objects the customer, or the
        # organization they act for, is tied to, and never another.
        tied = await uow.objects.objects_for(named_by, limit=OBJECTS_CEILING) if named_by else []
        batch_ids = {s.session_id for s in sessions}
        episodes = [e for e in episodes if e.session_id not in batch_ids]
        ordered = sorted(sessions, key=lambda s: (s.last_event_at, s.session_id))
        return BatchMaterial(
            sessions=tuple(SessionMaterial(s, tuple(events[s.session_id])) for s in ordered),
            facts=tuple(select_candidates(facts, transcript)),
            open_items=tuple(items),
            links=tuple(links),
            delivered_item_ids=frozenset(delivered),
            recent_categories=recent_categories(episodes, rules),
            recent_matters=recent_matters(episodes),
            recorded_operations=recorded_operations(
                [a.operation for a in recorded],
                [*(e for s in sessions for e in events[s.session_id]), *confirmed],
            ),
            objects=known_objects(tied, config),
        ), apart

    async def _screen(
        self, space_id: UUID, material: BatchMaterial, triage: Triage, *, final: bool
    ) -> frozenset[UUID]:
        """The batch's turns the decision model reads as injection. The turns are checked one by one only
        when the triage's screen flagged the batch (C3): an unanswered triage flags it. Once per batch,
        whatever number of windows its extraction takes."""
        flagged: frozenset[UUID] = frozenset()
        if triage.injection >= INJECTION_SCREEN:
            # Every turn, whoever said it: an agent can repeat an instruction it was fed, and a
            # tool's output reaches the log as a turn of its own.
            said = [e for s in material.sessions for e in turns(s.events) if e.text]
            guards = await self._decide.decide_many(
                space_id,
                DecisionTask.INJECTION,
                [e.text or "" for e in said],
                [f"event:{e.event_id}" for e in said],
            )
            if not final and not all(answered(g) for g in guards):
                # A turn the decision model did not answer reads as an injection, which would quarantine
                # every fact of the session: the extraction waits for the model, and only its last attempt
                # settles.
                raise DecisionsPendingError
            flagged = frozenset(
                e.event_id for e, g in zip(said, guards, strict=True) if g.label == "injection"
            )
            # Only the model's own verdicts are marked on the log: a default for a model that did not answer
            # is this extraction's caution, not a fact about the turn.
            marked = sorted(
                e.event_id
                for e, g in zip(said, guards, strict=True)
                if g.label == "injection" and answered(g)
            )
            if marked:
                # Objects applies actions before the extraction runs, so the mark must reach the log right
                # away: actions ingested later are quarantined at apply time, earlier ones in `reconcile`
                # (18.4). As the system role: the application role cannot change a stored event.
                async with self._uow.system(space_id) as uow:
                    await uow.ingest.mark_content(marked, {"injection": True})
        return flagged

    async def _prompt(
        self, space_id: UUID, material: BatchMaterial, rules: ExtractionRules, flagged: frozenset[UUID]
    ) -> tuple[Prompt, list[list[PiiSpan]]]:
        """One call's prompt, and the PII spans of every text it shows."""
        pairs = redactable(material)
        try:
            # Masked whole, a text tells the extraction model nothing, and the run would store that as the
            # session's memory: a models pod that did not answer means try again, and the last attempt gives
            # the batch the template (`_retry_or_fallback`).
            spans = await self._decide.pii_spans(
                space_id, [t for _, t in pairs], [k for k, _ in pairs], wait=True
            )
        except ModelsPendingError:
            raise DecisionsPendingError from None
        prompt = build_prompt(
            material, rules, flagged=flagged, spans=dict(zip([k for k, _ in pairs], spans, strict=True))
        )
        return prompt, spans

    async def _decided(
        self,
        space_id: UUID,
        material: BatchMaterial,
        rules: ExtractionRules,
        results: dict[UUID, ExtractionResult],
        triage: Triage,
        *,
        final: bool,
    ) -> dict[UUID, ExtractionResult]:
        """What the decision model decides about the validated output: the sensitive category of
        every text memory would keep as ordinary, asked only when the triage's screen found a special
        category in the batch (C3), and what each agent question does."""
        texts = (
            list(dict.fromkeys(t for r in results.values() for t in sensitive_candidates(r, rules)))
            if triage.sensitive >= SENSITIVE_SCREEN
            else []
        )
        topics: dict[str, tuple[str, ...]] = {}
        if texts:
            ref = f"session:{material.sessions[-1].session.session_id}"
            labels = await self._decide.decide_many(
                space_id, DecisionTask.SENSITIVE_CATEGORY, texts, [ref] * len(texts)
            )
            if not final and not all(answered(label) for label in labels):
                # Unanswered reads as sensitive, which drops the text: wait for the model instead.
                raise DecisionsPendingError
            topics = {t: label_topics(label.label) for t, label in zip(texts, labels, strict=True)}
        # Idempotent: the numbers were withheld by the validation; this files what the model called sensitive.
        decided = {
            sid: withhold_result(r, rules, {}, language(rules.language), lambda text: topics.get(text, ()))
            for sid, r in results.items()
        }
        return await self._question_types(space_id, material, decided)

    async def _question_types(
        self, space_id: UUID, material: BatchMaterial, results: dict[UUID, ExtractionResult]
    ) -> dict[UUID, ExtractionResult]:
        """Each agent question's type is the decision model's; a question it did not answer, or answered
        without confidence, is `ambiguous`, which the measurement does not count (19.2)."""
        said = {e.event_id: e.text for e in material.events if e.text}
        asked = [q for r in results.values() for q in r.agent_questions if said.get(q.event_id)]
        if not asked:
            return results
        labels = await self._decide.decide_many(
            space_id,
            DecisionTask.QUESTION_TYPE,
            [said[q.event_id] or "" for q in asked],
            [f"event:{q.event_id}" for q in asked],
        )
        decided = {q.event_id: QuestionType(label.label) for q, label in zip(asked, labels, strict=True)}
        return {
            sid: dataclasses.replace(
                r,
                agent_questions=tuple(
                    dataclasses.replace(q, type=decided.get(q.event_id, QuestionType.AMBIGUOUS))
                    for q in r.agent_questions
                ),
            )
            for sid, r in results.items()
        }

    async def _retry_or_fallback(
        self,
        task: Task,
        material: BatchMaterial,
        rules: ExtractionRules,
        profile_id: UUID | None,
        answer: LlmResult | None,
        flagged: frozenset[UUID],
        *,
        settle: bool = False,
    ) -> None:
        """Retries the task's batch with backoff; its last attempt, or a failure no retry would change
        (`settle`), gives every session of the batch the template episode, marked `failed` for the nightly
        job."""
        attempt = int(task.payload.get(ATTEMPT_KEY, 1))
        newest = material.sessions[-1].session
        async with self._uow(task.space_id) as uow:
            if attempt < self._settings.max_attempts and not settle:
                if answer is not None:
                    # The call's cost stays on the record, on the session whose task ran it.
                    await uow.extract.save_run(
                        self._run(newest, rules, RunStatus.INVALID, None, _spend(answer))
                    )
                retry = dataclasses.replace(
                    task, id=uuid7(), payload={**task.payload, ATTEMPT_KEY: attempt + 1}
                )
                wait = timedelta(
                    seconds=self._settings.retry_base_s * self._settings.retry_factor ** (attempt - 1)
                )
                await uow.pipeline.schedule(retry, self._clock.now() + wait, DelayReason.LLM_RETRY)
                return
            for s in material.sessions:
                result = dataclasses.replace(
                    minimal_result(s.session.channel, s.events, rules.language),
                    flagged_event_ids=tuple(sorted(e.event_id for e in s.events if e.event_id in flagged)),
                )
                usage = _spend(answer) if answer is not None and s.session is newest else None
                await self._finish(
                    uow, task.space_id, s.session, rules, RunStatus.FAILED, result, profile_id, usage
                )

    async def _finish(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        session: SessionRecord,
        rules: ExtractionRules,
        status: RunStatus,
        result: ExtractionResult,
        profile_id: UUID | None,
        usage: _Spend | None = None,
    ) -> None:
        """Writes the session's run and its `reconcile` task, unless a run of another batch finished it
        meanwhile (two batches raced for it: the first one's memory stands)."""
        key = run_key(rules.versions_key, session)
        if _finished(await uow.extract.get_run(session.session_id, key)):
            return
        await uow.extract.save_run(self._run(session, rules, status, result, usage))
        payload = {"session_id": str(session.session_id), "versions_key": key}
        await uow.pipeline.enqueue([Task(TaskName.RECONCILE, space_id, payload, profile_id=profile_id)])

    async def _delay(self, task: Task, wait: timedelta, reason: DelayReason) -> None:
        async with self._uow(task.space_id) as uow:
            await uow.pipeline.schedule(
                dataclasses.replace(task, id=uuid7()), self._clock.now() + wait, reason
            )

    def _run(
        self,
        session: SessionRecord,
        rules: ExtractionRules,
        status: RunStatus,
        result: ExtractionResult | None,
        usage: _Spend | None,
    ) -> ExtractionRun:
        return ExtractionRun(
            session_id=session.session_id,
            versions_key=run_key(rules.versions_key, session),
            status=status,
            created_at=self._clock.now(),
            model=usage.model if usage else "",
            provider=usage.provider if usage else "",
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            cached_tokens=usage.cached_tokens if usage else 0,
            cost_micros=usage.cost_micros if usage else 0,
            dropped_items=result.dropped if result else 0,
            result=result,
        )


def recent_categories(episodes: Sequence[Episode], rules: ExtractionRules) -> tuple[str, ...]:
    """The categories the customer's other conversations got, newest first: only ones the schema still
    offers, and never the placeholder of a session nobody extracted."""
    offered = set(rules.episode_categories)
    ordered = sorted(episodes, key=lambda e: (e.ended_at, e.episode_id), reverse=True)
    names = [e.category for e in ordered if e.category in offered]
    return tuple(dict.fromkeys(names))[:RECENT_CATEGORIES_MAX]


def recent_matters(episodes: Sequence[Episode]) -> tuple[tuple[str, str], ...]:
    """The matters of the customer's other conversations, newest first, each once with its newest label."""
    ordered = sorted(episodes, key=lambda e: (e.ended_at, e.episode_id), reverse=True)
    found: dict[str, str] = {}
    for episode in ordered:
        if episode.matter and episode.matter not in found:
            found[episode.matter] = episode.matter_label or ""
    return tuple(found.items())[:RECENT_MATTERS_MAX]


_WORDS = re.compile(r"[a-z]+")


def effort(material: BatchMaterial, spans: Sequence[Sequence[PiiSpan]]) -> Reasoning:
    """`none` when every session has at most `QUIET_TURNS` messages and nothing else, and nothing the model
    reads names a document, a card, a bank account or a secret, by the PII model's spans or by its words;
    `low` otherwise (C2)."""
    for s in material.sessions:
        said = [e for e in s.events if e.kind is EventKind.MESSAGE]
        if len(said) != len(s.events) or len(said) > QUIET_TURNS:
            return REASONING
    if any(span.kind in WITHHELD_KINDS for found in spans for span in found):
        return REASONING
    if held_numbers(e.text for e in material.events if e.text):
        return REASONING
    # A word that names a document, a card, an account or a secret anywhere in a turn, even after the number
    # ("anota aí: 54726939, é o meu documento"): the planted-document test of 28/09 found the one phrasing
    # the words before a number miss, and at `none` the model copied such a number once in 200 sessions.
    if any(label_word(w) for e in material.events if e.text for w in _WORDS.findall(fold(e.text))):
        return REASONING
    return QUIET_REASONING


_DONE = (RunStatus.OK, RunStatus.MINIMAL, RunStatus.FAILED)


def _finished(run: ExtractionRun | None) -> bool:
    return run is not None and run.status in _DONE


def _order(session: SessionRecord) -> tuple[datetime, UUID]:
    """Which of a customer's closed sessions is the newest: the last to close, then the last created."""
    assert session.closed_at is not None
    return session.closed_at, session.session_id


def _handles(events: Sequence[EventRecord]) -> set[UUID]:
    return {h for e in events for h in e.handle_ids}


def _same_view(a: ProfileRecord, b: ProfileRecord) -> bool:
    return (a.handle_ids, a.provisional_handle_ids, a.hidden_handle_ids) == (
        b.handle_ids,
        b.provisional_handle_ids,
        b.hidden_handle_ids,
    )


def extractor_key(config: SpaceConfig) -> str:
    """The versions every source of the space extracts with: extractor, prompt and schema (5.5, L13)."""
    return f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.{config.extraction.version}"


def extractor_fingerprint(key: str) -> int:
    """The key as a signed 63-bit number for `space_state`."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") >> 1


def reextract_window(config: SpaceConfig) -> timedelta:
    """Sessions of the last 30 days, and never older than the hot window: their events are in the database."""
    return timedelta(days=min(REEXTRACT_DAYS, config.settings.events_hot_days - 1))


def needs_reextraction(runs: list[tuple[str, RunStatus]], key: str) -> bool:
    """A session whose newest good extraction came from another extractor, and none from this one. A
    session the gate skipped or whose extraction failed is left as it is."""
    bases = {versions.split("#", 1)[0]: status for versions, status in runs}
    return key not in bases and any(status is RunStatus.OK for status in bases.values())


async def _read_sees(uow: UnitOfWork, space_id: UUID, run: ExtractionRun) -> bool:
    """Memory applied the run, and the profile's compilation reached the request that covers it: one
    primary-key read of the work counter, and none when the run asked for no compilation."""
    if run.applied_work is None:
        return False  # not applied yet
    if run.applied_profile_id is None or run.applied_work == 0:
        return True  # applied to nothing, or it asked for no compilation
    done = await uow.pipeline.work_done(space_id, run.applied_profile_id, TaskName.COMPILE)
    return done >= run.applied_work


def run_key(versions_key: str, session: SessionRecord) -> str:
    """A session a late turn reopened is extracted again, as a new run: the episode it gives is
    the next version of the same episode, never a second episode."""
    return versions_key if not session.reopened else f"{versions_key}#r{session.reopened}"


def _day(at: datetime) -> date:
    return at.astimezone(UTC).date()


def said_by_staff(config: SpaceConfig, session: SessionRecord, kind: SubjectKind | None) -> bool:
    """Whether the person of a session is the company's staff: a task's always is, and in a conversation only
    staff named by the company's own system id is; a customer at an internal key is a customer.
    """
    source = config.source(session.source_id)
    conversation = session.conversation_id is not None and session.task_id is None
    return staff(conversation_audience(source.audience if source else None, kind, conversation=conversation))


async def _profile_kind(uow: UnitOfWork, profile_id: UUID | None) -> SubjectKind | None:
    profile = await uow.identity.get_profile(profile_id) if profile_id is not None else None
    return profile.kind if profile is not None else None
