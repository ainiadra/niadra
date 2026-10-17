"""Ingestion use cases: the write path of the cell (5.2; 06, 4.1).

One transaction per batch: dedupe keys under the shared rotation lock, events and their
`event_handles`, conversations, sessions, identity attachment and one outbox row per task, plus
the hot-layer safety net scheduled 2 s ahead. The hot layer is written after the commit.

The safety net is never cancelled: `TaskStore` has no cancel, and `hot_repair` re-adds turns with
an idempotent sorted-set add, so letting it fire costs one redundant write per batch and removes a
race between the commit and the cache write.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
from collections import Counter, defaultdict
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import orjson

from niadra.app.collaborators import AttachResult, IdentityAttacher, PendingMerge
from niadra.app.edge import source_principal, space_ref
from niadra.app.ingest_mapping import map_payload
from niadra.app.intake import Intake, PreparedEvent, SubjectGroup, groups_of
from niadra.app.memory import holds_feedback_target
from niadra.app.notify import enqueue_event
from niadra.app.object_memory import known_objects
from niadra.app.pending_proofs import HeldProof, PendingProofs, held_proofs
from niadra.app.raw_index import raw_object, sealed_tokens
from niadra.app.sessions import SessionService, warm_task
from niadra.config.models import Mapping as WebhookMapping
from niadra.config.models import SpaceConfig, WebhookAuth
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.events import (
    MAX_BATCH_ITEMS,
    ActionInfo,
    BatchItem,
    BatchRequest,
    BatchResponse,
    ConversationEndedItem,
    EventItem,
    FeedbackRequest,
    HandoffItem,
    HeartbeatItem,
    IdentifyItem,
    InteractionItem,
    ItemError,
    MediaUploadResponse,
    SpeakerRef,
    TaskEndedItem,
    VerifyItem,
    VoiceInfo,
)
from niadra.contracts.ingest import (
    CoverageDay,
    CoverageOut,
    EventsArrived,
    MediaUploadIn,
    SourceCoverage,
    validate_items,
)
from niadra.contracts.privacy import CorrectionRequest
from niadra.domain.common.errors import (
    Forbidden,
    InvalidInput,
    NiadraError,
    NotFound,
    ScopeMissing,
    Unavailable,
)
from niadra.domain.common.ids import uuid7, uuid7_floor
from niadra.domain.common.space import SourcePrincipal, SpaceRef, governance_principal, owns_secret
from niadra.domain.identity.handles import HandleKey, NormalizedHandle, normalize_handle
from niadra.domain.ingest import webhook_auth
from niadra.domain.ingest.coverage import coverage_since
from niadra.domain.ingest.events import (
    FEEDBACK_PREFIX,
    HANDOFF,
    ConversationTouch,
    NewEvent,
    export_row,
    is_feedback,
    marks_dirty,
    starts_object_work,
    touches_session,
)
from niadra.domain.ingest.files import (
    FileFormat,
    FileImport,
    ImportStatus,
    RecordSplitter,
    csv_item,
    jsonl_item,
    row_error,
    split_records,
)
from niadra.domain.ingest.hot import (
    HANDLE_CACHE_PURPOSE,
    cached_handle,
    handle_cache_input,
    handle_cache_name,
    hot_aad,
    occurred_ms,
    pack_envelope,
    turn_document,
)
from niadra.domain.ingest.intake import (
    ERASE_PAGE,
    MAX_FAILED_LISTED,
    MAX_INTAKE_ATTEMPTS,
    FailedIntake,
    IntakeRow,
    NewIntake,
    intake_aad,
)
from niadra.domain.ingest.masking import (
    Held,
    Hold,
    added,
    fingerprint,
    language,
    mask_fields,
    mask_lines,
    mask_payload,
    mask_text,
    needs_context,
)
from niadra.domain.ingest.media_text import MAX_BYTES as MEDIA_TEXT_MAX_BYTES
from niadra.domain.ingest.media_text import MIN_CHARS as MEDIA_TEXT_MIN_CHARS
from niadra.domain.ingest.media_text import allowed as media_allowed
from niadra.domain.ingest.media_text import is_derived, is_identity_document, one_line, parts, sniff, trimmed
from niadra.domain.ingest.otlp import otlp_items
from niadra.domain.ingest.rules import (
    attestation_level,
    capped_hint,
    channel_proof,
    check_action,
    check_identify_method,
    check_known,
    check_namespaces,
    check_scope,
    default_subject_kind,
    highest,
    sent_by_systems,
    verify_method_assertion,
)
from niadra.domain.ingest.skew import correct_occurred_at
from niadra.domain.ingest.storage import import_part_key, import_prefix, media_key, media_ref, raw_key
from niadra.domain.memory.bound import OBJECTS_CEILING
from niadra.domain.memory.restore import RESTORE_ACTION
from niadra.domain.metering.prompt_cache import model_usage_rows
from niadra.domain.notify.webhooks import WebhookSubject, external_reference
from niadra.domain.objects.written import normalized as written_value
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.due import STRAGGLER_AGE
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import Held as LegalHeld
from niadra.domain.privacy.mentions import Redacted
from niadra.domain.privacy.raw_index import event_tokens, redact_event, redact_value
from niadra.domain.records import EventRecord, ObjectKey, SessionRecord
from niadra.domain.sessions.rules import HOT_LAYER, CloseReason, check_verify_level, session_kind
from niadra.domain.signals.storage import intake_body, object_aad
from niadra.domain.signals.storage import intake_key as interaction_intake_key
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import HMAC_PURPOSE as TURN_HMAC
from niadra.domain.vocabulary import (
    AssertionMethod,
    EventKind,
    Feature,
    Scope,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.ports.coordination import owned_by_coordination
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import (
    Clock,
    ConfigProvider,
    Doorbell,
    HotLayer,
    HotLayerState,
    HotTurn,
    KeyValueCache,
    KeyVault,
    MediaTextReader,
    ModelsClient,
    ObjectStore,
    SecretStore,
    TaskBus,
)
from niadra.ports.repos.ingest import IntakeLog
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

WRITE_SCOPES = frozenset({Scope.TRACK, Scope.ACT, Scope.IDENTIFY})
SAFETY_NET_DELAY = timedelta(seconds=2)
HOT_QUIET = timedelta(seconds=10)
REBUILD_LIMIT = 20_000
REBUILD_PAGE = 2_000
REBUILD_LEASE_MS = 60_000
"""The rebuild's claim on the hot layer's marker, extended after every page: a rebuilder that dies lets
it lapse in a minute, and the next check starts over."""
DEDUPE_MIN_AGE = timedelta(days=29)
WARM_WITHIN = timedelta(minutes=10)
MEDIA_URL_TTL_S = 900
WEBHOOK_SCOPES = frozenset({Scope.TRACK, Scope.ACT})
MASK_PAGE = 500
MENTION_PAGE = 500
"""Events an erasure redacts, or the first indexing of what stored events name, per transaction."""
LAST_EVENT_ID = UUID(int=(1 << 128) - 1)
MASK_PAUSE = timedelta(seconds=2)
"""Between pages of the masking pass over stored events: the background queue stays open to other work."""
MASK_ROLLOUT = timedelta(minutes=15)
"""How long after a pass starts it keeps looking for new rows: processes that have not read the new rules
yet may still ingest events masked the old way."""
MASK_TAIL_PAUSE = timedelta(minutes=1)
HAND_OVER_TIMEOUT_S = 0.1
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
APPLY_GROUP = 50
"""Intake rows stored per transaction: under load a group amortizes the transaction and adds up what the
batches share (tasks, safety net, counters); idle, it is one row."""


class _IntakeBusy(Exception):
    """Another transaction holds the space's intake lock: a worker is storing what was acknowledged."""


INTAKE_BUSY_PAUSES_S = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)
"""The waits of a worker that found the space's intake lock held by another, about 1.9 s in all."""
HANDLE_CACHE_TTL_S = 86_400
MASK_CONTEXT = timedelta(minutes=30)
"""How far back the turn that gives a bare number its meaning ("qual o número do cartão?") may be."""

Keyed = IdentifyItem | VerifyItem | EventItem | ConversationEndedItem | TaskEndedItem | HandoffItem
BeforeCommit = Callable[[UnitOfWork, BatchResponse], Awaitable[None]]


@dataclasses.dataclass
class _Batch:
    """What one batch accumulates while its transaction is open."""

    caller: SourcePrincipal
    config: SpaceConfig
    received_at: datetime
    raw_body: bytes | None
    errors: list[ItemError]
    generation: int = 0
    duplicates: int = 0
    accepted: int = 0
    interactions: int = 0
    """Interaction items taken for their worker (24.3.2): accepted apart from the events."""
    events: list[NewEvent] = dataclasses.field(default_factory=list)
    profiles: dict[UUID, UUID | None] = dataclasses.field(default_factory=dict)
    tasks: list[Task] = dataclasses.field(default_factory=list)
    released: list[str] = dataclasses.field(default_factory=list)
    conversations: dict[str, str] = dataclasses.field(default_factory=dict)
    raw_only_for_systems: bool = False
    """`/v1/batch`: the payload is kept only when a system sent a system event in it; an agent's
    turns are the event log itself and would only be stored twice."""
    raw_stored: str | None = None
    """The key the batch's payload was stored under, before the commit."""
    proofs: dict[tuple[str | None, str | None], Verification | None] = dataclasses.field(default_factory=dict)
    attested: dict[tuple[str | None, str | None], Verification] = dataclasses.field(default_factory=dict)
    masked: Counter[str] = dataclasses.field(default_factory=Counter)
    last_attach: tuple[Any, AttachResult] | None = None
    learned: dict[HandleKey, tuple[UUID, bool]] = dataclasses.field(default_factory=dict)
    """Handle ids the batch's attaches found or created, and whether identity lets each stand for its
    subject, for the handle cache after the commit."""
    cards: set[str] = dataclasses.field(default_factory=set)
    """Card digits the batch's events held, in memory only: its raw payload masks them too."""
    merge_now: bool = False
    """An API or webhook batch runs the merges its `identify` items ask for before it answers; a file
    import leaves them to the workers, row after row."""
    merges: dict[UUID, PendingMerge] = dataclasses.field(default_factory=dict)
    """The merges the batch's `identify` items asked for, run after the commit and before the answer."""
    lang: str = dataclasses.field(init=False)
    masking: frozenset[Held] = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.lang = language(self.config.settings.locale)
        self.masking = self.config.masking_types()

    def hold(self, held: Sequence[Hold]) -> None:
        self.masked.update(h.kind.value for h in held)
        self.cards.update(h.digits for h in held if h.digits)

    @property
    def space_id(self) -> UUID:
        return self.caller.space.space_id

    @property
    def channel(self) -> str:
        source = self.config.source(self.caller.source_id)
        return (source.channel if source else None) or "api"

    def reject(self, index: int, error: NiadraError, key: str | None = None) -> None:
        self.errors.append(ItemError(index=index, code=error.code, detail=error.detail or None))
        if key is not None:
            self.released.append(key)


