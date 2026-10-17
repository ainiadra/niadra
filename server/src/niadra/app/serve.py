"""The read path (5.11): `context()`, the history navigation (16) and the subject tokens of MCP (5.17).

`context()` walks the tiers T0 (pin or cache) -> T1 (stored pack) -> T2 (compile on demand) ->
T3 (hot layer only) -> T4 (empty, degraded), and says which one answered. Every pack it serves
passes the same policy function again (read-time recheck), the privacy feed, and leaves a receipt
before the response; every `live` turn passes the same policy function as the pack's items. The read
process never writes to PostgreSQL except the `seed_gen` request.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from niadra.app.compile import (
    PACK_TTL_S,
    T0_TTL_S,
    CompileService,
    learned_for,
    open_pack,
    reader_variant,
    seal_pack,
    trim_for,
)
from niadra.app.pending_proofs import PendingProofs
from niadra.app.prefetch import PrefetchQueue, PreparedTurn, PreparedTurns
from niadra.app.prewarm import PREWARM_DEADLINE_S, Prewarmer
from niadra.app.read_state import ReadState
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.app.serve_history import HistoryNavigator
from niadra.app.serve_reads import RECEIPT_TIMEOUT_S, UNASSIGNED, ReadSupport, Target
from niadra.config.models import SpaceConfig
from niadra.contracts.common import Handle, ObjectRef, SourceCoverage
from niadra.contracts.context import (
    CacheDirectives,
    ContextPack,
    ContextRequest,
    ContextResponse,
    LiveTurn,
    OpenedItem,
    PackGuard,
    PackSection,
    PackSlot,
    PackStamp,
    PrefetchRequest,
    SearchRequest,
    SearchResponse,
    SlotChannelRank,
    SlotWhy,
    TargetModel,
    TimelineRequest,
    TimelineResponse,
    ToolDefinition,
    VerificationResult,
)
from niadra.contracts.serve import SubjectTokenRequest, SubjectTokenResponse
from niadra.domain.agent_memory.model import AgentNoteRecord
from niadra.domain.agent_memory.rules import TASK_VIEW_TOKENS, Block, may_read, render_block, still_valid
from niadra.domain.agent_memory.rules import Reader as AgentReader
from niadra.domain.common.errors import (
    AboutWithoutLink,
    Forbidden,
    NiadraError,
    NotFound,
    Unavailable,
    VerificationNotAllowed,
)
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.compile import (
    COMPILER_VERSION,
    CompiledPack,
    Variant,
    item_key,
    pack_key,
    profile_target,
    render_delta,
    says_contact_time,
    untrimmed_key,
    variant_reader,
)
from niadra.domain.compile.model import Layer, Section, canonical_json, public_item_id, slot_budget
from niadra.domain.compile.templates import Formatter, pack_year, unlabeled
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.compile.trim import whole_kind
from niadra.domain.compile.views import SLOTS, learned_slot_budget, view_spec
from niadra.domain.identity.provenance import compiled_for, provisional_reader, reader_view
from niadra.domain.ingest.hot import hot_aad, unpack_envelope
from niadra.domain.measure.experiments import (
    BLOCK_OF_ELEMENT,
    CONSTRAINT_PARTS,
    CONTROL,
    Arm,
    agent_control,
    arms_text,
    held_sections,
    withheld_elements,
)
from niadra.domain.measure.trim import ContextTrim
from niadra.domain.measure.views import ViewBudgets
from niadra.domain.measure.weights import RetrievalWeights, weights_for
from niadra.domain.memory.bound import BoundRow, ObjectMemory
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.policy import (
    Decision,
    Effect,
    EffectiveVerification,
    ItemAttrs,
    PolicyContext,
    PolicySpec,
    Reader,
    effective_verification,
    evaluate,
)
from niadra.domain.policy.audience import claimed_kind, in_conversation, is_task
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.records import ProfileRecord
from niadra.domain.serve.cache_keys import (
    CacheEpoch,
    account_key,
    gen_key,
    latest_mark_key,
    pin_key,
    seed_key,
    t0_key,
    wanted_key,
    watermark_key,
)
from niadra.domain.serve.guards import CONTROL_ARM, GUARD_KIND, Guard, guards_for, short_id, wanted
from niadra.domain.serve.guards import WINDOW_DAYS as GUARD_WINDOW_DAYS
from niadra.domain.serve.guards import arm as guard_arm
from niadra.domain.serve.holdout import Assignment
from niadra.domain.serve.intent import gate
from niadra.domain.serve.lexicon import Lexicon
from niadra.domain.serve.live import LiveLine, screen_live
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.serve.records import PIN_TTL, decode_mark, encode_mark
from niadra.domain.serve.retrieval import ChannelStatus, ProfileEntry
from niadra.domain.serve.search import ranking
from niadra.domain.serve.slots import (
    NOTHING_UNSEEN,
    PER_CHANNEL,
    VALUES_CHANNEL,
    ChannelHits,
    Derived,
    Slot,
    SlotItem,
    SlotPlan,
    UnseenValues,
    asks_value,
    max_slots,
    plan_slots,
    run_channels,
    slot_budget_rule,
    standard_channels,
    unseen_values,
    value_hits,
)
from niadra.domain.serve.subject_token import TOKEN_TTL, SubjectClaims, SubjectRef, SubjectTokens
from niadra.domain.serve.temporal import temporal_channel
from niadra.domain.serve.turn import QuestionKind, TurnAnalysis, analyze
from niadra.domain.sessions.rules import HOT_LAYER
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    Audience,
    DeliveryPath,
    EventKind,
    ExclusionReason,
    Feature,
    ReceiptKind,
    ReceiptSurface,
    Scope,
    Sensitivity,
    SpokenBy,
    SubjectKind,
    Verification,
    Visibility,
)
from niadra.ports.infra import (
    Clock,
    ConfigProvider,
    HotLayer,
    KeyValueCache,
    KeyVault,
    ModelsClient,
    NoTelemetry,
    ReceiptSink,
    RetrievalTelemetry,
    SpaceDatabases,
)
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

PIN_TTL_S = int(PIN_TTL.total_seconds())
HEALTH_TTL = timedelta(seconds=1)
SEED_THROTTLE_S = 30
ACCOUNT_THROTTLE_S = 10
"""How often a person's `about` packs may be asked to compile again by reads that found them behind."""
MAX_WANTED = 32
LIVE_TURNS = 50
"""At most this many recent turns of other conversations per read, the newest. Every turn is decrypted and
screened on the read path: a number with hundreds of turns in the hot layer (a busy test number) made each
read's CPU starve the event loop, and every connection checkout waited behind it."""
LIVE_READ = 2 * LIVE_TURNS
"""Turns read per handle: room for the caller's own conversation, which is left out after decryption."""
TRIMS_TTL = timedelta(seconds=60)
AGENT_NOTES_TTL = timedelta(seconds=60)
"""How long a process reuses a space's active agent notes for task views (10, C.3): a note written now
reaches the task views within a minute, and the read path reads them at most once a minute."""
"""The trims change at most once per measured session; a read keeps a space's list for a minute."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)
DEFAULT_RETRIEVAL_BYTES = 128 << 20

VALUES_DEADLINE_S = 0.05
MAX_TURN_KEYS = 10
"""Numbers of one turn the values channel looks up, in one statement: 2,084 benchmark turns write at most
one, and a dense e-mail naming three cases, two amounts and a protocol writes six. Past it a
number is said as unknown, never as "no record"."""
"""A settled-values read on a turn whose customer is not in the retrieval cache yet."""
_VALUE_TYPES = frozenset(t.value for t in ValueType)
INCLUDE_FEATURES: dict[str, Feature] = {
    "state": Feature.STATE,
    "constraints": Feature.SIGNALS,
    "coordination": Feature.COORDINATION,
    "budget": Feature.TURNS,
}
"""The feature each block of a read's `include` needs on in the space (24.3.1, 24.2.10)."""


class SlotSource(Protocol):
    """A slot channel that needs I/O (11, 3.3): the settled values, with items of their own, or the
    semantic channel, which encodes the turn. It runs beside the rule channels within `deadline_s`; past
    it, or failing, the slots go out without it and the manifest marks the channel `skipped`."""

    @property
    def name(self) -> str: ...

    @property
    def deadline_s(self) -> float: ...

    async def hits(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        turn: TurnAnalysis,
        pool: Sequence[SlotItem],
        *,
        query: str,
        fmt: Formatter,
        voice: bool,
    ) -> ChannelHits:
        """Lines the channel brings are rendered with `fmt`, as sentences when `voice`."""
        ...


@dataclass(frozen=True, slots=True)
class PackCost:
    """What the served pack costs, and what this source's measurement of context use cuts from its view:
    what the budget block shows."""

    total: int
    sections: Mapping[str, int]
    cut: tuple[str, ...] = ()
    applied: bool = False
    window_days: int = 0
    min_deliveries: int = 0


@dataclass(frozen=True, slots=True)
class BlockGate:
    """What the pack of the same read lets its reader see, for the blocks read beside it: each item of a
    block passes the pack's own policy by its own attributes, as the pack's line of it would. An object, a
    shared object the subject showed interest in, a constraint or an attribute goes by the type it is of
    or names (the type is its category, so a sensitive one waits for its level and purpose), and never
    names a field its type holds private. What the pack withholds reaches no block, and nothing else is
    held back for it (L8: the same function decides)."""

    policy: PolicySpec
    reader: Reader
    context: PolicyContext
    private: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """Per type, the fields no text for a model carries (`ObjectTypeRegistry.private_fields`)."""

    def admits_about(self, name: str, target: Target, handle_ids: tuple[UUID, ...] = ()) -> bool:
        """An item of a block about `name`, an object type or an attribute's own name, of the subject."""
        attrs = ItemAttrs(
            kind="object",
            category=name,
            subject_kind=target.profile.kind,
            handle_ids=handle_ids,
            profile_id=target.profile.profile_id,
        )
        return evaluate(self.policy, self.reader, attrs, self.context).effect is Effect.ALLOW

    def admits_field(self, path: str, target: Target) -> bool:
        """An entry that names `type.field` (a constraint, a preference, an attribute) or a type alone."""
        name, _, field_name = path.partition(".")
        return field_name not in self.private.get(name, frozenset()) and self.admits_about(name, target)


@dataclass(frozen=True, slots=True)
class BlockAsk:
    conversation_id: str | None
    task_id: str | None
    view: str
    for_: str = "self"
    """Whom the block is for: the subject, one of their beneficiaries, or a gift (its own route only)."""
    withheld: frozenset[str] = frozenset()
    """The elements an experiment holds out of this read (`soft`, `size`): the control arm's (6.4)."""
    purpose: str = "display"
    """What the read is for (`display`, `claim`, `decide`): the state block's own route names it (24.3.13)."""
    pack: PackCost | None = None
    """Set when the read serves a pack and names `budget`."""
    gate: BlockGate | None = None
    """Set when the read serves a pack: the blocks pass what it passes."""
    memory: tuple[ObjectMemory, ...] = ()
    """What the served pack's reader may read of each object's memory: the instructions of
    the objects in focus enter the constraints block, and the state block's lines carry it."""
    accounts: tuple[tuple[UUID, BoundRow], ...] = ()
    """The instructions about the organizations the read is about, naming no object: in the constraints
    block, as an object's in focus."""
    carried: frozenset[str] = frozenset()
    """The item ids of the served pack's lines: an instruction it already says is in the block's entries
    for a program, never said again in the block's text."""
    hours_said: bool = False
    """The served pack says the subject's contact time as a contact preference: the constraints block keeps
    the windows in its entries, and its text does not say them again."""


@dataclass(frozen=True, slots=True)
class BlockRead:
    """A block's answer: `value` goes in the answer's field named after the block, and the receipt of the
    block's surface keeps `item_ids` and `detail` (24.5.4)."""

    value: Any
    item_ids: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)


class ContextBlock(Protocol):
    """A block a read adds with `include` (24.2.10): read beside the pack, from the cache stores and the
    process's memory, never from the database on a warm read. `target` is None for a first contact."""

    @property
    def name(self) -> str: ...

    @property
    def feature(self) -> Feature: ...

    @property
    def surface(self) -> ReceiptSurface: ...

    async def read(self, caller: SourcePrincipal, target: Target | None, ask: BlockAsk) -> BlockRead: ...


@dataclass(frozen=True, slots=True)
class _Ask:
    view: str = "chat"
    verification: Verification = Verification.V0
    conversation_id: str | None = None
    task_id: str | None = None
    query: str | None = None
    delta: bool = False
    target: TargetModel | None = None
    known_etag: str | None = None
    about: Handle | None = None
    format: str = "text"
    explain: bool = False
    """The typed slots say why each was chosen; nothing else of the read changes (12, WP-E)."""
    include: tuple[str, ...] = ()

    @property
    def thread(self) -> str | None:
        return self.conversation_id or self.task_id

    def block(
        self,
        withheld: frozenset[str] = frozenset(),
        pack: PackCost | None = None,
        gate: BlockGate | None = None,
        memory: tuple[ObjectMemory, ...] = (),
        accounts: tuple[tuple[UUID, BoundRow], ...] = (),
        carried: frozenset[str] = frozenset(),
        hours_said: bool = False,
    ) -> BlockAsk:
        return BlockAsk(
            self.conversation_id,
            self.task_id,
            self.view,
            withheld=withheld,
            pack=pack,
            gate=gate,
            memory=memory,
            accounts=accounts,
            carried=carried,
            hours_said=hours_said,
        )


