"""Reconciliation and memory (5.8, 5.9, 18.4): apply a validated extraction under the profile lease.

Every step is idempotent, because a task can be delivered twice and sessions of one profile can
be processed in any order: facts dedupe by the unique (origin handle, predicate, HMAC of the
normalized value) and fold observations with order-free aggregates; mentions, relations, items
and episodes are keyed so a replay inserts nothing; statuses are recomputed from the rows.
"""

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.app.decide import DecideService
from niadra.app.extract import organization_handles, session_events
from niadra.app.notify import enqueue_event, subscribes
from niadra.app.object_memory import participant_profiles, take_part
from niadra.app.objects import closing_payload, parents_of
from niadra.app.pipeline import fence
from niadra.app.profile_view import load_profile, read_fact_history, read_memory
from niadra.app.purposes import WRITE_BATCH, tag_rows
from niadra.config.models import PredicateSpec, SpaceConfig
from niadra.contracts.memory import FactHistory, ProfileMemory
from niadra.domain.common.errors import Conflict, NotFound, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal, governance_source
from niadra.domain.compile.model import ItemKind
from niadra.domain.compile.templates import Formatter
from niadra.domain.coordinate.window import Window
from niadra.domain.decide import DecisionTask, pair
from niadra.domain.extract.deadlines import is_deadline_item
from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    FactCommand,
    FactOp,
    HoursDraft,
    InstructionDraft,
    MemoryRef,
    OpenItemDraft,
    SlotDraft,
)
from niadra.domain.identity.provenance import (
    derivation_view,
    governance_view,
    session_view,
)
from niadra.domain.ingest.events import FEEDBACK_PREFIX
from niadra.domain.memory.anchor import Anchor, anchor
from niadra.domain.memory.bound import (
    MAX_HANDLES,
    OBJECTS_CEILING,
    ROWS_CEILING,
    audience_of,
    said_by,
    same_value,
    staff,
)
from niadra.domain.memory.contradictions import MAX_PAIRS, BandPair, band_pairs
from niadra.domain.memory.facts import (
    HALF_LIFE_DAYS,
    Relation,
    confidence,
    contention_group,
    decayed,
    merge_observation,
    normalize_value,
    ref,
    retire,
    settle,
    slot_mates,
    supersede,
)
from niadra.domain.memory.ids import stable_uuid7
from niadra.domain.memory.items import (
    CLOSING_WINDOW,
    OPEN_STATUSES,
    Closing,
    amount_of,
    amounts_in,
    closes,
    is_overdue,
    item_id_for,
    item_words,
    noted_at,
    parse_amount,
    recorded_words,
    restates,
    restates_elsewhere,
    restates_on_object,
    same_day,
    same_family,
    to_judge,
    to_judge_again,
    to_judge_retelling,
    with_closed_by,
)
from niadra.domain.memory.profile import merge_attr
from niadra.domain.memory.restore import (
    REFUSAL_DETAIL,
    RESTORE_ACTION,
    Refusal,
    is_correction_of,
    refusal,
    replacing,
    restored,
)
from niadra.domain.memory.spoken import conversation_spoken_by
from niadra.domain.memory.summaries import LEVELS, TailState, tail_summaries
from niadra.domain.memory.values import SettledValue, ValueOrigin, ValueType, find_values, fold, value_key
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.notify.webhooks import WebhookSubject, external_reference
from niadra.domain.objects.operations import (
    canonical_operations,
    operation_of_event_type,
    trusts,
)
from niadra.domain.objects.state import operation_of
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.policy import ItemAttrs
from niadra.domain.policy.audience import conversation_audience
from niadra.domain.privacy.purposes import Tags, configured, readers
from niadra.domain.records import (
    BusinessObject,
    Episode,
    EventRecord,
    Fact,
    HandleRecord,
    ObjectKey,
    OpenItem,
    ProfileAttr,
    ProfileRecord,
    SessionRecord,
    declared_validity,
)
from niadra.domain.review.sampling import CONTRADICTION_BAND
from niadra.domain.serve.history import pooled, vector_chunks, vector_text
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    ActionStatus,
    Audience,
    EventKind,
    FactStatus,
    HandleStatus,
    MemoryAudience,
    OpenItemKind,
    OpenItemStatus,
    RelationType,
    Sensitivity,
    Speaker,
    SpokenBy,
    SubjectKind,
    Visibility,
)
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import Clock, ConfigProvider, ModelsClient, NoTelemetry, Telemetry
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions, SaidHours, SaidInstruction
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

CONFIRMED_EVENTS = 200
"""The system events a new item looks through for an operation already confirmed (`_already_confirmed`)."""

ALL_FACT_STATUSES = tuple(FactStatus)
RECOMPUTED_FROM_STATE = (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.TRIGGER)
# A correction sent by the company through `/v1/feedback` weighs like a confirmed record.
CORRECTION_CONFIDENCE = 0.95
ENCODER_PROBE = "niadra"
# The loops run the backfill per space database: again soon while it finds rows, up to ten minutes apart
# once the space is done.
DUE_SWEEP_ITEMS = 2000
NIGHT_HOUR = 3
BAND_DRAW_FACTOR = 3
NIGHTLY_PROFILES = 50_000
NIGHTLY_EVERY_S = 600.0
BACKFILL_EVERY_S = 5.0
BACKFILL_IDLE_CAP_S = 600.0
RESOLVE_ITEM_TYPE = f"{FEEDBACK_PREFIX}resolve_open_item"
# The delivery gate (`close_items`): a customer-facing agent's action waits for the agent's reply in its
# conversation, up to `REPLY_WAITS` times `REPLY_WAIT`; then the last reply before it, within
# `REPLY_LOOKBACK`, is the one checked, and with none nothing closes.
REPLY_WAIT = timedelta(seconds=20)
REPLY_WAITS = 6
REPLY_LOOKBACK = timedelta(minutes=2)
AGENTS = frozenset({Speaker.AI_AGENT, Speaker.HUMAN_AGENT})
DELIVERED = frozenset({"delivers", "asks"})
MAX_JUDGED = 20
"""The pairs one run or one closing puts to the decision model at most, the newest open items first: an
ungrounded retelling is compared with the open items on every object of the subject, and each pair is a
request to the model under the space's daily budget."""
CONVERSATION_KEY = "conversation_id"
WAITS_KEY = "reply_waits"
JUDGED_KEY = "judged"
ASKED_KEY = "asked"


@dataclass(frozen=True, slots=True)
class MemorySettings:
    closing_window: timedelta = CLOSING_WINDOW
    half_life_days: float = HALF_LIFE_DAYS
    embed_batch: int = 64
    min_words_to_embed: int = 3
    # The backfill shares the encoder with searches, which wait at most 100 ms for it: small batches.
    backfill_batch: int = 16
    band_pairs_per_night: int = MAX_PAIRS


@dataclass(frozen=True, slots=True)
class _RestorePlan:
    refusal: Refusal | None
    corrections: list[Fact]
    restored_before: bool


@dataclass(frozen=True, slots=True)
class _Scope:
    """The profile as one session sees it: its handles and the session's events."""

    profile: ProfileRecord
    handles: dict[UUID, HandleRecord]
    session: SessionRecord
    events: dict[UUID, EventRecord]

    @property
    def handle_ids(self) -> list[UUID]:
        return sorted(self.handles)

    @property
    def conversation_handles(self) -> list[UUID]:
        return sorted({h for e in self.events.values() for h in e.handle_ids if h in self.handles})

    def anchor(self, event_ids: Sequence[UUID]) -> Anchor:
        evidence = [self.events[e] for e in event_ids if e in self.events]
        return anchor(evidence, self.handles, self.conversation_handles)

    def noted_at(self, event_ids: Sequence[UUID]) -> datetime:
        times = [self.events[e].occurred_at for e in event_ids if e in self.events]
        return max(times) if times else self.ended_at

    @property
    def ended_at(self) -> datetime:
        """When the conversation ended: its last event. The close comes later (an inactivity window, or on
        arrival for an imported history) and would make every imported episode as recent as the import."""
        return self.session.last_event_at

    @property
    def closed_at(self) -> datetime:
        return self.session.closed_at or self.session.last_event_at


def _settled(
    config: SpaceConfig, scope: _Scope, result: ExtractionResult
) -> dict[str, tuple[ValueType, str, list[EventRecord], ValueOrigin]]:
    """What the session settled (11, 3.2), by value key: the extraction's values, then whatever the
    deterministic net finds in the same turns, so a gated or failed session keeps its values too."""
    floor = config.settings.stt_confidence_floor
    flagged = set(result.flagged_event_ids)
    turns = [
        e
        for e in sorted(scope.events.values(), key=lambda e: (e.occurred_at, e.event_id))
        if e.kind is EventKind.MESSAGE
        and e.text
        and e.visibility is Visibility.PUBLIC
        and e.event_id not in flagged
        and (e.stt_confidence is None or e.stt_confidence >= floor)
    ]
    usable = {e.event_id: e for e in turns}
    found: dict[str, tuple[ValueType, str, list[EventRecord], ValueOrigin]] = {}
    for draft in result.values:
        evidence = [usable[e] for e in draft.evidence_event_ids if e in usable]
        if evidence:
            found.setdefault(value_key(draft.raw), (draft.type, draft.raw, evidence, ValueOrigin.EXTRACTION))
    for event in turns:
        for hit in find_values(event.text or ""):
            key = value_key(hit.raw)
            known = found.setdefault(key, (hit.type, hit.raw, [], ValueOrigin.RULE))
            if known[3] is ValueOrigin.RULE and event not in known[2]:
                known[2].append(event)
    return found