class IngestService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        identity: IdentityAttacher,
        sessions: SessionService,
        keys: KeyVault,
        hot: HotLayer,
        clock: Clock,
        config: ConfigProvider,
        objects: ObjectStore,
        secrets: SecretStore,
        *,
        media: MediaTextReader | None = None,
        models: ModelsClient | None = None,
        intake: IntakeLog | None = None,
        cache: KeyValueCache | None = None,
        workers: TaskBus | None = None,
        doorbell: Doorbell | None = None,
        issues: DataIssues | None = None,
    ) -> None:
        """With `intake`, a batch of conversation turns is acknowledged once its intake row is written and
        stored right after (5.2): by a worker with `workers`, whom the answer hands the row's task, so the
        process that answers spends no time storing; before returning without it. `cache` remembers which
        handle id each handle has, so the acknowledgement writes the hot layer too. `doorbell` wakes the
        space's relay for the row's outbox copy when the hand-over fails."""
        self._media = media
        self._models = models
        self._intake = intake
        self._issues = issues
        self._handle_cache = cache
        self._workers = workers
        self._doorbell = doorbell
        self._handing: set[asyncio.Task[None]] = set()
        self._draining: set[UUID] = set()
        self._drain_again: set[UUID] = set()
        self._uow = uow
        self._identity = identity
        self._sessions = sessions
        self._keys = keys
        self._hot = hot
        self._proofs = PendingProofs(keys, hot)
        self._clock = clock
        self._config = config
        self._objects = objects
        self._secrets = secrets

    async def ingest_batch(self, caller: SourcePrincipal, request: BatchRequest) -> BatchResponse:
        return await self.ingest_items(caller, list(enumerate(request.items)))

    async def record_coordination_events(self, caller: SourcePrincipal, events: Sequence[EventItem]) -> None:
        """`ports.coordination.SystemEvents`: a commitment's action and a handoff's outcome, recorded as the
        caller's. Coordination's key holds `coordinate`, so the scopes and the closed list of operations of
        the source are not asked here, and every event must be one of coordination's own actions (24.3.4)."""
        if not all(owned_by_coordination(event) for event in events):
            raise Forbidden("coordination records only the actions of its commitments and handoffs")
        granted = dataclasses.replace(
            caller, scopes=caller.scopes | {Scope.TRACK, Scope.ACT}, trusted_action_ops=frozenset()
        )
        response = await self._ingest(granted, list(enumerate(events)), (), coordination=True)
        if response.errors:
            raise InvalidInput(f"a coordination event was refused: {response.errors[0].code}")

    async def ingest_items(
        self,
        caller: SourcePrincipal,
        items: Sequence[tuple[int, BatchItem]],
        rejected: Sequence[ItemError] = (),
        *,
        raw_body: bytes | None = None,
    ) -> BatchResponse:
        """Items already validated one by one; `rejected` are the ones that failed validation (207).
        `raw_body` is the batch as it arrived: kept like a webhook's when it carries a system event."""
        return await self._ingest(caller, items, rejected, raw_body=raw_body, raw_only_for_systems=True)

    async def _ingest(
        self,
        caller: SourcePrincipal,
        items: Sequence[tuple[int, BatchItem]],
        rejected: Sequence[ItemError],
        *,
        raw_body: bytes | None = None,
        before_commit: BeforeCommit | None = None,
        raw_only_for_systems: bool = False,
        coordination: bool = False,
    ) -> BatchResponse:
        """`raw_body` is a webhook payload, stored before the commit; `before_commit` joins the batch's
        transaction, which is how a file import moves its progress atomically with the events. `coordination`
        when coordination records its own actions, the only ones that may name its namespace."""
        if not caller.scopes & WRITE_SCOPES:
            raise Forbidden("the key cannot write")
        config = self._config.space(caller.space.space_id)
        batch = _Batch(caller, config, self._clock.now(), raw_body, list(rejected))
        batch.raw_only_for_systems = raw_only_for_systems
        batch.merge_now = before_commit is None
        keyed: list[tuple[int, Keyed]] = []
        heartbeats: list[HeartbeatItem] = []
        interactions: list[tuple[int, InteractionItem]] = []
        for index, item in items:
            if isinstance(item, HeartbeatItem):
                heartbeats.append(item)
                continue
            if isinstance(item, InteractionItem):
                interactions.append((index, item))
                continue
            try:
                self._check(caller, item, coordination=coordination)
            except NiadraError as exc:
                batch.reject(index, exc)
                continue
            keyed.append((index, item))
        if interactions:
            await self._take_interactions(batch, interactions)
            if not keyed and not heartbeats and before_commit is None:
                return _response(batch)

        if self._intake is not None and before_commit is None:
            turns = _turns_only(batch, keyed)
            if turns is not None:
                return await self._accept(batch, turns, heartbeats)
        applied: list[_Applied] = []
        for pause in (*INTAKE_BUSY_PAUSES_S, None):
            try:
                async with self._uow(batch.space_id) as uow:
                    # What was acknowledged before this batch is stored before it (5.2). While a worker stores
                    # it, the transaction ends before anything else ran and starts again after a pause, so the
                    # acknowledgement never holds a server connection blocked on the lock; the last try waits.
                    applied = await self._apply_pending(uow, batch.space_id, wait=pause is None)
                    fresh = await self._dedupe(uow, batch, keyed)
                    await self._apply(uow, batch, fresh)
                    await self._count(
                        uow, batch, [(b.window_start, b.sent) for b in heartbeats], len(batch.errors)
                    )
                    response = _response(batch)
                    if before_commit is not None:
                        await before_commit(uow, response)
            except _IntakeBusy:
                assert pause is not None
                await asyncio.sleep(pause)
                continue
            except Exception:
                # The payload stored for a batch that rolled back lost its index row with it: it goes too.
                if batch.raw_stored is not None:
                    await self._objects.delete(batch.raw_stored)
                raise
            break
        # An `identify` that names two customers' handles answers with one customer: its merge runs
        # now, under the profiles' lease, and the next `context()` reads the merged profile.
        for merge in batch.merges.values():
            await self._identity.merge_now(batch.space_id, merge)
        await self._after_apply(applied)
        await self._write_hot_after_commit(batch)
        await self._remember_handles(batch)
        return response

    async def _accept(
        self, batch: _Batch, turns: Sequence[tuple[int, EventItem]], heartbeats: Sequence[HeartbeatItem]
    ) -> BatchResponse:
        """The acknowledgement of a batch of conversation turns: masked, sealed and claimed in one statement,
        written to the hot layer, and stored right after (5.2). Every check that could refuse an item ran
        before (`_turns_only`), so storing it later never turns an accepted item into an error."""
        assert self._intake is not None
        arrival_ms = int(batch.received_at.timestamp() * 1000)
        seen: set[str] = set()
        prepared: list[PreparedEvent] = []
        held: dict[str, list[Hold]] = {}
        for _, item in turns:
            if item.idempotency_key in seen:
                batch.duplicates += 1
                continue
            seen.add(item.idempotency_key)
            event, holds = await self._prepare(batch, item, uuid7(arrival_ms), None)
            prepared.append(event)
            held[event.idempotency_key] = holds
        hot = await self._known_handles(batch.space_id, prepared)
        caller = batch.caller
        intake = Intake(
            intake_id=uuid7(),
            received_at=batch.received_at,
            source_id=caller.source_id,
            key_id=caller.key_id,
            scopes=caller.scopes,
            audience=caller.audience,
            ceiling=caller.verification_ceiling,
            events=tuple(prepared),
            rejected=len(batch.errors),
            duplicates=batch.duplicates,
            heartbeats=tuple((b.window_start, b.sent) for b in heartbeats),
            hot=hot,
        )
        body, version = await self._keys.encrypt(
            batch.space_id, orjson.dumps(intake.to_json()), intake_aad(intake.intake_id)
        )
        # The hot layer is written while the row commits, so a read that arrives with this batch finds the
        # turn; a batch that fails, and a turn that turns out to repeat an earlier batch, leave it again.
        early = [
            _provisional(e, caller.source_id, batch.received_at, hot[e.event_id])
            for e in prepared
            if e.event_id in hot
        ]
        writing = asyncio.create_task(self._write_hot(batch.space_id, early))
        # What the turns prove for their conversation is held before the row commits, so a read right after
        # the answer finds it, and the worker that stores the row always releases it after it was held.
        proofs = held_proofs(prepared, caller.verification_ceiling)
        try:
            await self._proofs.hold(batch.space_id, proofs)
            known = await self._intake.accept(
                NewIntake(
                    intake_id=intake.intake_id,
                    space_id=batch.space_id,
                    received_at=batch.received_at,
                    source_id=caller.source_id,
                    claims=tuple((e.idempotency_key, e.event_id) for e in prepared),
                    body_ct=body,
                    dek_version=version,
                )
            )
        except BaseException:
            await writing
            await self._retract_hot(batch.space_id, early)
            await self._proofs.release(batch.space_id, proofs)
            raise
        await writing
        stored = [e for e in prepared if e.idempotency_key not in known]
        repeated = {e.event_id for e in prepared if e.idempotency_key in known}
        await self._retract_hot(batch.space_id, [r for r in early if r.event_id in repeated])
        await self._proofs.release(batch.space_id, [p for p in proofs if p.event_id in repeated])
        batch.duplicates += len(repeated)
        batch.accepted = len(stored)
        for event in stored:
            batch.hold(held[event.idempotency_key])
        if self._workers is not None:
            self._hand_over(Task(TaskName.APPLY_INTAKE, batch.space_id, {"intake_id": str(intake.intake_id)}))
        else:
            await self._drain(batch.space_id)
        return _response(batch)

    async def _take_interactions(self, batch: _Batch, items: Sequence[tuple[int, InteractionItem]]) -> None:
        """Interaction items (24.3.2), in a space with the `signals` feature on: kept sealed in object storage
        and handed to their worker by one outbox row, the request's one statement (24.6). Duplicates and
        handles are the worker's."""
        taken: list[tuple[int, InteractionItem]] = []
        for index, item in items:
            try:
                if Feature.SIGNALS not in batch.config.features.enabled:
                    raise InvalidInput("interaction items need the signals feature on in the space")
                check_scope(batch.caller.scopes, item.type)
            except NiadraError as exc:
                batch.reject(index, exc)
                continue
            taken.append((index, item))
        if not taken:
            return
        space_id = batch.space_id
        key = interaction_intake_key(space_id, uuid7())
        dumped = [item.model_dump(mode="json", by_alias=True, exclude_none=True) for _, item in taken]
        body = intake_body(batch.caller.source_id, batch.received_at, dumped)
        sealed, version = await self._keys.encrypt(space_id, body, object_aad(key))
        await self._objects.put(key, pack_envelope(version, sealed))
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue([Task(TaskName.APPLY_INTERACTIONS, space_id, {"key": key})])
        batch.interactions = len(taken)

    async def apply_intake(self, task: Task) -> None:
        """A worker stores what the space has pending, in order. The answer's task, and the relay's copy of
        a row nobody stored within two seconds, both land here; while one of them stores the space's rows,
        the others of this process only ask it for one more pass."""
        space_id = task.space_id
        self._drain_again.add(space_id)
        if space_id in self._draining:
            return
        self._draining.add(space_id)
        try:
            while space_id in self._drain_again:
                self._drain_again.discard(space_id)
                await self._drain(space_id)
        finally:
            self._draining.discard(space_id)

    async def settle(self, space_id: UUID) -> None:
        """Stores every batch of the space acknowledged so far: an erasure or an export waits for this, so
        what was sent before it is erased or exported with the rest."""
        if self._intake is not None:
            await self._drain(space_id)

    async def close(self) -> None:
        """For shutdown: waits for the tasks handed to the workers. A task that never left is not lost: the
        relay publishes the row's copy in the outbox."""
        while self._handing:
            await asyncio.gather(*self._handing, return_exceptions=True)

    def _hand_over(self, task: Task) -> None:
        job = asyncio.create_task(self._publish(task))
        self._handing.add(job)
        job.add_done_callback(self._handing.discard)

    async def _publish(self, task: Task) -> None:
        assert self._workers is not None
        try:
            async with asyncio.timeout(HAND_OVER_TIMEOUT_S):
                await self._workers.publish([task])
        except Exception:  # the relay publishes the row's copy in the outbox after two seconds (5.5)
            log.info("handing an acknowledged batch to the workers deferred to the relay")
            if self._doorbell is not None:
                with contextlib.suppress(Exception):
                    await self._doorbell.ring(task.space_id, self._clock.now() + STRAGGLER_AGE)

    async def _drain(self, space_id: UUID) -> None:
        while await self._apply_next(space_id):
            pass

    async def _apply_next(self, space_id: UUID, limit: int = APPLY_GROUP) -> bool:
        """Stores the space's oldest pending batches, up to `limit`, in one transaction; False when none is
        left. When a group fails, its oldest batch goes alone, so only the one that fails counts it.

        While another worker stores the space's rows it holds the intake lock, and it stores ours too before
        it lets go: this one waits outside any transaction and asks again, instead of holding one of the
        space's two server connections blocked on the lock (28/09/2026). A lock busy for longer than the
        pauses leaves the rows to their holder, and to the relay's copy after two seconds."""
        rows: list[IntakeRow] = []
        for pause in (*INTAKE_BUSY_PAUSES_S, None):
            try:
                async with self._uow(space_id) as uow:
                    taken = await uow.ingest.try_take_intake(limit)
                    if taken is not None:
                        rows = taken
                        if not rows:
                            return False
                        applied = await self._apply_rows(uow, space_id, rows)
            except (NiadraError, ValueError, KeyError, TypeError) as exc:
                if not rows or isinstance(exc, Unavailable):
                    raise
                if len(rows) > 1:
                    return await self._apply_next(space_id, 1)
                await self._intake_failed(space_id, rows[0])
                raise
            if taken is not None:
                await self._after_apply(applied)
                return True
            if pause is None:
                return False
            await asyncio.sleep(pause)
        return False

    async def _intake_failed(self, space_id: UUID, row: IntakeRow) -> None:
        async with self._uow(space_id) as uow:
            attempts = await uow.ingest.fail_intake(row.intake_id)
            if attempts >= MAX_INTAKE_ATTEMPTS:
                # It would hold back every later batch of the space: set aside, sealed as it came, never
                # deleted, and said loudly to the company as a data issue it can retry.
                await uow.ingest.set_aside_intake(row.intake_id, self._clock.now())
                if self._issues is not None:
                    await self._issues.open(uow, space_id, "row_failed", field="acknowledged_batch")
        if attempts >= MAX_INTAKE_ATTEMPTS:
            log.error(
                "an acknowledged batch could not be stored and was kept aside for a retry",
                extra={"space_id": str(space_id), "intake_id": str(row.intake_id), "attempts": attempts},
            )

    async def failed(self, space_id: UUID) -> list[FailedIntake]:
        """The batches set aside after failing every attempt, oldest first, as the Console lists them."""
        async with self._uow(space_id) as uow:
            rows = await uow.ingest.failed_intake(MAX_FAILED_LISTED)
        return [
            FailedIntake(r.intake_id, "events", r.source_id, r.received_at, r.failed_at, r.attempts)
            for r in rows
            if r.source_id is not None and r.failed_at is not None
        ]

    async def kept(self, space_id: UUID) -> int:
        """How many batches are set aside, every one: a listing shows `MAX_FAILED_LISTED` at most."""
        async with self._uow(space_id) as uow:
            return await uow.ingest.count_failed_intake()

    async def retry(self, space_id: UUID, intake_ids: Sequence[UUID] | None) -> int:
        """Puts batches set aside back in the space's order, all of them when `intake_ids` is None, and has a
        worker store them; returns how many went back."""
        async with self._uow(space_id) as uow:
            put_back = await uow.ingest.retry_intake(intake_ids)
            if put_back:
                await uow.pipeline.enqueue([Task(TaskName.APPLY_INTAKE, space_id, {"retry": len(put_back)})])
        return len(put_back)

    async def _mentioned(self, space_id: UUID, events: Sequence[NewEvent]) -> list[NewEvent]:
        """Each event with what its text, fields and result name a person by, keyed-hashed
        (`domain.privacy.raw_index`): an erasure finds an event of someone else that names the target."""
        return [dataclasses.replace(e, mentions=await self._event_tokens(space_id, e.record)) for e in events]

    async def _event_tokens(self, space_id: UUID, record: EventRecord) -> frozenset[bytes]:
        return await sealed_tokens(self._keys, space_id, event_tokens(record))

    async def redact_named(
        self,
        space_id: UUID,
        scope: ErasureScope,
        plain: frozenset[str],
        tokens: frozenset[bytes],
        held: LegalHeld,
    ) -> Redacted:
        """The events of others whose text names the target keep, with the target's phones, e-mails,
        documents and ids replaced (`ERASED`), found by `event_mentions` a page at a time and rewritten as the
        system role, as the masking pass rewrites a held value; their turns in the hot layer are
        written again from the rewritten log. An event of a held handle or conversation keeps as it is. The
        target's own events go with the cascade. Repeating finds nothing left to redact. The batches set aside
        after a failed intake are read whole, as the fence reads them: others' events in them are redacted
        too (`_redact_failed`)."""
        await self._mention_stored(space_id)
        done = await self._redact_failed(space_id, scope, plain, held)
        after: UUID | None = None
        while True:
            async with self._uow.system(space_id) as uow:
                ids = await uow.ingest.events_naming(
                    sorted(tokens), besides=list(scope.handle_ids), after=after, limit=MENTION_PAGE
                )
                found = await uow.ingest.get_events(ids)
                free = [e for e in found if not await self._held_event(space_id, e, held)]
                redacted = [r for r in (redact_event(e, plain) for e in free) if r is not None]
                await uow.ingest.rewrite_content(redacted)
                live = {e.event_id for e in found}
                mentions = [(r.event_id, await self._event_tokens(space_id, r)) for r in redacted]
                # An id the log no longer holds was archived since: the archive's own index names it.
                await uow.ingest.index_mentions(
                    [*mentions, *((i, frozenset()) for i in ids if i not in live)]
                )
            done.rows += len(redacted)
            done.held += len(found) - len(free)
            done.handles |= {h for r in redacted for h in r.handle_ids}
            if redacted:
                await self._rewrite_hot(space_id, self._config.space(space_id), redacted)
            if len(ids) < MENTION_PAGE:
                return done
            after = ids[-1]

    async def _redact_failed(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], held: LegalHeld
    ) -> Redacted:
        """Others' events in the batches set aside that name the target, sealed again with the target redacted
        out; the target's own leave with the fence, and an event of a held handle or conversation keeps."""
        done = Redacted()
        if self._intake is None:
            return done
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.ingest.failed_intake(ERASE_PAGE, after=after)
                for row in rows:
                    body = await self._keys.decrypt(
                        space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version
                    )
                    intake = Intake.from_json(orjson.loads(body))
                    events: list[PreparedEvent] = []
                    for event in intake.events:
                        fixed = _redacted(event, plain)
                        if fixed is None or await _names_target(uow, intake, event, scope):
                            events.append(event)
                            continue
                        handles = intake.hot.get(event.event_id, ())
                        conversation = event.conversation_id
                        if any(h in held.handles for h in handles) or (
                            conversation
                            and held.conversations
                            and await self._keys.hmac(space_id, TURN_HMAC, conversation.encode())
                            in held.conversations
                        ):
                            done.held += 1
                            events.append(event)
                            continue
                        events.append(fixed)
                        done.rows += 1
                        done.handles |= set(handles)
                    if events != list(intake.events):
                        left = dataclasses.replace(intake, events=tuple(events))
                        sealed, version = await self._keys.encrypt(
                            space_id, orjson.dumps(left.to_json()), intake_aad(row.intake_id)
                        )
                        await uow.ingest.rewrite_intake(row.intake_id, sealed, version)
            if len(rows) < ERASE_PAGE:
                return done
            after = rows[-1].intake_id

    async def _held_event(self, space_id: UUID, record: EventRecord, held: LegalHeld) -> bool:
        """Under a legal hold: one of its handles is held, or its conversation is."""
        if any(h in held.handles for h in record.handle_ids):
            return True
        if not held.conversations or not record.conversation_id:
            return False
        return (
            await self._keys.hmac(space_id, TURN_HMAC, record.conversation_id.encode()) in held.conversations
        )

    async def _mention_stored(self, space_id: UUID) -> None:
        """Indexes, once, what the events stored before `event_mentions` existed name: read a page at a time
        as the masking pass reads them, by the first erasure of the space."""
        async with self._uow(space_id) as uow:
            if await uow.ingest.mentions_ready():
                return
        after: UUID | None = None
        while True:
            async with self._uow.system(space_id) as uow:
                page = await uow.ingest.events_after(after, LAST_EVENT_ID, MENTION_PAGE)
                mentions = [(r.event_id, await self._event_tokens(space_id, r)) for r, _ in page]
                await uow.ingest.index_mentions([(e, t) for e, t in mentions if t])
            if len(page) < MENTION_PAGE:
                break
            after = page[-1][0].event_id
        async with self._uow(space_id) as uow:
            await uow.ingest.mark_mentions_ready()

    async def fence(self, space_id: UUID, scope: ErasureScope) -> None:
        """Before an erasure's cascade (24.5.5), once `settle` stored what was acknowledged: the target's
        events leave the batches set aside, read while its handles still exist."""
        if self._intake is None:
            return
        await self._erase_failed(space_id, scope)

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None:
        """Once the cascade is done: a batch set aside meanwhile loses the target's events too."""
        if self._intake is None:
            return
        await self._erase_failed(space_id, scope)

    async def kept_for(self, space_id: UUID, scope: ErasureScope) -> list[dict[str, Any]]:
        """The subject's events in the batches set aside, unsealed for their access package (5.14), each as
        the export writes an event, marked `kept_aside` with its batch: the same events an erasure takes."""
        found: list[dict[str, Any]] = []
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.ingest.failed_intake(ERASE_PAGE, after=after)
                for row in rows:
                    body = await self._keys.decrypt(
                        space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version
                    )
                    intake = Intake.from_json(orjson.loads(body))
                    for event in intake.events:
                        if not await _names_target(uow, intake, event, scope):
                            continue
                        handles = tuple(intake.hot.get(event.event_id, ()))
                        record = _provisional(event, intake.source_id, intake.received_at, handles)
                        found.append(
                            {
                                **export_row(record),
                                "kept_aside": True,
                                "intake_id": row.intake_id,
                                "failed_at": row.failed_at,
                            }
                        )
            if len(rows) < ERASE_PAGE:
                return found
            after = rows[-1].intake_id

    async def _erase_failed(self, space_id: UUID, scope: ErasureScope) -> None:
        """Each batch set aside keeps its other events, sealed again; one left with none goes."""
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.ingest.failed_intake(ERASE_PAGE, after=after)
                for row in rows:
                    body = await self._keys.decrypt(
                        space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version
                    )
                    intake = Intake.from_json(orjson.loads(body))
                    kept = [e for e in intake.events if not await _names_target(uow, intake, e, scope)]
                    if intake.events and not kept:
                        await uow.ingest.drop_intake([row.intake_id])
                        continue
                    # The cursor stays on a row still there: one this pass deleted would end the read.
                    after = row.intake_id
                    if len(kept) == len(intake.events):
                        continue
                    left = dataclasses.replace(
                        intake,
                        events=tuple(kept),
                        hot={k: v for k, v in intake.hot.items() if k in {e.event_id for e in kept}},
                    )
                    sealed, version = await self._keys.encrypt(
                        space_id, orjson.dumps(left.to_json()), intake_aad(row.intake_id)
                    )
                    await uow.ingest.rewrite_intake(row.intake_id, sealed, version)
            if len(rows) < ERASE_PAGE:
                return

    async def _apply_pending(self, uow: UnitOfWork, space_id: UUID, *, wait: bool = True) -> list["_Applied"]:
        """Raises `_IntakeBusy`, having written nothing, when `wait` is False and a worker holds the lock."""
        if self._intake is None or not await uow.ingest.has_intake():
            return []
        applied: list[_Applied] = []
        taken = (
            await uow.ingest.take_intake(APPLY_GROUP)
            if wait
            else await uow.ingest.try_take_intake(APPLY_GROUP)
        )
        if taken is None:
            raise _IntakeBusy
        rows = taken
        while rows:
            applied += await self._apply_rows(uow, space_id, rows)
            rows = await uow.ingest.take_intake(APPLY_GROUP)
        return applied

    async def _apply_rows(
        self, uow: UnitOfWork, space_id: UUID, rows: Sequence[IntakeRow]
    ) -> list["_Applied"]:
        """What `_ingest` does with the database, for batches acknowledged earlier, in their order. What adds
        up across them is written once: their tasks, the hot layer's safety net, the sources' counters."""
        group = _Group()
        applied = [await self._apply_row(uow, space_id, row, group) for row in rows]
        await uow.ingest.insert_events(await self._mentioned(space_id, group.events))
        for touch in group.touches.values():
            await uow.ingest.upsert_conversation(space_id, touch)
        if group.tasks:
            await uow.pipeline.enqueue(group.tasks)
        if group.hot:
            net = Task(TaskName.HOT_REPAIR, space_id, {"event_ids": [str(i) for i in group.hot]})
            await uow.pipeline.schedule(net, group.latest + SAFETY_NET_DELAY, reason=DelayReason.SAFETY_NET)
        for (source_id, minute), count in group.counts.items():
            await uow.ingest.bump_heartbeat(
                space_id,
                source_id,
                minute,
                sent=count.sent,
                received=count.received,
                rejected=count.rejected,
                last_event_at=count.last_event_at,
            )
        await uow.ingest.drop_intake([r.intake_id for r in rows])
        return applied

    async def _apply_row(
        self, uow: UnitOfWork, space_id: UUID, row: IntakeRow, group: "_Group"
    ) -> "_Applied":
        body = await self._keys.decrypt(space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version)
        intake = Intake.from_json(orjson.loads(body))
        config = self._config.space(space_id)
        caller = SourcePrincipal(
            space=space_ref(config),
            source_id=intake.source_id,
            key_id=intake.key_id,
            scopes=intake.scopes,
            audience=intake.audience,
            verification_ceiling=intake.ceiling,
            purposes=frozenset(),
        )
        batch = _Batch(caller, config, intake.received_at, None, [])
        # The conversations the group found: the first batch's upsert makes the next one find the same.
        batch.conversations = group.conversations
        batch.duplicates = intake.duplicates + len(row.skip)
        conversations: dict[UUID, str | None] = {}
        stored: list[PreparedEvent] = []
        for event in intake.events:
            if event.idempotency_key in row.skip:
                continue
            await self._store_event(uow, batch, event)
            conversations[event.event_id] = event.conversation_id
            stored.append(event)
            batch.accepted += 1
        await self._persist(uow, batch, group)
        group.add(batch, intake)
        return _Applied(batch, intake.hot, conversations, held_proofs(stored, intake.ceiling))

    async def _after_apply(self, applied: Sequence["_Applied"]) -> None:
        """The hot layer of what the acknowledgement could not write, or wrote under a handle or a
        conversation the store resolved otherwise, the handles the store learned, and the proofs the
        acknowledgement held, which the sessions now hold."""
        for done in applied:
            batch = done.batch
            await self._proofs.release(batch.space_id, done.proofs)
            wrong: list[EventRecord] = []
            missing: list[EventRecord] = []
            for new in batch.events:
                record = new.record
                written = done.hot.get(record.event_id)
                if written is None:
                    missing.append(record)
                elif set(written) != set(
                    record.handle_ids
                ) or record.conversation_id != done.conversations.get(record.event_id):
                    wrong.append(dataclasses.replace(record, handle_ids=written))
                    missing.append(record)
            await self._retract_hot(batch.space_id, wrong)
            await self._write_hot(batch.space_id, missing)
            await self._remember_handles(batch)

    async def ingest_webhook(
        self, source_id: UUID, headers: Mapping[str, str], raw_body: bytes, query: Mapping[str, str]
    ) -> BatchResponse:
        config, mapping = self._mapping(source_id)
        source = config.source(source_id)
        if source is None:
            raise NotFound("unknown source")
        now = self._clock.now()
        try:
            await self._authenticate_webhook(config.space_id, mapping, headers, raw_body, query, now)
        except NiadraError:
            await self._count_source(config.space_id, source_id, now, rejected=1)
            raise
        try:
            payload = orjson.loads(raw_body)
        except orjson.JSONDecodeError:
            await self._store_unowned(config, raw_body, now)
            raise InvalidInput("the webhook body is not JSON") from None
        raw_items = map_payload(mapping, payload)
        if not raw_items:
            # Unmapped types stay only in cold storage and in the source's counters (5.2).
            await self._store_unowned(config, raw_body, now)
            await self._count_source(config.space_id, source_id, now, unmapped=1)
            return BatchResponse(accepted=0, duplicates=0, errors=[])
        items, errors = validate_items(raw_items)
        caller = source_principal(config, source, key_id=f"webhook:{source_id}", scopes=WEBHOOK_SCOPES)
        return await self._ingest(caller, items, errors, raw_body=raw_body)

    async def webhook_challenge(self, source_id: UUID, query: Mapping[str, str]) -> str:
        """Echoes the verification challenge some systems send by GET before delivering.

        Only a URL-token mapping answers: the token in the query is the one credential such a GET carries
        (Meta's verification request is unsigned; its signature header comes only with the POSTs). Under
        any other mode the GET could not be authenticated, so it looks like a source without a challenge."""
        config, mapping = self._mapping(source_id)
        param = mapping.verification_challenge
        if not param or param not in query or mapping.auth is None or mapping.auth.mode != "url_token":
            raise NotFound("no verification challenge for this source")
        webhook_auth.check_url_token(query, await self._webhook_secret(config.space_id, mapping.auth))
        return query[param]

    async def ingest_otlp(self, caller: SourcePrincipal, body: Mapping[str, Any]) -> BatchResponse:
        source = self._config.space(caller.space.space_id).source(caller.source_id)
        raw = otlp_items(
            body,
            default_channel=(source.channel if source else None) or "otel",
            user_scope=source.name if source else str(caller.source_id),
        )
        if not raw:
            return BatchResponse(accepted=0, duplicates=0, errors=[])
        items, errors = validate_items(raw)
        return await self.ingest_items(caller, items, errors)

    async def create_media_upload(
        self, caller: SourcePrincipal, request: MediaUploadIn
    ) -> MediaUploadResponse:
        """Media never travels inline: the event carries only the reference and the hash (5.2). With a
        subject, the object lives under the subject's handle, where `forget` deletes it by prefix."""
        space_id = caller.space.space_id
        handle_id: UUID | None = None
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            if request.subject is not None:
                subject = request.subject
                attached = await self._identity.attach(
                    uow,
                    [subject],
                    subject_kind=default_subject_kind(subject.type, subject.subject_kind),
                    method=AssertionMethod.SAME_EVENT,
                    origin_event_id=None,
                    level=Verification.V0,
                    at=now,
                )
                handle_id = attached.handle_ids[0]
            ref = media_ref(handle_id, uuid7())
            key = media_key(space_id, ref)
            assert key is not None
            # Indexed before the upload, so the `raw_content` retention finds it (5.14.0).
            row = await raw_object(self._keys, space_id, key, handle_id, now)
            await uow.privacy.index_raw(space_id, [row])
        upload = await self._objects.presign_put(
            key,
            request.content_type,
            MEDIA_URL_TTL_S,
            size_bytes=request.size_bytes,
            sha256_hex=request.sha256,
        )
        expires_at = now + timedelta(seconds=MEDIA_URL_TTL_S)
        return MediaUploadResponse(
            media_ref=ref, upload_url=upload.url, upload_headers=dict(upload.headers), expires_at=expires_at
        )

    async def record_feedback(
        self, caller: SourcePrincipal, request: FeedbackRequest, *, requested_by: str | None = None
    ) -> BatchResponse:
        """A correction is an event (L1): a `feedback.*` system event the memory module applies. One that
        names a fact or an open item the subject's memory does not hold is refused, never stored to be
        skipped."""
        unknown = await self._unknown_targets(caller, [request])
        if unknown:
            raise NotFound(unknown[0])
        return await self.ingest_items(caller, [(0, self._feedback_item(request, requested_by))])

    async def record_feedback_batch(
        self, caller: SourcePrincipal, requests: Sequence[FeedbackRequest]
    ) -> BatchResponse:
        """Many corrections as one batch of `feedback.*` events: per-item errors, keyed replays skipped."""
        unknown = await self._unknown_targets(caller, requests)
        refused = [ItemError(index=i, code="not_found", detail=detail) for i, detail in unknown.items()]
        items = [(i, self._feedback_item(r, None)) for i, r in enumerate(requests) if i not in unknown]
        return await self.ingest_items(caller, items, refused)

    async def _unknown_targets(
        self, caller: SourcePrincipal, requests: Sequence[FeedbackRequest]
    ) -> dict[int, str]:
        """By position, the corrections whose fact or open item is not the subject's (nor, for an
        organization, one its linked people noted): the memory module would skip them in silence."""
        named = [(i, r) for i, r in enumerate(requests) if r.fact_id or r.open_item_id]
        if not named:
            return {}
        unknown: dict[int, str] = {}
        async with self._uow(caller.space.space_id) as uow:
            for i, request in named:
                subject = request.subject
                try:
                    key = normalize_handle(
                        subject.type, subject.value, subject.scope, subject.subject_kind
                    ).key
                except (NiadraError, ValueError):
                    continue  # the item's own validation refuses the handle
                handle = await uow.identity.find_handle(key.type, key.scope, key.value)
                profile = await uow.identity.profile_of(handle.handle_id) if handle else None
                held = profile is not None and await holds_feedback_target(
                    uow, profile, fact_id=request.fact_id, open_item_id=request.open_item_id
                )
                if not held:
                    what = "fact" if request.fact_id else "open item"
                    unknown[i] = f"no {what} with this id for this subject"
        return unknown

    def _feedback_item(self, request: FeedbackRequest, requested_by: str | None) -> EventItem:
        fields = request.model_dump(
            mode="json", include={"action", "fact_id", "open_item_id", "value", "reason"}, exclude_none=True
        )
        if requested_by:
            fields["requested_by"] = requested_by
        return EventItem(
            kind=EventKind.SYSTEM_EVENT,
            idempotency_key=request.idempotency_key,
            channel="feedback",
            conversation_id=request.conversation_id,
            handles=[request.subject],
            speaker=SpeakerRef(role=Speaker.SYSTEM),
            canonical_type=f"{FEEDBACK_PREFIX}{request.action}",
            fields=fields,
            occurred_at=self._clock.now(),
        )

    async def record_correction(
        self, space: SpaceRef, actor: str, subject: Handle, request: CorrectionRequest, idempotency_key: str
    ) -> BatchResponse:
        """A person's correction: the same event as an agent's feedback, from the space's
        governance source, with who asked for it among its fields."""
        feedback = FeedbackRequest(
            idempotency_key=idempotency_key,
            subject=subject,
            action=request.action,
            fact_id=request.fact_id,
            open_item_id=request.open_item_id,
            value=request.value,
            reason=request.reason,
        )
        return await self.record_feedback(governance_principal(space, actor), feedback, requested_by=actor)

    async def record_restore(
        self,
        space: SpaceRef,
        actor: str,
        subject: Handle,
        fact_id: UUID,
        reason: str | None,
        idempotency_key: str,
    ) -> BatchResponse:
        """Undoing a retraction or a correction (WP-G): a `feedback.restore_fact` event from the space's
        governance source, which memory applies like a correction. `/v1/feedback` cannot send it: only the
        security role undoes what the company retracted."""
        fields = {"action": RESTORE_ACTION, "fact_id": str(fact_id), "requested_by": actor}
        if reason:
            fields["reason"] = reason
        item = EventItem(
            kind=EventKind.SYSTEM_EVENT,
            idempotency_key=idempotency_key,
            channel="feedback",
            handles=[subject],
            speaker=SpeakerRef(role=Speaker.SYSTEM),
            canonical_type=f"{FEEDBACK_PREFIX}{RESTORE_ACTION}",
            fields=fields,
            occurred_at=self._clock.now(),
        )
        return await self.ingest_items(governance_principal(space, actor), [(0, item)])

    async def upload_file(
        self, caller: SourcePrincipal, format: FileFormat, chunks: AsyncIterable[bytes]
    ) -> FileImport:
        """`POST /v1/ingest/files` (7.1): the body is cut into parts in object storage while it streams,
        then `import_file` ingests them. Nothing reaches the log before the whole file is stored."""
        if Scope.TRACK not in caller.scopes:
            raise ScopeMissing("the key lacks the scope track")
        space_id, import_id = caller.space.space_id, uuid7()
        splitter = RecordSplitter(format)
        digest = hashlib.sha256()
        part_rows: list[int] = []
        config = self._config.space(space_id)
        lang, types = language(config.settings.locale), config.masking_types()

        def staged(data: bytes) -> bytes:
            # JSONL items wait here up to two days: masked like the events they become (WP-A). A CSV only
            # seeds identity, and masking inside its quoted cells could break a row, so it waits as sent.
            return mask_lines(data, lang, types) if format is FileFormat.JSONL else data

        try:
            async for chunk in chunks:
                digest.update(chunk)
                for part in splitter.feed(chunk):
                    key = import_part_key(space_id, import_id, len(part_rows))
                    await self._objects.put(key, staged(part.data))
                    part_rows.append(part.records)
            for part in splitter.finish():
                await self._objects.put(
                    import_part_key(space_id, import_id, len(part_rows)), staged(part.data)
                )
                part_rows.append(part.records)
            if not part_rows:
                raise InvalidInput("the file has no records")
        except Exception:
            await self._objects.delete_prefix(import_prefix(space_id, import_id))
            raise
        record = FileImport(
            import_id=import_id,
            source_id=caller.source_id,
            key_id=caller.key_id,
            scopes=frozenset(s.value for s in caller.scopes),
            format=format,
            status=ImportStatus.QUEUED,
            size_bytes=splitter.size,
            sha256=digest.hexdigest(),
            part_rows=tuple(part_rows),
            created_at=self._clock.now(),
            header=splitter.header,
        )
        async with self._uow(space_id) as uow:
            await uow.ingest.create_import(space_id, record)
            await uow.pipeline.enqueue([Task(TaskName.IMPORT_FILE, space_id, {"import_id": str(import_id)})])
        return record

    async def file_import(self, caller: SourcePrincipal, import_id: UUID) -> FileImport:
        async with self._uow(caller.space.space_id) as uow:
            record = await uow.ingest.get_import(import_id)
        if record is None or record.source_id != caller.source_id:
            raise NotFound("unknown import")
        return record

    async def import_file(self, task: Task) -> None:
        """Ingests an uploaded file part by part, in batches of at most 500 rows (5.2). A retry resumes
        at the first row whose batch did not commit; row keys make even a replayed batch a duplicate."""
        space_id, import_id = task.space_id, UUID(task.payload["import_id"])
        async with self._uow(space_id) as uow:
            record = await uow.ingest.get_import(import_id)
        if record is None or record.status in (ImportStatus.COMPLETED, ImportStatus.FAILED):
            return
        config = self._config.space(space_id)
        source = config.source(record.source_id)
        if source is None:
            await self._finish_import(
                space_id, record, ImportStatus.FAILED, "the source is no longer configured"
            )
            return
        scopes = frozenset(Scope(s) for s in record.scopes)
        caller = source_principal(config, source, key_id=record.key_id, scopes=scopes)
        while record.next_part < len(record.part_rows):
            try:
                data = await self._objects.get(import_part_key(space_id, import_id, record.next_part))
            except NotFound:
                await self._finish_import(
                    space_id, record, ImportStatus.FAILED, "a part of the file is missing"
                )
                return
            record = await self._import_part(caller, record, data)
        await self._finish_import(space_id, record, ImportStatus.COMPLETED, None)

    async def coverage(self, space_id: UUID, days: int) -> CoverageOut:
        """Sent, received, rejected and unmapped per source and day, from the heartbeats (5.2)."""
        since = coverage_since(self._clock.now(), days)
        async with self._uow(space_id) as uow:
            found = await uow.ingest.coverage(since)
        config = self._config.space(space_id)
        configured = [s.source_id for s in config.sources]
        items = []
        for source_id in [*configured, *sorted(set(found) - set(configured))]:
            source = config.source(source_id)
            rows = found.get(source_id, [])
            items.append(
                SourceCoverage(
                    source_id=source_id,
                    name=source.name if source else None,
                    channel=source.channel if source else None,
                    sent=sum(r.sent for r in rows),
                    received=sum(r.received for r in rows),
                    rejected=sum(r.rejected for r in rows),
                    unmapped=sum(r.unmapped for r in rows),
                    last_event_at=max((r.last_event_at for r in rows if r.last_event_at), default=None),
                    days=[CoverageDay.of(r) for r in rows],
                )
            )
        return CoverageOut(since=since, items=items)

    async def events_arrived(self, space_id: UUID, days: int) -> EventsArrived:
        """What any Console role may know of the coverage: whether an event arrived at all (5.2)."""
        since = coverage_since(self._clock.now(), days)
        async with self._uow(space_id) as uow:
            arrived = await uow.ingest.received_since(since)
        return EventsArrived(arrived=arrived, since=since)

    async def dedupe_rotate(self, task: Task, min_age: timedelta = DEDUPE_MIN_AGE) -> int | None:
        """The space's rotation, every 30 days; a second run inside `min_age` is a no-op, which makes the
        task idempotent. The system role truncates the retired table."""
        async with self._uow.system(task.space_id) as uow:
            return await uow.ingest.rotate_dedupe(self._clock.now(), min_age)

    async def hot_repair(self, task: Task) -> None:
        ids = [UUID(i) for i in task.payload.get("event_ids", ())]
        async with self._uow(task.space_id) as uow:
            records = await uow.ingest.get_events(ids)
        await self._add_hot(task.space_id, records)
        await self._sessions.clear_degraded(HOT_LAYER, HOT_QUIET)

    async def hot_rebuild(self, task: Task) -> None:
        """After a restore, which purged the space's turns: the whole window comes back (5.4)."""
        started = self._clock.now()
        await self._sessions.mark_degraded(HOT_LAYER, "rebuild")
        await self._rebuild_hot(task.space_id)
        await self._sessions.clear_degraded(HOT_LAYER, self._clock.now() - started)

    async def watch_hot_layer(self) -> bool:
        """The state store keeps nothing across a restart, and says so by losing its marker (06, 3). Reads
        see the marker unsealed and declare `live` incomplete; this check, run by the cell's single loops
        process every few seconds, claims the marker, re-adds every space's turns of the hot window from
        the event log, and seals it. The cache epoch stays: the cache store kept its packs. True when it
        sealed the marker."""
        try:
            if await self._hot.state() is not HotLayerState.LOST:
                return False
        except ConnectionError:
            return False  # the store is down: counted and alerted, and reads already say `live` is incomplete
        token = uuid4().hex
        if not await self._hot.claim_rebuild(token, REBUILD_LEASE_MS):
            return False
        started = self._clock.now()
        log.warning("the state store lost the hot layer; rebuilding it from the event log")
        await self._sessions.mark_degraded(HOT_LAYER, "state store restarted")
        for config in self._config.spaces():
            try:
                if not await self._rebuild_hot(config.space_id, token):
                    log.warning("hot layer rebuild interrupted: the marker changed; starting over")
                    return False
            except (Unavailable, NotFound):
                continue  # not serving: being created (no turns yet), restored (it rebuilds) or deleted
        if not await self._hot.seal(token):
            return False
        await self._sessions.clear_degraded(HOT_LAYER, self._clock.now() - started)
        log.info("hot layer rebuilt", extra={"seconds": (self._clock.now() - started).total_seconds()})
        return True

    async def _rebuild_hot(self, space_id: UUID, token: str | None = None) -> bool:
        """Every turn of the space's hot window, in pages by event id. Turns an earlier compilation covers
        come back too: the reads skip them, and their key keeps the cap until the next compilation shortens
        it. With a rebuild `token`, False as soon as the marker is no longer that rebuild's."""
        since = self._clock.now() - timedelta(hours=self._config.space(space_id).settings.hot_layer_cap_hours)
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.ingest.recent_events(since, REBUILD_PAGE, after=after)
            await self._add_hot(space_id, page)
            if token is not None and not await self._hot.extend_rebuild(token, REBUILD_LEASE_MS):
                return False
            if len(page) < REBUILD_PAGE:
                return True
            after = page[-1].event_id

    def _check(self, caller: SourcePrincipal, item: Keyed, *, coordination: bool) -> None:
        match item:
            case EventItem():
                check_scope(caller.scopes, "event", item.kind)
                check_namespaces({ref.namespace for ref in item.object_refs}, coordination=coordination)
                if item.action is not None:
                    check_action(item.action.operation, caller.trusted_action_ops)
                capped_hint(item.verification_hint, caller.verification_ceiling, caller.audience)
                _uuid(item.corrects_event_id)
            case IdentifyItem():
                check_scope(caller.scopes, "identify")
                check_identify_method(item.method, caller.verification_ceiling)
            case VerifyItem():
                check_scope(caller.scopes, "verify")
                check_verify_level(item.level, caller.verification_ceiling)
                if not (item.conversation_id or item.task_id):
                    raise InvalidInput("verify needs conversation_id or task_id")
            case _:
                check_scope(caller.scopes, item.type)

    async def _dedupe(
        self, uow: UnitOfWork, batch: _Batch, keyed: Sequence[tuple[int, Keyed]]
    ) -> list[tuple[int, Keyed, UUID]]:
        """Dedupe by the provider's id only, never by content similarity (5.2)."""
        arrival_ms = int(batch.received_at.timestamp() * 1000)
        fresh: list[tuple[int, Keyed, UUID]] = []
        seen: set[str] = set()
        for index, item in keyed:
            if item.idempotency_key in seen:
                batch.duplicates += 1
                continue
            seen.add(item.idempotency_key)
            fresh.append((index, item, uuid7(arrival_ms)))
        claims = [(item.idempotency_key, event_id) for _, item, event_id in fresh]
        batch.generation, known = await uow.ingest.claim_keys(batch.space_id, claims, batch.received_at)
        batch.duplicates += sum(1 for _, item, _ in fresh if item.idempotency_key in known)
        return [f for f in fresh if f[1].idempotency_key not in known]

    async def _apply(self, uow: UnitOfWork, batch: _Batch, fresh: Sequence[tuple[int, Keyed, UUID]]) -> None:
        # Events first, so sessions exist before `verify` raises their level and `*.ended` closes them.
        later: list[tuple[int, Keyed, UUID]] = []
        for index, item, event_id in fresh:
            try:
                match item:
                    case EventItem():
                        await self._event(uow, batch, item, event_id)
                    case HandoffItem():
                        await self._handoff(uow, batch, item, event_id)
                        later.append((index, item, event_id))
                    case IdentifyItem():
                        await self._identify(uow, batch, item, event_id)
                    case _:
                        later.append((index, item, event_id))
                        continue
                batch.accepted += 1
            except NiadraError as exc:
                batch.reject(index, exc, item.idempotency_key)
        await self._store_raw(uow, batch)
        await self._persist(uow, batch)
        for index, item, event_id in later:
            try:
                await self._after_events(uow, batch, item, event_id)
                if not isinstance(item, HandoffItem):
                    batch.accepted += 1
            except NiadraError as exc:
                batch.reject(index, exc, item.idempotency_key)
        if batch.released:
            await uow.ingest.release_keys(batch.space_id, batch.generation, batch.released)
        await self._finish(uow, batch)

    async def _finish(self, uow: UnitOfWork, batch: _Batch) -> None:
        """The batch's tasks, and the hot layer's safety net two seconds ahead."""
        if batch.tasks:
            await uow.pipeline.enqueue(batch.tasks)
        hot = [e.record.event_id for e in batch.events if e.record.handle_ids]
        if hot:
            net = Task(TaskName.HOT_REPAIR, batch.space_id, {"event_ids": [str(i) for i in hot]})
            await uow.pipeline.schedule(
                net, batch.received_at + SAFETY_NET_DELAY, reason=DelayReason.SAFETY_NET
            )

    async def _event(self, uow: UnitOfWork, batch: _Batch, item: EventItem, event_id: UUID) -> None:
        item = await self._known_objects(uow, batch, item)
        conversation_id = await self._conversation(
            uow, batch, item.conversation_id, item.conversation_aliases
        )
        context = None
        text = _text_of(item)
        if text and conversation_id and needs_context(text, batch.masking):
            occurred_at = correct_occurred_at(item.occurred_at, batch.received_at)[0]
            context = await self._turn_before(uow, conversation_id, occurred_at, batch.events)
        prepared, held = await self._prepare(batch, item, event_id, context)
        batch.hold(held)
        await self._store_event(uow, batch, prepared)

    async def _known_objects(self, uow: UnitOfWork, batch: _Batch, item: EventItem) -> EventItem:
        """A customer-facing agent's action names only objects a system sent: stored, or sent by a system
        event earlier in the same batch. A ref whose id is an identifier the agent read on the object's line
        names the one object of its subject that holds it (`_written_refs`), and the item goes on with that
        object's own ref."""
        types = batch.config.object_types.types
        mirrored = {t.type: t.mirror_of.system for t in types if t.ownership != "agent" and t.mirror_of}
        refs = [ObjectKey(o.type, o.namespace, o.id) for o in item.object_refs]
        named = sent_by_systems(batch.caller.audience, item.kind, refs, mirrored.keys())
        if not named:
            return item
        sent = {
            ref
            for e in batch.events
            if e.record.kind is EventKind.SYSTEM_EVENT
            for ref in e.record.object_refs
        }
        known = sent | {o.key for o in await uow.objects.get_objects([r for r in named if r not in sent])}
        missing = [r for r in named if r not in known]
        found = await self._written_refs(uow, batch, item, missing) if missing else {}
        if found:
            item = item.model_copy(
                update={
                    "object_refs": [
                        ObjectRef(type=k.type, namespace=k.namespace, id=k.external_id)
                        for k in dict.fromkeys(found.get(r, r) for r in refs)
                    ]
                }
            )
            named = tuple(dict.fromkeys(found.get(r, r) for r in named))
            known |= set(found.values())
        check_known(named, known, mirrored)
        return item

    async def _written_refs(
        self, uow: UnitOfWork, batch: _Batch, item: EventItem, missing: Sequence[ObjectKey]
    ) -> dict[ObjectKey, ObjectKey]:
        """The stored object each of `missing` means when its id is an identifier one object of the action's
        subject (the person, the people of its profile and the organizations it acts for) holds, as a
        conversation writing it names that object (`app.object_memory.known_objects`): the attendant that
        acts on "legal_case:legaldesk:1012345-64.2026.8.26.0100", the case number it read on the case's line,
        means that case (juridico-sandbox, 06/10/2026). A value two of those objects hold names neither."""
        keys = [normalized(h, kind).key for kind, _, handles in _subject_groups(item) for h in handles]
        records = await uow.identity.find_handles(keys) if keys else []
        handle_ids = {r.handle_id for r in records}
        for record in records:
            profile = await uow.identity.profile_of(record.handle_id)
            if profile is not None:
                handle_ids |= set(profile.handle_ids)
        if not handle_ids:
            return {}
        handle_ids |= {link.org_handle_id for link in await uow.identity.active_links(sorted(handle_ids))}
        # Every object of the subject below the runaway guard: the 20 newest refused the sixth case an
        # attendant named by its number. Their state is sealed, so the subject's objects, read
        # by the index of who they involve, are the search; this runs only for a ref no stored object holds.
        objects = await uow.objects.objects_for(sorted(handle_ids, key=str), limit=OBJECTS_CEILING)
        known = known_objects(objects, batch.config)
        found: dict[ObjectKey, ObjectKey] = {}
        for ref in missing:
            said = written_value(ref.external_id)
            holders = [
                k.key
                for k in known
                if k.key.type == ref.type and any(written_value(v) == said for _, v in k.keys)
            ]
            if len(holders) == 1:
                found[ref] = holders[0]
        return found

    async def _prepare(
        self, batch: _Batch, item: EventItem, event_id: UUID, context: str | None
    ) -> tuple[PreparedEvent, list[Hold]]:
        """Everything the item and the caller decide alone. Held back before anything is stored: the
        event, its raw payload, the hot layer (WP-A)."""
        occurred_at, skewed = correct_occurred_at(item.occurred_at, batch.received_at)
        content = item.content
        text = _text_of(item)
        held: list[Hold] = []
        if text:
            masked = mask_text(text, batch.lang, batch.masking, context=context)
            text, held = masked.text, [*masked.held]
        fields, found = mask_fields(item.fields, batch.lang, batch.masking)
        action, from_action = _action_doc(item.action, batch.lang, batch.masking)
        held += [*found, *from_action]
        meta = _content_meta(item)
        if held:
            meta = {**(meta or {}), "masked": added(None, held)}
        closes = item.action.closes if item.action else None
        ceiling = batch.caller.verification_ceiling
        prepared = PreparedEvent(
            event_id=event_id,
            idempotency_key=item.idempotency_key,
            kind=item.kind,
            channel=item.channel,
            speaker=item.speaker.role,
            occurred_at=occurred_at,
            skew_corrected=skewed,
            groups=groups_of(_subject_groups(item)),
            speaker_id=item.speaker.id,
            direction=item.direction,
            conversation_id=item.conversation_id,
            aliases=tuple(item.conversation_aliases),
            task_id=item.task_id,
            text=text,
            fields=fields,
            action=action,
            canonical_type=item.canonical_type,
            object_refs=tuple(ObjectKey(o.type, o.namespace, o.id) for o in item.object_refs),
            visibility=item.visibility,
            stt_confidence=content.stt_confidence if content else None,
            corrects_event_id=_uuid(item.corrects_event_id),
            content_meta=meta,
            voice=await self._voice(batch.space_id, item.voice),
            closes_ref=closes.model_dump(mode="json", exclude_none=True) if closes else None,
            verification_hint=capped_hint(item.verification_hint, ceiling, batch.caller.audience),
            network=attestation_level(item.voice.network_attestation if item.voice else None),
            channel_proof=channel_proof(item.verification_hint, ceiling, item.speaker.role, item.direction),
            guards=tuple(item.backing.guard_violations) if item.backing else (),
        )
        return prepared, held

    async def _store_event(self, uow: UnitOfWork, batch: _Batch, p: PreparedEvent) -> None:
        conversation_id = await self._conversation(uow, batch, p.conversation_id, p.aliases)
        proven = await self._proven(uow, batch, conversation_id, p.task_id)
        attested = highest(p.network, p.channel_proof) if p.network or p.channel_proof else None
        subjects, handle_ids, profile_id = await self._attach_subjects(
            uow, batch, p.groups, p.event_id, highest(proven, attested)
        )
        record = EventRecord(
            event_id=p.event_id,
            kind=p.kind,
            source_id=batch.caller.source_id,
            channel=p.channel,
            speaker=p.speaker,
            occurred_at=p.occurred_at,
            received_at=batch.received_at,
            handle_ids=handle_ids,
            text=p.text,
            conversation_id=conversation_id,
            task_id=p.task_id,
            canonical_type=p.canonical_type,
            fields=p.fields,
            object_refs=p.object_refs,
            subjects=subjects,
            action=p.action,
            visibility=p.visibility,
            stt_confidence=p.stt_confidence,
            speaker_id=p.speaker_id,
            corrects_event_id=p.corrects_event_id,
            content_meta=p.content_meta,
        )
        if p.guards:
            await self._guard_violated(uow, batch, record, profile_id, p.guards)
        batch.events.append(
            NewEvent(
                space_id=batch.space_id,
                record=record,
                idempotency_key=p.idempotency_key,
                direction=p.direction,
                verification_hint=p.verification_hint,
                voice=p.voice,
                closes_ref=p.closes_ref,
                skew_corrected=p.skew_corrected,
            )
        )
        batch.profiles[p.event_id] = profile_id
        if attested is not None and attested.rank <= batch.caller.verification_ceiling.rank:
            # Network attestation of a call, or the channel's own plausibility, proves a level for the
            # session it belongs to (2.2); a challenge or a document still needs a `verify`.
            key = (conversation_id, p.task_id)
            batch.attested[key] = highest(batch.attested.get(key), attested)

    async def _guard_violated(
        self,
        uow: UnitOfWork,
        batch: _Batch,
        record: EventRecord,
        profile_id: UUID | None,
        guards: Sequence[str],
    ) -> None:
        """`guard.violated` (13, 6.1): the SDK found the agent's turn going against a guard line, so the
        supervisor hears of it now, not when the session is measured. Ids only: the turn, the
        conversation, the agent's source and the guards' short ids; never the values."""
        profile = await uow.identity.get_profile(profile_id) if profile_id is not None else None
        data = {
            "event_id": str(record.event_id),
            "conversation_id": external_reference(record.conversation_id),
            "task_id": external_reference(record.task_id),
            "source_id": str(record.source_id),
            "speaker": record.speaker.value,
            "guards": list(dict.fromkeys(guards)),
            "detected_by": "sdk",
            "at": record.occurred_at.isoformat(),
        }
        subject = WebhookSubject(profile.pseudonym, profile.kind.value) if profile is not None else None
        await enqueue_event(uow, batch.config, "guard.violated", subject, data)

    async def _handoff(self, uow: UnitOfWork, batch: _Batch, item: HandoffItem, event_id: UUID) -> None:
        """Stored as a system event; the measurement reads it for "transfer without reading" (19.2)."""
        conversation_id = await self._conversation(uow, batch, item.conversation_id, ())
        record = EventRecord(
            event_id=event_id,
            kind=EventKind.SYSTEM_EVENT,
            source_id=batch.caller.source_id,
            channel=batch.channel,
            speaker=Speaker.SYSTEM,
            occurred_at=correct_occurred_at(item.occurred_at, batch.received_at)[0],
            received_at=batch.received_at,
            handle_ids=(),
            conversation_id=conversation_id,
            canonical_type=HANDOFF,
            fields=mask_fields(
                item.model_dump(include={"target", "target_source", "reason", "mode"}, exclude_none=True),
                batch.lang,
                batch.masking,
            )[0],
        )
        batch.events.append(NewEvent(batch.space_id, record, item.idempotency_key))
        batch.profiles[event_id] = None

    async def _identify(self, uow: UnitOfWork, batch: _Batch, item: IdentifyItem, event_id: UUID) -> None:
        conversation_id = await self._conversation(uow, batch, item.conversation_id, ())
        level = highest(await self._proven(uow, batch, conversation_id, None))
        result = await self._attach(
            uow,
            batch,
            item.handles,
            item.subject_kind,
            item.method,
            event_id,
            level,
            merge_after_commit=batch.merge_now,
        )
        if result.merge is not None:
            batch.merges[result.merge.op_id] = result.merge

    async def _after_events(self, uow: UnitOfWork, batch: _Batch, item: Keyed, event_id: UUID) -> None:
        match item:
            case VerifyItem():
                # The proof's handle joins the space and raises its level; the conversation's subject comes
                # from its turns, never from the proof (`SessionService.record_verification`).
                await self._attach(
                    uow,
                    batch,
                    [item.handle],
                    default_subject_kind(item.handle.type, item.handle.subject_kind),
                    verify_method_assertion(item.method),
                    event_id,
                    item.level,
                )
                conversation_id = await self._conversation(uow, batch, item.conversation_id, ())
                await self._sessions.record_verification(
                    uow,
                    batch.space_id,
                    source_id=batch.caller.source_id,
                    channel=batch.channel,
                    conversation_id=conversation_id,
                    task_id=item.task_id,
                    level=item.level,
                    ceiling=batch.caller.verification_ceiling,
                    valid_until=item.valid_until,
                    at=batch.received_at,
                )
            case ConversationEndedItem():
                conversation_id = await self._conversation(uow, batch, item.conversation_id, ())
                await self._sessions.request_close(
                    uow,
                    batch.space_id,
                    conversation_id=conversation_id,
                    task_id=None,
                    reason=CloseReason.ENDED,
                    ended_at=correct_occurred_at(item.occurred_at, batch.received_at)[0],
                )
            case TaskEndedItem():
                await self._sessions.request_close(
                    uow,
                    batch.space_id,
                    conversation_id=None,
                    task_id=item.task_id,
                    reason=CloseReason.ENDED,
                    ended_at=correct_occurred_at(item.occurred_at, batch.received_at)[0],
                )
            case HandoffItem():
                conversation_id = await self._conversation(uow, batch, item.conversation_id, ())
                await self._sessions.request_close(
                    uow,
                    batch.space_id,
                    conversation_id=conversation_id,
                    task_id=None,
                    reason=CloseReason.HANDOFF,
                )

    async def _store_raw(self, uow: UnitOfWork, batch: _Batch) -> None:
        """The raw payload goes under the handle of its first event, before the commit: an event never
        points at a payload that failed to store (5.2). Events of other handles in the same payload
        carry the same key, so forgetting any of them deletes it."""
        if batch.raw_body is None or not (batch.events or batch.errors):
            return
        if batch.raw_only_for_systems and not any(
            e.record.kind is EventKind.SYSTEM_EVENT for e in batch.events
        ):
            return
        handle_id = next((h for e in batch.events for h in e.record.handle_ids), None)
        key = raw_key(batch.space_id, handle_id, batch.received_at, uuid7())
        body, _ = mask_payload(batch.raw_body, batch.lang, batch.masking, batch.cards)
        row = await raw_object(self._keys, batch.space_id, key, handle_id, batch.received_at, batch.raw_body)
        await uow.privacy.index_raw(batch.space_id, [row])
        await self._objects.put(key, body, "application/json")
        batch.raw_stored = key
        batch.events = [dataclasses.replace(e, raw_ref=key) for e in batch.events]

    async def _store_unowned(self, config: SpaceConfig, raw_body: bytes, at: datetime) -> None:
        """A payload that becomes no event is kept under no handle, indexed before it is written by what it
        names as it came, so an erasure finds it without reading the space's payloads (5.14.0)."""
        key = raw_key(config.space_id, None, at, uuid7())
        row = await raw_object(self._keys, config.space_id, key, None, at, raw_body)
        async with self._uow(config.space_id) as uow:
            await uow.privacy.index_raw(config.space_id, [row])
        await self._objects.put(key, _masked_body(config, raw_body), "application/json")

    async def _import_part(self, caller: SourcePrincipal, record: FileImport, data: bytes) -> FileImport:
        first = record.first_row(record.next_part)
        rows = [
            (row, rec)
            for row, rec in split_records(record.format, data, first)
            if row - first >= record.next_row
        ]
        if not rows:
            return await self._save_progress(caller.space.space_id, record, record.next_part + 1, 0)
        for start in range(0, len(rows), MAX_BATCH_ITEMS):
            chunk = rows[start : start + MAX_BATCH_ITEMS]
            if start + MAX_BATCH_ITEMS >= len(rows):
                record = await self._import_batch(caller, record, chunk, record.next_part + 1, 0)
            else:
                record = await self._import_batch(
                    caller, record, chunk, record.next_part, chunk[-1][0] - first + 1
                )
        return record

    async def _import_batch(
        self,
        caller: SourcePrincipal,
        record: FileImport,
        rows: Sequence[tuple[int, bytes]],
        next_part: int,
        next_row: int,
    ) -> FileImport:
        items, errors = self._file_items(record, rows)
        progressed: list[FileImport] = []

        async def save(uow: UnitOfWork, response: BatchResponse) -> None:
            updated = record.progressed(
                next_part=next_part,
                next_row=next_row,
                accepted=response.accepted,
                duplicates=response.duplicates,
                errors=[row_error(e.index, e.code, e.detail) for e in response.errors],
            )
            await uow.ingest.save_import(updated)
            progressed.append(updated)

        await self._ingest(caller, items, errors, before_commit=save)
        return progressed[-1]

    def _file_items(
        self, record: FileImport, rows: Sequence[tuple[int, bytes]]
    ) -> tuple[list[tuple[int, BatchItem]], list[ItemError]]:
        """Parsed and validated one by one; every error carries the file's row number as its index."""
        raw: list[tuple[int, Any]] = []
        errors: list[ItemError] = []
        for row, rec in rows:
            try:
                if record.format is FileFormat.CSV:
                    assert record.header is not None
                    item = csv_item(
                        record.header, rec, row=row, file_sha256=record.sha256, occurred_at=record.created_at
                    )
                else:
                    item = jsonl_item(rec)
            except NiadraError as exc:
                errors.append(ItemError(index=row, code=exc.code, detail=exc.detail or None))
                continue
            raw.append((row, item))
        valid, invalid = validate_items([item for _, item in raw])
        rows_of = [row for row, _ in raw]
        items = [(rows_of[index], item) for index, item in valid]
        errors += [e.model_copy(update={"index": rows_of[e.index]}) for e in invalid]
        return items, errors

    async def _save_progress(
        self, space_id: UUID, record: FileImport, next_part: int, next_row: int
    ) -> FileImport:
        updated = record.progressed(
            next_part=next_part, next_row=next_row, accepted=0, duplicates=0, errors=[]
        )
        async with self._uow(space_id) as uow:
            await uow.ingest.save_import(updated)
        return updated

    async def _finish_import(
        self, space_id: UUID, record: FileImport, status: ImportStatus, reason: str | None
    ) -> None:
        errors = record.errors if reason is None else (*record.errors, row_error(0, "import_failed", reason))
        done = dataclasses.replace(record, status=status, finished_at=self._clock.now(), errors=errors)
        async with self._uow(space_id) as uow:
            await uow.ingest.save_import(done)
            await self._announce_import(uow, space_id, done)
        if status is ImportStatus.COMPLETED:
            await self._objects.delete_prefix(import_prefix(space_id, record.import_id))

    async def _announce_import(self, uow: UnitOfWork, space_id: UUID, record: FileImport) -> None:
        """`import.completed` or `import.failed` (7.5), to the importing source's own endpoints: counts and
        error codes only, never a row. Another vendor's endpoint does not learn that this one imported."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return
        mine = [e.endpoint_id for e in config.webhook_endpoints if e.source_id == record.source_id]
        if not mine:
            return
        data = {
            "import_id": str(record.import_id),
            "source_id": str(record.source_id),
            "status": record.status.value,
            "format": record.format.value,
            "records": record.records,
            "accepted": record.accepted,
            "duplicates": record.duplicates,
            "rejected": record.rejected,
            "error_codes": sorted({str(e.get("code")) for e in record.errors if e.get("code")}),
            "created_at": record.created_at.isoformat(),
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        }
        links = {"status": f"/v1/ingest/files/{record.import_id}"}
        if record.status is ImportStatus.COMPLETED:
            await enqueue_event(uow, config, "import.completed", None, data, endpoint_ids=mine, links=links)
        else:
            await enqueue_event(uow, config, "import.failed", None, data, endpoint_ids=mine, links=links)

    async def media_text(self, task: Task) -> None:
        """The text of a file or an image, read inside the cell, as a derived turn (10, D.1). A task retried
        after a crash finds the mark on the original and stops. The whole document is read, a window of
        pages at a time, and stored whole: a text past `MAX_CHARS` is several derived turns in order, each
        its `part` of `parts`."""
        if self._media is None:
            return
        event_id = UUID(task.payload["event_id"])
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            found_events = await uow.ingest.get_events([event_id])
        original = found_events[0] if found_events else None
        if original is None or (original.content_meta or {}).get("media_text"):
            return
        ref = (original.content_meta or {}).get("media_ref")
        key = media_key(task.space_id, ref) if isinstance(ref, str) else None
        if key is None:
            return
        try:
            data = await self._objects.get(key)
        except NotFound:
            await self._mark_media(task.space_id, event_id, "missing")
            return
        mime = sniff(data)
        if (
            mime is None
            or len(data) > MEDIA_TEXT_MAX_BYTES
            or not media_allowed(mime, config.media.extract_text_from)
        ):
            await self._mark_media(task.space_id, event_id, "type_not_allowed")
            return
        read = await _read_whole(self._media, data, mime)
        if read is None:
            await self._mark_media(task.space_id, event_id, "ocr_unavailable")
            return
        source, whole = read
        text = trimmed(whole)
        if len(text) < MEDIA_TEXT_MIN_CHARS:
            await self._mark_media(task.space_id, event_id, "no_text")
            return
        if self._models is None:
            return
        pieces = parts(text)
        # Fail closed: without the detector nothing proves the scan is not an identity document. The
        # detectors read the lines joined, the text they always read; any part that is one refuses all.
        found = await self._models.pii([one_line(piece) for piece in pieces])
        if any(is_identity_document({s.kind for s in spans}) for spans in found):
            await self._mark_media(task.space_id, event_id, "identity_document")
            return
        derived: list[EventRecord] = []
        for n, piece in enumerate(pieces, start=1):
            # The text of a card's photo is text like any other: masked before the derived turn is stored,
            # with the rules reading its line breaks as spaces.
            masked = mask_text(
                piece, language(config.settings.locale), config.masking_types(), lines_as_spaces=True
            )
            meta: dict[str, Any] = {"extracted_from": source, "derived_from_event": str(event_id)}
            if len(pieces) > 1:
                meta |= {"part": n, "parts": len(pieces)}
            if masked.held:
                meta["masked"] = masked.counts
            derived.append(
                dataclasses.replace(
                    original,
                    event_id=uuid7(),
                    received_at=self._clock.now(),
                    text=masked.text,
                    content_meta=meta,
                    fields={},
                    action=None,
                )
            )
        new = [
            NewEvent(
                task.space_id, event, f"media-text:{event_id}" + (f":{n}" if n > 1 else ""), direction=None
            )
            for n, event in enumerate(derived, start=1)
        ]
        new = await self._mentioned(task.space_id, new)
        async with self._uow(task.space_id) as uow:
            await uow.ingest.insert_events(new)
        # The application role never changes a stored event: the mark goes as the system role.
        # A retry inserts the derived events once, by their idempotency keys, and merges the same mark.
        mark: dict[str, Any] = {"media_text": source, "media_text_event": str(derived[0].event_id)}
        if len(derived) > 1:
            mark["media_text_parts"] = len(derived)
        async with self._uow.system(task.space_id) as uow:
            await uow.ingest.mark_content([event_id], mark)

    async def _mark_media(self, space_id: UUID, event_id: UUID, outcome: str) -> None:
        async with self._uow.system(space_id) as uow:
            await uow.ingest.mark_content([event_id], {"media_text": outcome})

    async def watch_masking(self, space_id: UUID) -> int:
        """A per-space loop step (WP-A): the first time a space runs these rules, and whenever the rules
        or the space's types change, the events already stored are masked too, a bounded pass that starts
        where ingestion started masking. Events archived past the hot window are masked when read
        (`ArchiveReader`)."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        current = fingerprint(config.masking_types())
        async with self._uow(space_id) as uow:
            if await uow.ingest.masking_state() == current:
                return 0
            await uow.ingest.set_masking_state(current)
            until = self._clock.now() + MASK_ROLLOUT
            before = uuid7_floor(int(until.timestamp() * 1000))
            payload = {"fingerprint": current, "before": str(before), "until": until.isoformat(), "masked": 0}
            await uow.pipeline.enqueue([Task(TaskName.MASK_EVENTS, space_id, payload)])
        return 1

    async def mask_events(self, task: Task) -> None:
        """One page of the masking pass: stored events masked like new ones, their raw payloads too, and
        the hot layer of their handles written again. Idempotent: masked text masks to itself, so a retried
        or repeated page changes nothing."""
        space_id = task.space_id
        try:
            config = self._config.space(space_id)
        except NotFound:
            return
        types = config.masking_types()
        if int(task.payload["fingerprint"]) != fingerprint(types):
            return  # the rules changed again, and that change started its own pass
        lang = language(config.settings.locale)
        after = UUID(task.payload["after"]) if task.payload.get("after") else None
        before = UUID(task.payload["before"])
        total = int(task.payload.get("masked", 0))
        changed: list[EventRecord] = []
        payloads: dict[str, set[str]] = {}
        async with self._uow.system(space_id) as uow:
            page = await uow.ingest.events_after(after, before, MASK_PAGE)
            for record, raw_ref in page:
                masked, held = await self._mask_stored(uow, record, lang, types)
                if held:
                    changed.append(masked)
                if raw_ref:
                    payloads.setdefault(raw_ref, set()).update(h.digits for h in held if h.digits)
        # The payloads before the rows, and outside any transaction: the page moves on only once they are
        # masked, and a retried page masks them again, to the same bytes.
        for key, cards in payloads.items():
            await self._mask_payload(key, lang, types, cards)
        async with self._uow.system(space_id) as uow:
            # An event erased since the read is simply not there to rewrite; the log is otherwise
            # append-only, and only `content_meta.masked` is merged, never the rest of the metadata.
            await uow.ingest.rewrite_content(changed)
            total += len(changed)
            last = str(page[-1][0].event_id) if page else task.payload.get("after")
            following = {**task.payload, "after": last, "masked": total}
            until = task.payload.get("until")
            if len(page) == MASK_PAGE:
                pause = MASK_PAUSE
            elif until and self._clock.now() < datetime.fromisoformat(until):
                pause = MASK_TAIL_PAUSE  # the end of the log for now; rows may still come under the old rules
            else:
                pause = None
                log.info("masking pass done: %d stored events masked", total)
            if pause is not None:
                await uow.pipeline.schedule(
                    Task(TaskName.MASK_EVENTS, space_id, following),
                    self._clock.now() + pause,
                    DelayReason.DELAY,
                )
        await self._rewrite_hot(space_id, config, changed)

    async def _mask_stored(
        self, uow: UnitOfWork, record: EventRecord, lang: str, types: frozenset[Held]
    ) -> tuple[EventRecord, list[Hold]]:
        held: list[Hold] = []
        text = record.text
        if text:
            derived = is_derived(record.content_meta)
            context = None
            if record.conversation_id and needs_context(one_line(text) if derived else text, types):
                context = await self._turn_before(uow, record.conversation_id, record.occurred_at, (), record)
            masked = mask_text(text, lang, types, context=context, lines_as_spaces=derived)
            text, held = masked.text, [*masked.held]
        fields, found = mask_fields(record.fields, lang, types)
        held += found
        action = record.action
        if action is not None:
            action, from_action = _masked_action(action, lang, types)
            held += from_action
        if not held:
            return record, []
        meta = {
            **(record.content_meta or {}),
            "masked": added((record.content_meta or {}).get("masked"), held),
        }
        return dataclasses.replace(record, text=text, fields=fields, action=action, content_meta=meta), held

    async def _mask_payload(self, key: str, lang: str, types: frozenset[Held], cards: set[str]) -> None:
        try:
            body = await self._objects.get(key)
        except NotFound:
            return  # expired or erased
        masked, held = mask_payload(body, lang, types, cards)
        if held or masked != body:
            await self._objects.put(key, masked, "application/json")

    async def _rewrite_hot(self, space_id: UUID, config: SpaceConfig, changed: Sequence[EventRecord]) -> None:
        """The hot layer keeps each turn as it was written, so the handles of a masked turn still in its
        window are dropped and written again from the masked log."""
        since = self._clock.now() - timedelta(hours=config.settings.hot_layer_cap_hours)
        handles = sorted({h for r in changed if r.received_at >= since for h in r.handle_ids})
        if not handles:
            return
        try:
            await self._hot.purge(space_id, handles)
            async with self._uow(space_id) as uow:
                records = await uow.ingest.events_for_handles(handles, since, REBUILD_LIMIT)
            await self._add_hot(space_id, records)
        except Exception:  # reads fall back to the log and declare `live` incomplete meanwhile (5.4)
            log.warning("hot layer rewrite after masking failed; marking it degraded", exc_info=True)
            await self._sessions.mark_degraded(HOT_LAYER, "rewrite failed")

    async def _turn_before(
        self,
        uow: UnitOfWork,
        conversation_id: str,
        occurred_at: datetime,
        pending: Sequence[NewEvent],
        current: EventRecord | None = None,
    ) -> str | None:
        """The text of the conversation's turn just before `occurred_at`: this batch's own first, then the
        log's, within `MASK_CONTEXT`. Only looked up for a bare number that passes the card check."""
        for new in reversed(pending):
            if new.record.conversation_id == conversation_id and new.record.text:
                return new.record.text
        earlier = await uow.ingest.events_between(
            conversation_id=conversation_id, since=occurred_at - MASK_CONTEXT, until=occurred_at
        )
        for record in reversed(earlier):
            if current is not None and (record.occurred_at, record.event_id) >= (
                current.occurred_at,
                current.event_id,
            ):
                continue
            if record.text:
                return record.text
        return None

    async def _persist(self, uow: UnitOfWork, batch: _Batch, group: "_Group | None" = None) -> None:
        """With a `group`, the events and the conversations' upserts wait for the group's end: nothing a
        later batch of the group reads comes from them, and one upsert of the widened touches ends where one
        per batch would."""
        if not batch.events:
            return
        if group is None:
            await uow.ingest.insert_events(await self._mentioned(batch.space_id, batch.events))
        else:
            group.events += batch.events
        # Deduplication already kept a retried turn out of `batch.events`: its usage counts once (15.3).
        if usage := model_usage_rows(e.record for e in batch.events):
            await uow.metering.add_model_usage(usage)
        conversations: dict[str, ConversationTouch] = {}
        sessions: dict[tuple[str, str], list[EventRecord]] = defaultdict(list)
        for new in batch.events:
            record = new.record
            if record.conversation_id:
                touch = _conversation_touch(record, batch.conversations)
                current = conversations.get(record.conversation_id)
                conversations[record.conversation_id] = current.widen(touch) if current else touch
            if touches_session(record):
                kind = session_kind(task_id=record.task_id)
                sessions[(kind, record.task_id or record.conversation_id or "")].append(record)
            payload = {"event_id": str(record.event_id)}
            profile_id = batch.profiles[record.event_id]
            if batch.config.media.extract_text_from and carries_readable_media(record):
                batch.tasks.append(Task(TaskName.MEDIA_TEXT, batch.space_id, payload))
            if observes_ownership(batch.config, record):
                # A service panel's session, a ticket closed: who holds the subject, a handoff's outcome
                # (24.3.4).
                batch.tasks.append(Task(TaskName.OBSERVE_OWNERSHIP, batch.space_id, payload))
            if starts_object_work(record):
                batch.tasks.append(
                    Task(TaskName.APPLY_OBJECT_EVENT, batch.space_id, payload, profile_id=profile_id)
                )
            elif is_feedback(record) and profile_id is not None:
                # The memory module applies the correction under the subject's lease (7.1, L1).
                batch.tasks.append(Task(TaskName.RECONCILE, batch.space_id, payload, profile_id=profile_id))
        for touch in conversations.values():
            if group is None:
                await uow.ingest.upsert_conversation(batch.space_id, touch)
            else:
                before = group.touches.get(touch.conversation_id)
                group.touches[touch.conversation_id] = before.widen(touch) if before else touch
        opened: list[SessionRecord] = []
        for records in sessions.values():
            profile_id = next((p for r in records if (p := batch.profiles.get(r.event_id))), None)
            session, started = await self._sessions.touch(uow, batch.space_id, records, profile_id)
            if (
                started
                and session is not None
                and session.profile_id is not None
                and _is_live(session, batch)
            ):
                opened.append(session)
        for (conversation_id, task_id), level in batch.attested.items():
            if conversation_id or task_id:
                await self._sessions.record_verification(
                    uow,
                    batch.space_id,
                    source_id=batch.caller.source_id,
                    channel=batch.channel,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    level=level,
                    ceiling=batch.caller.verification_ceiling,
                    valid_until=None,
                    at=batch.received_at,
                )
        # After the channel's proof, so the warm-up sees the level the first read will have.
        batch.tasks.extend(warm_task(batch.space_id, session) for session in opened)

    async def _attach_subjects(
        self,
        uow: UnitOfWork,
        batch: _Batch,
        groups: Sequence[SubjectGroup],
        event_id: UUID,
        level: Verification,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[UUID, ...], UUID | None]:
        """One attach per subject: `same_event` only links handles of the same subject (5.3)."""
        subjects: list[dict[str, Any]] = []
        handle_ids: list[UUID] = []
        profile_id: UUID | None = None
        people: list[UUID] = []
        organizations: list[UUID] = []
        for kind, role, handles in groups:
            result = await self._attach(
                uow, batch, handles, kind, AssertionMethod.SAME_EVENT, event_id, level
            )
            subjects.append(
                {"kind": kind.value, "role": role, "handle_ids": [str(h) for h in result.handle_ids]}
            )
            handle_ids.extend(h for h in result.handle_ids if h not in handle_ids)
            profile_id = profile_id or result.profile_id
            if result.anchor_handle_id is not None:
                (organizations if kind.is_organization else people).append(result.anchor_handle_id)
        # A person and an organization named by one event are linked, never merged (21.2).
        for person in people:
            await self._identity.link_subjects(
                uow, person, organizations, origin_event_id=event_id, at=batch.received_at
            )
        return tuple(subjects), tuple(handle_ids), profile_id

    async def _attach(
        self,
        uow: UnitOfWork,
        batch: _Batch,
        handles: Sequence[Handle],
        kind: SubjectKind,
        method: AssertionMethod,
        event_id: UUID,
        level: Verification,
        *,
        merge_after_commit: bool = False,
    ) -> AttachResult:
        """Identity's attach, remembering each handle's id for the handle cache (the acknowledgement of the
        next batch that names it writes the hot layer at once). An attach identical to the one just before
        it in the batch is not repeated: attach is idempotent, so the second would change nothing and answer
        the same (`test_attach_is_idempotent`); a customer's turn and the agent's answer name one handle."""
        named = [normalized(h, kind) for h in handles]
        same = (kind, method, level, merge_after_commit, tuple((n.key, n.subject_kind) for n in named))
        if batch.last_attach is not None and batch.last_attach[0] == same:
            return batch.last_attach[1]
        result = await self._identity.attach(
            uow,
            handles,
            subject_kind=kind,
            method=method,
            origin_event_id=event_id,
            level=level,
            at=batch.received_at,
            merge_after_commit=merge_after_commit,
        )
        batch.last_attach = (same, result)
        keys = list(dict.fromkeys(n.key for n in named))
        if len(keys) == len(result.handle_ids):  # one id per distinct handle, in input order
            batch.learned.update(
                (k, (h, k in result.evidence)) for k, h in zip(keys, result.handle_ids, strict=True)
            )
        batch.learned.update((k, (h, True)) for k, h in result.evidence.items())
        return result

    async def _conversation(
        self, uow: UnitOfWork, batch: _Batch, conversation_id: str | None, aliases: Sequence[str]
    ) -> str | None:
        """A call has 2+ ids (platform and trunk); any of them finds the stored conversation (5.2)."""
        if not conversation_id:
            return None
        if conversation_id not in batch.conversations:
            found = await uow.ingest.find_conversation([conversation_id, *aliases])
            canonical = found or conversation_id
            for name in (conversation_id, *aliases):
                batch.conversations.setdefault(name, canonical)
        return batch.conversations[conversation_id]

    async def _proven(
        self, uow: UnitOfWork, batch: _Batch, conversation_id: str | None, task_id: str | None
    ) -> Verification | None:
        if not (conversation_id or task_id):
            return None
        key = (None, task_id) if task_id else (conversation_id, None)
        if key not in batch.proofs:
            batch.proofs[key] = await uow.sessions.proven_level(
                conversation_id=key[0], task_id=key[1], at=batch.received_at
            )
        return batch.proofs[key]

    async def _voice(self, space_id: UUID, voice: VoiceInfo | None) -> dict[str, Any] | None:
        """Caller and called numbers are personal data: only their keyed hashes stay in the clear."""
        if voice is None:
            return None
        doc = voice.model_dump(mode="json", exclude_none=True, exclude={"ani", "dnis"})
        for name in ("ani", "dnis"):
            if number := getattr(voice, name):
                doc[f"{name}_hmac"] = (await self._keys.hmac(space_id, "voice_number", number.encode())).hex()
        return doc

    async def _count(
        self, uow: UnitOfWork, batch: _Batch, heartbeats: Sequence[tuple[datetime, int]], rejected: int
    ) -> None:
        source_id = batch.caller.source_id
        await uow.ingest.bump_heartbeat(
            batch.space_id,
            source_id,
            _minute(batch.received_at),
            received=batch.accepted + batch.duplicates,
            rejected=rejected,
            last_event_at=batch.received_at if batch.events else None,
        )
        for window_start, sent in heartbeats:
            await uow.ingest.bump_heartbeat(batch.space_id, source_id, _minute(window_start), sent=sent)

    async def _count_source(
        self, space_id: UUID, source_id: UUID, at: datetime, *, rejected: int = 0, unmapped: int = 0
    ) -> None:
        async with self._uow(space_id) as uow:
            await uow.ingest.bump_heartbeat(
                space_id, source_id, _minute(at), rejected=rejected, unmapped=unmapped
            )

    async def _write_hot_after_commit(self, batch: _Batch) -> None:
        await self._write_hot(batch.space_id, [e.record for e in batch.events])

    async def _write_hot(self, space_id: UUID, records: Sequence[EventRecord]) -> None:
        try:
            await self._add_hot(space_id, records)
        except Exception:  # the safety net repairs it; reads declare `live` incomplete meanwhile (5.4)
            log.warning("hot layer write failed; marking it degraded", exc_info=True)
            try:
                await self._sessions.mark_degraded(HOT_LAYER, "write failed")
            except Exception:
                log.exception("could not mark the hot layer degraded")

    async def _retract_hot(self, space_id: UUID, records: Sequence[EventRecord]) -> None:
        try:
            for record in records:
                for handle_id in record.handle_ids:
                    await self._hot.retract(space_id, handle_id, record.event_id, occurred_ms(record))
        except Exception:  # reads declare `live` incomplete until the safety net or a rebuild (5.4)
            log.warning("hot layer retract failed; marking it degraded", exc_info=True)
            with contextlib.suppress(Exception):
                await self._sessions.mark_degraded(HOT_LAYER, "retract failed")

    async def _add_hot(self, space_id: UUID, records: Sequence[EventRecord]) -> None:
        cap_ms = self._config.space(space_id).settings.hot_layer_cap_hours * 3_600_000
        by_handle: dict[UUID, list[HotTurn]] = defaultdict(list)
        for record in records:
            if not record.handle_ids:
                continue
            ciphertext, version = await self._keys.encrypt(
                space_id, orjson.dumps(turn_document(record)), hot_aad(space_id, record.event_id)
            )
            turn = HotTurn(record.event_id, occurred_ms(record), pack_envelope(version, ciphertext))
            for handle_id in record.handle_ids:
                by_handle[handle_id].append(turn)
        for handle_id, turns in by_handle.items():
            await self._hot.add(space_id, handle_id, turns, cap_ms)

    async def _known_handles(
        self, space_id: UUID, events: Sequence[PreparedEvent]
    ) -> dict[UUID, tuple[UUID, ...]]:
        """Where each event's hot-layer turn goes before it is stored: under each handle's id when the cache
        knows it, or else under the id of a handle it derives that identity let stand for the subject (a
        first WhatsApp message goes under its phone, which a voice call reads). An event with a handle known
        neither way gets its turn once stored."""
        if self._handle_cache is None:
            return {}
        named = {
            event.event_id: [normalized(h, kind) for kind, _, handles in event.groups for h in handles]
            for event in events
        }
        wanted = list(
            dict.fromkeys(
                k for ns in named.values() for n in ns for k in (n.key, *(d.key for d in n.derived))
            )
        )
        if not wanted:
            return {}
        try:
            names = [await self._handle_cache_key(space_id, k) for k in wanted]
            found = await self._handle_cache.get_many(names)
        except Exception:  # a cache that does not answer only moves the hot write to the store
            log.warning("handle cache read failed", exc_info=True)
            return {}
        cached = {
            k: cached_handle(v) for k, v in zip(wanted, found, strict=True) if v is not None and len(v) == 17
        }
        plan: dict[UUID, tuple[UUID, ...]] = {}
        for event_id, handles in named.items():
            ids = [_placed(n, cached) for n in handles]
            if handles and all(ids):
                plan[event_id] = tuple(dict.fromkeys(i for i in ids if i is not None))
        return plan

    async def _remember_handles(self, batch: _Batch) -> None:
        if self._handle_cache is None or not batch.learned:
            return
        try:
            for key, (handle_id, evidence) in batch.learned.items():
                name = await self._handle_cache_key(batch.space_id, key)
                await self._handle_cache.set(
                    name, handle_id.bytes + (b"\x01" if evidence else b"\x00"), HANDLE_CACHE_TTL_S
                )
        except Exception:
            log.warning("handle cache write failed", exc_info=True)

    async def _handle_cache_key(self, space_id: UUID, key: HandleKey) -> str:
        digest = await self._keys.hmac(space_id, HANDLE_CACHE_PURPOSE, handle_cache_input(key))
        return handle_cache_name(space_id, digest)

    def _mapping(self, source_id: UUID) -> tuple[SpaceConfig, WebhookMapping]:
        for config in self._config.spaces():
            if (mapping := config.mapping_for(source_id)) is not None:
                return config, mapping
        raise NotFound("no mapping for this source")

    async def _webhook_secret(self, space_id: UUID, auth: WebhookAuth) -> str:
        # The vault is the cell's: a mapping never reads a secret of another space (4.7).
        if not owns_secret(space_id, auth.secret_ref):
            raise Forbidden("the mapping's secret_ref is not a secret of this space")
        return await self._secrets.get(auth.secret_ref)

    async def _authenticate_webhook(
        self,
        space_id: UUID,
        mapping: WebhookMapping,
        headers: Mapping[str, str],
        body: bytes,
        query: Mapping[str, str],
        now: datetime,
    ) -> None:
        auth = mapping.auth
        if auth is None:
            raise Forbidden("the source's mapping declares no authentication")
        secret = await self._webhook_secret(space_id, auth)
        lowered = {k.lower(): v for k, v in headers.items()}
        match auth.mode:
            case "mtls":
                # The secret is the fingerprint of the certificate the source registered.
                webhook_auth.check_mtls(lowered, secret)
            case "bearer":
                webhook_auth.check_bearer(lowered, auth.header, secret)
            case "url_token":
                webhook_auth.check_url_token(query, secret)
            case "hmac":
                webhook_auth.check_hmac(
                    headers=lowered,
                    body=body,
                    secret=secret,
                    header=auth.header,
                    algorithm=auth.algorithm,
                    signed_payload=auth.signed_payload,
                    timestamp_header=auth.timestamp_header,
                    tolerance_seconds=auth.tolerance_seconds,
                    now=now,
                )