@dataclass(frozen=True, slots=True)
class _TurnSlots:
    turn: TurnAnalysis
    plan: SlotPlan
    text: str | None
    analysis: dict[str, Any]
    weights_version: int | None = None
    """The space's learned fusion weights this turn used (11, 3.4); None for the defaults."""
    inputs: tuple[Any, ...] = ()
    """What the slots were computed from, for a prefetch's read to compare (11, 3.5)."""
    reusable: bool = False
    """Every channel answered, and nothing in the turn depends on the moment it is read."""
    prefetched: bool = False
    gated: bool = False
    """The intent gate (13, 6.4): a closed-form turn, served the pinned skeleton alone."""


@dataclass(frozen=True, slots=True)
class _Timed:
    """A slot item as the temporal channel reads it."""

    item_id: str
    at: datetime
    text: str
    object_id: UUID | None


@dataclass
class _Timing:
    started: float = field(default_factory=time.perf_counter)
    stages: dict[str, float] = field(default_factory=dict)
    begun: float = field(default_factory=time.perf_counter)

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self.stages[stage] = round((now - self.started) * 1000, 3)
        self.started = now

    def server_ms(self) -> dict[str, str]:
        """The read's server time so far, for the receipt drafted at its end: each read auditable by its
        own receipt. What is left after the draft (sending it) is under a millisecond."""
        return {"server_ms": f"{(time.perf_counter() - self.begun) * 1000:.1f}"}


@dataclass(frozen=True, slots=True)
class Answer:
    """A read's answer and the confirmation of its receipt, which may still be pending: the answer can be
    rendered meanwhile, and never leaves before `delivered()` returns (5.11)."""

    response: ContextResponse
    receipt: "asyncio.Task[None] | None" = None
    warm: bool = False
    """Resolved from the read state and served from the cache (T0): such a read runs no database statement,
    unlike the first read of a conversation, which resolves its customer on the database."""

    async def delivered(self) -> ContextResponse:
        if self.receipt is not None:
            await self.receipt
        return self.response


def _unlinked(answer: Answer, resolved: "_Resolved") -> Answer:
    """The answer says `about` was left out because the subject has no active link to it."""
    if not resolved.about_unlinked:
        return answer
    return replace(answer, response=answer.response.model_copy(update={"about_unlinked": True}))


@dataclass(frozen=True, slots=True)
class _SpaceReads:
    at: datetime
    trims: list[ContextTrim]
    weights: RetrievalWeights | None
    views: ViewBudgets | None
    guarded: frozenset[str]


_NO_SPACE_READS = _SpaceReads(datetime.min, [], None, None, frozenset())


@dataclass(frozen=True, slots=True)
class _Resolved:
    target: Target | None
    level: EffectiveVerification
    space: _SpaceReads
    assignment: Assignment | None
    coverage: list[SourceCoverage]
    arms: tuple[Arm, ...] = ()
    about_unlinked: bool = False
    """`about` named an organization with no active link to the subject: the read goes on without it."""


@dataclass(frozen=True, slots=True)
class _Where:
    """Who a read names: a handle, a profile id, or a business object (with the customer an MCP
    connection is bound to, 5.17)."""

    subject: Handle | None = None
    profile_id: UUID | None = None
    object: ObjectRef | None = None
    bound: Handle | None = None

    def cached(self, reads: ReadSupport, space_id: UUID) -> Target | None:
        if self.subject is not None:
            return reads.cached_subject(space_id, self.subject)
        if self.profile_id is not None:
            return reads.cached_profile(space_id, self.profile_id)
        return None  # a read by object always asks the database

    async def locate(self, reads: ReadSupport, uow: UnitOfWork) -> Target | None:
        if self.subject is not None:
            return reads.cached_subject(uow.space_id, self.subject) or await reads.resolve_subject(
                uow, self.subject
            )
        if self.profile_id is not None:
            return reads.cached_profile(uow.space_id, self.profile_id) or await reads.resolve_profile(
                uow, self.profile_id
            )
        assert self.object is not None
        found = await reads.resolve_object(uow, self.object)
        if self.bound is None:
            return found
        owner = await reads.resolve_subject(uow, self.bound)
        if found is None or owner is None or found.profile.profile_id != owner.profile.profile_id:
            raise NotFound("unknown object")
        # The bound handle stands in as the subject, so `about` can check the person's link (21.4), and
        # the read sees what a read through that handle sees (5.3).
        return replace(found, profile=owner.profile, handle=owner.handle)


@dataclass(frozen=True, slots=True)
class _Served:
    pack: CompiledPack | None
    path: DeliveryPath
    latest: CompiledPack | None = None
    degraded: bool = False
    pin_held: bool = False
    """The pack is the conversation's pin as the cache holds it: the read renews its expiry only."""


@dataclass(frozen=True, slots=True)
class _Live:
    turns: list[tuple[UUID, LiveTurn]] = field(default_factory=list)
    complete: bool = False
    withheld: int = 0
    unverified: int = 0
    """The turns withheld until identity is verified further: what a verification would bring back."""


@dataclass(frozen=True, slots=True)
class LiveScreen:
    """Every recent turn with its policy decision, and the turn as the reader gets it (None if withheld)."""

    lines: list[tuple[LiveLine, Decision, LiveTurn | None]] = field(default_factory=list)
    complete: bool = False