class MemoryService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        models: ModelsClient,
        decide: DecideService,
        clock: Clock,
        config: ConfigProvider,
        settings: MemorySettings | None = None,
        telemetry: Telemetry | None = None,
        *,
        instructions: ObjectInstructions | None = None,
        issues: DataIssues | None = None,
    ) -> None:
        """`instructions` keeps what people said about an object in the signals module;
        `issues` returns to the company an observation that diverges from its system of record (2.4)."""
        self._uow = uow
        self._models = models
        self._decide = decide
        self._clock = clock
        self._config = config
        self._settings = settings or MemorySettings()
        self._telemetry = telemetry or NoTelemetry()
        self._instructions = instructions or NoObjectInstructions()
        self._issues = issues

    async def profile_memory(
        self, principal: Principal, profile_id: UUID, *, timeline_cursor: str | None = None
    ) -> ProfileMemory:
        """The Console's view of one customer's memory (5.22), under the policy of the reader's role."""
        space_id = principal.space.space_id
        async with self._uow(space_id) as uow:
            return await read_memory(
                uow,
                principal,
                self._config.space(space_id),
                profile_id,
                self._clock.now(),
                timeline_cursor=timeline_cursor,
            )

    async def fact_history(self, principal: Principal, profile_id: UUID, fact_id: UUID) -> FactHistory:
        """The values one fact's slot held over time, for the Console and governance keys (5.22)."""
        space_id = principal.space.space_id
        async with self._uow(space_id) as uow:
            return await read_fact_history(uow, principal, self._config.space(space_id), profile_id, fact_id)

    async def check_restore(self, principal: Principal, profile_id: UUID, fact_id: UUID) -> None:
        """Refuses at once a restore the memory module would refuse when it applies it (WP-G): an unknown
        fact, one not retracted, one that came through an unconfirmed link, an erased, an undone or a merged
        one."""
        async with self._uow(principal.space.space_id) as uow:
            profile = await load_profile(uow, profile_id)
            scoped = governance_view(principal, profile)
            facts = await uow.memory.facts_for(scoped.handle_ids, ALL_FACT_STATUSES)
            target = next((f for f in facts if f.fact_id == fact_id), None)
            if target is None:
                raise NotFound("fact not found")
            plan = await self._restore_plan(uow, profile, facts, target)
        # A fact already brought back takes the request as a no-op, so a replayed key is a duplicate.
        if plan.refusal is Refusal.NOT_RETRACTED and plan.restored_before:
            return
        if plan.refusal is not None:
            raise Conflict(REFUSAL_DETAIL[plan.refusal])

    async def reconcile(self, task: Task) -> None:
        """Applies a session's extraction run, or a `feedback.*` event when the payload names one."""
        if "event_id" in task.payload:
            await self.apply_feedback(task)
            return
        session_id = UUID(task.payload["session_id"])
        versions_key = str(task.payload["versions_key"])
        config = self._config.space(task.space_id)
        retold = await self._retellings(task.space_id, session_id, versions_key, task.profile_id)
        async with self._uow(task.space_id) as uow:
            await fence(uow)
            run = await uow.extract.get_run(session_id, versions_key)
            session = await uow.sessions.get_session(session_id)
            if run is None or run.result is None or session is None:
                log.warning("reconcile skipped: run or session not found")
                if run is not None:
                    await uow.extract.mark_applied(session_id, versions_key, None, 0)
                return
            scope = await self._session_scope(uow, session, task.profile_id)
            if scope is None:
                log.warning("reconcile skipped: the session has no profile")
                await uow.extract.mark_applied(session_id, versions_key, None, 0)
                return
            before = await _facts_if_announced(uow, config, scope.profile)
            crossing = await self._apply(uow, config, scope, run.result, retold)
            origin = {
                "by": "extraction",
                "session_id": str(session.session_id),
                "source_id": str(session.source_id),
                "conversation_id": external_reference(session.conversation_id),
                "task_id": external_reference(session.task_id),
            }
            await _announce_facts(uow, config, scope.profile, before, origin)
            await self._request_recompute(uow, task.space_id, scope.profile.profile_id)
            await take_part(uow, _speakers(scope), _objects_of(scope, run.result))
            await self._recompile_participants(uow, task.space_id, scope.profile.profile_id, crossing)
            # Ingest status says `ready` once the compilation that covers this request is published, so a
            # read made then sees the run (7.1). `begin_work` reads the counter the request just bumped.
            work = await uow.pipeline.begin_work(task.space_id, scope.profile.profile_id, TaskName.COMPILE)
            await uow.extract.mark_applied(session_id, versions_key, scope.profile.profile_id, work)
            follow = {"session_id": str(session_id), "versions_key": versions_key}
            await uow.pipeline.enqueue(
                [
                    Task(TaskName.EMBED, task.space_id, profile_id=scope.profile.profile_id),
                    Task(TaskName.MEASURE, task.space_id, follow, profile_id=scope.profile.profile_id),
                ]
            )
        self._telemetry.memory_ready((self._clock.now() - scope.closed_at).total_seconds())

    async def _retellings(
        self, space_id: UUID, session_id: UUID, versions_key: str, profile_id: UUID | None
    ) -> dict[int, OpenItem | None]:
        """The run's items (by index) that the decision model judges tell again an open item, when the words
        alone do not settle it: one an earlier run of this session wrote from the same turns
        (`to_judge_again`), which the item restates in place, or one another conversation bound to the same
        object (`domain.memory.items.to_judge_retelling`, None), which it leaves as its speaker made it. Asked
        before the run's transaction, so no database connection waits on the model; a spent budget or an
        unanswered pair leaves the item to be opened."""
        async with self._uow(space_id) as uow:
            run = await uow.extract.get_run(session_id, versions_key)
            session = await uow.sessions.get_session(session_id)
            if run is None or run.result is None or session is None or not run.result.open_items:
                return {}
            drafts = run.result.open_items
            scope = await self._session_scope(uow, session, profile_id)
            if scope is None:
                return {}
            here = str(session_id)
            known = await uow.memory.open_items_for(scope.handle_ids, OPEN_STATUSES)
            earlier = [i for i in known if i.origin.get("session_id") == here]
            elsewhere = [i for i in known if i.origin.get("session_id") != here]
            on_object: dict[ObjectKey, list[OpenItem]] = {}
            anywhere: list[OpenItem] | None = None
            asked: list[tuple[int, OpenItem]] = []
            for n, draft in enumerate(drafts):
                again = (draft.description, draft.evidence_event_ids, draft.due_at, draft.object)
                asked += [(n, i) for i in earlier if to_judge_again(*again, draft.expected_operation, i)]
                if draft.object is None:
                    continue
                if draft.object not in on_object:
                    on_object[draft.object] = [
                        i
                        for i in await _object_items(uow, draft.object, elsewhere)
                        if i.origin.get("session_id") != here
                    ]
                items = on_object[draft.object]
                args = (draft.description, draft.due_at, draft.object, draft.expected_operation)
                if any(restates_on_object(*args, i) for i in items):
                    continue
                asked += [(n, i) for i in items if to_judge_retelling(*args, i, relayed=draft.relayed)]
                if not draft.grounded:
                    # Bound by the model alone: the open item it tells may be on another of the subject's
                    # objects (`_open_items`).
                    if anywhere is None:
                        anywhere = await _subject_items(uow, scope, elsewhere, here)
                    others = [i for i in anywhere if i.object is not None and i.object != draft.object]
                    if any(_retells_elsewhere(draft, i) for i in others):
                        continue
                    asked += [
                        (n, i)
                        for i in others
                        if to_judge_retelling(
                            draft.description,
                            draft.due_at,
                            i.object,
                            draft.expected_operation,
                            i,
                            relayed=draft.relayed,
                        )
                    ]
        asked = _bounded(asked, lambda pair_: pair_[1])
        if not asked:
            return {}
        try:
            labels = await self._decide.decide_many(
                space_id,
                DecisionTask.ITEM_RESTATES_ITEM,
                [pair(item.description, drafts[n].description) for n, item in asked],
                [f"item:{item.item_id}|session:{session_id}:{n}" for n, item in asked],
            )
        except BudgetExceeded:
            return {}
        retold: dict[int, OpenItem | None] = {}
        for (n, item), label in zip(asked, labels, strict=True):
            if label.label != "restates":
                continue
            mine = item if item.origin.get("session_id") == here else None
            if retold.get(n) is None:
                retold[n] = mine
        return retold

    async def close_items(self, task: Task) -> None:
        """Closes open items that an action or a system-of-record event fulfils (18.4).

        The objects module sends `{event_id, action_id, object, parents, operation, closes, handle_ids,
        occurred_at}` for the profile that owns the object. An organization's object also closes the items
        its contacts noted about it (21.2): a lawyer told to approve the firm's filing, a buyer promised the
        chain's delivery; and any object closes what its participants promised on it (`_others_items`). An
        action that the words cannot settle (`to_judge`) is put to the decision model after the transaction.

        A customer-facing agent's declared action in a conversation closes only what the agent's own reply
        there gives the customer (the delivery gate): "Já estou enviando para você" with the copy still
        undelivered, or "Posso te passar a data?" for a promise to tell the date, closes nothing
        (juridico-sandbox, 06/10/2026). The reply usually follows the action, so the task waits for it.
        """
        judged = frozenset(UUID(i) for i in task.payload.get(JUDGED_KEY) or ())
        asked = await self._close(task, judged, None)
        if asked is None:
            return
        said, pending, waiting, strict = asked
        pending = _bounded(pending, lambda item: item)
        if pending and not task.payload.get(ASKED_KEY):
            try:
                labels = await self._decide.decide_many(
                    task.space_id,
                    DecisionTask.ACTION_FULFILS_ITEM,
                    [pair(item.description, said or "") for item in pending],
                    [f"item:{item.item_id}|event:{task.payload.get('event_id')}" for item in pending],
                )
            except BudgetExceeded:
                labels = []  # nothing is judged today: the items stay open, as for a "no"
            done = {i.item_id for i, label in zip(pending, labels, strict=False) if label.label == "fulfils"}
            judged = judged | done
            waiting = [*waiting, *(i for i in pending if i.item_id in done)]
        if not waiting:
            return
        if not strict:
            # A system event or an internal agent's action: what the model judged it does closes now.
            await self._close(task, judged, frozenset(i.item_id for i in waiting))
            return
        if not self._needs_reply(task):
            await self._close(task, judged, frozenset(i.item_id for i in waiting))
            return
        reply = await self._reply(task)
        if reply is None:
            await self._wait_for_reply(task, judged)
            return
        delivered = await self._delivered(task, waiting, reply)
        if delivered:
            await self._close(task, judged, delivered)

    def _needs_reply(self, task: Task) -> bool:
        """An action sent within a conversation is checked against the agent's reply there; one with no
        conversation has no reply to check, and its gates are the words and the decision model's."""
        return bool(task.payload.get(CONVERSATION_KEY))

    async def _reply(self, task: Task) -> EventRecord | None:
        """The customer-facing agent's own message that goes with the action: the first one after it in the
        conversation, or, once the wait is over, the last one in `REPLY_LOOKBACK` before it."""
        at = datetime.fromisoformat(task.payload["occurred_at"])
        conversation = str(task.payload[CONVERSATION_KEY])
        async with self._uow(task.space_id) as uow:
            events = await uow.ingest.events_between(
                conversation_id=conversation, since=at - REPLY_LOOKBACK, until=self._clock.now()
            )
        replies = [e for e in events if e.kind is EventKind.MESSAGE and e.text and e.speaker in AGENTS]
        after = [e for e in replies if e.occurred_at >= at]
        if after:
            return after[0]
        if int(task.payload.get(WAITS_KEY) or 0) >= REPLY_WAITS:
            before = [e for e in replies if e.occurred_at < at]
            return before[-1] if before else None
        return None

    async def _wait_for_reply(self, task: Task, judged: frozenset[UUID]) -> None:
        """The same closing again in `REPLY_WAIT`, its judged items kept; after `REPLY_WAITS` waits with no
        reply at all, nothing closes."""
        waits = int(task.payload.get(WAITS_KEY) or 0)
        if waits >= REPLY_WAITS:
            return
        payload = {
            **task.payload,
            WAITS_KEY: waits + 1,
            ASKED_KEY: True,
            JUDGED_KEY: sorted(str(i) for i in judged),
        }
        again = Task(task.name, task.space_id, payload, task.profile_id)
        async with self._uow(task.space_id) as uow:
            await uow.pipeline.schedule(again, self._clock.now() + REPLY_WAIT, DelayReason.AGENT_TURN)

    async def _delivered(
        self, task: Task, waiting: Sequence[OpenItem], reply: EventRecord
    ) -> frozenset[UUID]:
        """The items the decision model says the agent's reply gives the customer: a request asked of them,
        anything else told, sent or done. What the reply does not give is then asked as a request: a promise
        to ask the customer for something ("Solicitar ao cliente os extratos", a promise the firm made) is
        kept by asking for it, and the question for what is told scored that relay 0.2 where the one for what
        is asked scored it 0.88 (juridico-sandbox, round 10); on the replies that must leave an item open the
        second question stayed at or below 0.34. A spent budget or no answer delivers nothing."""
        found: set[UUID] = set()
        waiting = _bounded(waiting, lambda item: item)
        requests = [i for i in waiting if i.kind is OpenItemKind.REQUEST]
        told = [i for i in waiting if i.kind is not OpenItemKind.REQUEST]
        found |= await self._judged_reply(task, DecisionTask.TURN_DELIVERS_ITEM, told, reply)
        asked = [*requests, *(i for i in told if i.item_id not in found)]
        found |= await self._judged_reply(task, DecisionTask.TURN_ASKS_ITEM, asked, reply)
        return frozenset(found)

    async def _judged_reply(
        self, task: Task, kind_task: DecisionTask, items: Sequence[OpenItem], reply: EventRecord
    ) -> frozenset[UUID]:
        if not items:
            return frozenset()
        try:
            labels = await self._decide.decide_many(
                task.space_id,
                kind_task,
                [pair(i.description, reply.text or "") for i in items],
                [f"item:{i.item_id}|event:{reply.event_id}" for i in items],
            )
        except BudgetExceeded:
            return frozenset()
        return frozenset(
            i.item_id for i, label in zip(items, labels, strict=False) if label.label in DELIVERED
        )

    async def _close(
        self, task: Task, judged: frozenset[UUID], delivered: frozenset[UUID] | None
    ) -> tuple[str | None, list[OpenItem], list[OpenItem], bool] | None:
        """One closing pass. `judged` are the items the decision model judged the action does; `delivered`,
        the ones a customer-facing agent's action may close (the delivery gate), None on the first pass. The
        first pass returns what the closing said, the items to put to the model, the items waiting for the
        gate and whether it is strict (a customer-facing agent's declared action), if any."""
        config = self._config.space(task.space_id)
        window = self._settings.closing_window
        async with self._uow(task.space_id) as uow:
            await fence(uow)
            profile = await self._profile(uow, task.profile_id)
            if profile is None:
                return None
            closing = await self._closing(uow, config, profile, task.payload)
            items = await uow.memory.open_items_for(profile.handle_ids, OPEN_STATUSES)
            others = await self._others_items(uow, profile, closing, items)

            def ready(item: OpenItem) -> bool:
                if not closes(item, closing, window, judged=item.item_id in judged):
                    return False
                return not closing.strict or (delivered is not None and item.item_id in delivered)

            closed = [i for i in items if ready(i)]
            theirs = [i for i in others if ready(i)]
            closed_by = _closed_by(closing, task.payload.get("event_id"))
            for item in closed:
                resolved = _resolved_by(item, closing, closed_by)
                await uow.memory.update_open_item(resolved)
                await _announce_closed(uow, config, profile, resolved, closed_by)
            if closed:
                await self._request_recompute(uow, task.space_id, profile.profile_id)
            for item in theirs:
                contact = await uow.identity.profile_of(item.origin_handle_id)
                if contact is None:
                    continue
                resolved = _resolved_by(item, closing, closed_by)
                await uow.memory.update_open_item(resolved)
                await _announce_closed(uow, config, contact, resolved, closed_by)
                await self._request_recompute(uow, task.space_id, contact.profile_id)
        if delivered is not None:
            return None
        candidates = [*items, *others]
        # A strict closing waits for the delivery gate; any other closes what the model judged it does
        # (`close_items`) and has nothing left waiting.
        waiting = (
            [i for i in candidates if closes(i, closing, window, judged=i.item_id in judged)]
            if closing.strict
            else []
        )
        pending = [
            i
            for i in candidates
            if i not in waiting
            and i.item_id not in judged
            and closing.said
            and to_judge(i, closing, window)
            and not closes(i, closing, window)
        ]
        if not pending and not waiting:
            return None
        return closing.said, pending, waiting, closing.strict

    async def apply_feedback(self, task: Task) -> None:
        """A `feedback.*` event (7.1): retract or correct a fact, resolve an item, record an outcome.

        The event itself becomes the evidence, so every correction keeps its lineage (L1, L12).
        """
        event_id = UUID(task.payload["event_id"])
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            await fence(uow)
            event = next(iter(await uow.ingest.get_events([event_id])), None)
            profile = await self._profile(uow, task.profile_id)
            if event is None or profile is None:
                if event is not None:
                    await _note_refused_resolution(uow, event)
                log.warning("feedback skipped: event or profile not found")
                return
            action = (event.canonical_type or "").removeprefix(FEEDBACK_PREFIX)
            before = await _facts_if_announced(uow, config, profile)
            changed = await self._feedback(uow, config, profile, event, action)
            if not changed:
                await _note_refused_resolution(uow, event)
            if changed and configured(config.purposes):
                await tag_rows(uow, config, profile.handle_ids, self._clock.now(), limit=WRITE_BATCH)
            origin = {"by": "feedback", "event_id": str(event.event_id), "source_id": str(event.source_id)}
            await _announce_facts(uow, config, profile, before, origin)
            if changed:
                await self._request_recompute(uow, task.space_id, profile.profile_id)
                # A corrected fact or outcome is a new row, which needs its vector.
                await uow.pipeline.enqueue(
                    [Task(TaskName.EMBED, task.space_id, profile_id=profile.profile_id)]
                )

    async def embed(self, task: Task) -> None:
        """Encodes the profile's navigable items that have no vector yet, right after they are created or
        change, so a search encodes only its question (16.2). Structured facts never get one (15.7)."""
        async with self._uow(task.space_id) as uow:
            profile = await self._profile(uow, task.profile_id)
            if profile is None:
                return
            pending = await unembedded(uow, None, self._settings.embed_batch, profile)
        await self._encode(task.space_id, pending)

    async def backfill_vectors(self, space_id: UUID) -> int:
        """One small batch of the space's navigable items without a vector of the current encoder, run by
        the loops: rows whose `embed` failed, rows reworded in place and rows of a replaced encoder. Returns
        how many it handled, so an idle space is visited less often."""
        # The encoder names itself in every answer; one word is the cheapest way to ask.
        _, model = await self._models.embed([ENCODER_PROBE])
        async with self._uow(space_id) as uow:
            pending = await unembedded(uow, model, self._settings.backfill_batch, None)
        return await self._encode(space_id, pending)

    async def _encode(self, space_id: UUID, pending: Sequence[PendingVector]) -> int:
        if not pending:
            return 0
        config = self._config.space(space_id)
        specs = _predicate_specs(config)
        fmt = Formatter(config.settings.timezone, config.settings.locale)
        private = config.object_types.private_fields()
        declared = config.object_types.declared_names()
        structured: list[PendingVector] = []
        texts: list[PendingVector] = []
        for p in pending:
            (
                structured if isinstance(p.record, Fact) and not self._free_text(p.record, specs) else texts
            ).append(p)
        # Every piece of a long row is encoded, and the row's vector is their mean: none is cut to what the
        # encoder reads.
        pieces = [vector_chunks(vector_text(p.record, fmt, private, declared)) for p in texts]
        flat, model = (
            await self._models.embed([piece for row in pieces for piece in row]) if texts else ([], "")
        )
        vectors: list[list[float]] = []
        for row in pieces:
            vectors.append(pooled(flat[: len(row)]))
            flat = flat[len(row) :]
        async with self._uow(space_id) as uow:
            await fence(uow)
            for p in structured:
                await uow.memory.set_vector(p, None, model)
            for p, vector in zip(texts, vectors, strict=True):
                await _store_vector(uow, p, vector, model)
        return len(pending)

    async def consolidate(self, task: Task) -> None:
        """Nightly per profile (5.9): decay, overdue items, tail summaries."""
        now = self._clock.now()
        config = self._config.space(task.space_id)
        assert task.profile_id is not None, "consolidate is a per-profile task"
        drawn: list[BandPair] = []
        async with self._uow(task.space_id) as uow:
            await fence(uow)
            # Coalesced like the other per-profile work (5.5): without `finish_work` a profile's first
            # request would be its last, and the next night's would never be enqueued.
            seen = await uow.pipeline.begin_work(task.space_id, task.profile_id, TaskName.CONSOLIDATE)
            profile = await self._profile(uow, task.profile_id)
            if profile is not None:
                changed = await self._decay(uow, profile.handle_ids, now)
                changed |= await self._mark_overdue(uow, profile.handle_ids, now, config.settings.timezone)
                await self._contradiction_band(uow, config, profile, drawn)
                changed |= await self._summaries(uow, profile, config.settings.timezone)
                if changed:
                    await self._request_recompute(uow, task.space_id, profile.profile_id)
            if await uow.pipeline.finish_work(task.space_id, task.profile_id, TaskName.CONSOLIDATE, seen):
                again = Task(
                    TaskName.CONSOLIDATE, task.space_id, {"reason": "coalesced"}, profile_id=task.profile_id
                )
                await uow.pipeline.enqueue([again])
        confirmed = await self._confirm_band(task.space_id, drawn)
        if confirmed:
            async with self._uow(task.space_id) as uow:
                await fence(uow)
                await uow.memory.add_relations([p.relation() for p in confirmed])
                await _sample_band_pairs(uow, confirmed)
                await self._request_recompute(uow, task.space_id, task.profile_id)

    async def _confirm_band(self, space_id: UUID, drawn: Sequence[BandPair]) -> list[BandPair]:
        """The band's pairs that the decision model confirms conflict, asked after the night's
        transaction. A pair it judged before is not asked again; a pair with a sensitive value never leaves
        the cell and keeps its warning, as does a pair the model did not answer or answered without
        confidence. At most `band_pairs_per_night` pairs a night."""
        refs = {p: f"fact:{p.older.fact_id}|fact:{p.newer.fact_id}" for p in drawn}
        judged = await self._decide.answered_before(space_id, DecisionTask.CONTRADICTION, list(refs.values()))
        pairs = [p for p in drawn if refs[p] not in judged][: self._settings.band_pairs_per_night]
        held = [p for p in pairs if Sensitivity.SENSITIVE in (p.newer.sensitivity, p.older.sensitivity)]
        shown = [p for p in pairs if p not in held]
        if not shown:
            return held
        try:
            labels = await self._decide.decide_many(
                space_id,
                DecisionTask.CONTRADICTION,
                [pair(p.older.value, p.newer.value) for p in shown],
                [refs[p] for p in shown],
            )
        except BudgetExceeded:
            return pairs  # nothing could be asked: every pair keeps its warning
        confirmed = {p for p, label in zip(shown, labels, strict=True) if label.label == "contradicts"}
        return [p for p in pairs if p in held or p in confirmed]

    async def sweep_due(self, now: datetime | None = None) -> int:
        """The 5-minute sweep: a profile with an open item whose deadline passed gets its
        consolidation now, under its lease, so the item turns overdue and a broken promise reaches the
        patterns, the triggers and the context within minutes instead of at night."""
        now = now or self._clock.now()
        requested = 0
        for space in self._config.spaces():
            try:
                async with self._uow(space.space_id) as uow:
                    due = [
                        i
                        for i in await uow.memory.open_items_due(now, limit=DUE_SWEEP_ITEMS)
                        if is_overdue(i, now, space.settings.timezone)
                    ]
                    profiles: set[UUID] = set()
                    for item in due:
                        if (profile := await uow.identity.profile_of(item.origin_handle_id)) is not None:
                            profiles.add(profile.profile_id)
                    for profile_id in sorted(profiles):
                        await uow.pipeline.request_work(
                            Task(
                                TaskName.CONSOLIDATE, space.space_id, {"reason": "due"}, profile_id=profile_id
                            )
                        )
                    requested += len(profiles)
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return requested

    async def nightly(self, space_id: UUID) -> int:
        """A per-space loop step (5.9): once a day, after 03:00 in the space's time zone, every
        profile active within the hot window gets its consolidation (decay, overdue items, summaries),
        and whatever it changed re-derives the patterns. Returns how many profiles it asked for."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        now = self._clock.now()
        local = now.astimezone(ZoneInfo(config.settings.timezone))
        if local.hour < NIGHT_HOUR:
            return 0
        day = local.date().toordinal()
        async with self._uow(space_id) as uow:
            if await uow.memory.consolidated_day() == day:
                return 0
            await uow.memory.set_consolidated_day(day)
            since = now - timedelta(days=config.settings.events_hot_days)
            profiles = await uow.sessions.profiles_active_since(since, limit=NIGHTLY_PROFILES)
            for profile_id in profiles:
                await uow.pipeline.request_work(
                    Task(TaskName.CONSOLIDATE, space_id, {"reason": "nightly"}, profile_id=profile_id)
                )
        return len(profiles)

    async def drift(self, space_id: UUID, profile_id: UUID | None) -> int:
        """How many of the profile's fact statuses the current rules would change, writing nothing (L13)."""
        config = self._config.space(space_id)
        async with self._uow(space_id) as uow:
            profile = await self._profile(uow, profile_id)
            if profile is None:
                return 0
            facts = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
            edges = await self._contradicts(uow, profile.handle_ids)
        wanted, _ = self._settle_all(config, facts, edges, [ref(f) for f in facts])
        return sum(1 for f in facts if ref(f) in wanted and wanted[ref(f)] != (f.status, f.invalid_at))

    async def _apply(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        result: ExtractionResult,
        retold: Mapping[int, OpenItem | None] | None = None,
    ) -> list[ObjectKey]:
        """Applies the run; returns the objects whose other participants' packs now say something new."""
        if result.flagged_event_ids:
            await self._quarantine_actions(uow, config, scope)
        settled = _settled(config, scope, result)
        spoken = conversation_spoken_by(
            (e.speaker for e in scope.events.values() if e.kind is EventKind.MESSAGE and e.text),
            company_facts=sum(
                1 for f in result.facts if f.op is FactOp.ADD and f.speaker is not Speaker.CUSTOMER
            ),
            company_values=(
                type
                for type, _, evidence, _ in settled.values()
                if min(evidence, key=lambda e: (e.occurred_at, e.event_id)).speaker is not Speaker.CUSTOMER
            ),
        )
        episode = await self._episode(uow, config, scope, result.episode, spoken, _objects_of(scope, result))
        await self._values(uow, scope, settled)
        audience = _session_audience(config, scope.session, scope.profile)
        touched = [await self._fact(uow, scope, cmd, audience) for cmd in result.facts]
        await self._supersedes(uow, result, touched)
        await self._settle(uow, config, scope, [t for t in touched if t is not None])
        opened = await self._open_items(uow, config, scope, result.open_items, audience, retold)
        await self._resolve(uow, config, scope, result.resolved_items)
        await self._slots(uow, scope, result.profile)
        await self._instruct(uow, config, scope, result.instructions, audience)
        await self._hours(uow, config, scope, result.hours)
        if result.instruction_unscoped:
            # Counts only: an instruction without its object never reaches memory.
            log.info("extraction dropped %d instructions about no object", result.instruction_unscoped)
        # A row only its own speaker's readers may read changes no other pack.
        crossing = [
            *(
                c.object_ref
                for c in result.facts
                if c.op is FactOp.ADD
                and c.object_ref
                and not c.quarantined
                and audience_of(audience, _visibility(scope, c.evidence_event_ids), "observation")
                is MemoryAudience.SHARED
            ),
            *(i.object for i in opened if i.object is not None),
            *(i.object for i in result.instructions if i.object is not None),
        ]
        await self._diverging(uow, config, [c for c in result.facts if c.op is FactOp.ADD and c.object_ref])
        if configured(config.purposes):
            # Tagged before anything is announced, in this transaction: no read sees the rows untagged.
            await tag_rows(uow, config, scope.handle_ids, self._clock.now(), limit=WRITE_BATCH)
            if episode is not None:
                episode = await uow.memory.current_episode(scope.handle_ids, scope.session.session_id)
        if episode is not None:
            still_open = [i.item_id for i in opened if i.status in OPEN_STATUSES]
            await _announce_outcome(uow, config, scope.profile, episode, "extraction", still_open)
        return list(dict.fromkeys(k for k in crossing if k is not None))

    async def _instruct(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        drafts: Sequence[InstructionDraft],
        source: Audience | None = None,
    ) -> None:
        """Each instruction in the signals of who said it, about its object or, naming none, about the
        organization spoken of, `shared` by its kind: one statement in this transaction. A
        space with `signals` off keeps none of them. One the company's own person said (`staff` source) says
        so, as does the one person it holds with."""
        if not drafts:
            return
        session = str(scope.session.session_id)
        keys = _instruction_keys(drafts)
        said = [
            SaidInstruction(
                handle_id=scope.anchor(d.evidence_event_ids).origin_handle_id,
                key=str(stable_uuid7(scope.session.opened_at, session, "instruction", keys[n])),
                at=scope.noted_at(d.evidence_event_ids),
                object=_key_ref(d.object) if d.object else None,
                about=str(d.about_handle_id) if d.object is None and d.about_handle_id else None,
                text=d.text,
                strength=d.strength,
                audience=audience_of(None, Visibility.PUBLIC, "instruction").value,
                attr=d.attr,
                event_key=str(d.evidence_event_ids[0]),
                speaker=Speaker.HUMAN_AGENT.value if staff(source) else None,
                only_with=d.only_with,
                contact=d.contact,
            )
            for n, d in enumerate(drafts)
        ]
        kept = await self._instructions.instruct(uow, config.space_id, said)
        if kept < len(said):
            log.info("%d instructions about an object not kept: signals are off", len(said) - kept)

    async def _hours(
        self, uow: UnitOfWork, config: SpaceConfig, scope: _Scope, drafts: Sequence[HoursDraft]
    ) -> None:
        """The local hours each customer said they may be contacted in (the suppression-list spec, 6.1), in
        the space's time zone: a task that keeps them as a suppression of every purpose, outside those hours,
        on each of the person's destinations (the coordination module, retried apart from this extraction),
        and their contact window in their block, in this transaction. A lift ends both."""
        if not drafts:
            return
        session = str(scope.session.session_id)
        tasks: list[Task] = []
        said: list[SaidHours] = []
        for n, d in enumerate(drafts):
            window = (
                None if d.lift else Window.said(d.not_before, d.not_after, config.settings.timezone, d.days)
            )
            at = scope.noted_at(d.evidence_event_ids)
            tasks.append(
                Task(
                    TaskName.CONTACT_WINDOW,
                    config.space_id,
                    {
                        "handle_ids": [str(h) for h in scope.handle_ids],
                        "channel": d.channel,
                        "window": window.to_json() if window is not None else None,
                        "said_at": at.isoformat(),
                    },
                )
            )
            said.append(
                SaidHours(
                    handle_id=scope.anchor(d.evidence_event_ids).origin_handle_id,
                    key=str(stable_uuid7(scope.session.opened_at, session, "hours", str(n), d.channel or "")),
                    at=at,
                    channel=d.channel,
                    window=window.encode() if window is not None else "",
                    event_key=str(d.evidence_event_ids[0]),
                )
            )
        await uow.pipeline.enqueue(tasks)
        await self._instructions.hours(uow, config.space_id, said)

    async def _diverging(self, uow: UnitOfWork, config: SpaceConfig, facts: Sequence[FactCommand]) -> None:
        """An observation of a field the system of record holds another value for is a data issue of class
        `divergence` for the company, never a change of the field."""
        observes = {p.name: p.observes for p in config.extraction.predicates if p.observes}
        said = [f for f in facts if f.predicate in observes and f.object_ref is not None]
        if not said or self._issues is None:
            return
        objects = {
            o.key: o for o in await uow.objects.get_objects([f.object_ref for f in said if f.object_ref])
        }
        for fact in said:
            assert fact.object_ref is not None
            obj, field = objects.get(fact.object_ref), observes[fact.predicate]
            recorded = obj.state.get(field) if obj else None
            if recorded is None or same_value(fact.value, recorded):
                continue
            await self._issues.open(
                uow,
                config.space_id,
                "divergence",
                object_type=fact.object_ref.type,
                field=field,
                source="conversation",
                refs=[_key_ref(fact.object_ref)],
            )

    async def _recompile_participants(
        self, uow: UnitOfWork, space_id: UUID, profile_id: UUID, keys: Sequence[ObjectKey]
    ) -> None:
        """What one person said about an object reaches the packs of its owner and participants: theirs are
        compiled again, coalesced by the per-profile lease, at most `MAX_HANDLES` handles per object.
        Rows another participant may not read change nothing in their packs, and asking costs one
        compilation."""
        for other in await participant_profiles(uow, keys):
            if other != profile_id:
                await uow.pipeline.request_work(Task(TaskName.COMPILE, space_id, profile_id=other))

    async def _episode(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        draft: EpisodeDraft | None,
        spoken_by: SpokenBy | None = None,
        object_refs: tuple[ObjectKey, ...] = (),
    ) -> Episode | None:
        """Writes a new version when anything changed and returns it; a replay writes nothing, and neither
        does an internal agent's task that only acted, which has no episode."""
        if draft is None:
            return None
        session = scope.session
        current = await uow.memory.current_episode(scope.handle_ids, session.session_id)
        episode = Episode(
            episode_id=current.episode_id
            if current
            else stable_uuid7(session.opened_at, str(session.session_id), "episode"),
            version=current.version + 1 if current else 1,
            origin_handle_id=scope.anchor(list(scope.events)).origin_handle_id,
            session_id=session.session_id,
            channel=session.channel,
            source_id=session.source_id,
            summary=draft.summary,
            intent=draft.intent,
            category=draft.category,
            normalized_category=config.extraction.normalize_category(draft.category),
            outcome=draft.outcome,
            started_at=session.opened_at,
            ended_at=scope.ended_at,
            resolution=draft.resolution,
            sentiment=draft.sentiment,
            initiated_by=session.initiated_by,
            conversation_id=session.conversation_id,
            spoken_by=spoken_by,
            matter=draft.matter,
            matter_label=draft.matter_label,
            object_refs=object_refs,
        )
        # A stored version carries the purposes tagged after it was written; a draft never has them yet.
        if current is None or replace(current, version=episode.version, purposes=None) != episode:
            await uow.memory.add_episode_version(episode, current)
            return episode
        return None

    async def _values(
        self,
        uow: UnitOfWork,
        scope: _Scope,
        found: dict[str, tuple[ValueType, str, list[EventRecord], ValueOrigin]],
    ) -> None:
        """What the session settled (11, 3.2), as `_settled` found it. Written whether or not the space
        reads them yet: the write is additive and cheap."""
        if not found:
            return
        session = scope.session
        episode = await uow.memory.current_episode(scope.handle_ids, session.session_id)
        rows = []
        for key, (type, raw, evidence, origin) in found.items():
            first = min(evidence, key=lambda e: (e.occurred_at, e.event_id))
            refs = {k for e in evidence for k in e.object_refs}
            rows.append(
                SettledValue(
                    value_id=stable_uuid7(session.opened_at, str(session.session_id), "value", key),
                    origin_handle_id=scope.anchor([e.event_id for e in evidence]).origin_handle_id,
                    type=type,
                    raw=raw,
                    said_at=first.occurred_at,
                    speaker=first.speaker,
                    session_id=session.session_id,
                    channel=session.channel,
                    source_id=session.source_id,
                    created_at=self._clock.now(),
                    evidence_event_ids=tuple(e.event_id for e in evidence),
                    conversation_id=session.conversation_id,
                    episode_id=episode.episode_id if episode else None,
                    object_ref=next(iter(refs)) if len(refs) == 1 else None,
                    origin=origin,
                )
            )
        await uow.memory.insert_values(rows)

    async def _fact(
        self, uow: UnitOfWork, scope: _Scope, cmd: FactCommand, source: Audience | None = None
    ) -> MemoryRef | None:
        """Applies one command; returns the row whose contention group must be settled. A fact about an object
        carries its audience, and the same words about two objects are two facts."""
        if cmd.op is FactOp.RETIRE:
            return await self._retire(uow, scope, cmd)
        at = cmd.valid_at or scope.noted_at(cmd.evidence_event_ids)
        place = scope.anchor(cmd.evidence_event_ids)
        audience = (
            audience_of(source, _visibility(scope, cmd.evidence_event_ids), "observation")
            if cmd.object_ref
            else None
        )
        seen = Fact(
            fact_id=uuid7(),
            origin_handle_id=place.origin_handle_id,
            predicate=cmd.predicate,
            value=cmd.value,
            category=cmd.category,
            sensitivity=cmd.sensitivity,
            speaker=said_by(source, cmd.speaker),
            confidence=confidence(
                said_by(source, cmd.speaker), scope.session.proven_level, cmd.certainty, cmd.from_voice
            ),
            valid_at=at,
            created_at=self._clock.now(),
            status=FactStatus.QUARANTINED if cmd.quarantined else FactStatus.ACTIVE,
            last_confirmed_at=at,
            namespace=cmd.namespace,
            origin_conversation_id=scope.session.conversation_id,
            about_handle_id=cmd.about_handle_id,
            object_ref=cmd.object_ref,
            mention_handle_ids=place.mention_handle_ids,
            evidence_event_ids=cmd.evidence_event_ids,
            valid_until=declared_validity(
                [scope.events[e] for e in cmd.evidence_event_ids if e in scope.events]
            ),
            sessions_seen=0 if cmd.quarantined else 1,
            audience=audience,
        )
        key = normalize_value(cmd.value)
        if cmd.object_ref is not None:
            key = f"{key}\x1f{_key_ref(cmd.object_ref)}"
        if await uow.memory.insert_fact(seen, key):
            await uow.memory.add_mentions(ref(seen), cmd.evidence_event_ids)
            row = seen
        else:
            existing = await uow.memory.find_fact(place.origin_handle_id, cmd.predicate, key)
            assert existing is not None, "the unique key conflicted, so the row exists"
            row = merge_observation(existing, seen)
            if audience is MemoryAudience.SHARED and row.audience is not MemoryAudience.SHARED:
                # Said in front of the customer now: what was internal is theirs to hear too.
                row = replace(row, audience=audience)
            if await uow.memory.add_mentions(ref(row), cmd.evidence_event_ids):
                row = replace(
                    row,
                    times_seen=row.times_seen + 1,
                    sessions_seen=_sessions_after(existing, scope, cmd.quarantined),
                )
            if row != existing:
                await uow.memory.update_fact(row)
        await uow.memory.add_relations(
            [Relation(ref(row), c, RelationType.CONTRADICTS) for c in cmd.contradicts]
        )
        if row.valid_until is not None and row.valid_until > self._clock.now():
            # The compiled packs leave the fact out from that moment, not at the next unrelated change.
            recompile = Task(TaskName.COMPILE, uow.space_id, profile_id=scope.profile.profile_id)
            await uow.pipeline.schedule(recompile, row.valid_until, DelayReason.VALID_UNTIL)
        return ref(row)

    async def _retire(self, uow: UnitOfWork, scope: _Scope, cmd: FactCommand) -> MemoryRef | None:
        if cmd.target is None or cmd.quarantined:
            return None
        facts = await uow.memory.facts_for(scope.handle_ids, ALL_FACT_STATUSES)
        target = next((f for f in facts if ref(f) == cmd.target), None)
        if target is None:
            return None
        await uow.memory.add_mentions(cmd.target, cmd.evidence_event_ids)
        retired = retire(target, cmd.valid_at or scope.noted_at(cmd.evidence_event_ids))
        if retired is not None:
            await uow.memory.update_fact(self._with_expiry(target, retired.status, retired.invalid_at))
        return cmd.target

    async def _supersedes(
        self, uow: UnitOfWork, result: ExtractionResult, touched: Sequence[MemoryRef | None]
    ) -> None:
        """Retire and add of the same slot in one answer is a value change: the new supersedes the old."""
        retired = [(c, c.target) for c in result.facts if c.op is FactOp.RETIRE and c.target]
        added = [(c, r) for c, r in zip(result.facts, touched, strict=True) if c.op is FactOp.ADD and r]
        edges = [
            Relation(new_ref, old_ref, RelationType.SUPERSEDES)
            for new, new_ref in added
            for old, old_ref in retired
            if old_ref is not None
            and (new.predicate, new.about_handle_id) == (old.predicate, old.about_handle_id)
        ]
        await uow.memory.add_relations(edges)

    async def _settle(
        self, uow: UnitOfWork, config: SpaceConfig, scope: _Scope, seeds: Sequence[MemoryRef]
    ) -> None:
        if not seeds:
            return
        facts = await uow.memory.facts_for(scope.handle_ids, ALL_FACT_STATUSES)
        edges = await self._contradicts(uow, scope.handle_ids)
        wanted, superseded = self._settle_all(config, facts, edges, seeds)
        for fact in facts:
            status, invalid_at = wanted.get(ref(fact), (fact.status, fact.invalid_at))
            if (status, invalid_at) != (fact.status, fact.invalid_at):
                await uow.memory.update_fact(self._with_expiry(fact, status, invalid_at))
        await uow.memory.add_relations(superseded)

    def _settle_all(
        self,
        config: SpaceConfig,
        facts: Sequence[Fact],
        edges: Sequence[tuple[MemoryRef, MemoryRef]],
        seeds: Sequence[MemoryRef],
    ) -> tuple[dict[MemoryRef, tuple[FactStatus, datetime | None]], list[Relation]]:
        """The statuses the rows of the seeds' groups should have, and the `SUPERSEDES` edges that
        precedence by origin draws across the record line (11, 3.2)."""
        specs = _predicate_specs(config)

        def single_valued(fact: Fact) -> bool:
            spec = specs.get(fact.predicate)
            return spec is not None and spec.single_valued

        by_ref = {ref(f): f for f in facts}
        mates = {
            ref(m) for seed in seeds if seed in by_ref for m in slot_mates(by_ref[seed], facts, single_valued)
        }
        wanted: dict[MemoryRef, tuple[FactStatus, datetime | None]] = {}
        for seed in [*seeds, *sorted(mates - set(seeds), key=lambda r: (r.origin_handle_id, r.id))]:
            if seed in by_ref and seed not in wanted:
                group = contention_group(by_ref[seed], facts, edges, single_valued)
                wanted.update(settle(group))
        settled = [
            replace(f, status=wanted[ref(f)][0], invalid_at=wanted[ref(f)][1]) if ref(f) in wanted else f
            for f in facts
        ]
        superseded, relations = supersede([f for f in settled if ref(f) in mates], single_valued)
        wanted.update(superseded)
        return wanted, relations

    def _with_expiry(self, fact: Fact, status: FactStatus, invalid_at: datetime | None) -> Fact:
        """`expired_at` is when we learned the value stopped holding: processing time, not valid time."""
        expired_at = None if status is FactStatus.ACTIVE else fact.expired_at or self._clock.now()
        return replace(fact, status=status, invalid_at=invalid_at, expired_at=expired_at)

    async def _open_items(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        drafts: Sequence[OpenItemDraft],
        source: Audience | None = None,
        retold: Mapping[int, OpenItem | None] | None = None,
    ) -> list[OpenItem]:
        """The session's items. A session extracted again (a new extractor, prompt or schema; a reopened
        session) says its items again, often in other words, and a conversation that goes over what is still
        pending says the person's promises from other conversations again: a new item that restates an item
        memory holds updates that item in place, so one promise never gets a second row (B21). The item kept
        is the oldest it restates; it keeps its id and description and takes only the due date, object and
        operation it did not have. Other items it restates were told apart when written, and stay: merging
        two promises would lose one. The deadline net's generic item takes the words of the session's item
        due that day. An item that tells an open item another conversation bound to the same object (the
        attendant repeating the lawyer's promise to the client) is that promise, and writes nothing: the item
        stays the one its speaker made, under the speaker's handle (L6)."""
        session = str(scope.session.session_id)
        known = await uow.memory.open_items_for(scope.handle_ids, tuple(OpenItemStatus))
        earlier = [i for i in known if i.origin.get("session_id") == session]
        elsewhere = [i for i in known if i.origin.get("session_id") != session]
        on_object: dict[ObjectKey, list[OpenItem]] = {}
        anywhere: list[OpenItem] | None = None
        opened: list[OpenItem] = []
        for n, draft in enumerate(drafts):
            if draft.agent_day and draft.due_at is not None:
                # A day only the customer-facing agent wrote stays when memory holds an item of the
                # customer's, or on the object, due that day: the attendant's "até 27/10" was the case's
                # deadline to contest, not the day the client was to send the extratos (juridico-zero,
                # 07/10/2026).
                held = list(known)
                if draft.object is not None:
                    if draft.object not in on_object:
                        on_object[draft.object] = await _object_items(uow, draft.object, elsewhere)
                    held += on_object[draft.object]
                if not any(same_day(i.due_at, draft.due_at) for i in held):
                    draft = replace(draft, due_at=None)
            said = [
                i
                for i in earlier
                if restates(draft.description, draft.evidence_event_ids, draft.due_at, draft.object, i)
            ] + [i for i in elsewhere if restates_elsewhere(draft.description, draft.due_at, draft.object, i)]
            if not said:
                said = [i for i in earlier if _carries_deadline(draft, i)]
            if not said and n in (retold or {}):
                # Told again in other words, as the decision model judged before this transaction
                # (`_retellings`): an item of this session restates it in place; another speaker's promise
                # stays the one its speaker made, and nothing is written.
                earlier_item = (retold or {})[n]
                said = [i for i in earlier if earlier_item and i.item_id == earlier_item.item_id]
                if not said:
                    continue
            if not said and draft.object is not None:
                if draft.object not in on_object:
                    on_object[draft.object] = await _object_items(uow, draft.object, elsewhere)
                told = [
                    i
                    for i in on_object[draft.object]
                    if restates_on_object(
                        draft.description, draft.due_at, draft.object, draft.expected_operation, i
                    )
                ]
                if told:
                    continue
                if not draft.grounded:
                    # The model alone bound it: the attendant's untagged "o advogado prometeu enviar o
                    # comprovante ao financeiro" went to a hearing, the promise it tells being on another case
                    # (juridico-sandbox, 06/10/2026). An open item on any of the subject's objects that it
                    # tells again is that promise.
                    if anywhere is None:
                        anywhere = await _subject_items(uow, scope, elsewhere, session)
                    if any(_retells_elsewhere(draft, i) for i in anywhere if i.object != draft.object):
                        continue
            if not said and any(
                restates_on_object(draft.description, draft.due_at, draft.object, draft.expected_operation, i)
                for i in opened
            ):
                # The same promise twice in one run, in other words: one turn split into two of a kind.
                continue
            if not said:
                opened.append(await self._open_item(uow, config, scope, draft, source))
                continue
            kept = min(said, key=lambda i: (i.created_at, i.item_id))
            earlier = [i for i in earlier if i.item_id != kept.item_id]
            elsewhere = [i for i in elsewhere if i.item_id != kept.item_id]
            opened.append(await self._restate(uow, config, scope, kept, draft, source))
        return opened

    async def _restate(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        item: OpenItem,
        draft: OpenItemDraft,
        source: Audience | None = None,
    ) -> OpenItem:
        """The earlier item with what the new extraction found that it lacked."""
        # What the item already has stays: the new extraction only fills what it lacked.
        bound = item.object or draft.object
        restated = replace(
            item,
            description=draft.description if is_deadline_item(item.description) else item.description,
            due_at=item.due_at or draft.due_at,
            object=bound,
            expected_operation=item.expected_operation or draft.expected_operation,
            about_handle_id=item.about_handle_id or draft.about_handle_id,
            audience=item.audience or (_item_audience(scope, draft, source) if bound else None),
        )
        if restated == item:
            return item
        await uow.memory.restate_item(restated, reworded=restated.description != item.description)
        if restated.status not in OPEN_STATUSES or restated.expected_operation == item.expected_operation:
            return restated
        # It now waits for an operation: one done before may already have fulfilled it.
        closing = await self._already_done(uow, config, scope, restated)
        if closing is None:
            return restated
        closed_by = _closed_by(closing, closing.event_id)
        resolved = _resolved_by(restated, closing, closed_by)
        await uow.memory.update_open_item(resolved)
        await _announce_closed(uow, config, scope.profile, resolved, closed_by)
        return resolved

    async def _open_item(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        draft: OpenItemDraft,
        source: Audience | None = None,
    ) -> OpenItem:
        noted = scope.noted_at(draft.evidence_event_ids)
        session = str(scope.session.session_id)
        handle = scope.anchor(draft.evidence_event_ids).origin_handle_id
        item = OpenItem(
            item_id=item_id_for(handle, session, draft.description, draft.due_at, draft.object),
            origin_handle_id=handle,
            description=draft.description,
            # What the company's own person promises in an internal agent's conversation is the company's to
            # do: the extraction calls them the conversation's customer, and the client's attendant read a
            # lawyer's promise as "a cargo do cliente".
            owner="company" if staff(source) else draft.owner,
            status=OpenItemStatus.OPEN,
            created_at=self._clock.now(),
            origin={
                "session_id": session,
                "source_id": str(scope.session.source_id),
                "event_ids": [str(e) for e in draft.evidence_event_ids],
                "noted_at": noted.isoformat(),
            }
            | ({"speaker": Speaker.HUMAN_AGENT.value} if staff(source) else {}),
            due_at=draft.due_at,
            about_handle_id=draft.about_handle_id,
            object=draft.object,
            expected_operation=draft.expected_operation,
            kind=draft.kind,
            audience=_item_audience(scope, draft, source) if draft.object else None,
        )
        closing = await self._already_done(uow, config, scope, item)
        if closing is None:
            await uow.memory.insert_open_item(item)
            return item
        closed_by = _closed_by(closing, closing.event_id)
        stored = _resolved_by(item, closing, closed_by)
        if await uow.memory.insert_open_item(stored):
            await _announce_closed(uow, config, scope.profile, stored, closed_by)
        return stored

    async def _already_done(
        self, uow: UnitOfWork, config: SpaceConfig, scope: _Scope, item: OpenItem
    ) -> Closing | None:
        """Retroactive closing (18.4, rev. 10): the action may have been applied before the item existed."""
        if item.object is None:
            return None if item.expected_operation is None else await self._already_paid(uow, scope, item)
        found = await uow.objects.get_object(item.object)
        window = self._settings.closing_window
        if found is not None and item.expected_operation is not None:
            actions = await uow.objects.actions_for(
                scope.handle_ids, since=noted_at(item) - window, limit=500
            )
            for action in sorted(actions, key=lambda a: (a.occurred_at, a.action_id)):
                key = item.object if action.object_id == found.object_id else None
                closing = Closing.of_action(
                    action,
                    key,
                    _trusted_ops(config, action.source_id),
                    # A stored action keeps no conversation: the key's own audience decides here, as a task's
                    # action closes what it did.
                    customer_facing=_customer_facing(config, action.source_id, scope.profile.kind, None),
                )
                # A customer-facing agent's declared action closes only through the delivery gate
                # (`close_items`), which judges the agent's reply: no item born after it is closed by its
                # words alone.
                if not closing.strict and closes(item, closing, window):
                    return closing
        return await self._already_confirmed(uow, config, scope, item, found)

    async def _already_confirmed(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        scope: _Scope,
        item: OpenItem,
        found: BusinessObject | None,
    ) -> Closing | None:
        """A system of record may have confirmed the operation before the extraction wrote the item, on its
        object or on a part of it (a filing of the case): the event that would have closed it came first. The
        subject's events and those of the organizations it acts for (21.4).

        The event closes the item as it would have on arrival (`closes`). One the words cannot settle
        (`to_judge`) goes to the closing pass that arrival runs (`close_items`), which puts it to the decision
        model after this transaction: the court's "custas.paid" (`pay_court_fees`) at 01:07 never closed the
        lawyer's "Recolher as custas de preparo" (`pay`) extracted at 01:08, since only the exact operation
        was looked for here and nothing was judged (juridico-zero, 09/10/2026).

        The events looked through are those of the subject, of the organizations it acts for, and of the
        object's owner and participants (`found`): a system of record writes an event about a case under the
        case's owner, and with no link the lawyer's conversation reaches no handle of the firm's client. The
        court's "custas.paid" on Helena's case at 15:19:15, under her account, never closed the lawyer's
        "Recolher custas de preparo", extracted at 15:19:46 under the lawyer's own handle (juridico-zero,
        09/10/2026, final round). An item that waits for no operation is judged against what the event
        records, as on arrival (`to_judge`)."""
        window = self._settings.closing_window
        links = await uow.identity.active_links(scope.handle_ids) if scope.handle_ids else []
        theirs = await _object_handles(uow, found) if found is not None else set()
        handles = sorted(
            {*scope.handle_ids, *await organization_handles(uow, scope.handle_ids, links), *theirs}, key=str
        )
        if not handles:
            return None
        events = await uow.ingest.system_events_for_handles(
            handles, noted_at(item) - window, CONFIRMED_EVENTS
        )
        judged: dict[UUID, dict[str, Any]] = {}
        candidates = [
            (event, operation)
            for event in sorted(events, key=lambda e: (e.occurred_at, e.event_id))
            if (operation := operation_of(event)) is not None
            and (item.expected_operation is None or same_family(item.expected_operation, operation))
        ]
        # The owner's events are about all of its objects: the other ones are read together, in one statement.
        # One each was up to `CONFIRMED_EVENTS` statements inside the item's write for a firm the court posts
        # about all day (10/10/2026).
        others = list(dict.fromkeys(k for e, _ in candidates for k in e.object_refs if k != item.object))
        parts = {o.key: o for o in await uow.objects.get_objects(others)} if others else {}
        for event, operation in candidates:
            for key in event.object_refs:
                part = parts.get(key)
                parents = tuple(parents_of(config, part)) if part is not None else ()
                closing = Closing(
                    operation, event.occurred_at, ActionStatus.CONFIRMED, key, parents=parents,
                    event_id=event.event_id, said=recorded_words(event.canonical_type),
                )  # fmt: skip
                if closes(item, closing, window):
                    return closing
                if event.event_id not in judged and to_judge(item, closing, window):
                    judged[event.event_id] = closing_payload(
                        config, event, key, operation, event.handle_ids, obj=part
                    )
        profile_id = scope.profile.profile_id
        await uow.pipeline.enqueue(
            [Task(TaskName.CLOSE_ITEMS, config.space_id, p, profile_id=profile_id) for p in judged.values()]
        )
        return None

    async def _already_paid(self, uow: UnitOfWork, scope: _Scope, item: OpenItem) -> Closing | None:
        """A promise that names no object but an amount ("refund the R$ 41,78 shipping fee"): the customer's
        business objects whose system of record already confirmed an operation of that family for that
        amount after the promise close it (17, WP-10). One read of the objects' current state; nothing when
        the promise states no amount."""
        if not amounts_in(item.description):
            return None
        window = self._settings.closing_window
        noted = noted_at(item)
        for obj in await uow.objects.objects_for(scope.handle_ids, limit=50):
            if obj.as_of < noted - window:
                continue
            operation = operation_of_event_type(obj.state.get("last_event_type"))
            amount = amount_of(obj.state)
            if operation is None or amount is None:
                continue
            closing = Closing(operation, obj.as_of, ActionStatus.CONFIRMED, obj.key, amount=amount)
            if closes(item, closing, window):
                return closing
        return None

    async def _quarantine_actions(self, uow: UnitOfWork, config: SpaceConfig, scope: _Scope) -> None:
        """A session with a turn flagged as injection has its actions quarantined, and whatever they
        closed opens again (18.4); objects applied them before the gate ran."""
        session_events = set(scope.events)
        actions = await uow.objects.actions_for(scope.handle_ids, since=scope.session.opened_at, limit=500)
        doomed = {
            a.action_id
            for a in actions
            if a.event_id in session_events and a.status in (ActionStatus.DECLARED, ActionStatus.CONFIRMED)
        }
        for action_id in sorted(doomed):
            await uow.objects.set_action_status(action_id, ActionStatus.QUARANTINED)
        for item in await uow.memory.open_items_for(scope.handle_ids, (OpenItemStatus.RESOLVED,)):
            if item.closed_by_action_id in doomed:
                reopened = with_closed_by(
                    replace(item, status=OpenItemStatus.OPEN, closed_by_action_id=None, closed_at=None), None
                )
                await uow.memory.update_open_item(reopened)
                # Whoever heard `open_item.closed` must hear that the closing no longer holds.
                await _announce_reopened(uow, config, scope.profile, reopened, item.closed_by_action_id)

    async def _closing(
        self, uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, payload: Mapping[str, Any]
    ) -> Closing:
        """The closing described by the objects module's payload, checked against the stored action."""
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        key = _payload_key(payload.get("object"))
        operation = str(payload["operation"])
        named = payload.get("closes") or {}
        if named.get("object") and named.get("operation"):
            key, operation = _payload_key(named["object"]), str(named["operation"])
        item_id = _uuid_or_none(named.get("item_id"))
        amount = parse_amount(payload.get("amount"))
        parents = tuple(k for k in map(_payload_key, payload.get("parents") or ()) if k is not None)
        recorded = recorded_words(payload.get("label"))
        if not payload.get("action_id"):
            # A system-of-record event is its own confirmation; it says what it records ("custas paid"), the
            # words an item that waits for no operation is judged against (`items.to_judge`).
            return Closing(
                operation,
                occurred_at,
                ActionStatus.CONFIRMED,
                key,
                item_id,
                amount=amount,
                parents=parents,
                said=recorded,
            )
        action_id = UUID(payload["action_id"])
        handles = sorted({*profile.handle_ids, *(UUID(h) for h in payload.get("handle_ids") or ())})
        actions = await uow.objects.actions_for(handles, since=occurred_at - timedelta(seconds=1), limit=500)
        action = next((a for a in actions if a.action_id == action_id), None)
        if action is None:
            # The objects module enqueues only actions it already vetted as able to close (18.4).
            return Closing(
                operation,
                occurred_at,
                ActionStatus.CONFIRMED,
                key,
                item_id,
                action_id,
                amount=amount,
                parents=parents,
                said=recorded,
            )
        conversation = payload.get("conversation_id")
        strict = (
            _customer_facing(config, action.source_id, profile.kind, conversation)
            and action.status is ActionStatus.DECLARED
        )
        # The delivery gate reads the reply in the conversation; with none, a source without a list of
        # trusted operations closes nothing a customer-facing agent declares (`trusts`).
        trusted = trusts(
            _trusted_ops(config, action.source_id), operation, checked=not strict or bool(conversation)
        )
        return Closing(
            operation,
            occurred_at,
            action.status,
            key,
            item_id or action.closes_item_id,
            action_id,
            trusted,
            amount,
            parents,
            strict=strict,
            said=action.result,
        )

    async def _feedback(
        self, uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, event: EventRecord, action: str
    ) -> bool:
        fields = event.fields
        if action in ("retract_fact", "correct_fact"):
            facts = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
            target = next((f for f in facts if str(f.fact_id) == fields.get("fact_id")), None)
            if target is None:
                return False
            await uow.memory.add_mentions(ref(target), [event.event_id], feedback=True)
            # The retire time never precedes the last confirmation, so the value stays retracted
            # until someone states it again (5.8).
            ended = max(event.occurred_at, target.last_confirmed_at)
            await uow.memory.update_fact(self._with_expiry(target, FactStatus.RETRACTED, ended))
            value = fields.get("value")
            if action == "correct_fact" and isinstance(value, str) and value.strip():
                await self._corrected(uow, config, profile, target, value.strip(), event)
            return True
        if action == "resolve_open_item":
            wanted = _uuid_or_none(fields.get("open_item_id"))
            rows = await uow.memory.open_items_for(profile.handle_ids, OPEN_STATUSES)
            item = next((i for i in rows if i.item_id == wanted), None)
            owner = profile
            if item is None:
                # An organization's view shows what its linked people noted (21.4): resolved through the
                # link, under the contact's own profile.
                linked = await linked_contacts_items(uow, profile, OPEN_STATUSES)
                theirs = next((i for i in linked if i.item_id == wanted), None)
                contact = await uow.identity.profile_of(theirs.origin_handle_id) if theirs else None
                if theirs is None or contact is None:
                    return False
                item, owner = theirs, contact
            closed_by = {
                "kind": "feedback",
                "event_id": str(event.event_id),
                "source_id": str(event.source_id),
            }
            resolved = with_closed_by(
                replace(item, status=OpenItemStatus.RESOLVED, closed_at=event.occurred_at), closed_by
            )
            # The link lets an erasure of the item find the event and strip its reason (8.4).
            await uow.memory.update_open_item(resolved, closed_by_event=event.event_id)
            await _announce_closed(uow, config, owner, resolved, closed_by)
            if owner is not profile:
                await self._request_recompute(uow, config.space_id, owner.profile_id)
            return True
        if action == "conversation_outcome":
            return await self._outcome(uow, config, profile, event)
        if action == RESTORE_ACTION:
            return await self._restore(uow, config, profile, event)
        return False

    async def _restore(
        self, uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, event: EventRecord
    ) -> bool:
        """The retracted fact comes back as it was and the corrections of it are retracted, each with the
        edge that says so; the restore event joins their evidence (WP-G)."""
        if event.source_id != governance_source(config.space_id):
            # Any source may send a `feedback.*` system event; only the security role's route restores.
            log.warning("restore skipped: not from the governance source")
            return False
        facts = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
        target = next((f for f in facts if str(f.fact_id) == event.fields.get("fact_id")), None)
        if target is None:
            return False
        plan = await self._restore_plan(uow, profile, facts, target)
        if plan.refusal is not None:
            log.info("restore refused: %s", plan.refusal.value)
            return False
        corrections = plan.corrections
        back = restored(target, event.occurred_at)
        await uow.memory.add_mentions(ref(back), [event.event_id], feedback=True)
        await uow.memory.update_fact(back)
        undone: list[Fact] = []
        for fixed in corrections:
            if fixed.status is FactStatus.ACTIVE:
                await uow.memory.add_mentions(ref(fixed), [event.event_id], feedback=True)
                ended = max(event.occurred_at, fixed.last_confirmed_at)
                await uow.memory.update_fact(self._with_expiry(fixed, FactStatus.RETRACTED, ended))
                undone.append(fixed)
        await uow.memory.add_relations(
            [Relation(ref(back), ref(c), RelationType.SUPERSEDES, origin="rule") for c in corrections]
        )
        # The rest of the slot settles as after a correction, over the confirmed rows only (5.3).
        view = derivation_view(profile)
        settled = await uow.memory.facts_for(view.handle_ids, ALL_FACT_STATUSES)
        edges = await self._contradicts(uow, view.handle_ids)
        wanted, superseded = self._settle_all(config, settled, edges, [ref(back)])
        for fact in settled:
            status, invalid_at = wanted.get(ref(fact), (fact.status, fact.invalid_at))
            if (status, invalid_at) != (fact.status, fact.invalid_at):
                await uow.memory.update_fact(self._with_expiry(fact, status, invalid_at))
        await uow.memory.add_relations(superseded)
        await _announce_restored(uow, config, profile, back, undone, event)
        return True

    async def _restore_plan(
        self, uow: UnitOfWork, profile: ProfileRecord, facts: Sequence[Fact], target: Fact
    ) -> _RestorePlan:
        """Why the fact cannot come back, or `None`, and the corrections of it a restore retracts."""
        by_ref = {ref(f): f for f in facts}
        edges = await uow.memory.relations_for(profile.handle_ids)
        later = [by_ref[r] for r in replacing(target, edges) if r in by_ref]
        mentioned = sorted({e for f in [target, *later] for e in f.evidence_event_ids})
        feedback = {
            e.event_id: ((e.canonical_type or "").removeprefix(FEEDBACK_PREFIX), e.fields.get("fact_id"))
            for e in await uow.ingest.get_events(mentioned)
            if (e.canonical_type or "").startswith(FEEDBACK_PREFIX)
        }
        corrections = [f for f in later if is_correction_of(target, f, feedback)]
        why = refusal(
            target,
            provisional=profile.provisional_handle_ids,
            feedback_event_ids=feedback.keys(),
            replaced_by=[f for f in later if f not in corrections],
            undone_by={
                e
                for e, (action, named) in feedback.items()
                if action == RESTORE_ACTION and named != str(target.fact_id)
            },
        )
        restores = {e for e, named in feedback.items() if named == (RESTORE_ACTION, str(target.fact_id))}
        return _RestorePlan(why, corrections, bool(restores & set(target.evidence_event_ids)))

    async def _corrected(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        profile: ProfileRecord,
        old: Fact,
        value: str,
        event: EventRecord,
    ) -> None:
        """The corrected value replaces the retracted one on the same handle, as the company stated it."""
        fixed = replace(
            old,
            fact_id=uuid7(),
            value=value,
            valid_at=event.occurred_at,
            last_confirmed_at=event.occurred_at,
            created_at=self._clock.now(),
            status=FactStatus.ACTIVE,
            invalid_at=None,
            expired_at=None,
            times_seen=1,
            sessions_seen=1,
            confidence=max(old.confidence, CORRECTION_CONFIDENCE),
            evidence_event_ids=(event.event_id,),
        )
        key = normalize_value(value)
        if not await uow.memory.insert_fact(fixed, key):
            existing = await uow.memory.find_fact(old.origin_handle_id, old.predicate, key)
            assert existing is not None, "the unique key conflicted, so the row exists"
            fixed = merge_observation(existing, fixed)
            await uow.memory.update_fact(fixed)
        await uow.memory.add_mentions(ref(fixed), [event.event_id], feedback=True)
        await uow.memory.add_relations(
            [Relation(ref(fixed), ref(old), RelationType.SUPERSEDES, origin="rule")]
        )
        facts = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
        edges = await self._contradicts(uow, profile.handle_ids)
        wanted, superseded = self._settle_all(config, facts, edges, [ref(fixed)])
        for fact in facts:
            status, invalid_at = wanted.get(ref(fact), (fact.status, fact.invalid_at))
            if (status, invalid_at) != (fact.status, fact.invalid_at):
                await uow.memory.update_fact(self._with_expiry(fact, status, invalid_at))
        await uow.memory.add_relations(superseded)

    async def _outcome(
        self, uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, event: EventRecord
    ) -> bool:
        outcome = event.fields.get("value")
        if outcome not in config.extraction.outcomes or event.conversation_id is None:
            return False
        episodes = [
            e
            for e in await uow.memory.episodes_for(profile.handle_ids)
            if e.conversation_id == event.conversation_id
        ]
        if not episodes:
            return False
        current = episodes[0]
        if current.outcome == outcome:
            return False
        corrected = replace(current, version=current.version + 1, outcome=outcome)
        await uow.memory.add_episode_version(corrected, current)
        await _announce_outcome(uow, config, profile, corrected, "feedback", None)
        return True

    async def _resolve(
        self, uow: UnitOfWork, config: SpaceConfig, scope: _Scope, refs: Sequence[MemoryRef]
    ) -> None:
        if not refs:
            return
        rows = await uow.memory.open_items_for(scope.handle_ids, OPEN_STATUSES)
        items = {MemoryRef(i.origin_handle_id, i.item_id): i for i in rows}
        closed_by = {"kind": "conversation", "session_id": str(scope.session.session_id)}
        for item_ref in refs:
            item = items.pop(item_ref, None)
            if item is not None:
                resolved = with_closed_by(
                    replace(item, status=OpenItemStatus.RESOLVED, closed_at=scope.ended_at), closed_by
                )
                await uow.memory.update_open_item(resolved)
                await _announce_closed(uow, config, scope.profile, resolved, closed_by)

    async def _slots(self, uow: UnitOfWork, scope: _Scope, slots: Sequence[SlotDraft]) -> None:
        if not slots:
            return
        rows = {
            (a.origin_handle_id, a.slot, a.source_id): a for a in await uow.memory.attrs_for(scope.handle_ids)
        }
        for slot in slots:
            seen = ProfileAttr(
                origin_handle_id=scope.anchor(slot.evidence_event_ids).origin_handle_id,
                slot=slot.slot,
                value=slot.value,
                source_id=scope.session.source_id,
                observed_at=scope.noted_at(slot.evidence_event_ids),
            )
            key = (seen.origin_handle_id, seen.slot, seen.source_id)
            merged = merge_attr(rows.get(key), seen)
            if merged is not None:
                await uow.memory.put_attr(merged)
                rows[key] = merged

    async def _decay(self, uow: UnitOfWork, handle_ids: Sequence[UUID], now: datetime) -> bool:
        changed = False
        for fact in await uow.memory.facts_for(handle_ids, (FactStatus.ACTIVE, FactStatus.EXPIRED)):
            value = decayed(fact, now, self._settings.half_life_days)
            if value < fact.confidence:
                await uow.memory.update_fact(replace(fact, confidence=value))
                changed = True
        return changed

    async def _mark_overdue(
        self, uow: UnitOfWork, handle_ids: Sequence[UUID], now: datetime, timezone: str
    ) -> bool:
        late = [i for i in await uow.memory.open_items_for(handle_ids) if is_overdue(i, now, timezone)]
        for item in late:
            await uow.memory.update_open_item(replace(item, status=OpenItemStatus.OVERDUE))
        return bool(late)

    async def _contradiction_band(
        self, uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, drawn: list[BandPair]
    ) -> None:
        """Draws the band's candidate pairs between free-text facts of one predicate, from
        the confirmed rows only (5.3). The pairs the decision model confirms become `CONTRADICTS` edges and
        send their conversation to the review by sample (`consolidate`). Nothing changes a status: a person
        settles the pair through feedback."""
        view = derivation_view(profile)
        specs = _predicate_specs(config)

        def single_valued(fact: Fact) -> bool:
            spec = specs.get(fact.predicate)
            return spec is not None and spec.single_valued

        facts = [
            f
            for f in await uow.memory.facts_for(view.handle_ids)
            if not single_valued(f) and self._free_text(f, specs)
        ]
        slots = Counter((f.predicate, f.namespace, f.about_handle_id) for f in facts)
        if not any(n > 1 for n in slots.values()):
            return  # nothing to compare: the vectors stay sealed
        vectors = {
            v.item_id: v for v in await uow.memory.vectors_for(view.handle_ids) if v.kind is ItemKind.FACT
        }
        related = [(r.a.id, r.b.id) for r in await uow.memory.relations_for(view.handle_ids)]
        # More than a night's share: the pairs the decision model already judged compatible are left out
        # after the transaction (`_confirm_band`), and must not keep the others out.
        drawn.extend(
            band_pairs(
                facts,
                vectors,
                single_valued=single_valued,
                provisional=profile.provisional_handle_ids,
                related=related,
                max_pairs=self._settings.band_pairs_per_night * BAND_DRAW_FACTOR,
            )
        )

    async def _summaries(self, uow: UnitOfWork, profile: ProfileRecord, timezone: str) -> bool:
        """The stored tail summaries, from the confirmed handles' episodes only (5.3): a reader whose view
        holds an unconfirmed link's rows gets its own, rebuilt on read (`load_snapshot`). A confirmation
        changes the text without moving the membership version or the newest episode, so the text itself
        is compared too."""
        view = derivation_view(profile)
        episodes = await uow.memory.episodes_for(view.handle_ids)
        if not episodes:
            return await self._blank_summaries(uow, profile)
        newest = max(e.ended_at for e in episodes)
        texts = tail_summaries(episodes, timezone)
        changed = False
        for level in LEVELS:
            state = await uow.memory.tail_state(profile.profile_id, level)
            if (
                state
                and state.covers_until >= newest
                and state.membership_version == profile.membership_version
                and state.text == texts[level]
            ):
                continue
            version = state.version + 1 if state else 1
            fresh = TailState(texts[level], version, newest, profile.membership_version)
            await uow.memory.put_tail_summary(profile.profile_id, level, fresh)
            changed = True
        return changed

    async def _blank_summaries(self, uow: UnitOfWork, profile: ProfileRecord) -> bool:
        """No confirmed episode left to summarize: a summary stored before holds nothing a reader may keep."""
        changed = False
        for level in LEVELS:
            state = await uow.memory.tail_state(profile.profile_id, level)
            if state and state.text:
                blank = TailState("", state.version + 1, state.covers_until, profile.membership_version)
                await uow.memory.put_tail_summary(profile.profile_id, level, blank)
                changed = True
        return changed

    async def _request_recompute(self, uow: UnitOfWork, space_id: UUID, profile_id: UUID) -> None:
        for name in RECOMPUTED_FROM_STATE:
            await uow.pipeline.request_work(Task(name, space_id, profile_id=profile_id))
        # An organization's pack renders its contacts' recent conversations and items: what changes a
        # contact's memory compiles the organizations they act for again, or their packs keep the old lines.
        for account in await _organizations_of(uow, profile_id):
            await uow.pipeline.request_work(Task(TaskName.COMPILE, space_id, profile_id=account))

    async def _session_scope(
        self, uow: UnitOfWork, session: SessionRecord, profile_id: UUID | None
    ) -> _Scope | None:
        profile_id = profile_id or session.profile_id
        if profile_id is None:
            return None  # a session of no customer (5.3); its task holds no lease, so it writes nothing
        events = await session_events(uow, session)
        profile = await self._profile(uow, profile_id)
        if profile is None:
            # The profile may have been merged away since the session closed: follow a handle (5.3),
            # never a blocked value, whose profile gathers what many people typed.
            named = list(dict.fromkeys(h for e in events for h in e.handle_ids))
            usable = {
                r.handle_id
                for r in await uow.identity.get_handles(named)
                if r.status is not HandleStatus.BLOCKED
            }
            handle = next((h for h in named if h in usable), None)
            profile = await uow.identity.profile_of(handle) if handle else None
        if profile is None:
            return None
        # A conversation on confirmed handles never reconciles against, supersedes or closes the rows of
        # an unconfirmed link, and one only on an unconfirmed link never touches the confirmed rows (5.3).
        profile = session_view(profile, {h for e in events for h in e.handle_ids})
        if not profile.handle_ids:
            return None
        handles = {h.handle_id: h for h in await uow.identity.get_handles(profile.handle_ids)}
        return _Scope(profile, handles, session, {e.event_id: e for e in events})

    async def _profile(self, uow: UnitOfWork, profile_id: UUID | None) -> ProfileRecord | None:
        return await uow.identity.get_profile(profile_id) if profile_id else None

    async def _others_items(
        self, uow: UnitOfWork, profile: ProfileRecord, closing: Closing, own: Sequence[OpenItem]
    ) -> list[OpenItem]:
        """The open items of other people a closing on an object may fulfil: the ones the organization's
        linked people noted about one of its objects (21.4), and the ones the object's participants bound to
        it or to an object it belongs to, as staff named by the company's own system and
        linked to no client. Only items that name an object, so an amount alone never closes
        another person's promise."""
        if closing.object is None:
            return []
        found = {i.item_id: i for i in own}
        others: list[OpenItem] = []
        contacts = await linked_contacts_items(uow, profile, OPEN_STATUSES)
        for key in (closing.object, *closing.parents):
            contacts += await _object_items(uow, key, ())
        for item in contacts:
            if item.object is not None and item.item_id not in found:
                found[item.item_id] = item
                others.append(item)
        return others

    async def _contradicts(
        self, uow: UnitOfWork, handle_ids: Sequence[UUID]
    ) -> list[tuple[MemoryRef, MemoryRef]]:
        return [
            (r.a, r.b)
            for r in await uow.memory.relations_for(handle_ids)
            if r.type is RelationType.CONTRADICTS and r.settles
        ]

    def _free_text(self, fact: Fact, specs: dict[str, PredicateSpec]) -> bool:
        spec = specs.get(fact.predicate)
        if spec is not None and spec.enum is not None:
            return False
        return len(fact.value.split()) >= self._settings.min_words_to_embed