@dataclasses.dataclass
class _Count:
    sent: int | None = None
    received: int = 0
    rejected: int = 0
    last_event_at: datetime | None = None


@dataclasses.dataclass
class _Group:
    """What stored intake rows add up to in one transaction. The source counters sum as their upsert would
    (`sent` keeps the largest), so one write per source and minute ends where one per batch would."""

    conversations: dict[str, str] = dataclasses.field(default_factory=dict)
    events: list[NewEvent] = dataclasses.field(default_factory=list)
    touches: dict[str, ConversationTouch] = dataclasses.field(default_factory=dict)
    tasks: list[Task] = dataclasses.field(default_factory=list)
    hot: list[UUID] = dataclasses.field(default_factory=list)
    latest: datetime = EPOCH
    counts: dict[tuple[UUID, datetime], _Count] = dataclasses.field(default_factory=dict)

    def add(self, batch: _Batch, intake: Intake) -> None:
        self.tasks += batch.tasks
        self.hot += [e.record.event_id for e in batch.events if e.record.handle_ids]
        self.latest = max(self.latest, batch.received_at)
        source = batch.caller.source_id
        count = self.counts.setdefault((source, _minute(batch.received_at)), _Count())
        count.received += batch.accepted + batch.duplicates
        count.rejected += intake.rejected
        if batch.events:
            count.last_event_at = max(filter(None, (count.last_event_at, batch.received_at)))
        for window_start, sent in intake.heartbeats:
            beat = self.counts.setdefault((source, _minute(window_start)), _Count())
            beat.sent = max(beat.sent or 0, sent)