class ServeService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cache: KeyValueCache,
        hot: HotLayer,
        keys: KeyVault,
        receipts: ReceiptSink,
        compile: CompileService,
        models: ModelsClient,
        clock: Clock,
        config: ConfigProvider,
        producer_id: str,
        *,
        tokens: SubjectTokens | None = None,
        epoch: CacheEpoch | None = None,
        guard: PrivacyGuard | None = None,
        buffer: ReceiptBuffer | None = None,
        databases: SpaceDatabases | None = None,
        receipt_timeout_s: float = RECEIPT_TIMEOUT_S,
        retrieval: RetrievalService | None = None,
        slot_sources: Sequence[SlotSource] = (),
        telemetry: RetrievalTelemetry | None = None,
        read_state: ReadState | None = None,
    ) -> None:
        self._uow = uow
        self._cache = cache
        self._hot = hot
        self._keys = keys
        self._compile = compile
        self._clock = clock
        self._config = config
        self._tokens = tokens
        self._epoch = epoch or CacheEpoch()
        self._reads = ReadSupport(
            uow,
            receipts,
            clock,
            producer_id,
            guard=guard,
            buffer=buffer,
            databases=databases,
            receipt_timeout_s=receipt_timeout_s,
            state=read_state,
            pending=PendingProofs(keys, hot),
        )
        self.read_state = self._reads.state
        self._history = HistoryNavigator(uow, keys, models, clock, config, self._reads, self._generation)
        self.retrieval = retrieval or RetrievalService(
            uow, config, clock, RetrievalCache(DEFAULT_RETRIEVAL_BYTES)
        )
        self._hot_health: tuple[datetime, bool] | None = None
        self._health_lock = asyncio.Lock()
        self._space: dict[UUID, _SpaceReads] = {}
        self._agent_notes: dict[UUID, tuple[datetime, list[AgentNoteRecord]]] = {}
        self._slot_sources = tuple(slot_sources)
        self._blocks: dict[str, ContextBlock] = {}
        self._prepared = PreparedTurns()
        self.prefetches = PrefetchQueue(self._prepare)
        # Predictive prewarm of the voice context (13, 6.4): off in every space until it sets a cap.
        self.prewarm = Prewarmer(
            cache,
            self.retrieval.cache,
            config,
            clock,
            self._prewarm_load,
            self._generations,
            telemetry or NoTelemetry(),
        )

    def add_block(self, block: ContextBlock) -> None:
        """A block `include` may name, wired by the module that owns it (`entrypoints.cell`)."""
        if INCLUDE_FEATURES.get(block.name) is not block.feature or block.name in self._blocks:
            raise ValueError(f"the block {block.name} is not an `include` name, or is added twice")
        self._blocks[block.name] = block

    async def read_block(self, caller: SourcePrincipal, name: str, subject: Handle, ask: BlockAsk) -> Any:
        """A block of `include` on its own route (`POST /v1/constraints`): the target a context read would
        find, from memory on a warm read, and the block's receipt before the answer."""
        self._reads.require(caller, Scope.CONTEXT)
        block = self._blocks[name]
        space_id = caller.space.space_id
        await self._reads.follow_feeds(space_id)
        await self._reads.ensure_can_record()
        config = self._config.space(space_id)
        where = _Where(subject)
        target = where.cached(self._reads, space_id)
        assigned = self._reads.cached_assigned(caller, config, target) if target is not None else UNASSIGNED
        if target is None or assigned is None:
            async with self._uow(space_id) as uow:
                target = target or await where.locate(self._reads, uow)
                assigned = (
                    await self._reads.assigned(uow, caller, config, target)
                    if target is not None
                    else UNASSIGNED
                )
        # A person the space's experiments hold out of this block reads it as a first contact would (6.4).
        withheld = withheld_elements(assigned.arms)
        memory = assigned.memory
        held = (memory is not None and memory.holdout) or agent_control(assigned.arms) is not None
        held = held or any(BLOCK_OF_ELEMENT.get(e) == name for e in withheld)
        read = await block.read(
            caller, None if held else target, replace(ask, withheld=withheld & CONSTRAINT_PARTS)
        )
        draft = self._reads.draft(
            caller,
            ReceiptKind.READ,
            block.surface,
            item_ids=read.item_ids,
            detail=read.detail,
            profile_id=target.profile.profile_id if target else None,
            conversation_id=ask.conversation_id,
            task_id=ask.task_id,
            experiment_group=memory.label if memory else None,
            extra={"arms": arms_text(assigned.arms)} if assigned.arms else {},
        )
        await self._reads.emit([draft])
        return read.value

    # context()

    async def context(
        self, caller: SourcePrincipal, request: ContextRequest, *, bound: Handle | None = None
    ) -> ContextResponse:
        """`bound` is the customer an MCP connection's subject token names (5.17): a read by object must
        land on that customer, so the model cannot switch customers by naming someone else's object."""
        return await (await self.context_answer(caller, request, bound=bound)).delivered()

    async def context_answer(
        self, caller: SourcePrincipal, request: ContextRequest, *, bound: Handle | None = None
    ) -> "Answer":
        """`context()`, with the receipt's confirmation still pending: the HTTP adapter renders the answer
        meanwhile and sends it only once `delivered()` returns."""
        ask = _Ask(
            request.view,
            request.verification,
            request.conversation_id,
            request.task_id,
            request.query,
            request.delta,
            request.target,
            request.known_etag,
            request.about,
            request.format,
            explain=request.explain,
            include=tuple(request.include),
        )
        if request.profile_id is not None:
            if bound is not None:
                raise NotFound("unknown profile")  # a connection bound to a customer reads only that one
            return await self._context(caller, ask, _Where(profile_id=request.profile_id))
        return await self._context(caller, ask, _Where(request.subject, None, request.object, bound))

    async def context_answer_for_profile(
        self,
        caller: SourcePrincipal,
        profile_id: UUID,
        *,
        view: str = "chat",
        verification: Verification = Verification.V0,
        conversation_id: str | None = None,
        task_id: str | None = None,
        known_etag: str | None = None,
        format: str = "text",
    ) -> "Answer":
        """`GET /v1/context?profile_id=`: the same read, addressed by the pseudonymous profile id, with the
        receipt's confirmation still pending, as `context_answer`."""
        ask = _Ask(view, verification, conversation_id, task_id, known_etag=known_etag, format=format)
        return await self._context(caller, ask, _Where(profile_id=profile_id))

    async def _context(self, caller: SourcePrincipal, ask: _Ask, where: "_Where") -> "Answer":
        self._reads.require(caller, Scope.CONTEXT)
        caller, ask = _in_conversation(caller, ask, where)
        timing = _Timing()
        space_id = caller.space.space_id
        config = await self._check_include(ask, self._config.space(space_id))
        view_spec(ask.view, config.views)
        if ask.view == "full" and caller.audience is Audience.CUSTOMER_AGENT:
            # Everything the policy allows, in 4,000 tokens, is for internal tools, never for an agent
            # that talks to the customer (5.10).
            raise Forbidden("the full view is for internal sources")
        await self._reads.follow_feeds(space_id)
        await self._reads.ensure_can_record()
        timing.mark("privacy")
        resolved = self._resolved_in_memory(caller, ask, where, config)
        in_memory = resolved is not None
        if resolved is None:
            resolved = await self._resolve(caller, ask, where, config)
        timing.mark("resolve")
        target, level, assignment, coverage, arms = (
            resolved.target,
            resolved.level,
            resolved.assignment,
            resolved.coverage,
            resolved.arms,
        )
        space = resolved.space
        trims, weights, guarded, views = space.trims, space.weights, space.guarded, space.views
        if target is None:
            return _unlinked(Answer(await self._unknown(caller, ask, level, timing)), resolved)
        variant = self._variant(caller, ask, level.effective, target, config, trims, views, arms)
        shadow: CompiledPack | None = None
        held_out = agent_control(arms)
        if held_out is not None and assignment is None:
            # The agent's own holdout (6.4): this agent reads nothing for the person, as the memory holdout's
            # control group reads nothing for anyone.
            assignment = Assignment(held_out.experiment, CONTROL)
        if assignment is not None and assignment.holdout:
            shadow = await self._on_demand(space_id, target, variant)
            if not _protected(shadow):
                held = await self._holdout(caller, ask, target, level, assignment, shadow, timing, arms)
                return _unlinked(Answer(held), resolved)
        served = (
            _Served(shadow, DeliveryPath.T2, shadow)
            if shadow is not None
            else await self._pack(caller, ask, target, variant, config)
        )
        if _protected(served.pack):
            # An overdue company promise takes the customer out of both groups (19.7): the read is
            # neither a control observation nor a free one.
            assignment, arms = None, ()
        timing.mark("pack")
        if served.latest is not None and variant.slotted:
            if ask.thread:
                # The first read of a conversation warms the customer's retrieval cache for the turns after
                # it. A read outside a conversation has no next turn: its load would only hold one of the
                # space's few connections while other reads wait for it.
                self.retrieval.warm(space_id, target.profile, served.latest.generation)
            if caller.audience is Audience.CUSTOMER_AGENT:
                self._consider_prewarm(caller, config, target, variant, served.latest)
        live = _Live()
        if served.path is not DeliveryPath.T4:
            live = await self._live(caller, ask, target, variant, served.pack, config)
        timing.mark("live")
        delta = await self._delta(caller, ask, target, served, config) if ask.delta else None
        slots = await self._turn_slots(
            caller, ask, target, variant, served, live, config, weights, guarded=guarded, views=views
        )
        if slots is not None:
            timing.mark("slots")
        gate = self._gate(caller, variant, config) if ask.include else None
        answer = await self._respond(
            caller,
            ask,
            target,
            level,
            assignment,
            served,
            live,
            delta,
            coverage,
            timing,
            config,
            slots,
            arms,
            gate,
        )
        return _unlinked(replace(answer, warm=in_memory and served.path is DeliveryPath.T0), resolved)

    def _gate(self, caller: SourcePrincipal, variant: Variant, config: SpaceConfig) -> BlockGate:
        """The pack's reader and level for the blocks beside it (L8: the same function decides)."""
        context = PolicyContext(
            variant.verification, variant.link_role, variant.can_see_contacts, variant.deny_sensitive
        )
        private = config.object_types.private_fields()
        return BlockGate(config.policy, self._reads.reader(caller), context, private)

    async def _check_include(self, ask: _Ask, config: SpaceConfig) -> SpaceConfig:
        """Before any work: a block of a feature the space left off answers 404, as its route would (7.7). A
        feature this process's copy does not have yet reads the copy again first, as the routes' gate does."""
        if all(INCLUDE_FEATURES[name] in config.features.enabled for name in ask.include):
            return config
        if await self._config.refresh_on_miss():
            config = self._config.space(config.space_id)
        if any(INCLUDE_FEATURES[name] not in config.features.enabled for name in ask.include):
            raise NotFound("not enabled for this space")
        return config

    def _pack_cost(
        self, caller: SourcePrincipal, ask: _Ask, config: SpaceConfig, pack: CompiledPack | None
    ) -> PackCost | None:
        """The served pack's tokens from its manifest, and the cut the last measurement found for this source
        and view, from memory."""
        if pack is None:
            return None
        tokens = pack.manifest["tokens"]
        trims = self._space.get(caller.space.space_id, _NO_SPACE_READS).trims
        trim = next((t for t in trims if t.source_id == caller.source_id and t.view == ask.view), None)
        applied = bool(trim_for(config, caller.source_id, ask.view, trims))
        return PackCost(
            total=int(tokens["total"]),
            sections={str(k): int(v) for k, v in tokens["sections"].items()},
            cut=tuple(sorted(trim.unit_names)) if trim is not None else (),
            applied=applied,
            window_days=trim.window_days if trim is not None else 0,
            min_deliveries=trim.min_deliveries if trim is not None else 0,
        )

    async def _read_blocks(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target | None,
        arms: Sequence[Arm] = (),
        pack: PackCost | None = None,
        gate: BlockGate | None = None,
        memory: tuple[ObjectMemory, ...] = (),
        accounts: tuple[tuple[UUID, BoundRow], ...] = (),
        carried: frozenset[str] = frozenset(),
        hours_said: bool = False,
    ) -> list[tuple[ContextBlock, BlockRead | None]]:
        """The blocks `include` names, read together outside any transaction. A block that fails is left
        out and the read says it is degraded: the agent goes on with its local copy (24.2.10). A block an
        experiment holds out of this person's reads is not read at all, and the read is not degraded (6.4)."""
        withheld = withheld_elements(arms)
        held = {BLOCK_OF_ELEMENT[e] for e in withheld if e in BLOCK_OF_ELEMENT}
        blocks = [self._blocks[name] for name in ask.include if name not in held]
        found = await asyncio.gather(
            *(
                block.read(
                    caller,
                    target,
                    ask.block(withheld & CONSTRAINT_PARTS, pack, gate, memory, accounts, carried, hours_said),
                )
                for block in blocks
            ),
            return_exceptions=True,
        )
        reads: list[tuple[ContextBlock, BlockRead | None]] = []
        for block, read in zip(blocks, found, strict=True):
            if isinstance(read, BaseException):
                if not isinstance(read, Exception):
                    raise read
                log.warning("a context block failed", extra={"block": block.name}, exc_info=read)
                reads.append((block, None))
            else:
                reads.append((block, read))
        return reads

    def _block_drafts(
        self,
        caller: SourcePrincipal,
        blocks: Sequence[tuple[ContextBlock, BlockRead | None]],
        common: dict[str, Any],
    ) -> list[ReceiptDraft]:
        return [
            self._reads.draft(
                caller, ReceiptKind.READ, block.surface, item_ids=read.item_ids, detail=read.detail, **common
            )
            for block, read in blocks
            if read is not None
        ]

    def _resolved_in_memory(
        self, caller: SourcePrincipal, ask: _Ask, where: "_Where", config: SpaceConfig
    ) -> "_Resolved | None":
        """A warm read's resolution with no database round trip: the target and the conversation's proof
        from the read state, the space's settings and coverage from memory. None when any of them needs the
        database (a first read, a change the feed dropped, a proof not kept, an `about` link not kept, draws
        not kept). An `about` read takes the link the read state keeps, which the change feed drops when a
        link, a handle or either profile changes."""
        space_id = caller.space.space_id
        target = where.cached(self._reads, space_id)
        space = self._space_reads_cached(space_id)
        coverage = self._reads.cached_coverage(config)
        if target is None or space is None or coverage is None:
            return None
        if ask.about is not None:
            about = self._reads.cached_about(space_id, target, ask.about)
            if about is None:
                return None
            target = replace(target, about=about)
        level = self._reads.cached_verification(
            caller, ask.verification, conversation_id=ask.conversation_id, task_id=ask.task_id, target=target
        )
        assigned = self._reads.cached_assigned(caller, config, target)
        if level is None or assigned is None:
            return None
        return _Resolved(target, level, space, assigned.memory, coverage, assigned.arms)

    async def _resolve(
        self, caller: SourcePrincipal, ask: _Ask, where: "_Where", config: SpaceConfig
    ) -> "_Resolved":
        """One short transaction says who the read is about. Only database statements run while it holds
        a connection of the space's small pool: the cache, the hot layer, the key vault and the receipt
        queue are awaited after it, and a cache miss opens a transaction of its own."""
        space_id = caller.space.space_id
        space = _NO_SPACE_READS
        assigned = UNASSIGNED
        coverage: list[SourceCoverage] = []
        unlinked = False
        async with self._uow(space_id) as uow:
            target = await where.locate(self._reads, uow)
            # The link the read state keeps serves while the subject is read from the database: a contact
            # linked seconds ago is never kept (`SETTLING`), and each of its first reads looked the
            # organization, the link and its generation up again, 4 statements of 7 (09/10/2026).
            kept = None
            if target is not None and ask.about is not None:
                kept = self._reads.cached_about(space_id, target, ask.about)
                target = replace(target, about=kept) if kept is not None else target
            if ask.about is not None and kept is None:
                try:
                    target = await self._reads.attach_about(uow, target, ask.about)
                except AboutWithoutLink:
                    # The hot read fails soft (L10): the subject's own memory without the organization's
                    # block, flagged, the same whether the organization exists or not; never a 422 that
                    # leaves the turn with no memory at all.
                    unlinked = True
            level = await self._reads.verification(
                uow,
                caller,
                ask.verification,
                conversation_id=ask.conversation_id,
                task_id=ask.task_id,
                target=target,
                inactivity=config.settings.session_inactivity_minutes,
            )
            if target is not None:
                space = await self._space_reads(uow, space_id)
                assigned = await self._reads.assigned(uow, caller, config, target)
                coverage = await self._reads.coverage(uow, config)
        level = await self._reads.with_held(
            caller,
            ask.verification,
            level,
            conversation_id=ask.conversation_id,
            task_id=ask.task_id,
            target=target,
        )
        return _Resolved(target, level, space, assigned.memory, coverage, assigned.arms, unlinked)

    # Prefetch (11, 3.5).

    def prefetch(self, caller: SourcePrincipal, request: PrefetchRequest) -> bool:
        """`POST /v1/context/prefetch`: what `context()` refuses before it opens a transaction is refused
        here too; the rest runs in the background, and the answer never waits for it. False when there is
        nothing to prepare."""
        self._reads.require(caller, Scope.CONTEXT)
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        view_spec(request.view, config.views)
        if request.view == "full" and caller.audience is Audience.CUSTOMER_AGENT:
            raise Forbidden("the full view is for internal sources")
        if slot_budget(request.view) == 0 or gate(request.query) is not None:
            # A view without slots (`brief`, `full`) or a closed-form turn (13, 6.4) gets no slots, so
            # there is nothing to prepare.
            return False
        thread = request.conversation_id or request.task_id
        # One job per conversation; without one, per customer the request names.
        key = (space_id, caller.source_id, thread or request.subject or request.object)
        return self.prefetches.submit(key, (caller, request))

    async def _prepare(self, job: tuple[SourcePrincipal, PrefetchRequest]) -> None:
        """What the read of this turn will do up to its slots, without its effects: no receipt, no pin, no
        watermark. It opens the customer's retrieval entry and encodes the turn, waiting for both as a read
        never does, and keeps the slots for the read to take when nothing they came from has changed."""
        caller, request = job
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        ask = _Ask(
            request.view,
            request.verification,
            request.conversation_id,
            request.task_id,
            request.query,
            about=request.about,
        )
        await self._reads.follow_feeds(space_id)
        where = _Where(request.subject, None, request.object)
        resolved = self._resolved_in_memory(caller, ask, where, config)
        if resolved is None:
            resolved = await self._resolve(caller, ask, where, config)
        target, level, space = resolved.target, resolved.level, resolved.space
        if target is None:
            return
        weights, guarded, views = space.weights, space.guarded, space.views
        variant = self._variant(caller, ask, level.effective, target, config, space.trims, views)
        if not variant.slotted:
            return  # a view without slots (`brief`, `full`) has nothing to prepare
        served = await self._pack(caller, ask, target, variant, config)
        latest = served.latest or served.pack
        if served.pack is None or latest is None:
            return
        profile = target.profile
        min_privacy = self._reads.required_privacy(profile)
        await self.retrieval.entry(space_id, profile, latest.generation, min_privacy=min_privacy)
        await self.retrieval.prepare_query(space_id, request.query)
        live = await self._live(caller, ask, target, variant, served.pack, config)
        slots = await self._turn_slots(
            caller,
            ask,
            target,
            variant,
            served,
            live,
            config,
            weights,
            guarded=guarded,
            views=views,
            use_prepared=False,
        )
        if slots is not None:
            self._prepared.put(
                _prepared_key(caller, ask, target),
                PreparedTurn(request.query, slots.inputs, config, slots, slots.reusable, time.monotonic()),
            )

    # Predictive prewarm (13, 6.4).

    def _consider_prewarm(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target,
        variant: Variant,
        latest: CompiledPack,
    ) -> None:
        """A customer who usually calls within 72 hours of a contact like this one gets watched: the voice
        call finds their retrieval entry loaded. Only a read through a confirmed handle, about the customer
        alone, may start it."""
        source = config.source(caller.source_id)
        channel = "voice" if variant.view == "voice" else source.channel if source is not None else None
        self.prewarm.consider(
            config,
            profile_id=target.profile.profile_id,
            generation=latest.generation,
            reader_channel=channel,
            confirmed_reader=variant.reader_handle_id is None and target.about is None,
            episodes=latest.episode_index,
            now=self._clock.now(),
        )

    async def _prewarm_load(self, space_id: UUID, profile_id: UUID) -> int | None:
        """One prewarm: the confirmed view's retrieval entry at the profile's current generation, loaded in a
        short transaction of its own like any read's. None when there is nothing to warm any more."""
        try:
            self._config.space(space_id)  # the space may be gone since the watch started
        except NotFound:
            return None
        async with self._uow(space_id) as uow:
            profile = await uow.identity.get_profile(profile_id)
            if profile is None:
                return None
            generation = await uow.compile.current_generation(profile_target(profile_id))
        # Rows of a link nobody confirmed stay with that link (5.3): the entry is the confirmed view's.
        view = reader_view(profile)
        await self.retrieval.entry(
            space_id,
            view,
            generation,
            min_privacy=self._reads.required_privacy(view),
            wait_s=PREWARM_DEADLINE_S,
        )
        return generation

    async def _generations(self, keys: Sequence[tuple[UUID, UUID]]) -> list[int | None]:
        epoch = self._epoch.value
        found = await self._cache.get_many(
            [gen_key(epoch, space_id, profile_id) for space_id, profile_id in keys]
        )
        return [_generation_of(raw) for raw in found]

    async def _task_view_notes(self, caller: SourcePrincipal, ask: _Ask, config: SpaceConfig) -> Block | None:
        """Up to 200 tokens of the agent's own notes tagged with the task view's object types, for task views
        that ask for them (`include_agent_memory`, 10 C.3), when the space has agent memory on."""
        if not config.agent_memory.enabled or not ask.view.startswith("task:"):
            return None
        view = next((v for v in config.views if v.name == ask.view), None)
        if view is None or not view.include_agent_memory:
            return None
        space_id = caller.space.space_id
        now = self._clock.now()
        cached = self._agent_notes.get(space_id)
        if cached is None:
            # Its own short transaction, after the read's: the read path holds no connection meanwhile.
            cached = await self._renew_agent_notes(space_id)
        elif now - cached[0] >= AGENT_NOTES_TTL:
            self._reads.later(("agent_notes", space_id), self._renew_agent_notes(space_id))
        reader = AgentReader(caller.source_id, caller.vendor)
        vendors = {s.source_id: s.vendor for s in config.sources}
        wanted = set(view.object_types)
        mine = [
            n
            for n in still_valid(cached[1], now)
            if may_read(n, reader, vendors) and wanted & set(n.draft.tags)
        ]
        if not mine:
            return None
        block = render_block(
            mine,
            language=Formatter(config.settings.timezone, config.settings.locale).lang,
            max_tokens=TASK_VIEW_TOKENS,
            wanted=wanted,
        )
        return block if block.text else None

    async def _renew_agent_notes(self, space_id: UUID) -> tuple[datetime, list[AgentNoteRecord]]:
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            found = (now, await uow.agent_memory.active_notes())
        self._agent_notes[space_id] = found
        return found

    def _space_reads_cached(self, space_id: UUID) -> "_SpaceReads | None":
        """The space's learned weights, views, trims and guarded value types (11, 3.4; 13, 6.1 and 6.3):
        they move at most once a night. Past `TRIMS_TTL` the last ones still serve while one background
        read renews them; None only before the first."""
        found = self._space.get(space_id)
        if found is not None and self._clock.now() - found.at >= TRIMS_TTL:
            self._reads.later(("space", space_id), self._renew_space_reads(space_id))
        return found

    async def _space_reads(self, uow: UnitOfWork, space_id: UUID) -> "_SpaceReads":
        found = self._space_reads_cached(space_id)
        return found if found is not None else await self._load_space_reads(uow, space_id)

    async def _renew_space_reads(self, space_id: UUID) -> None:
        async with self._uow(space_id) as uow:
            await self._load_space_reads(uow, space_id)

    async def _load_space_reads(self, uow: UnitOfWork, space_id: UUID) -> "_SpaceReads":
        now = self._clock.now()
        since = now.date() - timedelta(days=GUARD_WINDOW_DAYS)
        found = _SpaceReads(
            now,
            await uow.measure.trims(),
            await uow.measure.retrieval_weights(),
            await uow.measure.view_budgets(),
            # Only the value types the space measured a contradiction of in the last 30 days write guard
            # lines (13, 6.1).
            await uow.measure.contradicted_value_types(since),
        )
        self._space[space_id] = found
        return found

    def _variant(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        level: Verification,
        target: Target,
        config: SpaceConfig,
        trims: Sequence[ContextTrim] = (),
        views: ViewBudgets | None = None,
        arms: Sequence[Arm] = (),
    ) -> Variant:
        about = target.about
        # The skeleton this agent and model were learned to use (13, 6.3), unless the customer is in the
        # arm that keeps the view's own budgets.
        learned = learned_for(config, views, target.profile.profile_id)
        view = ask.view
        if target.profile.kind.is_organization and view == "chat":
            view = target.profile.kind.value  # an account or partner read gets its subject view (21.4)
        plain = reader_variant(
            config,
            source_id=caller.source_id,
            audience=caller.audience,
            purposes=caller.purposes,
            view=view,
            level=level,
            trims=trims,
            learned=learned,
        )
        held = held_sections(arms)
        if held:
            # A section experiment's control arm reads its own pack, without the section (6.4).
            plain = replace(plain, trim=plain.trim | {whole_kind(kind) for kind in held})
        variant = replace(
            plain,
            provider=ask.target.provider if ask.target else None,
            model=ask.target.model if ask.target else None,
            about_handle_id=about.handle_id if about else None,
            link_role=about.role if about else None,
            can_see_contacts=about.can_see_contacts if about else True,
            focus_object_id=target.object.object_id if target.object else None,
            deny_sensitive=self._reads.deny_sensitive,
            reader_handle_id=provisional_reader(
                target.profile, target.handle.handle_id if target.handle else None
            ),
        )
        if learned is not None and variant.slotted and variant.provider is not None:
            variant = replace(variant, budgets=learned.ceilings_for(caller.source_id, variant.target, view))
        return variant

    async def _pack(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        variant: Variant,
        config: SpaceConfig,
    ) -> _Served:
        """The pack through the tiers; an `about` pack the organization's memory moved past since it was
        compiled asks for its compilation (coalesced), so the next read finds it current, never at a read."""
        served = await self._tiers(caller, ask, target, variant, config)
        about, current = target.about, served.latest or served.pack
        if about is not None and about.profile is not None and current is not None:
            space_id, person = caller.space.space_id, target.profile.profile_id
            account = about.generation
            if account is None:
                account = await self._generation(space_id, about.profile.profile_id)
            if account is not None and current.account_generation < account:
                self._reads.later(("account", space_id, person), self._request_account(space_id, person))
        return served

    async def _request_account(self, space_id: UUID, profile_id: UUID) -> None:
        """The person's packs about an organization compile again: once per window, as a seed does."""
        try:
            first = await self._cache.incr_window(
                account_key(self._epoch.value, space_id, profile_id), ACCOUNT_THROTTLE_S
            )
        except _CACHE_ERRORS:
            return
        if first == 1:
            async with self._uow(space_id) as uow:
                await uow.pipeline.request_work(
                    Task(TaskName.COMPILE, space_id, {"reason": "account"}, profile_id=profile_id)
                )

    async def _tiers(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        variant: Variant,
        config: SpaceConfig,
    ) -> _Served:
        space_id, profile = caller.space.space_id, target.profile

        def acceptable(
            pack: CompiledPack | None, *, pinned: bool = False, generation: int | None = None
        ) -> CompiledPack | None:
            ok = (
                pack is not None
                # A conversation keeps its pinned bytes when its source's trim changes (15.8).
                and (
                    pack.variant == variant.key
                    or (pinned and untrimmed_key(pack.variant) == untrimmed_key(variant.key))
                )
                # A pack of an older generation than the profile's describes memory that has changed. An
                # `about` pack a compilation behind the organization's memory is served while that compiles
                # again (`_pack`), as a person's pack is until its own compilation lands; one compiled
                # before a privacy change of the organization never is.
                and (generation is None or pack.generation >= generation)
                and (
                    target.about is None
                    or target.about.profile is None
                    or pack.account_privacy_version >= target.about.profile.privacy_version
                )
                and pack.privacy_version >= self._reads.required_privacy(profile)
                and pack.policy_version == config.policy.version
                and pack.compiler_version == COMPILER_VERSION
                # Compiled for this reader's view (5.3): a join, a confirmation or a split since then
                # changed whose rows it may hold, so the pack, pinned or not, is compiled again.
                and compiled_for(pack.manifest, profile)
                and self._recheck(pack, caller, variant, config)
            )
            return pack if ok else None

        # The generation, the pack of the variant and the conversation's pin in one round trip.
        epoch, profile_id = self._epoch.value, profile.profile_id
        keys = [gen_key(epoch, space_id, profile_id), t0_key(epoch, space_id, profile_id, variant.key)]
        if ask.thread:
            keys.append(pin_key(epoch, space_id, profile_id, ask.thread, ask.view, caller.source_id))
        found = await self._cached_values(keys)
        generation = _generation_of(found[0])
        t0 = await self._opened(space_id, keys[1], found[1])
        pinned = await self._opened(space_id, keys[2], found[2]) if ask.thread else None
        if ask.thread and pinned is None and generation is None:
            # Without the profile's generation the cache lost its state, and the pin may be one of
            # the losses: the receipt writer's projection has it (rev. 11). With the generation there,
            # a missing pin was never made. Unless T0 answers, the pin is read in the same transaction as
            # the current pack, which such a read needs anyway: a new customer's first reads open one
            # transaction less on the space's two connections (28/09/2026).
            if acceptable(t0, generation=generation) is None:
                current, projected = await self._current_and_pin(
                    caller, target, variant, acceptable, generation, ask
                )
                # A request to the worker, which the answer never waits for.
                self._reads.later(
                    ("seed", space_id, profile.profile_id), self._request_seed(space_id, profile.profile_id)
                )
                if (pin := acceptable(projected, pinned=True)) is not None:
                    held = found[2] is not None
                    return _Served(pin, DeliveryPath.T0, current.pack or pin, pin_held=held)
                return current
            pinned = await self._projected_pin(caller, target, ask)
        pin = acceptable(pinned, pinned=True)
        held = pin is not None and bool(ask.thread) and found[2] is not None
        if pin is not None and (cached := acceptable(t0, generation=generation)) is not None:
            return _Served(pin, DeliveryPath.T0, cached, pin_held=held)
        if generation is None:
            # A request to the worker, which the answer never waits for.
            self._reads.later(
                ("seed", space_id, profile.profile_id), self._request_seed(space_id, profile.profile_id)
            )
        if pin is not None:
            if generation is not None and pin.generation >= generation:
                return _Served(pin, DeliveryPath.T0, pin, pin_held=held)  # nothing changed since the pin
            # The conversation keeps its pinned bytes, and still learns: the current memory, from the stored
            # pack or compiled, is what its delta and its slots read (5.11, 2.4). Without it, a pinned
            # conversation whose variant T0 does not hold at the current generation never saw anything
            # extracted after its first read.
            current = await self._current(space_id, target, variant, acceptable, generation)
            return _Served(pin, DeliveryPath.T0, current.pack or pin, pin_held=held)
        if cached := acceptable(t0, generation=generation):
            return _Served(cached, DeliveryPath.T0, cached)
        return await self._current(space_id, target, variant, acceptable, generation)

    async def _current(
        self,
        space_id: UUID,
        target: Target,
        variant: Variant,
        acceptable: Callable[..., CompiledPack | None],
        generation: int | None,
    ) -> _Served:
        """The pack of the current memory when T0 has none: the stored one (T1), else compiled now (T2), in
        one transaction. What fills the cache for the next reads (T0, the variants wanted) is written in the
        background: the answer never waits for it."""
        served, _ = await self._current_and_pin(None, target, variant, acceptable, generation, None, space_id)
        return served

    async def _current_and_pin(
        self,
        caller: SourcePrincipal | None,
        target: Target,
        variant: Variant,
        acceptable: Callable[..., CompiledPack | None],
        generation: int | None,
        ask: "_Ask | None",
        space_id: UUID | None = None,
    ) -> "tuple[_Served, CompiledPack | None]":
        """`_current`, and with `ask` the conversation's projected pin (`_projected_pin`) read first in the
        same transaction."""
        space_id = space_id if space_id is not None else caller.space.space_id if caller else None
        assert space_id is not None
        profile = target.profile
        # The replica has not caught up with a privacy change: nothing may be compiled from it.
        behind = profile.privacy_version < self._reads.required_privacy(profile)
        pack: CompiledPack | None = None
        projected: CompiledPack | None = None
        try:
            async with self._uow(space_id) as uow:
                if ask is not None:
                    projected = await self._pin_of(uow, target, ask)
                stored = await uow.compile.get_pack(pack_key(profile.profile_id, variant.key))
                current = (
                    generation
                    if generation is not None
                    else await uow.compile.current_generation(profile_target(profile.profile_id))
                )
                if stored and (
                    found := acceptable(_load(stored.body, stored.generation), generation=current)
                ):
                    self._reads.later(
                        ("t0", space_id, profile.profile_id, found.variant),
                        self._write_t0(space_id, profile.profile_id, found, generation),
                    )
                    return _Served(found, DeliveryPath.T1, found), projected
                if behind:
                    return _Served(None, DeliveryPath.T4, degraded=True), projected
                # The generation this transaction read; one from the cache may be ahead of what it reads.
                pack = await self._compile_on_demand(
                    uow, target, variant, None if generation is not None else current
                )
        except NiadraError:
            raise
        except Exception:
            # Availability fails open (L10): a failure drops to the hot layer, never to an error.
            log.exception("reading or compiling the current pack failed")
        if pack is None:
            return _Served(None, DeliveryPath.T3, degraded=True), projected
        self._reads.later(
            ("t0", space_id, profile.profile_id, pack.variant),
            self._fill_t0(space_id, profile, pack, variant),
        )
        return _Served(pack, DeliveryPath.T2, pack), projected

    async def _fill_t0(
        self, space_id: UUID, profile: ProfileRecord, pack: CompiledPack, variant: Variant
    ) -> None:
        generation = await self._generation(space_id, profile.profile_id)
        if generation is None and pack.generation == 0:
            # A profile no compilation published yet (a contact just linked, a first customer) has no
            # generation in the cache, and without one T0 takes nothing: every read compiled the pack again
            # until the worker's compilation landed, 46 statements each for a contact of an account of 500
            # (09/10/2026). Its generation starts at 0, which only a compilation moves past; no pin of a
            # profile with no stored pack can be lost with it.
            try:
                await self._cache.bump_generation(
                    gen_key(self._epoch.value, space_id, profile.profile_id), 0, T0_TTL_S
                )
            except _CACHE_ERRORS:
                return
            generation = 0
        await self._write_t0(space_id, profile.profile_id, pack, generation)
        await self._want(space_id, profile.profile_id, variant)

    def _recheck(
        self, pack: CompiledPack, caller: SourcePrincipal, variant: Variant, config: SpaceConfig
    ) -> bool:
        """Policy at compile time, authorization at read time (L8): items pass the same function again."""
        reader = self._reads.reader(caller)
        context = PolicyContext(
            variant.verification, variant.link_role, variant.can_see_contacts, variant.deny_sensitive
        )
        return all(
            evaluate(config.policy, reader, _attrs(item), context).included
            for item in pack.manifest.get("items", [])
        )

    async def _on_demand(self, space_id: UUID, target: Target, variant: Variant) -> CompiledPack | None:
        try:
            async with self._uow(space_id) as uow:
                return await self._compile_on_demand(uow, target, variant)
        except NiadraError:
            raise
        except Exception:
            # Availability fails open (L10): a compile failure drops to the hot layer, never to an error.
            log.exception("on-demand compilation failed")
            return None

    async def _compile_on_demand(
        self, uow: UnitOfWork, target: Target, variant: Variant, generation: int | None = None
    ) -> CompiledPack | None:
        about = target.about
        return await self._compile.compile_on_demand(
            uow,
            target.profile,
            variant,
            account=about.profile if about else None,
            # The person's handles the view holds, as the compilation ahead of the read takes them: a read
            # through the phone and one through the e-mail get the same pack.
            person_handle_ids=tuple(target.profile.handle_ids) if target.handle else (),
            generation=generation,
            # The organization's, as the read's resolution found it, before this transaction.
            account_generation=about.generation if about else None,
        )

    # Cache tiers.

    async def _projected_pin(self, caller: SourcePrincipal, target: Target, ask: _Ask) -> CompiledPack | None:
        """The pin the receipt writer projected (rev. 11). The projection keeps one pin per conversation and
        view: another source's pin fails the variant check of the caller, which then gets its current pack."""
        async with self._uow(caller.space.space_id) as uow:
            return await self._pin_of(uow, target, ask)

    @staticmethod
    async def _pin_of(uow: UnitOfWork, target: Target, ask: _Ask) -> CompiledPack | None:
        assert ask.thread is not None
        profile_id = target.profile.profile_id
        pin = await uow.serve.get_pin(ask.thread, ask.view)
        if pin is None or not pin.pack_key.startswith(f"{profile_id}/"):
            return None
        stored = await uow.compile.get_pack(pin.pack_key)
        return _load(stored.body, stored.generation) if stored and stored.pack_hash == pin.pack_hash else None

    async def _cached_values(self, keys: Sequence[str]) -> list[bytes | None]:
        try:
            return await self._cache.get_many(keys)
        except _CACHE_ERRORS:
            return [None] * len(keys)

    async def _opened(self, space_id: UUID, key: str, value: bytes | None) -> CompiledPack | None:
        if value is None:
            return None
        try:
            return await open_pack(self._keys, space_id, key, value)
        except (ValueError, KeyError):
            log.warning("unreadable cache entry ignored")
            return None

    async def _generation(self, space_id: UUID, profile_id: UUID) -> int | None:
        try:
            raw = await self._cache.get(gen_key(self._epoch.value, space_id, profile_id))
        except _CACHE_ERRORS:
            return None
        return int(raw) if raw is not None else None

    async def _write_t0(
        self, space_id: UUID, profile_id: UUID, pack: CompiledPack, generation: int | None
    ) -> None:
        """Only a pack of the current generation may enter T0, and only when the generation exists (06, 3)."""
        if generation is None or pack.generation != generation:
            return
        key = t0_key(self._epoch.value, space_id, profile_id, pack.variant)
        try:
            value = await seal_pack(self._keys, space_id, key, pack)
            await self._cache.put_if_generation(
                key, gen_key(self._epoch.value, space_id, profile_id), value, generation, PACK_TTL_S
            )
        except _CACHE_ERRORS:
            log.warning("could not write T0")

    async def _request_seed(self, space_id: UUID, profile_id: UUID) -> None:
        """First read of a profile in a new cache epoch: the worker rewrites generation and T0 (5.11)."""
        try:
            first = await self._cache.incr_window(
                seed_key(self._epoch.value, space_id, profile_id), SEED_THROTTLE_S
            )
        except _CACHE_ERRORS:
            return
        if first == 1:
            async with self._uow(space_id) as uow:
                await uow.pipeline.enqueue([Task(TaskName.SEED_GEN, space_id, profile_id=profile_id)])

    async def _want(self, space_id: UUID, profile_id: UUID, variant: Variant) -> None:
        """Variants compiled on demand become eager at the next compilation (5.10: lazy, then eager), an
        `about` one too: the organization's compilations compile its readers' packs again."""
        key = wanted_key(self._epoch.value, space_id, profile_id)
        try:
            raw = await self._cache.get(key)
            wanted = list(json.loads(raw)) if raw else []
            if variant.key not in wanted:
                await self._cache.set(
                    key, json.dumps([*wanted, variant.key][-MAX_WANTED:]).encode(), T0_TTL_S
                )
        except _CACHE_ERRORS:
            return

    # Live turns and delta (B3).

    async def _live(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        variant: Variant,
        pack: CompiledPack | None,
        config: SpaceConfig,
    ) -> "_Live":
        """Recent turns of the customer's other conversations, each through the pack's policy (5.12):
        the reader's level and purposes decide, and a turn left out counts in `withheld`."""
        # context() refreshed the privacy feed before opening its transaction; refreshing here would take
        # a second connection of the space's small pool while this read holds one.
        screened = await self._screen_live(
            caller.space.space_id, target.profile, variant, config, pack=pack, thread=ask.thread
        )
        turns = [(line.event_id, turn) for line, decision, turn in screened.lines if turn is not None]
        withheld = sum(1 for _, decision, _ in screened.lines if not decision.included)
        unverified = sum(1 for _, d, _ in screened.lines if d.reason is ExclusionReason.VERIFICATION)
        return _Live(turns, screened.complete, withheld, unverified)

    async def screen_live(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        variant: Variant,
        config: SpaceConfig,
        *,
        pack: CompiledPack | None,
        thread: str | None = None,
    ) -> LiveScreen:
        """The customer's recent turns of other conversations (all of them without `thread`), since the
        pack, each with the decision the pack's policy gives it for the variant's reader, and the turn as
        that reader gets it (masked when the policy masks it, None when it withholds it). The policy
        simulation calls it too, so the Console shows what a read would get (5.12). Called with no
        transaction open: it refreshes the privacy feed first."""
        await self._reads.refresh_privacy()
        return await self._screen_live(space_id, profile, variant, config, pack=pack, thread=thread)

    async def _screen_live(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        variant: Variant,
        config: SpaceConfig,
        *,
        pack: CompiledPack | None,
        thread: str | None = None,
    ) -> LiveScreen:
        now = self._clock.now()
        floor = now - timedelta(hours=config.settings.hot_layer_cap_hours)
        # What the pack already holds: every item up to its `as_of`, and conversations only up to the end of
        # the latest one extracted. A WhatsApp conversation still open when another agent's credit made the
        # pack newer is not in memory, so its turns still come here.
        items_since = max(pack.as_of, floor) if pack is not None and pack.as_of is not None else floor
        turns_since = items_since
        if pack is not None and pack.manifest.get("slots") is not None:
            until = pack.conversations_until
            turns_since = max(min(until, items_since), floor) if until is not None else floor
        items_ms = int(items_since.timestamp() * 1000) + 1
        turns_ms = int(turns_since.timestamp() * 1000) + 1
        since_ms = min(items_ms, turns_ms)
        held = _evidence(pack) if turns_ms < items_ms and pack is not None else frozenset()
        complete = not await self._hot_layer_degraded()
        docs: dict[UUID, dict[str, Any]] = {}
        handles: dict[UUID, list[UUID]] = {}
        skip: set[UUID] = set()
        try:
            # Every handle's newest turns in one round trip; each handle's list is screened on its own.
            found = await self._hot.read(space_id, profile.handle_ids, since_ms, newest=LIVE_READ)
        except _CACHE_ERRORS:
            return LiveScreen(complete=False)
        if not found.whole:
            complete = False  # the store came back empty and the rebuild has not sealed it yet (06, 3)
        for handle_id in profile.handle_ids:
            mark = self._reads.guard.handle_mark(handle_id)
            own = found.by_handle.get(handle_id, [])
            if len(own) >= LIVE_READ:
                complete = False  # older turns than these were left unread
            for turn in own:
                if turn.event_id in skip or (
                    mark is not None
                    and mark.kind == "erasure"
                    and turn.occurred_at_ms <= mark.at.timestamp() * 1000
                ):
                    continue  # lines of an erased handle from before the erasure never come back (06, 3)
                if turn.event_id not in docs:
                    if str(turn.event_id) in held:
                        skip.add(turn.event_id)  # an action the pack already carries
                        continue
                    doc = await self._open_turn(space_id, turn.event_id, turn.envelope_ct)
                    if doc is None:
                        complete = False
                        continue
                    if thread and thread in (doc.get("conversation_id"), doc.get("task_id")):
                        skip.add(turn.event_id)  # the caller already has its own conversation
                        continue
                    threaded = bool(doc.get("conversation_id") or doc.get("task_id"))
                    if turn.occurred_at_ms < (turns_ms if threaded else items_ms):
                        skip.add(turn.event_id)  # already in the pack
                        continue
                    docs[turn.event_id] = doc
                handles.setdefault(turn.event_id, []).append(handle_id)
        newest = sorted(docs.items(), key=lambda d: (d[1].get("occurred_at", ""), str(d[0])))[-LIVE_TURNS:]
        lines = [
            LiveLine(event_id, doc, tuple(sorted(set(handles[event_id]), key=str)))
            for event_id, doc in newest
        ]
        reader, context = variant_reader(variant, view_spec(variant.view, config.views))
        fmt = Formatter(config.settings.timezone, config.settings.locale)
        screened: list[tuple[LiveLine, Decision, LiveTurn | None]] = []
        for line, decision in screen_live(lines, config.policy, reader, context, profile.kind):
            screened.append((line, decision, _live_turn(line, decision, fmt) if decision.included else None))
        screened.sort(key=lambda s: (_occurred(s[0]), str(s[0].event_id)))
        return LiveScreen(screened, complete)

    async def _open_turn(self, space_id: UUID, event_id: UUID, envelope: bytes) -> dict[str, Any] | None:
        try:
            version, ciphertext = unpack_envelope(envelope)
            plain = await self._keys.decrypt(space_id, ciphertext, hot_aad(space_id, event_id), version)
            doc: dict[str, Any] = json.loads(plain)
            return doc
        except Exception:
            log.warning("unreadable hot-layer turn skipped")
            return None

    async def _delta(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        served: _Served,
        config: SpaceConfig,
    ) -> str | None:
        """What the latest pack has that this agent has not seen, rendered to append at the end (2.4)."""
        latest = served.latest
        if latest is None:
            return None
        seen: set[str] = (
            set(served.pack.item_ids) if served.pack is not None and served.pack is not latest else set()
        )
        since = served.pack.as_of if served.pack is not None and served.pack is not latest else None
        space_id, profile_id = caller.space.space_id, target.profile.profile_id
        thread = ask.thread or ""
        # The base is the source's mark in this thread, else its latest anywhere: a conversation it has not
        # read in yet gets what changed since it last talked to the customer (2.4). The cache first; the
        # receipt writer's projection when the cache lost them.
        mark = await self._cached_mark(space_id, caller.source_id, profile_id, thread)
        latest_cached = None
        if mark is None:
            key = latest_mark_key(self._epoch.value, space_id, profile_id, caller.source_id)
            latest_cached = await self._read_mark(key)
        async with self._uow(space_id) as uow:
            if mark is None:
                stored = await uow.serve.get_watermark(caller.source_id, profile_id, thread)
                if stored is None and latest_cached is None:
                    stored = await uow.serve.latest_watermark(caller.source_id, profile_id)
                mark = (stored.generation, stored.manifest_hash) if stored else latest_cached
            manifest = await uow.compile.get_manifest(mark[1]) if mark is not None else None
        if manifest is not None:
            seen |= {item_key(item["kind"], item["id"]) for item in manifest.get("items", [])}
            since = datetime.fromisoformat(manifest["as_of"]) if manifest.get("as_of") else since
        elif not seen:
            return None
        return render_delta(
            latest, seen, since, timezone=config.settings.timezone, locale=config.settings.locale
        )

    async def _cached_mark(
        self, space_id: UUID, source_id: UUID, profile_id: UUID, thread: str
    ) -> tuple[int, str] | None:
        return await self._read_mark(
            watermark_key(self._epoch.value, space_id, profile_id, source_id, thread)
        )

    async def _read_mark(self, key: str) -> tuple[int, str] | None:
        try:
            raw = await self._cache.get(key)
            return decode_mark(raw) if raw is not None else None
        except _CACHE_ERRORS:
            return None
        except (ValueError, KeyError):
            log.warning("unreadable watermark ignored")
            return None

    # Per-turn slots (11, 3.3).

    async def _turn_slots(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        variant: Variant,
        served: _Served,
        live: _Live,
        config: SpaceConfig,
        weights: RetrievalWeights | None = None,
        *,
        guarded: frozenset[str] = frozenset(),
        views: ViewBudgets | None = None,
        use_prepared: bool = True,
    ) -> _TurnSlots | None:
        """What the customer's last turn selects from memory for this turn, beside the pinned body: the
        channels over what the latest pack held back and the lines the pin does not carry, fused, within
        the view's slot budget. No model runs here; a slot source past its deadline is left out.

        A prefetch of this turn (11, 3.5) prepared the same computation: its slots are taken when every
        input is the same, which makes them the bytes this read would compute; otherwise only its reading
        of the words is, and the channels run here on the cache the prefetch warmed."""
        body = served.pack
        latest = served.latest or body
        if not (variant.slotted and ask.query and body is not None and latest is not None):
            return None
        if (closed := gate(ask.query)) is not None:
            # The intent gate (13, 6.4): thanks, a short confirmation, a request to repeat or a greeting
            # asks memory for nothing; the pinned skeleton goes alone, and the receipt says so.
            return _TurnSlots(TurnAnalysis(), SlotPlan(), None, closed.manifest(), gated=True)
        space_id, profile = caller.space.space_id, target.profile
        min_privacy = self._reads.required_privacy(profile)
        learned_views = learned_for(config, views, profile.profile_id)
        # The slots' budget this agent and model were learned to use (13, 6.3); the view's own otherwise.
        slot_factor = (
            learned_views.factors_for(caller.source_id, variant.target, variant.view).get(SLOTS)
            if learned_views is not None
            else None
        )
        # The customer's opened rows when this process holds them (a turn after the first, or a prefetch of
        # this one); never waited for.
        entry = await self.retrieval.entry(
            space_id, profile, latest.generation, min_privacy=min_privacy, wait_s=0
        )
        inputs = (
            profile.profile_id,
            profile.handle_ids,
            profile.privacy_version,
            min_privacy,
            caller.source_id,
            caller.audience,
            caller.purposes,
            variant,
            (body.pack_hash, body.manifest_hash, body.generation, body.variant),
            (latest.pack_hash, latest.manifest_hash, latest.generation, latest.variant),
            # `delta` changes the slots only when the latest pack says more than the pinned one.
            ask.delta and latest.pack_hash != body.pack_hash,
            live.withheld,
            weights.version if weights is not None else None,
            entry,  # compared by identity: a reload is another entry
            self.retrieval.semantic_on,
            guarded,
            slot_factor,
        )
        turn: TurnAnalysis | None = None
        if use_prepared and (prepared := self._prepared.get(_prepared_key(caller, ask, target), ask.query)):
            if prepared.reusable and prepared.config is config and prepared.inputs == inputs:
                slots: _TurnSlots = prepared.slots
                return replace(slots, prefetched=True)
            turn = prepared.slots.turn  # the same words read the same way (L5)
        settings = config.settings
        # The slots write dates as the pack does: without the year of the latest thing memory holds.
        fmt = Formatter(settings.timezone, settings.locale, pack_year(latest.as_of, settings.timezone))
        spec = view_spec(variant.view, config.views)
        reader = self._reads.reader(caller)
        context = PolicyContext(
            variant.verification, variant.link_role, variant.can_see_contacts, variant.deny_sensitive
        )

        def allowed(entry: dict[str, Any]) -> bool:
            # Held-back items passed the policy at compile time; they pass it again at read time (L8).
            return evaluate(config.policy, reader, _attrs(entry), context).included

        pool = _slot_pool(body, latest, fmt, allowed, delta=ask.delta)
        turn = turn or analyze(ask.query)
        now = self._clock.now()
        # Time words and anchors on the customer's own system events (11, 3.3): an anchored window
        # ranks and filters, a calendar one only ranks.
        temporal = temporal_channel(
            ask.query,
            [_Timed(i.item_id, i.at, i.text, i.object_id) for i in pool if i.at is not None],
            lexicon=config.anchor_lexicon(),
            marks=(*latest.marks, *(entry.marks if entry is not None else ())),
            reference=now,
            timezone=settings.timezone,
        )
        leading = [hit.item_id for hit in temporal.hits]
        lexicon = config.lexicon()
        hits = run_channels(turn, pool, standard_channels(leading=leading, lexicon=lexicon))
        values_hit, unseen = await self._value_hits(
            space_id, profile, turn, pool, entry, fmt, spec.voice, lexicon, _withheld_episodes(latest)
        )
        hits.append(values_hit)
        found = any(h.ranked for h in hits if h.channel in ("exact", "lexical"))
        if self.retrieval.semantic_on and (variant.view == "chat" or not found):
            # The semantic channel costs an encoding: chat always, voice only when the rules found nothing
            # (11, 3.3). It never waits for a cold customer or past its deadline.
            result = await self.retrieval.semantic_for(
                space_id,
                profile,
                ask.query,
                {i.item_id for i in pool},
                generation=latest.generation,
                min_privacy=min_privacy,
            )
            ranked = tuple(ranking(dict(result.scores))[:PER_CHANNEL])
            hits.append(ChannelHits(result.channel, ranked, skipped=result.status is ChannelStatus.SKIPPED))
        hits += await self._source_hits(
            caller.space.space_id, target.profile, turn, pool, ask.query, fmt, spec.voice
        )
        fusion = weights_for(weights, variant.view)
        version = weights.version if fusion is not None and weights is not None else None
        guards: tuple[Guard, ...] = ()
        if types := wanted(turn, guarded):
            # Guard lines (13, 6.1): only for the types the space measured agents contradicting.
            values = await self._guard_values(space_id, profile, types, pool, entry)
            guards = guards_for(turn, values, guarded, fmt=fmt, voice=spec.voice)
        plan = plan_slots(
            turn,
            pool,
            hits,
            fmt=fmt,
            voice=spec.voice,
            budget=learned_slot_budget(variant.view, slot_factor, question=asks_value(turn)),
            max_items=max_slots(variant.view, turn),
            episodes=latest.episode_index,
            now=now,
            withheld=_for_level(latest) + live.unverified,
            unproven=variant.verification is Verification.V0,
            withheld_said=bool(body.manifest.get("slots", {}).get("skeleton")),
            outside=temporal.outside,
            weights=fusion,
            weights_version=version,
            guards=guards,
            guard_arm=guard_arm(space_id, profile.profile_id),
            lexicon=lexicon,
            budget_rule=slot_budget_rule(turn),
            unseen=unseen,
        )
        analysis = turn.manifest() | ({"temporal": temporal.analysis()} if temporal.analysis() else {})
        reusable = (
            entry is not None
            and not self._slot_sources
            and not any(h.skipped for h in hits)
            # Time words and counts over a window read against the moment of the read.
            and temporal.window is None
            and temporal.anchor is None
            and turn.question is not QuestionKind.COUNT
        )
        return _TurnSlots(turn, plan, plan.render(fmt), analysis, version, inputs, reusable)

    async def _value_hits(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        turn: TurnAnalysis,
        pool: Sequence[SlotItem],
        entry: ProfileEntry | None,
        fmt: Formatter,
        voice: bool,
        lexicon: Lexicon,
        withheld_episodes: frozenset[str],
    ) -> tuple[ChannelHits, UnseenValues]:
        """The settled values channel (11, 3.2), only when the turn names a number or asks for a kind
        of value: from the retrieval cache when it holds the customer, else in a short transaction of its
        own within `VALUES_DEADLINE_S`. With it, what of those values this reader does not see, so the
        slots never say "no record" of them."""
        types = [ValueType(t) for t in turn.asked_types if t in _VALUE_TYPES]
        keys = turn.identifiers[:MAX_TURN_KEYS]
        # Numbers past the cap are not looked up: they are unknown, so no line may say memory holds none.
        beyond = tuple(("", n) for n in turn.identifiers[MAX_TURN_KEYS:])
        if not (types or keys):
            return ChannelHits(VALUES_CHANNEL), NOTHING_UNSEEN
        if entry is not None:
            rows = [i for t in types for i in entry.values_by_type.get(t, ())]
            rows += [i for key in keys for i in entry.values_by_key.get(key, ())]
            values = [entry.values[i] for i in dict.fromkeys(rows)]
        else:
            try:
                async with asyncio.timeout(VALUES_DEADLINE_S), self._uow(space_id) as uow:
                    values = await uow.memory.values_for(profile.handle_ids, types=types) if types else []
                    values += await uow.memory.values_by_keys(profile.handle_ids, keys)
            except TimeoutError:
                # Nothing is known of what memory holds: no line may say it holds nothing.
                return ChannelHits(VALUES_CHANNEL, skipped=True), UnseenValues(hidden=_unknown(turn))
        unseen = unseen_values(values, pool, withheld_episodes)
        if beyond:
            unseen = replace(unseen, hidden=(*unseen.hidden, *beyond))
        return value_hits(values, turn, pool, fmt=fmt, voice=voice, lexicon=lexicon), unseen

    async def _guard_values(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        types: Sequence[ValueType],
        pool: Sequence[SlotItem],
        entry: ProfileEntry | None,
    ) -> list[SettledValue]:
        """The settled values of the guarded types this reader may see: a value is seen only when the
        conversation it was said in is in the pool, as in the values channel. From the retrieval cache
        when it holds the customer, else in a short transaction within `VALUES_DEADLINE_S`; past it, the
        turn goes without guards."""
        if entry is not None:
            rows = [i for t in types for i in entry.values_by_type.get(t, ())]
            values = [entry.values[i] for i in dict.fromkeys(rows)]
        else:
            try:
                async with asyncio.timeout(VALUES_DEADLINE_S), self._uow(space_id) as uow:
                    values = await uow.memory.values_for(profile.handle_ids, types=list(types))
            except TimeoutError:
                return []
        visible = {i.item_id for i in pool if i.kind == "episode"}
        return [v for v in values if v.episode_id is not None and f"episode:{v.episode_id}" in visible]

    async def _source_hits(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        turn: TurnAnalysis,
        pool: Sequence[SlotItem],
        query: str,
        fmt: Formatter,
        voice: bool,
    ) -> list[ChannelHits]:
        async def one(source: SlotSource) -> ChannelHits:
            try:
                return await asyncio.wait_for(
                    source.hits(space_id, profile, turn, pool, query=query, fmt=fmt, voice=voice),
                    timeout=source.deadline_s,
                )
            except NiadraError:
                raise
            except TimeoutError:
                return ChannelHits(source.name, skipped=True)
            except Exception:
                # A slot channel never fails the read (L10): the slots go out without it.
                log.warning("slot channel %s failed", source.name)
                return ChannelHits(source.name, skipped=True)

        if not self._slot_sources:
            return []
        return list(await asyncio.gather(*(one(source) for source in self._slot_sources)))

    # Responses.

    async def _respond(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        level: EffectiveVerification,
        assignment: Assignment | None,
        served: _Served,
        live: "_Live",
        delta: str | None,
        coverage: list[SourceCoverage],
        timing: _Timing,
        config: SpaceConfig,
        slots: _TurnSlots | None = None,
        arms: Sequence[Arm] = (),
        gate: BlockGate | None = None,
    ) -> "Answer":
        pack = served.pack
        path = served.path if pack is not None else DeliveryPath.T3 if live.turns else DeliveryPath.T4
        notes = await self._task_view_notes(caller, ask, config) if pack is not None else None
        etag = pack.etag if pack else ""
        if pack is not None and notes is not None:
            # The notes change without the pack changing: the etag covers both, so `known_etag` never keeps
            # a text whose notes moved on.
            etag = f"{pack.etag}-{notes.etag.removeprefix('am-')[:12]}"
        not_modified = pack is not None and ask.known_etag is not None and ask.known_etag == etag
        common: dict[str, Any] = {
            "profile_id": target.profile.profile_id,
            "conversation_id": ask.conversation_id,
            "task_id": ask.task_id,
            "object_id": target.object.object_id if target.object else None,
            "about_handle_id": target.about.handle_id if target.about else None,
            "verification": level.effective.value,
            "experiment_group": assignment.label if assignment else None,
        }
        extra = {"path": path.value, **({"not_modified": "1"} if not_modified else {})}
        if arms:
            extra["arms"] = arms_text(arms)
        cost = self._pack_cost(caller, ask, config, pack) if "budget" in ask.include else None
        # A conversation keeps its pinned text, but what must or must not be done is the current memory's:
        # the blocks read the latest pack, so an instruction said after the pin still reaches them.
        current = served.latest or pack
        memory = current.object_memory if current is not None else ()
        accounts = current.account_instructions if current is not None else ()
        blocks = (
            await self._read_blocks(
                caller,
                ask,
                target,
                arms,
                cost,
                gate,
                memory,
                accounts,
                frozenset(pack.item_ids if pack else ()),
                says_contact_time(pack),
            )
            if ask.include
            else []
        )
        if blocks:
            timing.mark("blocks")
        if pack is not None:
            # The receipt writer projects the watermark (by generation) and the pin from these (5.11).
            extra["generation"] = str(pack.generation)
            # Every read of a stored variant counts for the cap's least-read cut; in a
            # conversation it also pins.
            extra["pack_key"] = pack_key(target.profile.profile_id, pack.variant)
            if ask.thread:
                extra["view"] = ask.view
        drafts = [
            self._reads.draft(
                caller,
                ReceiptKind.READ,
                ReceiptSurface.PACK,
                item_ids=pack.item_ids if pack else (),
                pack_hash=pack.pack_hash if pack else None,
                manifest_hash=pack.manifest_hash if pack else None,
                policy_version=pack.policy_version if pack else None,
                withheld=pack.withheld if pack else 0,
                extra=extra,
                detail=timing.server_ms(),
                **common,
            )
        ]
        if live.turns or live.withheld:
            drafts.append(
                self._reads.draft(
                    caller,
                    ReceiptKind.READ,
                    ReceiptSurface.LIVE,
                    item_ids=tuple(f"event:{event_id}" for event_id, _ in live.turns),
                    policy_version=config.policy.version,
                    withheld=live.withheld,
                    **common,
                )
            )
        if delta and served.latest is not None:
            drafts.append(
                self._reads.draft(
                    caller,
                    ReceiptKind.READ,
                    ReceiptSurface.DELTA,
                    item_ids=served.latest.item_ids,
                    manifest_hash=served.latest.manifest_hash,
                    extra={"generation": str(served.latest.generation)},
                    **common,
                )
            )
        if (
            slots is not None
            and (slots.text is not None or slots.plan.guards or slots.gated)
            and ask.query is not None
        ):
            # A control customer's guards leave no line but are recorded, so their effect is measured.
            # What the turn selected, how and why; the question itself only as its keyed hash (11, 9.7).
            drafts.append(
                self._reads.draft(
                    caller,
                    ReceiptKind.READ,
                    ReceiptSurface.SLOTS,
                    item_ids=slots.plan.item_ids,
                    policy_version=config.policy.version,
                    query_hash=await self._keys.hmac(
                        caller.space.space_id, "context_query", ask.query.encode()
                    ),
                    detail={
                        "slots": canonical_json(slots.plan.manifest()).decode(),
                        "analysis": canonical_json(slots.analysis).decode(),
                        # Why each slot was chosen, on every read: positions and scores, no line (12, WP-E).
                        "why": canonical_json(slots.plan.why()).decode(),
                        # Which learned weights fused this turn's channels, for lineage (11, 3.4).
                        **(
                            {"weights": str(slots.weights_version)}
                            if slots.weights_version is not None
                            else {}
                        ),
                        # The slots a prefetch of this turn computed (11, 3.5): the same bytes.
                        **({"prefetched": "1"} if slots.prefetched else {}),
                        # A closed-form turn the intent gate served without slots (13, 6.4).
                        **({"intent": "none"} if slots.gated else {}),
                    },
                    **common,
                )
            )
        if notes is not None:
            drafts.append(
                self._reads.draft(
                    caller,
                    ReceiptKind.AGENT_MEMORY,
                    ReceiptSurface.AGENT_BLOCK,
                    item_ids=tuple(f"agent_note:{i}" for i in notes.note_ids),
                    conversation_id=ask.conversation_id,
                    task_id=ask.task_id,
                )
            )
        drafts.extend(self._block_drafts(caller, blocks, common))
        # The receipt leaves before the response, and the answer waits for the queue's confirmation (5.11):
        # the pin, the watermark and the answer itself are made meanwhile, so the wait costs no time of its
        # own. The confirmation is awaited by `Answer.delivered`, before anything is sent.
        receipt = asyncio.create_task(self._reads.emit(drafts))
        try:
            await self._remember(caller, ask, target, served, delta)
        except BaseException:
            await receipt
            raise
        timing.mark("receipt")  # sent; its confirmation is awaited by `Answer.delivered`
        now = self._clock.now()
        text = None if not_modified or pack is None else pack.text
        if text is not None and notes is not None:
            # After the pack's closing tag: the agent's own notes are work instructions, not customer data.
            text = f"{text}\n{notes.text}"
        return Answer(
            ContextResponse(
                not_modified=not_modified,
                text=text,
                variables={} if not_modified or pack is None else pack.variables,
                version=f"g{pack.generation}" if pack else "g0",
                etag=etag,
                manifest_hash=pack.manifest_hash if pack else None,
                as_of=pack.as_of if pack else None,
                lag_seconds=round((now - pack.as_of).total_seconds(), 3) if pack and pack.as_of else None,
                coverage=coverage,
                verification=VerificationResult(
                    requested=level.requested, effective=level.effective, reason=level.reason
                ),
                withheld=(pack.withheld if pack else 0) + live.withheld,
                live=[turn for _, turn in live.turns],
                live_complete=live.complete,
                delta=delta,
                slots=slots.text if slots is not None else None,
                guards=_guards_out(slots.plan) if slots is not None else [],
                cache=_directives(pack) if pack and not not_modified else None,
                timing=timing.stages,
                path=DeliveryPath.NOT_MODIFIED if not_modified else path,
                degraded=served.degraded
                or pack is None
                or self._reads.guard.stale(now)
                or any(read is None for _, read in blocks),
                pack=(
                    _as_data(pack, ask, level.effective, config, slots.plan if slots else None)
                    if ask.format == "json" and pack is not None and not not_modified
                    else None
                ),
                **_block_fields(blocks),
            ),
            receipt,
        )

    async def _remember(
        self, caller: SourcePrincipal, ask: _Ask, target: Target, served: _Served, delta: str | None
    ) -> None:
        """Pin the served bytes for the rest of the conversation and move the agent's watermark (5.11), in
        one round trip on most turns: a pin the cache already holds only has its expiry renewed, and the
        watermarks move only when the pack does."""
        pack = served.pack
        if pack is None:
            return
        space_id, profile_id = caller.space.space_id, target.profile.profile_id
        try:
            seen = served.latest if delta and served.latest is not None else pack
            thread = ask.thread or ""
            epoch = self._epoch.value
            mark = (seen.generation, seen.manifest_hash)
            keys = [
                watermark_key(epoch, space_id, profile_id, caller.source_id, thread),
                latest_mark_key(epoch, space_id, profile_id, caller.source_id),
            ]
            if ask.thread:
                key = pin_key(epoch, space_id, profile_id, ask.thread, ask.view, caller.source_id)
                sealed = None if served.pin_held else await seal_pack(self._keys, space_id, key, pack)
                stored, found = await self._cache.put_and_get_many(key, sealed, PIN_TTL_S, keys)
                if not stored:
                    # Evicted since this read found it: written back, so the conversation keeps its bytes.
                    await self._cache.set(key, await seal_pack(self._keys, space_id, key, pack), PIN_TTL_S)
            else:
                found = await self._cache.get_many(keys)
            for key, raw in zip(keys, found, strict=True):
                current = _mark_of(raw)
                # Only forward, and only when it moves: most turns of a conversation see the same pack.
                if current is None or (current[0] <= seen.generation and current != mark):
                    await self._cache.set(key, encode_mark(*mark), T0_TTL_S)
        except _CACHE_ERRORS:
            log.warning("could not record the pin or the watermark")

    async def _unknown(
        self, caller: SourcePrincipal, ask: _Ask, level: EffectiveVerification, timing: _Timing
    ) -> ContextResponse:
        """A first contact: nobody to describe yet. Still a receipt, never an error."""
        common: dict[str, Any] = {
            "conversation_id": ask.conversation_id,
            "task_id": ask.task_id,
            "verification": level.effective.value,
        }
        blocks = await self._read_blocks(caller, ask, None) if ask.include else []
        await self._reads.emit(
            [
                self._reads.draft(
                    caller,
                    ReceiptKind.READ,
                    ReceiptSurface.PACK,
                    extra={"path": "t4"},
                    detail=timing.server_ms(),
                    **common,
                ),
                *self._block_drafts(caller, blocks, common),
            ]
        )
        timing.mark("receipt")
        return ContextResponse(
            version="g0",
            etag="",
            path=DeliveryPath.T4,
            timing=timing.stages,
            verification=VerificationResult(
                requested=level.requested, effective=level.effective, reason=level.reason
            ),
            degraded=any(read is None for _, read in blocks),
            **_block_fields(blocks),
        )

    async def _holdout(
        self,
        caller: SourcePrincipal,
        ask: _Ask,
        target: Target,
        level: EffectiveVerification,
        assignment: Assignment,
        shadow: CompiledPack | None,
        timing: _Timing,
        arms: Sequence[Arm] = (),
    ) -> ContextResponse:
        """Control group or shadow mode: nothing delivered, the would-be manifest recorded (19.7). The blocks
        of `include` carry what memory knows, so they stay out too."""
        await self._reads.emit(
            [
                self._reads.draft(
                    caller,
                    ReceiptKind.HOLDOUT,
                    ReceiptSurface.PACK,
                    profile_id=target.profile.profile_id,
                    conversation_id=ask.conversation_id,
                    task_id=ask.task_id,
                    verification=level.effective.value,
                    experiment_group=assignment.label,
                    item_ids=shadow.item_ids if shadow else (),
                    pack_hash=shadow.pack_hash if shadow else None,
                    manifest_hash=shadow.manifest_hash if shadow else None,
                    policy_version=shadow.policy_version if shadow else None,
                    withheld=shadow.withheld if shadow else 0,
                    extra={"arms": arms_text(arms)} if arms else {},
                    detail=timing.server_ms(),
                )
            ]
        )
        timing.mark("receipt")
        return ContextResponse(
            text="",
            version="g0",
            etag="",
            path=DeliveryPath.HOLDOUT,
            timing=timing.stages,
            verification=VerificationResult(
                requested=level.requested, effective=level.effective, reason=level.reason
            ),
        )

    # Navigation (16).

    async def search(self, caller: SourcePrincipal, request: SearchRequest) -> SearchResponse:
        return await self._history.search(caller, request)

    async def timeline(self, caller: SourcePrincipal, request: TimelineRequest) -> TimelineResponse:
        return await self._history.timeline(caller, request)

    async def timeline_for_profile(
        self,
        caller: SourcePrincipal,
        profile_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 20,
        verification: Verification = Verification.V0,
        conversation_id: str | None = None,
    ) -> TimelineResponse:
        return await self._history.timeline_for_profile(
            caller,
            profile_id,
            cursor=cursor,
            limit=limit,
            verification=verification,
            conversation_id=conversation_id,
        )

    async def open_item(
        self,
        caller: SourcePrincipal,
        item_id: str,
        *,
        verification: Verification = Verification.V0,
        conversation_id: str | None = None,
        profile_id: UUID | None = None,
        subject: Handle | None = None,
        about: Handle | None = None,
    ) -> OpenedItem:
        return await self._history.open_item(
            caller,
            item_id,
            verification=verification,
            conversation_id=conversation_id,
            profile_id=profile_id,
            subject=subject,
            about=about,
        )

    async def can_read(
        self,
        caller: SourcePrincipal,
        subject: Handle,
        item_id: str,
        *,
        verification: Verification = Verification.V0,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> bool:
        return await self._history.can_read(
            caller,
            subject,
            item_id,
            verification=verification,
            conversation_id=conversation_id,
            task_id=task_id,
        )

    def tools_definitions(self, caller: SourcePrincipal) -> list[ToolDefinition]:
        return self._history.tools_definitions(caller)

    def history_label(self, caller: SourcePrincipal) -> str:
        return self._history.history_label(caller)

    # Subject tokens (5.17).

    def mint_subject_token(
        self, caller: SourcePrincipal, request: SubjectTokenRequest
    ) -> SubjectTokenResponse:
        self._reads.require(caller, Scope.CONTEXT)
        if self._tokens is None:
            raise Unavailable("subject tokens are not configured in this cell")
        # The token carries at most what the source may claim; the proof is still checked at every read.
        level = effective_verification(
            request.verification,
            ceiling=caller.verification_ceiling,
            proven=request.verification,
            audience=caller.audience,
        )
        if level.reason == "source_ceiling":
            raise VerificationNotAllowed("the level is above the source's ceiling")
        expires_at = self._clock.now() + TOKEN_TTL
        subject, about = request.subject, request.about
        token = self._tokens.mint(
            SubjectClaims(
                space_id=caller.space.space_id,
                source_id=caller.source_id,
                subject=SubjectRef(subject.type, subject.value, subject.scope, subject.subject_kind),
                level=level.effective,
                expires_at=expires_at,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                about=SubjectRef(about.type, about.value, about.scope, about.subject_kind) if about else None,
            )
        )
        return SubjectTokenResponse(token=token, expires_at=expires_at)

    def subject_claims(self, caller: SourcePrincipal, token: str) -> SubjectClaims:
        """The customer an MCP connection is bound to; a token of another source or space is refused."""
        if self._tokens is None:
            raise Unavailable("subject tokens are not configured in this cell")
        claims = self._tokens.verify(token, now=self._clock.now())
        if claims.space_id != caller.space.space_id or claims.source_id != caller.source_id:
            raise Forbidden("the subject token belongs to another source")
        return claims

    async def emit_receipt(
        self,
        principal: SourcePrincipal,
        *,
        kind: ReceiptKind,
        surface: ReceiptSurface,
        profile_id: UUID | None = None,
        object_id: UUID | None = None,
        item_ids: Sequence[str] = (),
    ) -> None:
        """For other read surfaces of this process (business objects, 18.3): same sequence and buffer."""
        await self._reads.ensure_can_record()
        await self._reads.emit(
            [
                self._reads.draft(
                    principal,
                    kind,
                    surface,
                    profile_id=profile_id,
                    object_id=object_id,
                    item_ids=tuple(item_ids),
                )
            ]
        )

    async def guarded(self, space_id: UUID) -> PrivacyGuard:
        """For the other read surfaces of this process that keep copies of their own (typed state, 24.3): the
        privacy feed followed for `space_id`, as a context read follows it, and the guard it keeps (06, 3)."""
        await self._reads.follow_feeds(space_id)
        return self._reads.guard

    # Background duties of the read process.

    async def refresh_privacy(self) -> None:
        await self._reads.refresh_privacy()

    async def refresh_cache_epoch(self) -> int:
        """Called every 5 s: a failover bumps the epoch and every derived key goes invisible (06, 3)."""
        async with self._uow.cell() as uow:
            self._epoch.value = await uow.config.cache_epoch()
        return self._epoch.value

    async def _hot_layer_degraded(self) -> bool:
        """The hot layer's health mark lives in the cell database; a read of the live turns runs in the
        space's, so the mark is kept for a second rather than read per request. Past it, the last mark
        serves while one background read renews it: only the process's first read waits for one."""
        if self._hot_health is None:
            async with self._health_lock:
                if self._hot_health is None:
                    await self._renew_hot_health()
        elif self._clock.now() - self._hot_health[0] >= HEALTH_TTL:
            self._reads.later("hot_health", self._renew_hot_health())
        assert self._hot_health is not None
        return self._hot_health[1]

    async def _renew_hot_health(self) -> None:
        async with self._uow.cell() as uow:
            self._hot_health = (self._clock.now(), await uow.health.is_degraded(HOT_LAYER))

    async def drain(self) -> None:
        """Waits for the work reads started in the background: renewed settings, polls (tests)."""
        await self._reads.drain()

    async def flush_receipts(self) -> bool:
        return await self._reads.flush()

    async def record_denial(self, caller: SourcePrincipal, surface: ReceiptSurface, reason: str) -> None:
        """A read the policy refused (5.13): who tried, on which surface and why; never what was asked."""
        draft = self._reads.draft(caller, ReceiptKind.DENIED, surface, detail={"reason": reason})
        await self._reads.emit([draft])

    async def heartbeat(self, *, final: bool = False) -> None:
        await self._reads.heartbeat(final=final)


def _block_fields(blocks: Sequence[tuple[ContextBlock, BlockRead | None]]) -> dict[str, Any]:
    return {block.name: read.value for block, read in blocks if read is not None}


def _prepared_key(caller: SourcePrincipal, ask: _Ask, target: Target) -> tuple[Any, ...]:
    return (caller.space.space_id, caller.source_id, ask.thread, ask.view, target.profile.profile_id)


def _evidence(pack: CompiledPack) -> frozenset[str]:
    """The events behind every item the pack holds or held back (actions, open items, facts): a turn that is
    one of them is in memory already, whatever conversation it belongs to."""
    entries = [*pack.manifest.get("items", []), *pack.manifest.get("excluded", [])]
    entries += [item.refs for item in pack.reserve]
    return frozenset(str(e) for entry in entries for e in entry.get("evidence_event_ids", ()))


def _live_turn(line: LiveLine, decision: Decision, fmt: Formatter) -> LiveTurn:
    raw = line.doc
    text = raw.get("text") or raw.get("canonical_type") or raw.get("operation") or ""
    if raw.get("text_cut"):
        # The cache keeps the turn's start; the whole turn is in the history.
        text = f"{text}… ({fmt.word('continues')})"
    return LiveTurn(
        at=datetime.fromisoformat(raw["occurred_at"]),
        channel=raw["channel"],
        kind=EventKind(raw["kind"]),
        speaker=raw["speaker"],
        text=fmt.word("masked") if decision.effect is Effect.MASK else text,
        source_id=raw["source_id"],
    )


def _occurred(line: LiveLine) -> datetime:
    return datetime.fromisoformat(line.doc["occurred_at"])


def _slot_pool(
    body: CompiledPack,
    latest: CompiledPack,
    fmt: Formatter,
    allowed: Callable[[dict[str, Any]], bool],
    *,
    delta: bool,
) -> list[SlotItem]:
    """What the slot channels read: the pinned body's lines (seen, so never slotted again), the latest
    pack's lines the pin does not carry (seen too when `delta` renders them), and the latest pack's
    reserve that passes the policy again."""
    pool: dict[str, SlotItem] = {}
    # A conversation of the body keeps its date and channel, so "the latest on WhatsApp" can be the body's.
    episodes = {e.item_id: e for e in latest.episode_index}

    def add_lines(pack: CompiledPack, seen: bool) -> None:
        for line in pack.lines:
            if line.item_id is None or line.item_id in pool:
                continue
            text = unlabeled(line.text, fmt.section(line.section))
            tokens = estimate_tokens(line.text + "\n")
            kind = line.item_id.partition(":")[0]
            episode = episodes.get(line.item_id)
            pool[line.item_id] = SlotItem(
                line.item_id,
                kind,
                text,
                line.text,
                tokens,
                at=episode.started_at if episode else None,
                channel=episode.channel if episode else None,
                category=episode.category if episode else None,
                in_body=seen,
            )

    add_lines(body, True)
    if latest is not body:
        add_lines(latest, delta)
    for item in latest.reserve:
        if not allowed(item.refs):
            continue
        if (known := pool.get(item.item_id)) is not None:
            if known.in_body and known.text != unlabeled(item.line, fmt.section(item.section)):
                # The body pins this item shorter (a voice headline, a compacted line): the slots read
                # the whole line, and may bring what the headline left out (tpl-12).
                pool[item.item_id] = replace(
                    known,
                    text=unlabeled(item.line, fmt.section(item.section)),
                    line=item.line,
                    tokens=item.tokens,
                    in_body=False,
                    headline=True,
                )
            continue
        pool[item.item_id] = SlotItem(
            item.item_id,
            str(item.refs["kind"]),
            unlabeled(item.line, fmt.section(item.section)),
            item.line,
            item.tokens,
            at=item.at,
            channel=item.channel,
            category=item.refs.get("category"),
            object_id=UUID(item.refs["object_id"]) if item.refs.get("object_id") else None,
        )
    return list(pool.values())


def _load(body: bytes, generation: int) -> CompiledPack:
    return replace(CompiledPack.from_bytes(body), generation=generation)


def _protected(pack: CompiledPack | None) -> bool:
    """A company promise already overdue takes the customer out of the control group (19.7)."""
    return pack is not None and int(pack.manifest["flags"]["overdue_company_items"]) > 0


def _for_level(pack: CompiledPack) -> int:
    """What a pack kept from its reader for the level: the items a verification of the customer would
    release, never those kept for a purpose the reader lacks."""
    return int((pack.manifest.get("withheld") or {}).get(ExclusionReason.VERIFICATION.value, 0))


def _withheld_episodes(pack: CompiledPack) -> frozenset[str]:
    """The conversations a pack kept from its reader for the level (`episode:<id>`): what a verification of
    the customer would bring back."""
    return frozenset(
        item_key(e["kind"], e["id"])
        for e in pack.manifest.get("excluded", [])
        if e.get("kind") == "episode" and e.get("reason") == ExclusionReason.VERIFICATION.value
    )


def _unknown(turn: TurnAnalysis) -> tuple[tuple[str, str], ...]:
    """Every kind and number the turn asks for, as not seen: what a skipped lookup leaves unknown."""
    return (*((t, "") for t in turn.asked_types), *(("", n) for n in turn.identifiers))


def _attrs(item: dict[str, Any]) -> ItemAttrs:
    return ItemAttrs(
        kind=item["kind"],
        category=item["category"],
        sensitivity=Sensitivity(item["sensitivity"]),
        visibility=Visibility(item["visibility"]),
        subject_kind=SubjectKind(item["subject_kind"]),
        handle_ids=tuple(UUID(h) for h in item["handles"]),
        other_contact=bool(item["other_contact"]),
        purposes=frozenset(item["purposes"]),
        topics=tuple(item["topics"]),
        audiences=frozenset(Audience(a) for a in item["audiences"]),
        spoken_by=SpokenBy(item["spoken_by"]) if item.get("spoken_by") else None,
        own_words=bool(item.get("own_words")),
    )


def _directives(pack: CompiledPack) -> CacheDirectives:
    return CacheDirectives(
        breakpoints=list(pack.cache.breakpoints),
        ttl_seconds=pack.cache.ttl_seconds,
        floor_tokens=pack.cache.floor,
        cacheable=pack.cache.cacheable,
        salt=pack.cache.salt,
    )


def _generation_of(raw: bytes | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _mark_of(raw: bytes | None) -> tuple[int, str] | None:
    try:
        return decode_mark(raw) if raw is not None else None
    except (ValueError, KeyError):
        log.warning("unreadable watermark ignored")
        return None


def _as_data(
    pack: CompiledPack, ask: _Ask, level: Verification, config: SpaceConfig, plan: SlotPlan | None = None
) -> ContextPack:
    """The pack's own lines, grouped by section with the label taken off: the same content as `text`, so
    the two never disagree, and no second compilation."""
    settings = config.settings
    fmt = Formatter(settings.timezone, settings.locale)
    sections: list[PackSection] = []
    for line in pack.lines:
        label = fmt.section(line.section)
        body = unlabeled(line.text, label)
        if sections and sections[-1].name == line.section.value:
            if sections[-1].lines[-1] != body:  # the customer line repeats once per fragment it holds
                sections[-1].lines.append(body)
            continue
        sections.append(
            PackSection(name=line.section.value, label=label, layer=_LAYER[line.section.layer], lines=[body])
        )
    return ContextPack(
        view=ask.view,
        verification=level,
        withheld=pack.withheld,
        as_of=pack.as_of,
        preamble=pack.variables.get("b0", settings.b0_text),
        sections=sections,
        variables={k: v for k, v in pack.variables.items() if k not in _SECTION_NAMES},
        stamp=PackStamp(
            etag=pack.etag, version=f"g{pack.generation}", as_of=pack.as_of, manifest_hash=pack.manifest_hash
        ),
        slots=_slot_lines(plan, explain=ask.explain) if plan is not None else [],
    )


def _slot_lines(plan: SlotPlan, *, explain: bool = False) -> list[PackSlot]:
    derived = [
        PackSlot(
            section=GUARD_KIND if d.kind == GUARD_KIND else "derived",
            derived=None if d.kind == GUARD_KIND else d.kind,
            id=_guard_short_id(d),
            text=d.line,
            why=_derived_why(d, plan) if explain else None,
        )
        for d in plan.derived
    ]
    items = [
        PackSlot(
            section=_section_of(s.item.item_id),
            id=short_id(public_item_id(s.item.item_id)),
            channels=list(s.channels),
            text=s.item.line,
            why=_slot_why(s, plan) if explain else None,
        )
        for s in plan.slots
    ]
    return derived + items


def _guard_short_id(derived: Derived) -> str | None:
    """A guard line carries the short id of the value it states; other derived lines state no item."""
    basis = dict(derived.basis).get("basis") if derived.kind == GUARD_KIND else None
    return short_id(public_item_id(basis)) if isinstance(basis, str) else None


def _guards_out(plan: SlotPlan) -> list[PackGuard]:
    """What the guard lines state, typed, so the SDK checks the agent's answer against them at once
    (13, 6.1). Never for the control group, whose lines were never shown."""
    if plan.guard_arm == CONTROL_ARM:
        return []
    return [PackGuard(id=g.short_id, value_type=g.type.value, value=g.raw) for g in plan.guards]


def _slot_why(slot: Slot, plan: SlotPlan) -> SlotWhy:
    """The receipt's `why` of one slot, typed: the same numbers, rounded the same way."""
    why = slot.why.to_json() if slot.why is not None else {}
    return SlotWhy(
        item_id=public_item_id(slot.item.item_id),
        score=why.get("score"),
        channels=[SlotChannelRank(channel=name, **rank) for name, rank in why.get("channels", {}).items()],
        weights_version=plan.weights_version,
        excerpt=bool(why.get("excerpt")),
    )


def _derived_why(derived: Derived, plan: SlotPlan) -> SlotWhy:
    basis = {k: v for k, v in derived.why().items() if k not in ("kind", "rule")}
    if isinstance(basis.get("basis"), str):
        basis["basis"] = public_item_id(basis["basis"])
    return SlotWhy(rule=derived.rule, basis=basis, weights_version=plan.weights_version)


def _section_of(item_id: str) -> str:
    kind = item_id.partition(":")[0]
    return {
        "episode": "episodes",
        "object": "objects",
        "action": "actions",
        "fact": "facts",
        "open_item": "pending",
        "trait": "volatile_patterns",
        "summary": "summary",
        "attr": "customer",
    }.get(kind, kind)


_SECTION_NAMES = frozenset({"b0", *(s.value for s in Section)})
_LAYER = {Layer.ACCOUNT: "account", Layer.B1: "stable", Layer.B2: "volatile"}


def _in_conversation(caller: SourcePrincipal, ask: _Ask, where: "_Where") -> tuple[SourcePrincipal, _Ask]:
    """Who the conversation is with decides what an internal key reads in it: staff named by
    a system id keeps the key internal, anyone else, or a read by profile or object, narrows it to the
    customer-facing view. Narrowed, it asks what the conversation proved, never `no_customer`. A task (a
    `task:` view, or a task id) is internal, as the key is, whatever thread it runs in: the drafting task on
    a case read with the customer view lost the lawyer's internal word (e2e, 07/10/2026)."""
    if is_task(ask.view, ask.task_id):
        return caller, ask
    kind = claimed_kind(where.subject.type, where.subject.subject_kind) if where.subject else None
    narrowed = in_conversation(caller, kind, ask.conversation_id)
    if narrowed is caller:
        return caller, ask
    if ask.verification is Verification.NO_CUSTOMER:
        ask = replace(ask, verification=Verification.V0)
    return narrowed, ask