async def holds_feedback_target(
    uow: UnitOfWork, profile: ProfileRecord, *, fact_id: UUID | None, open_item_id: UUID | None
) -> bool:
    """Whether the fact or the open item a correction names is the profile's, in any status, or, for an
    open item of an organization, one its linked people noted: what `_feedback` can apply."""
    if fact_id:
        facts = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
        return any(f.fact_id == fact_id for f in facts)
    if open_item_id:
        every = tuple(OpenItemStatus)
        items = await uow.memory.open_items_for(profile.handle_ids, every)
        items += await linked_contacts_items(uow, profile, every)
        return any(i.item_id == open_item_id for i in items)
    return True


async def linked_contacts_items(
    uow: UnitOfWork, profile: ProfileRecord, statuses: Sequence[OpenItemStatus] = OPEN_STATUSES
) -> list[OpenItem]:
    """The open items an organization's actively linked people noted, which its own view shows (21.4);
    none for a person."""
    if not profile.kind.is_organization:
        return []
    org = set(profile.handle_ids)
    people = sorted(
        {link.person_handle_id for link in await uow.identity.active_links(profile.handle_ids)} - org
    )
    return await uow.memory.open_items_for(people, tuple(statuses)) if people else []


async def unembedded(
    uow: UnitOfWork, model: str | None, limit: int, profile: ProfileRecord | None
) -> list[PendingVector]:
    """Up to `limit` navigable items that need a vector, from the modules that own them: the profile's,
    or, without one, the whole space's."""
    handles = profile.handle_ids if profile else None
    found = await uow.memory.unembedded(model=model, handle_ids=handles, limit=limit)
    if len(found) < limit:
        found += await uow.objects.unembedded(model=model, handle_ids=handles, limit=limit - len(found))
    if len(found) < limit:
        owner = profile.profile_id if profile else None
        found += await uow.traits.unembedded(model=model, profile_id=owner, limit=limit - len(found))
    return found