@dataclasses.dataclass(frozen=True, slots=True)
class _Applied:
    """A stored intake row: its batch, the hot layer its acknowledgement wrote, each event's conversation
    as sent, which that write used, and the proofs it held (`app.pending_proofs`)."""

    batch: _Batch
    hot: Mapping[UUID, tuple[UUID, ...]]
    conversations: Mapping[UUID, str | None]
    proofs: Sequence[HeldProof] = ()


def _turns_only(batch: _Batch, keyed: Sequence[tuple[int, Keyed]]) -> list[tuple[int, EventItem]] | None:
    """The batch's items when all are conversation turns the intake log can take: messages whose storing can
    refuse nothing and needs nothing but themselves. None sends the batch through the full transaction: other
    item types, a stored raw payload, aliases, organizations (a link can be refused), guard lines, bad
    handles, or a bare number whose masking needs the turn before it in the log."""
    if batch.raw_body is not None and not batch.raw_only_for_systems:
        return None
    turns: list[tuple[int, EventItem]] = []
    for index, item in keyed:
        if not isinstance(item, EventItem) or item.kind is not EventKind.MESSAGE:
            return None
        if item.subjects or item.conversation_aliases or (item.backing and item.backing.guard_violations):
            return None
        for handle in item.handles:
            if default_subject_kind(handle.type, handle.subject_kind) is not SubjectKind.PERSON:
                return None
            try:
                normalized(handle, SubjectKind.PERSON)
            except NiadraError:
                return None
        text = _text_of(item)
        if text and item.conversation_id and needs_context(text, batch.masking):
            return None
        turns.append((index, item))
    return turns


def _placed(handle: NormalizedHandle, cached: Mapping[HandleKey, tuple[UUID, bool]]) -> UUID | None:
    if handle.key in cached:
        return cached[handle.key][0]
    for derived in handle.derived:
        found = cached.get(derived.key)
        if found is not None and found[1]:
            return found[0]
    return None


async def _names_target(uow: UnitOfWork, intake: Intake, event: PreparedEvent, scope: ErasureScope) -> bool:
    """Whether an erasure takes this event of a batch set aside: one of the target's conversation for a
    conversation, otherwise one that names any of the target's handles, as the acknowledgement resolved them
    (`hot`) or as identity resolves its handles now."""
    if scope.kind is TargetKind.CONVERSATION:
        names = {event.conversation_id, *event.aliases}
        return scope.conversation_id is not None and scope.conversation_id in names
    targets = set(scope.handle_ids)
    if targets & set(intake.hot.get(event.event_id, ())):
        return True
    keys = [normalized(h, kind).key for kind, _, handles in event.groups for h in handles]
    if not keys:
        return False
    found = [*await uow.identity.find_handles(keys), *await uow.identity.retired_handles(keys)]
    return bool(targets & {r.handle_id for r in found})


def _redacted(event: PreparedEvent, plain: frozenset[str]) -> PreparedEvent | None:
    """The event with the erased person redacted out of its text, fields and the action's result, as
    `redact_event` does for a stored one; None when it names them nowhere."""
    text, in_text = redact_value(event.text, plain) if event.text else (event.text, 0)
    fields, in_fields = redact_value(dict(event.fields), plain) if event.fields else (event.fields, 0)
    action, in_result = event.action, 0
    if action is not None and action.get("result"):
        result, in_result = redact_value(action["result"], plain)
        action = {**action, "result": result}
    if not in_text + in_fields + in_result:
        return None
    return dataclasses.replace(event, text=text, fields=fields, action=action)