async def _store_vector(uow: UnitOfWork, pending: PendingVector, vector: Sequence[float], model: str) -> None:
    if pending.kind in (ItemKind.OBJECT, ItemKind.ACTION):
        await uow.objects.set_vector(pending, vector, model)
    elif pending.kind is ItemKind.TRAIT:
        await uow.traits.set_vector(pending, vector, model)
    else:
        await uow.memory.set_vector(pending, vector, model)


def _predicate_specs(config: SpaceConfig) -> dict[str, PredicateSpec]:
    """Tenant predicates and every source's additions: memory holds facts from all sources."""
    extra = [p for specs in config.extraction.source_overrides.values() for p in specs]
    return {p.name: p for p in [*extra, *config.extraction.predicates]}


def _customer_facing(
    config: SpaceConfig, source_id: UUID, subject: SubjectKind | None, conversation_id: str | None
) -> bool:
    """The source speaks with the customer in this conversation: what it says it did closes only what it names
    or says. An internal key talking with anyone but staff speaks with a customer."""
    source = config.source(source_id)
    audience = conversation_audience(
        source.audience if source else None, subject, conversation=conversation_id is not None
    )
    return audience is Audience.CUSTOMER_AGENT


def _session_audience(config: SpaceConfig, session: SessionRecord, profile: ProfileRecord) -> Audience | None:
    """The audience a session's memory is written with: its source's, narrowed by whom the conversation was
    with, so a customer's words in an internal key's conversation never pass for the
    company's."""
    source = config.source(session.source_id)
    return conversation_audience(
        source.audience if source else None, profile.kind, conversation=_is_conversation(session)
    )