def normalized(handle: Handle, kind: SubjectKind) -> NormalizedHandle:
    """As identity normalizes a handle of an attach of `kind`."""
    return normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind, default_kind=kind)


def _provisional(
    event: PreparedEvent, source_id: UUID, received_at: datetime, handles: tuple[UUID, ...]
) -> EventRecord:
    """The event as its hot-layer turn reads it before it is stored: the conversation as sent."""
    return EventRecord(
        event_id=event.event_id,
        kind=event.kind,
        source_id=source_id,
        channel=event.channel,
        speaker=event.speaker,
        occurred_at=event.occurred_at,
        received_at=received_at,
        handle_ids=handles,
        text=event.text,
        conversation_id=event.conversation_id,
        task_id=event.task_id,
        canonical_type=event.canonical_type,
        fields=event.fields,
        object_refs=event.object_refs,
        action=event.action,
        visibility=event.visibility,
        speaker_id=event.speaker_id,
        content_meta=event.content_meta,
    )


def _response(batch: _Batch) -> BatchResponse:
    return BatchResponse(
        accepted=batch.accepted + batch.interactions,
        duplicates=batch.duplicates,
        errors=sorted(batch.errors, key=lambda e: e.index),
        masked=dict(sorted(batch.masked.items())),
    )


def _is_live(session: SessionRecord, batch: _Batch) -> bool:
    """A conversation whose latest turn is this recent may be read any moment; an import of past ones
    will not, and warming each of them would only load the compiler."""
    return batch.received_at - session.last_event_at <= WARM_WITHIN


def _subject_groups(item: EventItem) -> list[tuple[SubjectKind, str | None, list[Handle]]]:
    """Explicit subjects, plus loose handles grouped by kind; person subjects first, so the event's
    profile is the person's when there is one (5.2)."""
    groups: list[tuple[SubjectKind, str | None, list[Handle]]] = [
        (s.kind, s.role, list(s.handles)) for s in item.subjects
    ]
    loose: dict[SubjectKind, list[Handle]] = defaultdict(list)
    for handle in item.handles:
        loose[default_subject_kind(handle.type, handle.subject_kind)].append(handle)
    groups.extend((kind, None, handles) for kind, handles in loose.items())
    return sorted(groups, key=lambda g: g[0] is not SubjectKind.PERSON)


def _conversation_touch(record: EventRecord, resolved: Mapping[str, str]) -> ConversationTouch:
    assert record.conversation_id is not None
    aliases = frozenset(name for name, canonical in resolved.items() if canonical == record.conversation_id)
    return ConversationTouch(
        conversation_id=record.conversation_id,
        channel=record.channel,
        first_event_at=record.occurred_at,
        last_event_at=record.occurred_at,
        aliases=aliases - {record.conversation_id},
        primary_handle=record.handle_ids[0] if record.handle_ids else None,
        dirty=marks_dirty(record),
    )