def _is_conversation(session: SessionRecord) -> bool:
    """A session of a conversation, not of a task: a task is internal whoever it is about."""
    return session.conversation_id is not None and session.task_id is None


def _trusted_ops(config: SpaceConfig, source_id: UUID) -> frozenset[str]:
    source = config.source(source_id)
    return canonical_operations(source.trusted_action_ops) if source else frozenset()


def _resolved_by(item: OpenItem, closing: Closing, closed_by: Mapping[str, Any]) -> OpenItem:
    """`item` closed by `closing`, keeping what closed it (`closed_by`, as its webhook says it)."""
    return with_closed_by(
        replace(
            item,
            status=OpenItemStatus.RESOLVED,
            closed_by_action_id=closing.action_id,
            closed_at=closing.occurred_at,
        ),
        closed_by,
    )


def _closed_by(closing: Closing, event_id: object) -> dict[str, Any]:
    return {
        "kind": "action" if closing.action_id else "system_event",
        "action_id": str(closing.action_id) if closing.action_id else None,
        "event_id": str(event_id) if event_id else None,
        "operation": closing.operation,
    }


def _subject(profile: ProfileRecord) -> WebhookSubject:
    return WebhookSubject(profile.pseudonym, profile.kind.value)


FACT_EVENTS = ("fact.added", "fact.retracted")


async def _facts_if_announced(
    uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord
) -> dict[UUID, Fact] | None:
    """The profile's facts before a change, read only when an endpoint takes the fact events."""
    if not subscribes(config, FACT_EVENTS):
        return None
    return {f.fact_id: f for f in await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)}


async def _announce_facts(
    uow: UnitOfWork,
    config: SpaceConfig,
    profile: ProfileRecord,
    before: Mapping[UUID, Fact] | None,
    origin: Mapping[str, str | None],
) -> None:
    """`fact.added` for each fact that became active and `fact.retracted` for each that stopped being, in the
    transaction that changed them (7.5). A fact seen again is not news. Like every event, the payload holds
    ids, labels and times, never the value: the receiver reads it with its own key, under the policy."""
    if before is None:
        return
    after = await uow.memory.facts_for(profile.handle_ids, ALL_FACT_STATUSES)
    was_active = {i for i, f in before.items() if f.status is FactStatus.ACTIVE}
    added = [f for f in after if f.status is FactStatus.ACTIVE and f.fact_id not in was_active]
    left = [f for f in after if f.fact_id in was_active and f.status is not FactStatus.ACTIVE]
    by = {k: v for k, v in origin.items() if v is not None}

    def same_slot(a: Fact, b: Fact) -> bool:
        return (a.predicate, a.origin_handle_id, a.object_ref) == (
            b.predicate,
            b.origin_handle_id,
            b.object_ref,
        )

    subject = _subject(profile)
    for fact in added:
        replaces = sorted(str(o.fact_id) for o in left if same_slot(o, fact))
        data, attrs, links = _fact_event(config, profile, fact, by | {"replaces": replaces})
        await enqueue_event(uow, config, "fact.added", subject, data, links=links, item=attrs)
    for fact in left:
        replaced_by = sorted(str(n.fact_id) for n in added if same_slot(n, fact))
        data, attrs, links = _fact_event(config, profile, fact, by | {"replaced_by": replaced_by})
        await enqueue_event(uow, config, "fact.retracted", subject, data, links=links, item=attrs)