def _text_of(item: EventItem) -> str | None:
    content = item.content
    return (content.text or content.transcript) if content else None


def carries_readable_media(record: EventRecord) -> bool:
    meta = record.content_meta or {}
    return meta.get("type") in ("file", "image") and isinstance(meta.get("media_ref"), str)


def _content_meta(item: EventItem) -> dict[str, Any] | None:
    content = item.content
    meta: dict[str, Any] = {}
    if content is not None:
        meta = content.model_dump(mode="json", exclude_none=True, exclude={"text", "transcript"})
        if content.transcript and not content.text:
            meta["transcribed"] = True
    if item.context_stamp is not None:
        meta["context_stamp"] = item.context_stamp.model_dump(mode="json", exclude_none=True)
    if item.usage is not None:
        meta["model_usage"] = item.usage.model_dump(mode="json")
    if item.backing is not None:
        # Kinds, counts and short ids only (13, 6.2): the values stay in the text, masked and sealed.
        meta["backing"] = item.backing.model_dump(mode="json")
    if item.valid_until is not None:
        meta["valid_until"] = item.valid_until.isoformat()
    return meta or None


def _action_doc(
    action: ActionInfo | None, lang: str, types: frozenset[Held]
) -> tuple[dict[str, Any] | None, list[Hold]]:
    if action is None:
        return None, []
    return _masked_action(action.model_dump(mode="json", exclude_none=True), lang, types)


def _masked_action(
    action: dict[str, Any], lang: str, types: frozenset[Held]
) -> tuple[dict[str, Any], list[Hold]]:
    """An action's free text, its result and its purpose; the operation and the ids it closes stay."""
    doc = dict(action)
    held: list[Hold] = []
    for key in ("result", "purpose"):
        if isinstance(doc.get(key), str):
            masked = mask_text(doc[key], lang, types)
            doc[key] = masked.text
            held += masked.held
    return doc, held


def _masked_body(config: SpaceConfig, body: bytes) -> bytes:
    return mask_payload(body, language(config.settings.locale), config.masking_types())[0]


def _uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise InvalidInput("corrects_event_id is not a UUID") from None


def _minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


def observes_ownership(config: SpaceConfig, record: EventRecord) -> bool:
    """A system event the space's `coordination` document maps: to who holds a subject, or to a handoff's
    outcome."""
    if record.kind is not EventKind.SYSTEM_EVENT or record.canonical_type is None:
        return False
    if Feature.COORDINATION not in config.features.enabled:
        return False
    coordination = config.coordination
    return (
        coordination.ownership.observation(record.source_id, record.canonical_type) is not None
        or coordination.handoffs.report(record.source_id, record.canonical_type) is not None
    )


async def _read_whole(reader: MediaTextReader, data: bytes, mime: str) -> tuple[str, str] | None:
    """The reader's source and the document's whole text, read a window of pages at a time; None when this
    cell has no reader for the type. One window read by OCR makes the document `pdf_ocr`."""
    texts: list[str] = []
    sources: list[str] = []
    first: int | None = 0
    while first is not None:
        found = await reader.document_text(data, mime, first)
        if found is None:
            return None
        texts.append(found.text)
        sources.append(found.source)
        first = found.next_page if found.next_page is not None and found.next_page > first else None
    return ("pdf_ocr" if "pdf_ocr" in sources else sources[0]), "\n".join(texts)