async def _announce_restored(
    uow: UnitOfWork,
    config: SpaceConfig,
    profile: ProfileRecord,
    fact: Fact,
    undone: Sequence[Fact],
    event: EventRecord,
) -> None:
    """`fact.restored`: the fact is active again by the security role's hand, and which corrections of it
    were retracted. `fact.added` and `fact.retracted` go out too, to whoever takes them."""
    extra = {
        "by": "restore",
        "event_id": str(event.event_id),
        "retracted": sorted(str(f.fact_id) for f in undone),
    }
    data, attrs, links = _fact_event(config, profile, fact, extra)
    await enqueue_event(uow, config, "fact.restored", _subject(profile), data, links=links, item=attrs)


def _fact_event(
    config: SpaceConfig, profile: ProfileRecord, fact: Fact, extra: Mapping[str, Any]
) -> tuple[dict[str, Any], ItemAttrs, dict[str, str]]:
    """The payload, the policy attributes each endpoint's source is checked against, and the links."""
    data: dict[str, Any] = {
        "fact_id": str(fact.fact_id),
        "predicate": fact.predicate,
        "category": fact.category,
        "sensitivity": fact.sensitivity.value,
        "status": fact.status.value,
        "confidence": round(fact.confidence, 4),
        "valid_at": _iso(fact.valid_at),
        "invalid_at": _iso(fact.invalid_at),
        "valid_until": _iso(fact.valid_until),
        "object": _key_json(fact.object_ref),
        "evidence_event_ids": [str(e) for e in fact.evidence_event_ids],
        **extra,
    }
    attrs = ItemAttrs(
        "fact",
        fact.category,
        sensitivity=fact.sensitivity,
        subject_kind=profile.kind,
        handle_ids=(fact.origin_handle_id,),
        profile_id=profile.profile_id,
        purposes=_readers(config, fact.purposes),
    )
    links = {
        "memory": f"/v1/profiles/{profile.profile_id}/memory",
        "lineage": f"/v1/lineage/fact/{fact.fact_id}",
    }
    return data, attrs, links


def _key_json(key: ObjectKey | None) -> dict[str, str] | None:
    return {"type": key.type, "namespace": key.namespace, "id": key.external_id} if key else None


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _item_links(profile: ProfileRecord, item: OpenItem) -> dict[str, str]:
    if item.object:
        key = item.object
        return {"open": f"/v1/objects/{key.type}/{key.namespace}/{key.external_id}/timeline"}
    return {"open": f"/v1/profiles/{profile.profile_id}/memory"}


async def _item_attrs(
    uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, item: OpenItem
) -> ItemAttrs:
    """As the compiler sees the item: about the category of the conversation that opened it (5.12), read
    by the purposes it was collected for (13, 6.6)."""
    session_id = _uuid_or_none(item.origin.get("session_id"))
    episode = await uow.memory.current_episode(profile.handle_ids, session_id) if session_id else None
    topics = (episode.normalized_category,) if episode else ()
    return ItemAttrs(
        "open_item",
        "open_item",
        handle_ids=(item.origin_handle_id,),
        topics=topics,
        purposes=_readers(config, item.purposes),
    )


def _readers(config: SpaceConfig, tags: Tags | None) -> frozenset[str]:
    compatible = {k: frozenset(v) for k, v in config.policy.compatible_purposes.items()}
    return readers(tags, compatible)


async def _organizations_of(uow: UnitOfWork, profile_id: UUID) -> list[UUID]:
    """The profiles of the organizations a person acts for through an active link (21.2); none for an
    organization."""
    profile = await uow.identity.get_profile(profile_id)
    if profile is None or profile.kind.is_organization:
        return []
    mine = set(profile.handle_ids)
    orgs = {link.org_handle_id for link in await uow.identity.active_links(profile.handle_ids)} - mine
    # One statement for all of them: a person may act for any number of organizations.
    members = await uow.identity.memberships(sorted(orgs)) if orgs else {}
    return sorted(set(members.values()))


async def _announce_closed(
    uow: UnitOfWork,
    config: SpaceConfig,
    profile: ProfileRecord,
    item: OpenItem,
    closed_by: Mapping[str, Any],
) -> None:
    """`open_item.closed` (7.5): ids and pointers only; the description is conversation content."""
    data = {
        "open_item_id": str(item.item_id),
        "owner": item.owner,
        "object": _key_json(item.object),
        "expected_operation": item.expected_operation,
        "due_at": _iso(item.due_at),
        "opened_in": {"session_id": item.origin.get("session_id"), "source_id": item.origin.get("source_id")},
        "closed_by": dict(closed_by),
        "as_of": _iso(item.closed_at),
    }
    await enqueue_event(
        uow,
        config,
        "open_item.closed",
        _subject(profile),
        data,
        links=_item_links(profile, item),
        item=await _item_attrs(uow, config, profile, item),
    )


async def _announce_reopened(
    uow: UnitOfWork, config: SpaceConfig, profile: ProfileRecord, item: OpenItem, action_id: UUID | None
) -> None:
    data = {
        "open_item_id": str(item.item_id),
        "reason": "closing_action_quarantined",
        "action_id": str(action_id) if action_id else None,
        "object": _key_json(item.object),
    }
    await enqueue_event(
        uow,
        config,
        "open_item.reopened",
        _subject(profile),
        data,
        links=_item_links(profile, item),
        item=await _item_attrs(uow, config, profile, item),
    )


async def _announce_outcome(
    uow: UnitOfWork,
    config: SpaceConfig,
    profile: ProfileRecord,
    episode: Episode,
    by: str,
    open_item_ids: Sequence[UUID] | None,
) -> None:
    """`conversation.outcome` (7.5): how a conversation or task ended, for the CRM to register. Labels
    from the tenant's closed lists only; the summary and the resolution are content and stay home."""
    data: dict[str, Any] = {
        "episode_id": str(episode.episode_id),
        "version": episode.version,
        "session_id": str(episode.session_id),
        "conversation_id": external_reference(episode.conversation_id),
        "source_id": str(episode.source_id),
        "channel": episode.channel,
        "intent": episode.intent,
        "category": episode.normalized_category,
        "outcome": episode.outcome,
        "by": by,
        "started_at": _iso(episode.started_at),
        "ended_at": _iso(episode.ended_at),
        "as_of": _iso(episode.ended_at),
    }
    if open_item_ids is not None:
        data["open_item_ids"] = [str(i) for i in open_item_ids]
    attrs = ItemAttrs(
        "episode",
        episode.normalized_category,
        handle_ids=tuple(h for h in (episode.origin_handle_id, episode.about_handle_id) if h),
        purposes=_readers(config, episode.purposes),
    )
    links = {"open": f"/v1/history/items/{episode.episode_id}"}
    await enqueue_event(uow, config, "conversation.outcome", _subject(profile), data, links=links, item=attrs)


async def _object_items(uow: UnitOfWork, key: ObjectKey, own: Sequence[OpenItem]) -> list[OpenItem]:
    """The open items bound to the object: its owner's and participants' (what object memory reads to them)
    and the subject's own from other conversations."""
    found = await uow.objects.get_object(key)
    if found is None:
        return [i for i in own if i.object == key]
    return await _items_on(uow, [found], own)


async def _object_handles(uow: UnitOfWork, obj: BusinessObject) -> set[UUID]:
    """The handles a system of record writes the object's events under: its owner and its participants, at
    most `MAX_HANDLES`."""
    parts = await uow.objects.participants_of([obj.object_id], per_object=MAX_HANDLES)
    handles = {h for pairs in parts.values() for h, _ in pairs}
    if obj.origin_handle_id is not None:
        handles.add(obj.origin_handle_id)
    return handles


async def _items_on(
    uow: UnitOfWork, objects: Sequence[BusinessObject], own: Sequence[OpenItem]
) -> list[OpenItem]:
    """The open items bound to `objects`, their owners' and participants' and the subject's own, in two
    statements whatever the number of objects: every one, never the newest few of each."""
    if not objects:
        return []
    parts = await uow.objects.participants_of([o.object_id for o in objects], per_object=MAX_HANDLES)
    handles = {h for pairs in parts.values() for h, _ in pairs}
    handles |= {o.origin_handle_id for o in objects if o.origin_handle_id is not None}
    keys = [o.key for o in objects]
    theirs = await uow.memory.object_items(keys, sorted(handles, key=str), per_object=ROWS_CEILING)
    seen = {i.item_id for i in theirs}
    wanted = set(keys)
    return [*theirs, *(i for i in own if i.object in wanted and i.item_id not in seen)]


async def _subject_items(
    uow: UnitOfWork, scope: "_Scope", own: Sequence[OpenItem], session: str
) -> list[OpenItem]:
    """The open items, from other conversations, on every object the subject or the organization it acts for
    is tied to (the objects its extraction was offered, up to `OBJECTS_CEILING`)."""
    links = await uow.identity.active_links(scope.handle_ids) if scope.handle_ids else []
    handles = sorted({*scope.handle_ids, *await organization_handles(uow, scope.handle_ids, links)})
    objects = await uow.objects.objects_for(handles, limit=OBJECTS_CEILING) if handles else []
    found: dict[UUID, OpenItem] = {}
    for item in await _items_on(uow, objects, own):
        if item.status in OPEN_STATUSES and item.origin.get("session_id") != session:
            found.setdefault(item.item_id, item)
    return list(found.values())


def _bounded[T](pairs: Sequence[T], item_of: Callable[[T], OpenItem]) -> list[T]:
    """At most `MAX_JUDGED` of `pairs`, those about the newest open items first."""
    if len(pairs) <= MAX_JUDGED:
        return list(pairs)
    ranked = sorted(pairs, key=lambda p: (item_of(p).created_at, item_of(p).item_id), reverse=True)
    return ranked[:MAX_JUDGED]


def _retells_elsewhere(draft: OpenItemDraft, item: OpenItem) -> bool:
    """The draft tells again an open item on another object, compared as if it were on that one."""
    return restates_on_object(draft.description, draft.due_at, item.object, draft.expected_operation, item)


def _carries_deadline(draft: OpenItemDraft, item: OpenItem) -> bool:
    """`item` is the deadline net's generic one, still open and due the day `draft` is: the model now wrote
    the item that carries that deadline."""
    return (
        is_deadline_item(item.description)
        and not is_deadline_item(draft.description)
        and item.status in OPEN_STATUSES
        and same_day(item.due_at, draft.due_at)
    )


def _payload_key(data: Mapping[str, str] | None) -> ObjectKey | None:
    """Objects' JSON form of a key: `{type, namespace, id}`."""
    return ObjectKey(data["type"], data["namespace"], data["id"]) if data else None


async def _note_refused_resolution(uow: UnitOfWork, event: EventRecord) -> None:
    """A `feedback.resolve_open_item` event that closed nothing (the item was already closed, or is not the
    subject's) keeps a pair with the item it names, so an erasure of the item strips its reason (8.4)."""
    if event.canonical_type != RESOLVE_ITEM_TYPE:
        return
    item_id = _uuid_or_none(event.fields.get("open_item_id"))
    if item_id is not None:
        await uow.memory.note_item_feedback([(item_id, event.event_id)])


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _sessions_after(existing: Fact, scope: _Scope, quarantined: bool) -> int:
    """A session confirms a value once, however many turns repeat it: the count moves
    only when none of the row's evidence so far came from this session. A quarantined observation never
    confirms."""
    if quarantined:
        return existing.sessions_seen
    if existing.status is FactStatus.QUARANTINED:
        return 1  # a clean observation replaces the untrusted ones (`merge_observation`)
    if any(e in scope.events for e in existing.evidence_event_ids):
        return existing.sessions_seen
    return existing.sessions_seen + 1


async def _sample_band_pairs(uow: UnitOfWork, pairs: Sequence[BandPair]) -> None:
    """The conversation that last stated the newer fact of each pair goes to the review by sample under its
    own condition, so the pairs the band draws are judged apart from everything else (22.2)."""
    wanted = sorted({e for p in pairs for e in p.newer.evidence_event_ids})
    events = {e.event_id: e for e in await uow.ingest.get_events(wanted)}
    sessions = await uow.sessions.sessions_of(list(events.values()))
    chosen: set[UUID] = set()
    for found in pairs:
        said = [events[e] for e in found.newer.evidence_event_ids if e in events and e in sessions]
        if said:
            latest = max(said, key=lambda e: (e.occurred_at, e.event_id))
            chosen.add(sessions[latest.event_id])
    if not chosen:
        return
    await uow.pipeline.enqueue(
        [
            Task(
                TaskName.SAMPLE_REVIEW,
                uow.space_id,
                {"session_id": str(session_id), "conditions": [CONTRADICTION_BAND]},
            )
            for session_id in sorted(chosen)
        ]
    )


def _key_ref(key: ObjectKey) -> str:
    return f"{key.type}:{key.namespace}:{key.external_id}"


def _target(draft: InstructionDraft) -> str:
    return _key_ref(draft.object) if draft.object else f"about:{draft.about_handle_id}"


def _instruction_keys(drafts: Sequence[InstructionDraft]) -> list[str]:
    """Each instruction's key within its session, stable across extractions of the same turns
    (`SaidInstruction.key`): what it is about, the turns it rests on, its strength and field, the one person
    it holds with and whether it is a contact preference, never its words. A conversation extracted again (a
    turn of the same activity reopens it) words its instruction again, with its reason this time,
    and the text in the key made it a second instruction on the case. Two instructions of one kind said in
    the same turns about the same thing are told apart by their words' order."""
    bases = [
        "\x1f".join(
            [
                _target(d),
                *sorted(str(e) for e in d.evidence_event_ids),
                d.strength,
                d.attr or "",
                fold(d.only_with or ""),
                "contact" if d.contact else "",
            ]
        )
        for d in drafts
    ]
    keys = list(bases)
    for base in set(bases):
        same = sorted((n for n, b in enumerate(bases) if b == base), key=lambda n: _said(drafts[n]))
        if len(same) > 1:
            for rank, n in enumerate(same):
                keys[n] = f"{base}\x1f{rank}"
    return keys


def _said(draft: InstructionDraft) -> str:
    return " ".join(sorted(item_words(draft.text))) or normalize_value(draft.text)


def _visibility(scope: _Scope, event_ids: Sequence[UUID]) -> Visibility:
    """Internal when any turn the row rests on was internal."""
    events = [scope.events[e] for e in event_ids if e in scope.events]
    internal = any(e.visibility is Visibility.INTERNAL for e in events)
    return Visibility.INTERNAL if internal else Visibility.PUBLIC


def _item_audience(scope: _Scope, draft: OpenItemDraft, source: Audience | None) -> MemoryAudience:
    return audience_of(source, _visibility(scope, draft.evidence_event_ids), "item")


def _speakers(scope: _Scope) -> dict[UUID, str]:
    """The handles of the people who spoke in the conversation, each with the role its events gave it."""
    found: dict[UUID, str] = {}
    for event in scope.events.values():
        if event.kind is not EventKind.MESSAGE or event.speaker is not Speaker.CUSTOMER:
            continue
        roles = {
            UUID(str(h)): str(s["role"])
            for s in event.subjects
            if s.get("role")
            for h in s.get("handle_ids", ())
        }
        for handle in event.handle_ids:
            if handle in scope.handles:
                found[handle] = roles.get(handle) or found.get(handle) or "participant"
    return found


def _objects_of(scope: _Scope, result: ExtractionResult) -> tuple[ObjectKey, ...]:
    """The objects a conversation was about: those its turns named, then those its rows were resolved to."""
    found = [
        k
        for e in sorted(scope.events.values(), key=lambda e: (e.occurred_at, e.event_id))
        for k in e.object_refs
    ]
    found += [c.object_ref for c in result.facts if c.object_ref is not None]
    found += [i.object for i in result.open_items if i.object is not None]
    found += [i.object for i in result.instructions if i.object is not None]
    return tuple(dict.fromkeys(found))
