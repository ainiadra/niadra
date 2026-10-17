"""Ownership and effects (24.3.4): the check, declarations, claims by declaration, effects exactly once,
contact and paid budgets with admission, the contact token and its public keys, the suppression list, the
batch writer of the durable record, and the reload of the state store.

The check reads no table: the state store decides (the scripts of `adapters/outbound/redis/coordinate.py`),
what it reserved (a budget's unit, an effect's attempt, a contact) goes to the confirmed queue before the
answer, and the batch writer stores it with one statement per batch. When a dependency is down the check
answers the declared default of its purpose (L23): the customer's own message always goes on, a purpose that
fails closed and any effect's key wait, the rest go on unchecked. A subject is keyed by its handle's keyed
hash, so nothing on the path resolves an identity; the channel's service window reads the customer's last
message from the hot layer, under the handle ids the handle cache holds.
"""

import contextlib
import dataclasses
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, TypeVar
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ValidationError

from niadra.app.edge import source_principal
from niadra.app.raw_index import sealed_tokens
from niadra.app.serve import BlockAsk, BlockRead
from niadra.app.serve_reads import Target
from niadra.config.coordination import (
    BudgetSlice,
    ChannelRule,
    CoordinationSettings,
    Gateway,
    PaidBudget,
    PurposeRule,
    ReportedOutcome,
)
from niadra.config.models import SpaceConfig
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import ContextRequest, Include
from niadra.contracts.coordinate import (
    ANY_PURPOSE,
    CaseClosed,
    CaseOpened,
    ChannelState,
    CheckBatchRequest,
    CheckBatchResponse,
    CheckRequest,
    CheckResult,
    ClaimRequest,
    CommitmentDecided,
    CommitmentMade,
    CommitmentRef,
    CommitmentWithdrawn,
    ConflictCounts,
    ContactBudget,
    ContactBudgetUse,
    ContactCount,
    ContactKey,
    ContactKeys,
    ContactMade,
    ContactTokenClaims,
    CoordinationBlock,
    CoordinationOverview,
    CoordinationReport,
    CoordinationReportPage,
    Declaration,
    DeclareRequest,
    DeclareResult,
    Effect,
    EffectCount,
    EffectDeclared,
    EffectReserve,
    EffectSeen,
    EffectSettle,
    EffectStatus,
    Handoff,
    HandoffContext,
    HandoffCreate,
    HandoffDeclared,
    HandoffOutcome,
    HandoffPackage,
    LeaseDeclared,
    Lock,
    OpenObject,
    OutcomeRequest,
    Owner,
    OwnershipClaim,
    OwnershipCount,
    PromiseRef,
    ShadowRequest,
    ShadowRun,
    Suppression,
    SuppressionAdded,
    SuppressionAddedDetail,
    SuppressionLifted,
    SuppressionPage,
    SuppressionSalt,
    TaskLockDeclared,
    terms_text,
)
from niadra.contracts.events import ActionInfo, EventItem, InteractionItem, SpeakerRef
from niadra.contracts.state import StateView
from niadra.contracts.window import ContactWindow
from niadra.domain.common.errors import (
    Conflict,
    CoordinationUnavailable,
    EffectInFlight,
    InvalidInput,
    LeaseHeld,
    NiadraError,
    NotFound,
    TaskLocked,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal, SourcePrincipal, owns_secret
from niadra.domain.compile.handoff import HandoffLines, Holder, render
from niadra.domain.compile.templates import Formatter
from niadra.domain.coordinate.decide import (
    REBUILD_CLOSED_S,
    Cause,
    ChannelView,
    channel_view,
    fallback,
    quiet_until,
)
from niadra.domain.coordinate.destination import (
    SUBJECT_HMAC,
    b64url,
    canonical_destination,
    subject_canonical,
    suppression_key,
    unb64url,
)
from niadra.domain.coordinate.model import (
    Claim,
    ClaimKind,
    ClaimResult,
    ClaimVia,
    Decision,
    EffectKind,
    EffectSnapshot,
    EffectState,
    FailDirection,
    Settler,
    StoreLife,
)
from niadra.domain.coordinate.records import (
    CommitmentRecord,
    CommitmentStatusRecord,
    ContactRecord,
    CoordinationRecord,
    EffectRecord,
    LiftRecord,
    OwnershipRecord,
    ReleaseRecord,
    SpendRecord,
    SuppressionRecord,
    about,
    by_space,
    unfenced,
)
from niadra.domain.coordinate.shadow import Message as ShadowMessage
from niadra.domain.coordinate.shadow import Policy as ShadowPolicy
from niadra.domain.coordinate.shadow import run as run_shadow
from niadra.domain.coordinate.token import TokenClaims, issue, key_id, public_x, recipient, signing_key
from niadra.domain.coordinate.window import Window, windows_until
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.ingest.hot import (
    HANDLE_CACHE_PURPOSE,
    cached_handle,
    handle_cache_input,
    handle_cache_name,
    hot_aad,
    unpack_envelope,
)
from niadra.domain.memory.items import OPEN_STATUSES
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import (
    COORDINATION_MENTIONS_READY,
    MENTION_PAGE,
    MentionKey,
    MentionStore,
    Redacted,
)
from niadra.domain.privacy.raw_index import body_tokens, redact_value
from niadra.domain.records import EventRecord, HandleRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    HANDOFF_OUTCOME,
    EventKind,
    Feature,
    HandleType,
    ReceiptSurface,
    Scope,
    Speaker,
)
from niadra.ports.admission import Outcome, PaidReservation
from niadra.ports.coordination import (
    BudgetSpend,
    ClaimSlot,
    CommitmentEntry,
    ContextReader,
    CoordinationFeed,
    CoordinationLog,
    CoordinationStore,
    NoSystemEvents,
    SystemEvents,
)
from niadra.ports.erasure import ErasureMarks
from niadra.ports.infra import (
    CacheUnavailable,
    Clock,
    ConfigProvider,
    HotLayer,
    KeyValueCache,
    KeyVault,
    SecretStore,
)
from niadra.ports.repos.coordinate import HandoffRow, SealedOutcome, SealedTerms, SuppressionRow, TargetState
from niadra.ports.signals import InteractionSink, NoInteractions
from niadra.ports.state import WorkingSet
from niadra.ports.turns import AppliedTurn
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

PUBLISH_TIMEOUT_S = 0.15
"""How long a reservation waits for the broker's confirmation before it is undone (24.4.1: the Lua and a
confirmed publish within 30 ms at p95; this is the ceiling, under the SDK's 200 ms)."""
WRITER_BATCH = 500
IDEMPOTENCY_TTL_MS = 10 * 60 * 1000
IDEMPOTENCY_PENDING_MS = 60 * 1000
REBUILD_LEASE_MS = 60 * 1000
REBUILD_PAGE = 500
SALT_VERSION = 1
SALT_SINCE = datetime(2026, 9, 29, tzinfo=UTC)
"""Salts are derived per source and version, so a version holds from the day the list opened."""
GATEWAY_KEY_TTL_S = 300.0
"""How long a process keeps a gateway's shared key after reading it from the vault: the check then reads no
table, and a rotated key reaches every process within five minutes."""
HOT_NEWEST = 20
"""How many of each handle's newest turns the service window looks through for the customer's last message."""
FORCED_LIMIT = 2**31 - 1
"""A contact that went out without a decision still counts in its budget's window, over the limit or not."""

COORDINATION_CHANNEL = "coordination"
"""The channel of the actions coordination records in a subject's memory, a commitment and a handoff's
outcome. They act on objects of `COORDINATION_NAMESPACE` (`commitment`, `handoff`): an action reaches the
subject's context through the object it names."""
HANDOFF_EFFECTS = 20
MAX_OPEN_OBJECTS = 50
"""What a handoff package carries of the subject's objects (the coordination spec, 7)."""
PURGE_PAGE = 500
ERASURE_FENCE_S = 3600
"""How long an erasure fences off its subject's older records: the writer lingers half a second and a failed
batch waits in the queue for minutes, never an hour."""
REPORTED_KEY = "reported_by_event"
"""The key id a reported outcome acts under: no key made the call, the source's system event did."""
SHADOW_PAGE = 500

OBJECT_HMAC = "coordination_object"
EFFECT_HMAC = "coordination_effect"
SALT_HMAC = "suppression_salt"
STATED_PREFERENCE = "stated_preference"
"""The reason of a contact window a person said ("não liguem antes das 9h"), which Niadra suppresses for
them: no coordination document needs to declare it (zero touch)."""
STATED_SOURCE = UUID("00000000-0000-7000-8000-000000000001")
DESTINATIONS = frozenset({HandleType.PHONE_E164, HandleType.WA_ID, HandleType.WA_JID, HandleType.EMAIL})
"""The handles a contact goes to, which the suppression list keys (the suppression-list spec, 3)."""
"""The source a window a person said is suppressed under: one per purpose and channel, so the person's
next word on the same channel replaces it, and no vendor's source lifts it."""
CONTACT_KEY_HMAC = "contact_token_key"
"""The space's contact token key is derived from its vault's keyed-hash key, per generation: never stored, the
same in every process, and no statement to find it."""
SPACE_TAG = "space"

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _Subject:
    tag: str
    hmac: bytes
    canonical: str
    handle_type: str


@dataclass(frozen=True, slots=True)
class _Target:
    """A subject, an object or the space in the state store: `tag` names its keys, `raw` its row in the
    record."""

    tag: str
    raw: bytes
    kind: str = "subject"

    @property
    def is_object(self) -> bool:
        return self.kind == "object"


@dataclass(frozen=True, slots=True)
class _Contact:
    """What a check reserved on the subject's side, until the queue confirms it."""

    budget: str | None
    slice: str | None
    window_ms: int
    gateway: Gateway | None


@dataclass(frozen=True, slots=True)
class _Hold:
    """One claim as the state store takes it: the slot names its source (`d:<source>:<holder>` for a
    declaration, `o:<ownership source>` for an observation, `t:<task>` for a task lock)."""

    kind: ClaimKind
    holder: str
    level: str
    rank: int
    slot: str
    via: ClaimVia
    since_ms: int
    until_ms: int
    intents: tuple[str, ...] = ()
    task: str | None = None
    holder_kind: str = "agent"
    observer: str | None = None
    basis_event_id: UUID | None = None


class CoordinateService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: CoordinationStore,
        log_: CoordinationLog,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        *,
        marks: ErasureMarks,
        feed: CoordinationFeed | None = None,
        secrets: SecretStore | None = None,
        hot: HotLayer | None = None,
        handle_cache: KeyValueCache | None = None,
        views: ContextReader | None = None,
        working_set: WorkingSet | None = None,
        publish_timeout_s: float = PUBLISH_TIMEOUT_S,
    ) -> None:
        """`secrets` holds the keys gateways share for the token's `rcpt`; `hot` and `handle_cache` give the
        channel's service window; `views` compiles a handoff's package. Without them, no token goes out, a
        paid template is always required where the channel says so, and no handoff is created. `events` and
        `interactions` take a handoff's outcome and a commitment into the subject's memory and signals; the
        cell wires them once those modules exist. `working_set` takes the shared object a commitment is made
        on into the objects module's working set (24.3.12). `marks` keeps what erasures reached and when,
        shared with the other modules' fences (`ports.erasure`)."""
        self._uow = uow
        self._store = store
        self._erasures = marks
        self._log = log_
        self._keys = keys
        self._clock = clock
        self._config = config
        self._feed = feed
        self._secrets = secrets
        self._hot = hot
        self._handle_cache = handle_cache
        self._publish_timeout_s = publish_timeout_s
        self._gateway_keys: dict[tuple[UUID, str], tuple[bytes, float]] = {}
        self._views = views
        self._working_set = working_set
        self.events: SystemEvents = NoSystemEvents()
        self.interactions: InteractionSink = NoInteractions()

    # The check.

    async def check(self, caller: SourcePrincipal, request: CheckRequest) -> CheckResult:
        settings = self._settings(caller.space.space_id)
        rule = _purpose(settings, request.purpose)
        decision_id = uuid7()
        try:
            return await self._check(caller, request, settings, rule, decision_id)
        except CacheUnavailable:
            log.warning("coordination answered its declared default: the state store is down")
            return self._fallback(request, rule, settings, decision_id, Cause.UNAVAILABLE)

    async def check_batch(self, caller: SourcePrincipal, request: CheckBatchRequest) -> CheckBatchResponse:
        """Each check answered as if asked alone, in order (the coordination spec, 3.7)."""
        return CheckBatchResponse(results=[await self.check(caller, check) for check in request.checks])

    async def _check(
        self,
        caller: SourcePrincipal,
        request: CheckRequest,
        settings: CoordinationSettings,
        rule: PurposeRule,
        decision_id: UUID,
    ) -> CheckResult:
        space_id = caller.space.space_id
        now = self._clock.now()
        now_ms = _ms(now)
        inbound = request.direction == "inbound"
        tokened = rule.token and not inbound
        gateway = _gateway(settings, request) if tokened else None
        gateway_key = await self._gateway_key(space_id, gateway) if gateway is not None else None
        life = await self._store.life(now_ms=now_ms)
        closed_window = not life.whole and now_ms < life.since_ms + REBUILD_CLOSED_S * 1000
        if closed_window and not inbound and rule.fail is FailDirection.CLOSED:
            return self._fallback(request, rule, settings, decision_id, Cause.REBUILDING)

        effect: EffectStatus | None = None
        effect_hash: str | None = None
        if request.effect_key is not None:
            effect_hash = await self._effect_hash(space_id, request.effect_key)
            found = await self._effect(space_id, effect_hash, life, now_ms)
            if found is None and closed_window and not inbound:
                # Its reservation may still be on the queue the durable record is fed from.
                return self._fallback(request, rule, settings, decision_id, Cause.REBUILDING)
            effect = _effect_status(found)
            if effect.state != "none" and not inbound:
                reason = f"effect_{effect.state}"
                return CheckResult(
                    decision="deny",
                    reasons=[reason],
                    effect=effect,
                    decision_id=decision_id,
                    valid_for_s=settings.decision_valid_s,
                )

        blocked_by, blocked_until, blocked_holder = None, None, None
        owner: Claim | None = None
        locks: list[Claim] = []
        if request.object is not None:
            target = await self._object(space_id, request.object)
            await self._ensure(space_id, target, life, settings, now)
            key = self._store.claims_key(space_id, target.tag)
            held = await self._store.owner(key, now_ms=now_ms)
            locks = await self._store.locks(key, now_ms=now_ms)
            locked = next(
                (c for c in locks if c.slot == f"t:{request.task}" and c.holder != request.agent), None
            )
            if held is not None and _blocks(held, request.agent, request.intent):
                blocked_by, blocked_until, blocked_holder = "owner_active", held.until_ms, held.holder
            elif request.task is not None and locked is not None:
                blocked_by, blocked_until, blocked_holder = "lock_held", locked.until_ms, locked.holder
            owner = held

        channel_rule = next((c for c in settings.channels if c.name == request.channel), None)
        window_until_at: datetime | None = None
        commitments: list[CommitmentRef] = []
        reasons: list[str] = []
        holder: str | None = None
        suppressed: tuple[str, ...] = ()
        budget: ContactBudget | None = None
        contact: _Contact | None = None
        quiet_ms = None if inbound else self._quiet_until_ms(caller.space.space_id, channel_rule, now)
        subject: _Subject | None = None
        if request.subject is not None:
            subject = await self._subject(space_id, request.subject)
            await self._ensure(space_id, _Target(subject.tag, subject.hmac), life, settings, now)
            window_end = None
            if not inbound:
                windowed = await self._store.windows(space_id, subject.tag, now_ms=now_ms)
                window_end = windows_until(windowed, request.purpose, request.channel, now)
            limits = rule.budget if not inbound else None
            portion = _slice(limits.slices, request.intent) if limits is not None else None
            window_ms = limits.per_hours * 3_600_000 if limits is not None else 0
            seen = await self._store.check_subject(
                space_id,
                subject.tag,
                str(decision_id),
                direction=request.direction,
                purpose=request.purpose,
                channel=request.channel,
                agent=request.agent,
                intent=request.intent,
                units=1,
                budget=_contact_budget(request.purpose),
                limit=limits.limit if limits is not None else None,
                window_ms=window_ms,
                quiet_until_ms=quiet_ms,
                slice_budget=_contact_budget(request.purpose, portion) if portion is not None else None,
                slice_limit=portion.limit if portion is not None else None,
                spacing_ms=_spacing_ms(portion),
                blocked_by=blocked_by,
                blocked_until_ms=blocked_until,
                blocked_holder=blocked_holder,
                window_until_ms=_ms(window_end) if window_end is not None else None,
                now_ms=now_ms,
            )
            commitments = await self._commitments_of(space_id, subject.tag, now_ms)
            if seen.admission.reason == "contact_window":
                window_until_at = _at(seen.admission.retry_at_ms)
            admission = seen.admission
            decision, holder, suppressed = admission.decision, seen.holder, seen.suppressed
            owner = admission.holder or owner
            if admission.reason:
                reasons.append(admission.reason)
            if limits is not None:
                budget = ContactBudget(
                    remaining=max(admission.remaining, 0),
                    next_allowed_at=_at(admission.retry_at_ms)
                    if admission.reason in ("budget_exhausted", "budget_paced")
                    else None,
                )
            if decision is Decision.ALLOW and not inbound:
                contact = _Contact(
                    _contact_budget(request.purpose) if limits is not None else None,
                    _contact_budget(request.purpose, portion) if portion is not None else None,
                    window_ms,
                    gateway,
                )
        elif blocked_by is not None:
            decision, holder = (Decision.HANDOFF_TO, blocked_holder) if inbound else (Decision.DEFER, None)
            reasons.append(blocked_by)
        elif quiet_ms is not None and quiet_ms > now_ms:
            decision = Decision.DEFER
            reasons.append("quiet_hours")
        else:
            decision = Decision.ALLOW

        view = None
        if channel_rule is not None:
            last_inbound = None
            if inbound:
                last_inbound = now  # the customer's own message opens the window
            elif request.subject is not None and channel_rule.free_form_hours is not None:
                since = now - timedelta(hours=channel_rule.free_form_hours)
                last_inbound = await self._last_inbound(space_id, request.subject, channel_rule.name, since)
            view = channel_view(
                free_form_hours=channel_rule.free_form_hours,
                template_outside_window=channel_rule.template_outside_window,
                last_inbound=last_inbound,
                quiet_until=_at(quiet_ms) if quiet_ms and quiet_ms > now_ms else None,
                now=now,
            )
            if decision is Decision.ALLOW and not inbound and view.template_required:
                reasons.append("template_required")

        records: list[CoordinationRecord] = []
        effect_undo: tuple[str, int] | None = None
        if (
            decision is Decision.ALLOW
            and effect_hash is not None
            and effect is not None
            and effect.state == "none"
        ):
            subject_hmac = subject.hmac if subject is not None else None
            reserved, record = await self._reserve(
                caller, request, settings, effect_hash, subject_hmac, now_ms
            )
            if reserved.state != "none":
                await self._undo(space_id, subject, contact, decision_id)
                if not inbound:
                    return CheckResult(
                        decision="deny",
                        reasons=[f"effect_{reserved.state}"],
                        effect=reserved,
                        decision_id=decision_id,
                        valid_for_s=settings.decision_valid_s,
                    )
            else:
                records.append(record)
                effect_undo = (self._store.effect_key(space_id, effect_hash), record.snapshot.attempt)
            effect = reserved

        token: str | None = None
        if contact is not None and subject is not None:
            if tokened and gateway is not None and gateway_key is not None:
                token = await self._token(
                    space_id, settings, request, decision_id, gateway, gateway_key, subject, now
                )
            records += self._contact_records(caller, request, subject, contact, decision_id, now_ms)
        if records and not await self._log.publish(records, self._publish_timeout_s):
            spent = contact is not None and contact.budget is not None
            if effect_undo is not None or token is not None or spent:
                await self._undo(space_id, subject, contact, decision_id)
                if effect_undo is not None:
                    await self._store.cancel_effect(*effect_undo)
                log.warning("a check's reservation was undone: the queue did not confirm it")
                return self._fallback(request, rule, settings, decision_id, Cause.UNAVAILABLE)
            log.warning("a contact missed the durable record")

        return CheckResult(
            decision=decision.value,
            reasons=reasons,
            holder=holder if decision is Decision.HANDOFF_TO else None,
            owner=_owner(owner),
            effect=effect,
            locks=[_lock(c) for c in locks],
            channel=_channel(view),
            contact_budget={request.purpose: budget} if budget is not None else {},
            suppressions=list(suppressed),
            contact_window_until=window_until_at,
            commitments_active=commitments,
            decision_id=decision_id,
            valid_for_s=settings.decision_valid_s,
            contact_token=token,
        )

    def _fallback(
        self,
        request: CheckRequest,
        rule: PurposeRule,
        settings: CoordinationSettings,
        decision_id: UUID,
        cause: Cause,
    ) -> CheckResult:
        decision, reason = fallback(
            inbound=request.direction == "inbound",
            fails=rule.fail,
            effect_key=request.effect_key is not None,
            cause=cause,
        )
        return CheckResult(
            decision=decision.value,
            reasons=[reason],
            decision_id=decision_id,
            valid_for_s=settings.decision_valid_s,
        )

    async def _reserve(
        self,
        caller: SourcePrincipal,
        request: CheckRequest,
        settings: CoordinationSettings,
        effect_hash: str,
        subject_hmac: bytes | None,
        now_ms: int,
    ) -> tuple[EffectStatus, EffectRecord]:
        """The effect's attempt for this caller, with the record the check publishes; a lapsed lease the call
        found is published on its own."""
        key = self._store.effect_key(caller.space.space_id, effect_hash)
        attempt = await self._store.reserve_effect(
            key,
            kind=request.effect_kind.value,
            source=str(caller.source_id),
            agent=request.agent,
            fact=_fact(request.object),
            lease_ms=settings.effect_lease_s * 1000,
            ttl_ms=_effect_ttl_ms(settings, request.effect_kind),
            now_ms=now_ms,
        )
        record = EffectRecord(
            caller.space.space_id, unb64url(effect_hash), attempt.snapshot, caller.source_id, subject_hmac
        )
        if attempt.outcome != "reserved":
            if attempt.lapsed:
                await self._publish_quietly([record])
            return EffectStatus(state=_check_state(attempt.outcome), attempt=attempt.attempt), record
        return EffectStatus(state="none", attempt=attempt.attempt), record

    async def _undo(
        self, space_id: UUID, subject: "_Subject | None", contact: _Contact | None, decision_id: UUID
    ) -> None:
        """Gives back the budget units a check spent, when the rest of it cannot go on."""
        if subject is None or contact is None:
            return
        for budget in (contact.budget, contact.slice):
            if budget is not None:
                await self._store.settle_units(space_id, subject.tag, budget, str(decision_id), None)

    def _contact_records(
        self,
        caller: SourcePrincipal,
        request: CheckRequest,
        subject: "_Subject",
        contact: _Contact,
        decision_id: UUID,
        now_ms: int,
    ) -> list[CoordinationRecord]:
        """The contact the check allowed, reserved under its decision id (the token's `jti`), and the budget
        unit it spent, which reloads the budget after a restart of the state store."""
        space_id = caller.space.space_id
        records: list[CoordinationRecord] = []
        if request.channel is not None:
            records.append(
                ContactRecord(
                    space_id,
                    decision_id,
                    subject.hmac,
                    request.purpose,
                    request.channel,
                    caller.source_id,
                    request.agent,
                    contact.gateway.gateway_id if contact.gateway is not None else None,
                    decision_id,
                    "reserved",
                    reserved_at_ms=now_ms,
                )
            )
        if contact.budget is not None:
            records.append(
                SpendRecord(
                    space_id,
                    decision_id,
                    contact.budget,
                    contact.slice,
                    "subject",
                    subject.hmac,
                    1,
                    now_ms,
                    now_ms + contact.window_ms,
                )
            )
        return records

    async def _token(
        self,
        space_id: UUID,
        settings: CoordinationSettings,
        request: CheckRequest,
        decision_id: UUID,
        gateway: Gateway,
        gateway_key: bytes,
        subject: "_Subject",
        now: datetime,
    ) -> str:
        """The contact token of an allowed outbound contact (the contact-token spec): its `jti` is the
        decision's id, and `rcpt` the destination keyed with the gateway's key, unless the caller gave it."""
        if request.destination_hash is not None:
            rcpt = request.destination_hash
        elif subject.handle_type in ("phone", "email"):
            rcpt = recipient(gateway_key, subject.canonical)
        else:
            raise InvalidInput("a contact token needs a phone or e-mail subject, or `destination_hash`")
        assert request.channel is not None, "a gateway is found only for a check with a channel"
        key = await self._signing(space_id, settings.contact_keys.generation)
        iat = int(now.timestamp())
        claims = TokenClaims(
            kid=key_id(public_x(key)),
            space=str(space_id),
            jti=str(decision_id),
            purpose=request.purpose,
            channel=request.channel,
            rcpt=rcpt,
            gateway=gateway.gateway_id,
            iat=iat,
            exp=iat + settings.token_ttl_s,
        )
        # Every token is issued in the published format: a payload the contract refuses is never signed.
        ContactTokenClaims.model_validate(asdict(claims))
        return issue(claims, key)

    # Claims: declared, observed, and task locks.

    async def claim(
        self, caller: SourcePrincipal, request: ClaimRequest, idempotency_key: str
    ) -> OwnershipClaim:
        return await self._once(
            caller, "claim", idempotency_key, OwnershipClaim, lambda: self._claim(caller, request)
        )

    async def _claim(self, caller: SourcePrincipal, request: ClaimRequest) -> OwnershipClaim:
        if request.source is not None:
            return await self._observed(caller, request, request.source)
        return await self._declare_claim(
            caller, request.subject, request.object, request.kind, request.holder, request.level,
            request.lease_s, request.intents, task=request.task,
        )  # fmt: skip

    async def _declare_claim(
        self,
        caller: SourcePrincipal,
        subject: Handle | None,
        object_: ObjectRef | None,
        kind: ClaimKind,
        holder: str,
        level: str | None,
        lease_s: int,
        intents: Sequence[str],
        *,
        task: str | None = None,
    ) -> OwnershipClaim:
        """A lease or a case refused while another holder's claim is at least as restrictive (409
        `lease_held`); a task lock refused while another holder has the task (409 `task_locked`)."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        levels = settings.ownership.levels
        chosen = level or levels[-1]
        if chosen not in levels:
            raise InvalidInput(_unknown("ownership level", chosen, levels))
        locked = kind is ClaimKind.TASK_LOCK
        if locked and (task is None or object_ is None):
            raise InvalidInput("a task lock names its task and the object it is on")
        now_ms = _ms(self._clock.now())
        target = await self._target(space_id, subject, object_)
        hold = _Hold(
            kind=kind,
            holder=holder,
            level=chosen,
            rank=levels.index(chosen),
            slot=f"t:{task}" if locked else f"d:{caller.source_id}:{holder}",
            via=ClaimVia.DECLARATION,
            since_ms=now_ms,
            until_ms=now_ms + min(lease_s, settings.ownership.declaration_max_s) * 1000,
            intents=tuple(intents),
            task=task,
        )
        result, claim_id = await self._hold(space_id, caller.source_id, target, hold, now_ms)
        if not result.accepted:
            held = result.owner
            if held is None:
                raise InvalidInput("the lease ends before it starts")
            until = f"{_at(held.until_ms):%Y-%m-%dT%H:%M:%SZ}"
            if locked:
                raise TaskLocked(f"{task} is locked by {held.holder} until {until}")
            raise LeaseHeld(f"held by {held.holder} until {until}")
        return _claimed(claim_id, hold, result.epoch)

    async def _observed(self, caller: SourcePrincipal, request: ClaimRequest, name: str) -> OwnershipClaim:
        """What the company's worker read of a source (a middleware's pause flag): never refused, it replaces
        what the source said before, and the most restrictive valid claim still decides who holds."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        source = next((s for s in settings.ownership.sources if s.name == name and s.via == "worker"), None)
        if source is None:
            raise InvalidInput("not an ownership source the worker reports for")
        levels = settings.ownership.levels
        chosen = request.level or levels[-1]
        if chosen not in source.levels:
            raise InvalidInput(f"the source {name} may not state the level {chosen}")
        if request.kind is ClaimKind.TASK_LOCK:
            raise InvalidInput("an observation says who holds a subject or an object, never a task lock")
        now_ms = _ms(self._clock.now())
        since_ms = min(_ms(request.observed_at), now_ms) if request.observed_at is not None else now_ms
        hold = _Hold(
            kind=request.kind,
            holder=request.holder,
            level=chosen,
            rank=levels.index(chosen),
            slot=f"o:{name}",
            via=ClaimVia.WORKER,
            since_ms=since_ms,
            until_ms=since_ms + min(request.lease_s, source.max_validity_s) * 1000,
            intents=tuple(request.intents),
            holder_kind="system",
            observer=name,
        )
        target = await self._target(space_id, request.subject, request.object)
        result, claim_id = await self._hold(space_id, caller.source_id, target, hold, now_ms)
        if not result.accepted:
            raise Conflict(f"the source {name} reported something later")
        return _claimed(claim_id, hold, result.epoch)

    async def observe(self, task: Task) -> None:
        """The worker of ownership by observation (24.3.4): a system event a mapped source sent (a service
        panel opened or closed a human session) holds its subject, or its objects, at the rule's level from
        the event's time, or ends what the source said before; one the receiving side of a handoff sent (a
        ticket closed as solved) reports the handoff's outcome. Applying it again changes nothing."""
        space_id = task.space_id
        config = self._config.space(space_id)
        if Feature.COORDINATION not in config.features.enabled:
            return
        event_id = UUID(task.payload["event_id"])
        async with self._uow(space_id) as uow:
            found = await uow.ingest.get_events([event_id])
            event = found[0] if found else None
            if event is None or event.canonical_type is None:
                return
            reported = config.coordination.handoffs.report(event.source_id, event.canonical_type)
            mapped = config.coordination.ownership.observation(event.source_id, event.canonical_type)
            if mapped is None:
                handles = []
            else:
                source, rule = mapped
                handles = await uow.identity.get_handles(event.handle_ids) if rule.target == "subject" else []
        if reported is not None:
            await self._reported_outcome(config, event, reported)
        if mapped is None:
            return
        targets = [
            _Target(subject.tag, subject.hmac)
            for subject in [await self._event_subject(space_id, h) for h in handles]
            if subject is not None
        ]
        targets += [
            await self._object(space_id, ObjectRef(type=o.type, namespace=o.namespace, id=o.external_id))
            for o in (event.object_refs if rule.target == "object" else ())
        ]
        levels = config.coordination.ownership.levels
        now_ms = _ms(self._clock.now())
        at_ms = min(_ms(event.occurred_at), now_ms)
        keep_ms = at_ms + source.max_validity_s * 1000
        releases: list[CoordinationRecord] = []
        try:
            for target in dict.fromkeys(targets):
                if rule.level is None:
                    key = self._store.claims_key(space_id, target.tag)
                    ended = await self._store.end_observation(
                        key, f"o:{source.name}", at_ms=at_ms, keep_until_ms=keep_ms, now_ms=now_ms
                    )
                    if ended.released is not None:
                        releases.append(ReleaseRecord(space_id, UUID(ended.released.claim_id), at_ms))
                    continue
                assert rule.holder is not None
                hold = _Hold(
                    kind=ClaimKind.OWNER,
                    holder=rule.holder,
                    level=rule.level,
                    rank=levels.index(rule.level),
                    slot=f"o:{source.name}",
                    via=ClaimVia.MAPPING,
                    since_ms=at_ms,
                    until_ms=at_ms + min(rule.valid_s or source.max_validity_s, source.max_validity_s) * 1000,
                    intents=tuple(sorted(rule.intents)),
                    holder_kind=rule.holder_kind,
                    observer=source.name,
                    basis_event_id=event_id,
                )
                await self._hold(space_id, event.source_id, target, hold, now_ms)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=5) from None
        if releases and not await self._log.publish(releases, self._publish_timeout_s):
            raise CoordinationUnavailable("the end of an observation could not be recorded", retry_after_s=5)

    async def _reported_outcome(
        self, config: SpaceConfig, event: EventRecord, reported: ReportedOutcome
    ) -> None:
        """A handoff's outcome the receiving side's system reported by an event: applied as the outcome API
        applies it, on behalf of the source that sent the event. An event naming no open handoff is left."""
        named = next((o.external_id for o in event.object_refs if o.type == "handoff"), None)
        handoff_id = named or event.fields.get(reported.handoff_field)
        source = next((s for s in config.sources if s.source_id == event.source_id), None)
        if not isinstance(handoff_id, str) or source is None:
            log.info("a reported handoff outcome names no handoff", extra={"event_id": str(event.event_id)})
            return
        caller = source_principal(config, source, key_id=REPORTED_KEY, scopes=frozenset({Scope.COORDINATE}))
        detail = {"reported_by_event": str(event.event_id)}
        try:
            await self._outcome(caller, handoff_id, reported.outcome, detail)
        except (Conflict, NotFound, InvalidInput) as exc:
            # Already closed, unknown or erased: the event stays in memory, the handoff as it is.
            log.info("a reported handoff outcome was not applied", extra={"reason": exc.code})

    async def _event_subject(self, space_id: UUID, record: HandleRecord) -> "_Subject | None":
        """The subject a stored event's handle names, or None for one coordination keys no subject by."""
        if not record.value:
            return None
        try:
            return await self._subject(space_id, _handle(record))
        except (NiadraError, ValueError):
            return None

    async def _hold(
        self, space_id: UUID, source_id: UUID, target: _Target, hold: "_Hold", now_ms: int
    ) -> tuple[ClaimResult, UUID]:
        """One claim into the state store and, accepted, onto the confirmed queue; one the queue does not
        confirm is taken back."""
        settings = self._settings(space_id)
        claim_id = uuid7()
        where = ClaimSlot(self._store.claims_key(space_id, target.tag), hold.slot)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, target, life, settings, self._clock.now())
            result = await self._store.claim(
                where.key,
                where.slot,
                rank=hold.rank,
                level=hold.level,
                holder=hold.holder,
                kind=hold.kind,
                via=hold.via,
                until_ms=hold.until_ms,
                claim_id=str(claim_id),
                intents=hold.intents,
                since_ms=hold.since_ms,
                now_ms=now_ms,
            )
            if not result.accepted:
                return result, claim_id
            await self._store.index_claim(space_id, claim_id, where, hold.until_ms - now_ms)
            record = OwnershipRecord(
                space_id,
                claim_id,
                None if target.is_object else target.raw,
                target.raw.decode() if target.is_object else None,
                hold.kind.value,
                hold.holder,
                hold.level,
                hold.via.value,
                source_id,
                hold.since_ms,
                hold.until_ms,
                hold.intents,
                result.epoch,
                holder_kind=hold.holder_kind,
                task=hold.task,
                observer=hold.observer,
                basis_event_id=hold.basis_event_id,
            )
            if not await self._log.publish([record], self._publish_timeout_s):
                await self._store.release(where.key, where.slot, result.epoch, now_ms=now_ms)
                raise CoordinationUnavailable(
                    "the claim could not be recorded; nothing holds it", retry_after_s=1
                )
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        return result, claim_id

    async def release(
        self, caller: SourcePrincipal, claim_id: str, epoch: int, idempotency_key: str
    ) -> OwnershipClaim:
        return await self._once(
            caller, "release", idempotency_key, OwnershipClaim, lambda: self._release(caller, claim_id, epoch)
        )

    async def _release(self, caller: SourcePrincipal, claim_id: str, epoch: int) -> OwnershipClaim:
        space_id = caller.space.space_id
        try:
            found_id = UUID(claim_id)
        except ValueError:
            raise NotFound("no such claim") from None
        now_ms = _ms(self._clock.now())
        try:
            where = await self._store.find_claim(space_id, found_id)
            if where is None:
                raise NotFound("no such claim, or it ended")
            result = await self._store.release(where.key, where.slot, epoch, now_ms=now_ms)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        if not result.accepted or result.released is None:
            raise Conflict("another epoch holds this claim, or it ended")
        # A release that misses the durable record only lingers there until the claim's own end.
        await self._publish_quietly([ReleaseRecord(space_id, found_id, now_ms)])
        ended = result.released
        return OwnershipClaim(
            claim_id=claim_id,
            kind=ended.kind,
            holder=ended.holder,
            level=ended.level,
            valid_until=_at(now_ms),
            epoch=result.epoch,
        )

    # Effects.

    async def reserve_effect(
        self, caller: SourcePrincipal, request: EffectReserve, idempotency_key: str
    ) -> Effect:
        return await self._once(
            caller, "reserve_effect", idempotency_key, Effect, lambda: self._reserve_effect(caller, request)
        )

    async def _reserve_effect(self, caller: SourcePrincipal, request: EffectReserve) -> Effect:
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        now_ms = _ms(self._clock.now())
        effect_hash = await self._effect_hash(space_id, request.effect_key)
        key = self._store.effect_key(space_id, effect_hash)
        try:
            life = await self._store.life(now_ms=now_ms)
            found = await self._effect(space_id, effect_hash, life, now_ms)
            if found is None and not life.whole and now_ms < life.since_ms + REBUILD_CLOSED_S * 1000:
                raise CoordinationUnavailable("the state store is being reloaded", retry_after_s=5)
            attempt = await self._store.reserve_effect(
                key,
                kind=request.kind.value,
                source=str(caller.source_id),
                fact=_fact(request.fact),
                cost_units=request.cost_units,
                lease_ms=settings.effect_lease_s * 1000,
                ttl_ms=_effect_ttl_ms(settings, request.kind),
                now_ms=now_ms,
            )
            record = EffectRecord(space_id, unb64url(effect_hash), attempt.snapshot, caller.source_id)
            if attempt.outcome == "in_flight":
                raise EffectInFlight(f"attempt {attempt.attempt} holds the key")
            if attempt.outcome == "reserved":
                if not await self._log.publish([record], self._publish_timeout_s):
                    await self._store.cancel_effect(key, attempt.attempt)
                    raise CoordinationUnavailable(
                        "the reservation could not be recorded; it was undone", retry_after_s=1
                    )
            elif attempt.lapsed:
                await self._publish_quietly([record])
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        return _effect(effect_hash, attempt.snapshot)

    async def settle_effect(
        self, caller: SourcePrincipal, effect_id: str, request: EffectSettle, idempotency_key: str
    ) -> Effect:
        return await self._once(
            caller,
            "settle_effect",
            idempotency_key,
            Effect,
            lambda: self._settle(caller, effect_id, EffectState(request.state), request.attempt),
        )

    async def _settle(
        self, caller: SourcePrincipal, effect_hash: str, state: EffectState, attempt: int
    ) -> Effect:
        space_id = caller.space.space_id
        now_ms = _ms(self._clock.now())
        key = self._store.effect_key(space_id, effect_hash)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._effect(space_id, effect_hash, life, now_ms)
            status, snapshot, _ = await self._store.settle_effect(
                key, state, attempt=attempt, by=Settler.RESERVER, now_ms=now_ms
            )
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        if snapshot is None:
            raise NotFound("no such effect")
        if status == "fenced":
            raise Conflict(f"attempt {snapshot.attempt} holds the key now")
        if status == "conflict":
            raise Conflict(f"the effect is {snapshot.state.value}")
        # A settlement that misses the durable record reads there as a lapsed reservation, `unknown_outcome`,
        # which is never sent again: the safe side.
        await self._publish_quietly(
            [EffectRecord(space_id, unb64url(effect_hash), snapshot, caller.source_id)]
        )
        return _effect(effect_hash, snapshot)

    # Declarations.

    async def declare(
        self, caller: SourcePrincipal, request: DeclareRequest, idempotency_key: str
    ) -> DeclareResult:
        return await self._once(
            caller, "declare", idempotency_key, DeclareResult, lambda: self._declare(caller, request.root)
        )

    async def _declare(self, caller: SourcePrincipal, request: Declaration) -> DeclareResult:
        match request:
            case CaseOpened() | LeaseDeclared():
                kind = ClaimKind.CASE if isinstance(request, CaseOpened) else ClaimKind.OWNER
                detail = request.detail
                await self._declare_claim(
                    caller, request.subject, request.object, kind, request.agent, detail.level,
                    detail.lease_s, detail.intents,
                )  # fmt: skip
            case CaseClosed():
                await self._release(caller, str(request.detail.claim_id), request.detail.epoch)
            case EffectDeclared():
                effect_hash = await self._effect_hash(caller.space.space_id, request.detail.effect_key)
                await self._settle(
                    caller, effect_hash, EffectState(request.detail.state), request.detail.attempt
                )
            case SuppressionAdded():
                await self._suppress(caller, request)
            case SuppressionLifted():
                return DeclareResult(accepted=await self._lift(caller, request))
            case ContactMade():
                await self._contact_made(caller, request)
            case TaskLockDeclared():
                detail_lock = request.detail
                await self._declare_claim(
                    caller, None, request.object, ClaimKind.TASK_LOCK, request.agent, None,
                    detail_lock.lease_s, (), task=detail_lock.task,
                )  # fmt: skip
            case CommitmentMade():
                return DeclareResult(accepted=await self._commitment_made(caller, request))
            case CommitmentWithdrawn():
                await self._commitment_ended(
                    caller, request.subject, request.detail.commitment_id, "withdrawn"
                )
            case CommitmentDecided():
                await self._commitment_ended(
                    caller, request.subject, request.detail.commitment_id, request.detail.outcome
                )
            case HandoffDeclared():
                await self._handoff_declared(caller, request)
        return DeclareResult(accepted=True)

    # Commitments.

    async def _commitment_made(self, caller: SourcePrincipal, request: CommitmentMade) -> bool:
        """An offer, a discount or a proposal an agent made (24.3.4): it holds or not by its type's
        compatibility with those that hold for the subject, and it becomes an action of the agent in the
        subject's memory, so another agent's context shows it as done by someone else. True when it holds."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        detail = request.detail
        kind = next((t for t in settings.commitments if t.name == detail.type), None)
        if kind is None:
            raise InvalidInput(
                _unknown("commitment type", detail.type, [t.name for t in settings.commitments])
            )
        if request.subject is None:
            raise InvalidInput("a commitment names the subject it was made to")
        now = self._clock.now()
        now_ms = _ms(now)
        longest = now + timedelta(days=kind.max_validity_days)
        until = min(detail.valid_until, longest) if detail.valid_until is not None else longest
        if until <= now:
            raise InvalidInput("the commitment ends before it starts")
        subject = await self._subject(space_id, request.subject)
        commitment_id = uuid5(space_id, f"commitment:{detail.commitment_id}")
        entry = CommitmentEntry(
            str(commitment_id), kind.name, kind.compatibility.value, _ms(until), detail.score, request.agent
        )
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, _Target(subject.tag, subject.hmac), life, settings, now)
            holds, superseded = await self._store.commit(
                space_id, subject.tag, detail.commitment_id, entry, now_ms=now_ms
            )
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        terms_json = json.dumps(detail.terms, sort_keys=True).encode()
        sealed, version = await self._keys.encrypt(space_id, terms_json, _terms_aad(commitment_id))
        named = tuple(sorted(await sealed_tokens(self._keys, space_id, body_tokens(terms_json))))
        records: list[CoordinationRecord] = [
            CommitmentRecord(
                space_id, commitment_id, detail.commitment_id, subject.hmac, kind.name,
                kind.compatibility.value, sealed, version, "active" if holds else "superseded",
                caller.source_id, request.agent, now_ms, _ms(until), detail.score, named,
            ),
            *(CommitmentStatusRecord(space_id, UUID(other), "superseded", now_ms) for other in superseded),
        ]  # fmt: skip
        if not await self._log.publish(records, self._publish_timeout_s):
            raise CoordinationUnavailable("the commitment could not be recorded yet", retry_after_s=1)
        terms = terms_text(detail.terms)
        action = EventItem(
            kind=EventKind.ACTION,
            idempotency_key=f"commitment:{detail.commitment_id}",
            channel=COORDINATION_CHANNEL,
            handles=[request.subject],
            speaker=SpeakerRef(role=Speaker.AI_AGENT, id=request.agent),
            object_refs=[
                ObjectRef(type="commitment", namespace=COORDINATION_NAMESPACE, id=detail.commitment_id)
            ],
            action=ActionInfo(operation=f"commitment.{kind.name}", result=terms or None),
            occurred_at=now,
            valid_until=until,
        )
        await self.events.record_coordination_events(caller, [action])
        if request.object is not None and self._working_set is not None:
            # A commitment on a shared object (a proposal on a listing) keeps that object fresh for as long as
            # the working set holds it: one statement, through the objects module.
            ref = f"{request.object.type}:{request.object.namespace}:{request.object.id}"
            await self._working_set.enter_working_set(space_id, [ref], "committed")
        return holds

    async def _commitment_ended(
        self, caller: SourcePrincipal, subject_handle: Handle | None, external_id: str, status: str
    ) -> None:
        """A commitment withdrawn, or accepted or declined by the person, maybe in a later conversation: it
        leaves the ones that hold, and its state is kept."""
        space_id = caller.space.space_id
        if subject_handle is None:
            raise InvalidInput("a commitment names the subject it was made to")
        subject = await self._subject(space_id, subject_handle)
        now = self._clock.now()
        now_ms = _ms(now)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(
                space_id, _Target(subject.tag, subject.hmac), life, self._settings(space_id), now
            )
            await self._store.uncommit(space_id, subject.tag, external_id, now_ms=now_ms)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        commitment_id = uuid5(space_id, f"commitment:{external_id}")
        record = CommitmentStatusRecord(space_id, commitment_id, status, now_ms)
        if not await self._log.publish([record], self._publish_timeout_s):
            raise CoordinationUnavailable("the commitment's state could not be recorded yet", retry_after_s=1)

    async def _commitments_of(self, space_id: UUID, tag: str, now_ms: int) -> list[CommitmentRef]:
        held = await self._store.commitments(space_id, tag, now_ms=now_ms)
        return [
            CommitmentRef(id=external_id, type=entry.type, valid_until=_at(entry.until_ms))
            for external_id, entry in sorted(held, key=lambda pair: pair[1].until_ms)
        ]

    # Handoffs.

    async def create_handoff(
        self, caller: SourcePrincipal, request: HandoffCreate, idempotency_key: str
    ) -> Handoff:
        return await self._once(
            caller, "handoff", idempotency_key, Handoff, lambda: self._create_handoff(caller, request)
        )

    async def _create_handoff(self, caller: SourcePrincipal, request: HandoffCreate) -> Handoff:
        """The package, compiled at the receiving side's level (the `handoff` view), with what coordination
        knows of the subject; kept sealed, one statement. While the outcome is due, the subject is held for
        the receiving side at the space's handoff level, when no stricter claim holds it."""
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        settings = config.coordination
        subject = await self._subject(space_id, request.subject)
        now = self._clock.now()
        now_ms = _ms(now)
        handoff_id = uuid7()
        expected_by = request.expected_by or now + timedelta(hours=settings.handoffs.expected_hours)
        if expected_by <= now:
            raise InvalidInput("the outcome is due before the handoff starts")
        level = min(request.level, caller.verification_ceiling, key=lambda v: v.rank)
        # The subject's objects come from the `now` view when the space keeps typed state.
        wanted: list[Include] = ["state"] if Feature.STATE in config.features.enabled else []
        read = ContextRequest(
            subject=request.subject,
            view="chat",
            verification=level,
            conversation_id=request.conversation_id,
            include=wanted,
        )
        if self._views is None:
            raise Unavailable("the context compiler is not wired here", retry_after_s=5)
        # The package is a read of the subject's context for the receiver, recorded with its own receipt,
        # which a key that may coordinate may compile (the coordination spec, 11.2).
        reader = dataclasses.replace(caller, scopes=caller.scopes | {Scope.CONTEXT})
        context = await self._views.context(reader, read)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, _Target(subject.tag, subject.hmac), life, settings, now)
            key = self._store.claims_key(space_id, subject.tag)
            owner = await self._store.owner(key, now_ms=now_ms)
            commitments = await self._commitments_of(space_id, subject.tag, now_ms)
            suppressed = await self._store.suppressed(space_id, subject.tag, now_ms=now_ms)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        async with self._uow(space_id) as uow:
            effects = await uow.coordinate.subject_effects(subject.hmac, now, HANDOFF_EFFECTS)
            promises = await _open_promises(uow, request.subject)
        vocabulary = [o.name for o in settings.handoffs.outcomes]
        lines = HandoffLines(
            source=request.agent or caller.key_id,
            target=request.target,
            reason=request.reason,
            holder=Holder(owner.holder, owner.level, _at(owner.until_ms) or now)
            if owner is not None
            else None,
            commitments=[(c.type, c.valid_until or expected_by) for c in commitments],
            effects=[(e.kind, e.state.value) for _, e in effects],
            suppressed=suppressed,
            vocabulary=vocabulary,
            expected_by=expected_by,
        )
        text = render(lines, context.text or "", Formatter(config.settings.timezone, config.settings.locale))
        open_objects, more_objects = unfinished_objects(context.state)
        package = HandoffPackage(
            handoff_id=str(handoff_id),
            created_at=now,
            from_agent=request.agent or caller.key_id,
            target=request.target,
            subject=request.subject,
            level=level,
            reason=request.reason,
            context=HandoffContext(
                text=text, etag=context.etag or None, policy_version=context.version or None
            ),
            owner=_owner(owner),
            open_objects=open_objects,
            open_objects_more=more_objects,
            commitments_active=commitments,
            promises_open=promises,
            effects=[
                EffectSeen(effect_id=b64url(key), kind=EffectKind(e.kind), state=e.state.value)
                for key, e in effects
            ],
            suppressions=list(suppressed),
            outcome=OutcomeRequest(vocabulary=vocabulary, expected_by=expected_by),
        )
        body = package.model_dump_json().encode()
        package_ct, version = await self._keys.encrypt(space_id, body, _handoff_aad(handoff_id, "package_ct"))
        subject_ct, _ = await self._keys.encrypt(
            space_id, request.subject.model_dump_json().encode(), _handoff_aad(handoff_id, "subject_ct")
        )
        row = HandoffRow(
            handoff_id, subject.hmac, subject_ct, caller.source_id, request.agent, request.target, package_ct,
            hashlib.sha256(body).digest(), version, "created", None, now, expected_by,
            named=await sealed_tokens(self._keys, space_id, body_tokens(body)),
        )  # fmt: skip
        async with self._uow(space_id) as uow:
            await uow.coordinate.add_handoff(row)
        if settings.handoffs.level is not None:
            await self._hold_for_receiver(
                caller, subject, request.target, settings.handoffs.level, expected_by
            )
        return Handoff(
            handoff_id=str(handoff_id),
            status="created",
            package=package,
            created_at=now,
            expected_by=expected_by,
        )

    async def _hold_for_receiver(
        self, caller: SourcePrincipal, subject: "_Subject", target: str, level: str, until: datetime
    ) -> None:
        """The subject waits for the receiving side: other agents' outbound contacts defer while the outcome
        is due. A stricter claim already holding it keeps it, and a state store that does not answer leaves
        the handoff without the hold."""
        space_id = caller.space.space_id
        levels = self._settings(space_id).ownership.levels
        now_ms = _ms(self._clock.now())
        hold = _Hold(
            kind=ClaimKind.OWNER,
            holder=target,
            level=level,
            rank=levels.index(level),
            slot=f"d:{caller.source_id}:{target}",
            via=ClaimVia.DECLARATION,
            since_ms=now_ms,
            until_ms=_ms(until),
            holder_kind="queue",
        )
        try:
            await self._hold(space_id, caller.source_id, _Target(subject.tag, subject.hmac), hold, now_ms)
        except CoordinationUnavailable:
            log.warning("a handoff went out without holding its subject: the state store did not answer")

    async def read_handoff(self, caller: SourcePrincipal, handoff_id: str) -> Handoff:
        space_id = caller.space.space_id
        async with self._uow(space_id) as uow:
            row = await uow.coordinate.handoff(_handoff_id(handoff_id))
        if row is None:
            raise NotFound("no such handoff")
        return await self._handoff(space_id, row)

    async def accept_handoff(self, caller: SourcePrincipal, handoff_id: str, idempotency_key: str) -> Handoff:
        return await self._once(
            caller, "handoff_accept", idempotency_key, Handoff, lambda: self._accept(caller, handoff_id)
        )

    async def _accept(self, caller: SourcePrincipal, handoff_id: str) -> Handoff:
        space_id = caller.space.space_id
        found = _handoff_id(handoff_id)
        async with self._uow(space_id) as uow:
            row = await uow.coordinate.move_handoff(
                found, status="accepted", from_statuses=("created",), at=self._clock.now()
            )
            if row is None:
                row = await uow.coordinate.handoff(found)
        if row is None:
            raise NotFound("no such handoff")
        if row.status != "accepted":
            raise Conflict(f"the handoff is {row.status}")
        return await self._handoff(space_id, row)

    async def handoff_outcome(
        self, caller: SourcePrincipal, handoff_id: str, request: HandoffOutcome, idempotency_key: str
    ) -> Handoff:
        return await self._once(
            caller,
            "handoff_outcome",
            idempotency_key,
            Handoff,
            lambda: self._outcome(caller, handoff_id, request.outcome, request.detail),
        )

    async def _outcome(
        self, caller: SourcePrincipal, handoff_id: str, outcome: str, detail: dict[str, Any]
    ) -> Handoff:
        """The outcome the receiving side reports closes the handoff and feeds the subject's memory (the
        receiving side's `handoff.outcome` action), the suppressions and the signals the outcome's rule names;
        the subject is no longer held for the receiving side."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        rule = next((o for o in settings.handoffs.outcomes if o.name == outcome), None)
        if settings.handoffs.outcomes and rule is None:
            raise InvalidInput("not an outcome of the space's handoff vocabulary")
        found = _handoff_id(handoff_id)
        now = self._clock.now()
        said = json.dumps(detail, sort_keys=True).encode()
        sealed, version = await self._keys.encrypt(space_id, said, _handoff_aad(found, "outcome_ct"))
        async with self._uow(space_id) as uow:
            row = await uow.coordinate.move_handoff(
                found,
                status="closed",
                from_statuses=("created", "accepted"),
                at=now,
                outcome=outcome,
                outcome_ct=SealedOutcome(sealed, version),
                named=await sealed_tokens(self._keys, space_id, body_tokens(said)),
            )
            if row is None:
                row = await uow.coordinate.handoff(found)
        if row is None:
            raise NotFound("no such handoff")
        if row.status != "closed" or row.outcome != outcome:
            raise Conflict(f"the handoff is {row.status}")
        handle = await self._handoff_subject(space_id, row)
        subject = await self._subject(space_id, handle)
        if settings.handoffs.level is not None:
            await self._release_receiver(space_id, subject, row)
        for entry in rule.suppress if rule is not None else ():
            await self._suppress_for(caller, handle, entry.purpose, entry.channel, entry.reason)
        items = [
            InteractionItem(
                idempotency_key=f"handoff:{found}:{i}",
                handle=handle,
                agent=row.target,
                occurred_at=now,
                interaction=interaction,
            )
            for i, interaction in enumerate(rule.interactions if rule is not None else ())
        ]
        if items:
            await self.interactions.record(space_id, caller.source_id, items)
        # A person or a queue of people usually receives a handoff: their outcome is their action.
        event = EventItem(
            kind=EventKind.ACTION,
            idempotency_key=f"handoff:{found}:outcome",
            channel=COORDINATION_CHANNEL,
            handles=[handle],
            speaker=SpeakerRef(role=Speaker.HUMAN_AGENT, id=row.target),
            object_refs=[ObjectRef(type="handoff", namespace=COORDINATION_NAMESPACE, id=str(found))],
            action=ActionInfo(operation=HANDOFF_OUTCOME, result=f"{row.target}: {outcome}"),
            occurred_at=now,
        )
        await self.events.record_coordination_events(caller, [event])
        return await self._handoff(space_id, row)

    async def _handoff_declared(self, caller: SourcePrincipal, request: HandoffDeclared) -> None:
        detail = request.detail
        if detail.status == "created":
            raise InvalidInput("a handoff is created with its package through POST /v1/handoffs")
        if detail.status == "accepted":
            await self._accept(caller, detail.handoff_id)
            return
        if detail.outcome is None:
            raise InvalidInput("a closed handoff declares its outcome")
        await self._outcome(caller, detail.handoff_id, detail.outcome, {})

    async def _release_receiver(self, space_id: UUID, subject: "_Subject", row: HandoffRow) -> None:
        now_ms = _ms(self._clock.now())
        key = self._store.claims_key(space_id, subject.tag)
        try:
            ended = await self._store.end_observation(
                key, f"d:{row.source_id}:{row.target}", at_ms=now_ms, keep_until_ms=now_ms + 1, now_ms=now_ms
            )
        except CacheUnavailable:
            log.warning("a closed handoff still holds its subject until the hold's own end")
            return
        if ended.released is not None:
            await self._publish_quietly([ReleaseRecord(space_id, UUID(ended.released.claim_id), now_ms)])

    async def _handoff(self, space_id: UUID, row: HandoffRow) -> Handoff:
        plain = await self._keys.decrypt(
            space_id, row.package_ct, _handoff_aad(row.handoff_id, "package_ct"), row.dek_version
        )
        status = row.status
        late = row.expected_by is not None and row.expected_by <= self._clock.now()
        if status in ("created", "accepted") and late:
            status = "expired"
        return Handoff(
            handoff_id=str(row.handoff_id),
            status=status,
            package=HandoffPackage.model_validate_json(plain),
            outcome=row.outcome,
            created_at=row.created_at,
            expected_by=row.expected_by,
        )

    async def _handoff_subject(self, space_id: UUID, row: HandoffRow) -> Handle:
        if row.subject_ct is None:
            raise NotFound("the handoff's subject was erased")
        plain = await self._keys.decrypt(
            space_id, row.subject_ct, _handoff_aad(row.handoff_id, "subject_ct"), row.dek_version
        )
        return Handle.model_validate_json(plain)

    # The coordination block of a context read, and the shadow run.

    async def subject_block(self, space_id: UUID, handle: Handle | None) -> CoordinationBlock:
        """What coordination knows of the subject, from the state store alone: who holds it, what it is
        suppressed for, what each budgeted purpose has left, and the commitments that hold."""
        config = self._config.space(space_id)
        if handle is None:
            return CoordinationBlock()
        settings = config.coordination
        subject = await self._subject(space_id, handle)
        now_ms = _ms(self._clock.now())
        budgeted = [p for p in settings.purposes if p.budget is not None]
        windows = [(_contact_budget(p.name), p.budget.per_hours * 3_600_000) for p in budgeted if p.budget]
        key = self._store.claims_key(space_id, subject.tag)
        owner = await self._store.owner(key, now_ms=now_ms)
        suppressed = await self._store.suppressed(space_id, subject.tag, now_ms=now_ms)
        used = await self._store.budgets_used(space_id, subject.tag, windows, now_ms=now_ms)
        commitments = await self._commitments_of(space_id, subject.tag, now_ms)
        return CoordinationBlock(
            owner=_owner(owner),
            suppressions=list(suppressed),
            contact_budget={
                p.name: ContactBudget(remaining=max(p.budget.limit - u, 0))
                for p, u in zip(budgeted, used, strict=True)
                if p.budget is not None
            },
            commitments_active=commitments,
        )

    async def request_shadow(self, caller: Principal, request: ShadowRequest) -> ShadowRun:
        """A shadow run over the space's last `days` of outbound messages; its task writes the report."""
        space_id = caller.space.space_id
        report_id = uuid7()
        async with self._uow(space_id) as uow:
            await uow.coordinate.add_report(report_id, request.days, self._clock.now())
            await uow.pipeline.enqueue(
                [Task(TaskName.COORDINATION_SHADOW, space_id, {"report_id": str(report_id)})]
            )
        return ShadowRun(report_id=str(report_id), status="pending")

    async def overview(self, caller: Principal, days: int) -> CoordinationOverview:
        """The Console's picture of coordination, from the durable record (admin, never the hot path)."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        now = self._clock.now()
        since = now - timedelta(days=days)
        budgets = {_contact_budget(p.name): p for p in settings.purposes if p.budget is not None}
        limits = {name: rule.budget.limit for name, rule in budgets.items() if rule.budget is not None}
        async with self._uow(space_id) as uow:
            found = await uow.coordinate.overview(since, now, limits)
        spent = {name: (subjects, full, units) for name, subjects, full, units in found.budgets}
        return CoordinationOverview(
            since=since,
            ownership=[
                OwnershipCount(kind=k, level=lv, via=via, holder_kind=hk, claims=n, holders=h)
                for k, lv, via, hk, n, h in found.ownership
            ],
            effects=[EffectCount(kind=k, state=st, effects=n) for k, st, n in found.effects],
            awaiting_decision=found.awaiting_decision,
            conflicts=ConflictCounts(
                ownership_overlaps=found.overlaps,
                commitments_superseded=found.commitments_superseded,
                handoffs_expired=found.handoffs_expired,
                tokens_reused=found.tokens_reused,
            ),
            budgets=[
                ContactBudgetUse(
                    purpose=rule.name,
                    limit=rule.budget.limit,
                    per_hours=rule.budget.per_hours,
                    subjects=spent.get(name, (0, 0, 0))[0],
                    at_limit=spent.get(name, (0, 0, 0))[1],
                    spent=spent.get(name, (0, 0, 0))[2],
                )
                for name, rule in sorted(budgets.items())
                if rule.budget is not None
            ],
            contacts=[
                ContactCount(purpose=p, direction=d, state=st, contacts=n) for p, d, st, n in found.contacts
            ],
        )

    async def reports(self, caller: Principal, cursor: str | None, limit: int) -> CoordinationReportPage:
        space_id = caller.space.space_id
        try:
            before = UUID(cursor) if cursor else None
        except ValueError:
            raise InvalidInput("not a cursor of the reports") from None
        async with self._uow(space_id) as uow:
            rows = await uow.coordinate.reports(before, limit)
        items = [
            CoordinationReport(
                report_id=str(r.report_id), status=r.status, days=r.days, requested_at=r.requested_at,
                finished_at=r.finished_at, summary=r.summary,
            )
            for r in rows
        ]  # fmt: skip
        next_cursor = str(rows[-1].report_id) if len(rows) == limit else None
        return CoordinationReportPage(items=items, next_cursor=next_cursor)

    async def run_shadow(self, task: Task) -> None:
        """The shadow run (24.3.4): the space's policy over its outbound messages already ingested, nothing
        enforced. It reads the events a page at a time, off the hot path, and writes the counts, per 1,000
        customers a month, with a sealed sample of conflicts for a person to review."""
        space_id = task.space_id
        report_id = UUID(task.payload["report_id"])
        config = self._config.space(space_id)
        settings = config.coordination
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            row = await uow.coordinate.report(report_id)
        if row is None or row.status != "pending":
            return
        since = now - timedelta(days=row.days)
        messages: list[ShadowMessage] = []
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.ingest.recent_events(since, SHADOW_PAGE, after=after)
            messages += [m for m in (_shadow_message(e) for e in page) if m is not None]
            if len(page) < SHADOW_PAGE:
                break
            after = page[-1].event_id
        policy = ShadowPolicy(
            windows={c.name: c.free_form_hours for c in settings.channels if c.free_form_hours is not None},
            quiet=lambda channel, at: self._quiet_at(space_id, settings, channel, at),
            budget=_tightest(settings),
        )
        report = run_shadow(messages, policy, days=row.days, sample=settings.shadow.sample)
        async with self._uow(space_id) as uow:
            reused = await uow.coordinate.tokens_used_twice(since)
        summary = {
            "customers": report.customers,
            "messages": report.messages,
            "conflicts": {**report.conflicts, "tokens_used_twice": reused},
            "per_1000_customers_a_month": report.per_thousand_customers_a_month,
        }
        sample = [
            {"kind": c.kind, "event_id": str(c.event_id), "at": c.at.isoformat()} for c in report.sample
        ]
        sealed, version = await self._keys.encrypt(
            space_id, json.dumps(sample).encode(), f"coord_reports:{report_id}:sample_ct".encode()
        )
        async with self._uow(space_id) as uow:
            await uow.coordinate.finish_report(
                report_id,
                status="done",
                summary=summary,
                sample_ct=sealed,
                dek_version=version,
                at=self._clock.now(),
            )

    def _quiet_at(
        self, space_id: UUID, settings: CoordinationSettings, channel: str, at: datetime
    ) -> datetime | None:
        rule = next((c for c in settings.channels if c.name == channel), None)
        until = self._quiet_until_ms(space_id, rule, at)
        return _at(until)

    # What an erasure leaves in the state store (24.5.5).

    async def fence(self, space_id: UUID, scope: ErasureScope) -> None:
        """Before an erasure's cascade: the subject's records still in the writer's queue, or in the state
        store for a reload, are fenced off for an hour, far past the writer's linger and its retries. Fails
        when the store does not answer, so the erasure waits rather than let erased data come back."""
        if not scope.subject_hashes:
            return
        try:
            await self._erasures.fence(space_id, scope.subject_hashes, self._clock.now(), ERASURE_FENCE_S)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=5) from None

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None:
        """After an erasure's cascade: everything the state store keeps of the subject but its suppressions,
        which outlive it as the proof that the person asked not to be contacted, and its fence; then the
        durable record is erased once more, since a batch the writer checked before the fence went up may have
        landed after the cascade's pass."""
        for subject_hmac in scope.subject_hashes:
            await self._store.purge(space_id, b64url(subject_hmac))
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.coordinate.erase_for(scope, limit=PURGE_PAGE)
            if len(rows) < PURGE_PAGE:
                return

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """`NamedInText`: the terms of others' commitments, and the packages and outcomes of others'
        handoffs, that name the target keep, sealed again with the target redacted out, found by the index of
        mentions a page at a time. The target's own go with the cascade; a held subject's keep as they are.
        Rows written before the index existed are read into it once."""
        await self._index_written_before(space_id)
        held_subjects = await self._held_subjects(space_id, held)
        own = set(scope.subject_hashes)
        done = Redacted()
        after: MentionKey | None = None
        stores = [MentionStore.COMMITMENT, MentionStore.HANDOFF]
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.naming(
                    sorted(tokens), plain, stores=stores, after=after, limit=MENTION_PAGE
                )
                index: list[tuple[MentionKey, frozenset[bytes]]] = []
                commitments = {
                    str(t.commitment_id): t
                    for t in await uow.coordinate.terms_of(
                        [UUID(k.row) for k in keys if k.store == MentionStore.COMMITMENT]
                    )
                }
                for key in keys:
                    if key.store == MentionStore.COMMITMENT:
                        terms = commitments.get(key.row)
                        subject = terms.subject_hmac if terms is not None else None
                        gone = terms is None
                    else:
                        handoff = await uow.coordinate.handoff(UUID(key.row))
                        subject = handoff.subject_hmac if handoff is not None else None
                        gone = handoff is None
                    if gone:
                        index.append((key, frozenset()))
                        continue
                    if subject is not None and subject in own:
                        continue
                    if terms is not None and key.store == MentionStore.COMMITMENT:
                        fixed = await self._redacted_terms(space_id, terms, plain)
                        if fixed is None:
                            continue
                        if subject is not None and subject in held_subjects:
                            done.held += 1
                            continue
                        await uow.coordinate.rewrite_terms(fixed[0])
                        index.append((key, fixed[1]))
                    elif handoff is not None:
                        found = await self._redacted_handoff(uow, space_id, handoff, plain)
                        if found is None:
                            continue
                        if subject is not None and subject in held_subjects:
                            done.held += 1
                            continue
                        await uow.coordinate.rewrite_handoff(found[0], found[1])
                        index.append((key, found[2]))
                    done.rows += 1
                await uow.mentions.index(index, replace=True)
            if len(keys) < MENTION_PAGE:
                return done
            after = keys[-1]

    async def _redacted_terms(
        self, space_id: UUID, terms: SealedTerms, plain: frozenset[str]
    ) -> tuple[SealedTerms, frozenset[bytes]] | None:
        aad = _terms_aad(terms.commitment_id)
        opened = json.loads(await self._keys.decrypt(space_id, terms.terms_ct, aad, terms.dek_version))
        redacted, named = redact_value(opened, plain)
        if not named:
            return None
        body = json.dumps(redacted, sort_keys=True).encode()
        sealed, version = await self._keys.encrypt(space_id, body, aad)
        tokens = await sealed_tokens(self._keys, space_id, body_tokens(body))
        return dataclasses.replace(terms, terms_ct=sealed, dek_version=version), tokens

    async def _redacted_handoff(
        self, uow: UnitOfWork, space_id: UUID, row: HandoffRow, plain: frozenset[str]
    ) -> tuple[HandoffRow, SealedOutcome | None, frozenset[bytes]] | None:
        """The handoff's package and outcome with the target redacted out, its subject sealed again beside
        the package under the same key; None when neither names them."""
        package_aad = _handoff_aad(row.handoff_id, "package_ct")
        package = json.loads(await self._keys.decrypt(space_id, row.package_ct, package_aad, row.dek_version))
        package, in_package = redact_value(package, plain)
        outcome = await uow.coordinate.outcome_of(row.handoff_id)
        detail, in_detail = None, 0
        if outcome is not None:
            outcome_aad = _handoff_aad(row.handoff_id, "outcome_ct")
            opened = json.loads(
                await self._keys.decrypt(space_id, outcome.outcome_ct, outcome_aad, outcome.dek_version)
            )
            detail, in_detail = redact_value(opened, plain)
        if not in_package + in_detail:
            return None
        try:
            body = HandoffPackage.model_validate(package).model_dump_json().encode()
        except ValidationError:  # an id field that was the identifier: the package keeps
            log.warning("a handoff package names an erased person where only an id fits; it keeps")
            return None
        package_ct, version = await self._keys.encrypt(space_id, body, package_aad)
        subject_ct = row.subject_ct
        if subject_ct is not None:
            subject_aad = _handoff_aad(row.handoff_id, "subject_ct")
            subject = await self._keys.decrypt(space_id, subject_ct, subject_aad, row.dek_version)
            subject_ct, _ = await self._keys.encrypt(space_id, subject, subject_aad)
        said = b""
        if outcome is not None and in_detail:
            said = json.dumps(detail, sort_keys=True).encode()
            sealed, outcome_version = await self._keys.encrypt(
                space_id, said, _handoff_aad(row.handoff_id, "outcome_ct")
            )
            outcome = SealedOutcome(sealed, outcome_version)
        fixed = dataclasses.replace(
            row,
            package_ct=package_ct,
            package_sha256=hashlib.sha256(body).digest(),
            subject_ct=subject_ct,
            dek_version=version,
        )
        tokens = await sealed_tokens(self._keys, space_id, body_tokens(body + b"\n" + said))
        return fixed, outcome if in_detail else None, tokens

    async def _held_subjects(self, space_id: UUID, held: Held) -> frozenset[bytes]:
        """The keyed digests coordination names the held handles by."""
        if not held.handles:
            return frozenset()
        async with self._uow(space_id) as uow:
            handles = await uow.identity.get_handles(sorted(held.handles, key=str))
        found = set()
        for h in handles:
            if not h.value:
                continue
            try:
                canonical, _ = subject_canonical(h.type, h.value, h.scope or None, h.subject_kind)
            except NiadraError:
                continue
            found.add(await self._keys.hmac(space_id, SUBJECT_HMAC, canonical.encode()))
        return frozenset(found)

    async def _index_written_before(self, space_id: UUID) -> None:
        """Reads, once, the commitments and handoffs written before the index existed into it, by the first
        erasure."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(COORDINATION_MENTIONS_READY):
                return
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.coordinate.terms_page(after, MENTION_PAGE)
                entries = []
                for t in page:
                    opened = await self._keys.decrypt(
                        space_id, t.terms_ct, _terms_aad(t.commitment_id), t.dek_version
                    )
                    named = await sealed_tokens(self._keys, space_id, body_tokens(opened))
                    entries.append((MentionKey(MentionStore.COMMITMENT, str(t.commitment_id)), named))
                await uow.mentions.index([(k, n) for k, n in entries if n])
            if len(page) < MENTION_PAGE:
                break
            after = page[-1].commitment_id
        after = None
        while True:
            async with self._uow(space_id) as uow:
                handoffs = await uow.coordinate.handoffs_page(after, MENTION_PAGE)
                entries = []
                for h in handoffs:
                    aad = _handoff_aad(h.handoff_id, "package_ct")
                    said = await self._keys.decrypt(space_id, h.package_ct, aad, h.dek_version)
                    if (outcome := await uow.coordinate.outcome_of(h.handoff_id)) is not None:
                        outcome_aad = _handoff_aad(h.handoff_id, "outcome_ct")
                        said += b"\n" + await self._keys.decrypt(
                            space_id, outcome.outcome_ct, outcome_aad, outcome.dek_version
                        )
                    named = await sealed_tokens(self._keys, space_id, body_tokens(said))
                    entries.append((MentionKey(MentionStore.HANDOFF, str(h.handoff_id)), named))
                await uow.mentions.index([(k, n) for k, n in entries if n])
            if len(handoffs) < MENTION_PAGE:
                break
            after = handoffs[-1].handoff_id
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(COORDINATION_MENTIONS_READY)

    async def _suppress_for(
        self, caller: SourcePrincipal, handle: Handle, purpose: str, channel: str | None, reason: str
    ) -> None:
        declaration = SuppressionAdded(
            kind="suppression.added",
            subject=handle,
            agent=caller.key_id,
            detail=SuppressionAddedDetail(purpose=purpose, channel=channel, reason=reason),
        )
        await self._suppress(caller, declaration)

    # Contacts and their tokens.

    async def _contact_made(self, caller: SourcePrincipal, request: ContactMade) -> None:
        """A contact that left, recorded whatever order it arrives in (the coordination spec, 5.1): under the
        token's `jti` or the decision's id, so a second declaration of one token is a second use of it. One
        that went out without a decision still counts in its purpose's budget."""
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        detail = request.detail
        rule = _purpose(settings, detail.purpose)
        now_ms = _ms(self._clock.now())
        subject = await self._subject(space_id, request.subject) if request.subject is not None else None
        contact_id = detail.jti or detail.decision_id or uuid7()
        records: list[CoordinationRecord] = [
            ContactRecord(
                space_id,
                contact_id,
                subject.hmac if subject is not None else None,
                detail.purpose,
                detail.channel,
                caller.source_id,
                request.agent,
                detail.gateway_id,
                detail.decision_id,
                "made",
                unchecked=detail.unchecked,
                used_at_ms=now_ms,
                use_ids=(uuid7(),),
            )
        ]
        undecided = detail.jti is None and detail.decision_id is None
        if undecided and subject is not None and rule.budget is not None:
            budget = _contact_budget(detail.purpose)
            window_ms = rule.budget.per_hours * 3_600_000
            try:
                await self._store.spend(
                    space_id,
                    subject.tag,
                    budget,
                    str(contact_id),
                    units=1,
                    limit=FORCED_LIMIT,
                    window_ms=window_ms,
                    now_ms=now_ms,
                )
            except CacheUnavailable:
                raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
            records.append(
                SpendRecord(
                    space_id, contact_id, budget, None, "subject", subject.hmac, 1, now_ms, now_ms + window_ms
                )
            )
        if not await self._log.publish(records, self._publish_timeout_s):
            raise CoordinationUnavailable("the contact could not be recorded yet", retry_after_s=1)

    async def contact_keys(self, space_id: UUID) -> ContactKeys:
        """The public keys a gateway checks the space's contact tokens with (the contact-token spec, 5): the
        current generation's, and the previous one as `retiring` until the document says."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            raise NotFound("no contact keys for this space") from None
        if Feature.COORDINATION not in config.features.enabled:
            raise NotFound("no contact keys for this space")
        settings = config.coordination.contact_keys
        keys = [await self._public_key(space_id, settings.generation, "active", None)]
        until = settings.previous_until
        if settings.generation > 1 and until is not None and until > self._clock.now():
            keys.append(await self._public_key(space_id, settings.generation - 1, "retiring", until))
        return ContactKeys(keys=keys)

    async def _public_key(
        self, space_id: UUID, generation: int, status: str, not_after: datetime | None
    ) -> ContactKey:
        x = public_x(await self._signing(space_id, generation))
        return ContactKey(kid=key_id(x), x=x, space=space_id, status=status, not_after=not_after)

    async def _signing(self, space_id: UUID, generation: int) -> Ed25519PrivateKey:
        seed = await self._keys.hmac(space_id, CONTACT_KEY_HMAC, str(generation).encode())
        return signing_key(seed)

    async def _gateway_key(self, space_id: UUID, gateway: Gateway) -> bytes | None:
        """The key the gateway shares with the producer, from the vault once per five minutes per process.
        None without a vault: no token goes out, and the gateway refuses the contact (it fails closed)."""
        if self._secrets is None:
            return None
        if not owns_secret(space_id, gateway.secret_ref):
            raise InvalidInput(f"gateway {gateway.gateway_id}: its key is not a secret of this space")
        at = self._clock.now().timestamp()
        held = self._gateway_keys.get((space_id, gateway.secret_ref))
        if held is not None and held[1] > at:
            return held[0]
        try:
            value = unb64url((await self._secrets.get(gateway.secret_ref)).strip())
        except NotFound:
            raise InvalidInput(f"gateway {gateway.gateway_id}: its key is not in the vault") from None
        except (ConnectionError, OSError, TimeoutError):
            raise CacheUnavailable("the vault did not answer") from None
        self._gateway_keys[(space_id, gateway.secret_ref)] = (value, at + GATEWAY_KEY_TTL_S)
        return value

    async def _last_inbound(
        self, space_id: UUID, handle: Handle, channel: str, since: datetime
    ) -> datetime | None:
        """The customer's last message on the channel since `since`, as the hot layer holds it. Unknown when
        the handle cache or the hot layer does not answer, so the channel then asks for its paid template."""
        if self._hot is None or self._handle_cache is None:
            return None
        normalized = normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind)
        wanted = [normalized.key, *(d.key for d in normalized.derived)]
        try:
            names = [
                handle_cache_name(
                    space_id, await self._keys.hmac(space_id, HANDLE_CACHE_PURPOSE, handle_cache_input(k))
                )
                for k in wanted
            ]
            found = await self._handle_cache.get_many(names)
            # A handle's derived forms count as the same destination here (the phone behind a WhatsApp id),
            # as they do for the subject's key.
            ids = [cached_handle(value)[0] for value in found if value is not None and len(value) == 17]
            if not ids:
                return None
            read = await self._hot.read(space_id, list(dict.fromkeys(ids)), _ms(since), newest=HOT_NEWEST)
        except (ConnectionError, OSError, TimeoutError):
            log.warning("the service window is unknown: the hot layer did not answer")
            return None
        for turn in reversed(read.turns):
            doc = await self._hot_turn(space_id, turn.event_id, turn.envelope_ct)
            if doc is not None and doc.get("speaker") == Speaker.CUSTOMER and doc.get("channel") == channel:
                return _at(turn.occurred_at_ms)
        return None

    async def _hot_turn(self, space_id: UUID, event_id: UUID, envelope: bytes) -> dict[str, Any] | None:
        try:
            version, ciphertext = unpack_envelope(envelope)
            plain = await self._keys.decrypt(space_id, ciphertext, hot_aad(space_id, event_id), version)
            doc: dict[str, Any] = json.loads(plain)
            return doc
        except Exception:
            log.warning("an unreadable hot-layer turn was skipped")
            return None

    # Paid admission (`ports.admission.PaidBudgets`).

    async def reserve(
        self,
        space_id: UUID,
        operation: str,
        *,
        object: ObjectRef | None = None,
        subject: Handle | None = None,
        units: int | None = None,
    ) -> PaidReservation:
        """Admission before a costly act (24.3.4): the units are reserved in the state store and confirmed on
        the queue before the answer; neither answering, nothing is admitted (it fails closed)."""
        config = self._config.space(space_id)
        rule = next((b for b in config.coordination.paid_budgets if b.operation == operation), None)
        if Feature.COORDINATION not in config.features.enabled or rule is None:
            return PaidReservation(admitted=True, operation=operation, units=units or 0)
        target = await self._paid_target(space_id, rule, object, subject)
        now = self._clock.now()
        now_ms = _ms(now)
        budget = _paid_budget(operation)
        window_ms = rule.per_hours * 3_600_000
        asked = units or 0
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, target, life, config.coordination, now)
            if units is None:
                asked = await self._store.paid_max(space_id, operation) or rule.max_units_per_call or 1
            if rule.max_units_per_call is not None and asked > rule.max_units_per_call:
                return PaidReservation(False, operation, asked, reason="over_call_ceiling")
            reservation_id = uuid7()
            got = await self._store.spend(
                space_id, target.tag, budget, str(reservation_id), units=asked, limit=rule.units,
                window_ms=window_ms, now_ms=now_ms,
            )  # fmt: skip
            if got.decision is not Decision.ALLOW:
                return PaidReservation(
                    False, operation, asked, got.reason, got.remaining, _at(got.retry_at_ms)
                )
            record = SpendRecord(
                space_id,
                reservation_id,
                budget,
                None,
                target.kind,
                target.raw,
                asked,
                now_ms,
                now_ms + window_ms,
            )
            if not await self._log.publish([record], self._publish_timeout_s):
                await self._store.settle_units(space_id, target.tag, budget, str(reservation_id), None)
                return PaidReservation(False, operation, asked, reason="unavailable")
        except CacheUnavailable:
            return PaidReservation(False, operation, asked, reason="unavailable")
        return PaidReservation(
            True,
            operation,
            asked,
            remaining=got.remaining,
            reservation_id=reservation_id,
            target=f"{target.kind}:{b64url(target.raw)}",
        )

    async def settle(
        self, space_id: UUID, reservation: PaidReservation, *, outcome: Outcome, units: int | None = None
    ) -> None:
        if reservation.reservation_id is None or reservation.target is None:
            return
        rule = next(
            (b for b in self._settings(space_id).paid_budgets if b.operation == reservation.operation), None
        )
        if rule is None:
            return
        if outcome == "done" and units is None:
            raise InvalidInput("a paid call that was done settles with its real cost")
        cost = {"done": units or 0, "failed": max(units or 0, reservation.units), "skipped": 0}[outcome]
        kind, _, raw = reservation.target.partition(":")
        target = await self._target_of(space_id, kind, unb64url(raw))
        budget = _paid_budget(reservation.operation)
        spent_ms = _uuid7_ms(reservation.reservation_id)
        try:
            await self._store.settle_units(
                space_id, target.tag, budget, str(reservation.reservation_id), cost if cost else None
            )
            if outcome == "done" and cost:
                await self._store.raise_paid_max(space_id, reservation.operation, cost)
        except CacheUnavailable:
            log.warning("a paid reservation was not settled: it keeps counting what it reserved")
            return
        # A settlement that misses the durable record reloads as what was reserved: the safe side.
        until_ms = spent_ms + rule.per_hours * 3_600_000
        record = SpendRecord(
            space_id, reservation.reservation_id, budget, None, target.kind, target.raw, cost, spent_ms,
            until_ms, settled=True,
        )  # fmt: skip
        await self._publish_quietly([record])

    async def _paid_target(
        self, space_id: UUID, rule: PaidBudget, object_: ObjectRef | None, subject: Handle | None
    ) -> _Target:
        if rule.scope == "space":
            return _Target(SPACE_TAG, b"", "space")
        if rule.scope == "object":
            if object_ is None:
                raise InvalidInput(f"the paid operation {rule.operation} counts per object: name it")
            return await self._object(space_id, object_)
        if subject is None:
            raise InvalidInput(f"the paid operation {rule.operation} counts per subject: name it")
        found = await self._subject(space_id, subject)
        return _Target(found.tag, found.hmac)

    async def _target_of(self, space_id: UUID, kind: str, raw: bytes) -> _Target:
        if kind == "space":
            return _Target(SPACE_TAG, b"", "space")
        if kind == "object":
            return _Target(b64url(await self._keys.hmac(space_id, OBJECT_HMAC, raw)), raw, "object")
        return _Target(b64url(raw), raw)

    # Suppressions.

    async def _suppress(self, caller: SourcePrincipal, request: SuppressionAdded) -> None:
        space_id = caller.space.space_id
        settings = self._settings(space_id)
        detail = request.detail
        if detail.purpose != ANY_PURPOSE:
            _purpose(settings, detail.purpose)
        reason = next((r for r in settings.suppression_reasons if r.name == detail.reason), None)
        if reason is None and detail.reason != STATED_PREFERENCE:
            raise InvalidInput(
                _unknown("suppression reason", detail.reason, [r.name for r in settings.suppression_reasons])
            )
        await self._suppress_subject(
            space_id,
            settings,
            await self._destination(space_id, request.subject),
            detail,
            caller.source_id,
            reason.resume_after_hours if reason is not None else None,
        )

    async def _suppress_subject(
        self,
        space_id: UUID,
        settings: CoordinationSettings,
        subject: _Subject,
        detail: SuppressionAddedDetail,
        source_id: UUID,
        resume_after_hours: int | None,
    ) -> None:
        now = self._clock.now()
        now_ms = _ms(now)
        resume_s = resume_after_hours * 3600 if resume_after_hours is not None else None
        until = detail.until or (now + timedelta(seconds=resume_s) if resume_s is not None else None)
        if until is not None and until <= now:
            raise InvalidInput("the suppression ends before it starts")
        window = detail.window.window() if detail.window is not None else None
        field = _suppression_field(detail.purpose, detail.channel, source_id)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, _Target(subject.tag, subject.hmac), life, settings, now)
            suppression_id = await self._store.suppress(
                space_id,
                subject.tag,
                field,
                until_ms=_ms(until) if until is not None else None,
                suppression_id=uuid7(),
                now_ms=now_ms,
                window=window,
            )
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        sealed, version = await self._keys.encrypt(
            space_id, subject.canonical.encode(), f"suppressions:{suppression_id}:handle_ct".encode()
        )
        record = SuppressionRecord(
            space_id,
            suppression_id,
            subject.handle_type,
            subject.hmac,
            sealed,
            version,
            detail.purpose,
            detail.channel,
            detail.reason,
            source_id,
            now_ms,
            _ms(until) if until is not None else None,
            resume_s,
            window=window.encode() if window is not None else None,
        )
        # The store already applies it; a caller that retries records it (the opt-out never waits).
        if not await self._log.publish([record], self._publish_timeout_s):
            raise CoordinationUnavailable(
                "the suppression applies and could not be recorded yet", retry_after_s=1
            )

    async def _lift(self, caller: SourcePrincipal, request: SuppressionLifted) -> bool:
        space_id = caller.space.space_id
        return await self._lift_subject(
            space_id,
            self._settings(space_id),
            await self._destination(space_id, request.subject),
            request.detail.purpose,
            request.detail.channel,
            caller.source_id,
        )

    async def stated_window(self, task: Task) -> None:
        """`contact_window`: the local hours a customer said they may be contacted in (the suppression-list
        spec, 6.1), as a suppression of every purpose that holds in the window, under the stated source, on
        each of the person's destinations (their phones and e-mail addresses, read from the identity store by
        the task's handle ids); with no window, what they said before on that channel is lifted. The task
        carries the hours, never a handle's value."""
        space_id = task.space_id
        settings = self._settings(space_id)
        payload = task.payload
        channel = payload.get("channel")
        window = ContactWindow.model_validate(payload["window"]) if payload.get("window") else None
        async with self._uow(space_id) as uow:
            records = await uow.identity.get_handles([UUID(h) for h in payload["handle_ids"]])
        for record in records:
            if record.type not in DESTINATIONS:
                continue
            try:
                subject = await self._destination(space_id, Handle(type=record.type, value=record.value))
            except InvalidInput:
                continue  # not a number or an address the list can key
            if window is None:
                await self._lift_subject(space_id, settings, subject, ANY_PURPOSE, channel, STATED_SOURCE)
                continue
            detail = SuppressionAddedDetail(
                purpose=ANY_PURPOSE, channel=channel, reason=STATED_PREFERENCE, window=window
            )
            await self._suppress_subject(space_id, settings, subject, detail, STATED_SOURCE, None)

    async def _lift_subject(
        self,
        space_id: UUID,
        settings: CoordinationSettings,
        subject: _Subject,
        purpose: str,
        channel: str | None,
        source_id: UUID,
    ) -> bool:
        now = self._clock.now()
        now_ms = _ms(now)
        field = _suppression_field(purpose, channel, source_id)
        try:
            life = await self._store.life(now_ms=now_ms)
            await self._ensure(space_id, _Target(subject.tag, subject.hmac), life, settings, now)
            lifted = await self._store.lift(space_id, subject.tag, field, now_ms=now_ms)
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        if lifted is None:
            return False
        await self._publish_quietly([LiftRecord(space_id, lifted, now_ms)])
        return True

    async def suppressions(self, caller: SourcePrincipal, cursor: str | None, limit: int) -> SuppressionPage:
        """The list's changes, keyed with the reader's salt (the suppression-list spec, 5)."""
        space_id = caller.space.space_id
        salt_id, salt = await self._salt(space_id, caller.source_id)
        after, reset = _cursor(cursor, salt_id)
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            rows = await uow.coordinate.suppression_changes(after, limit, now)
        items = [await self._entry(space_id, salt, row) for row in rows]
        last = rows[-1].change_seq if rows else after
        return SuppressionPage(
            salt_id=salt_id,
            reset=reset,
            items=items,
            next_cursor=f"c_{salt_id}_{last}" if last is not None else None,
        )

    async def salt(self, caller: SourcePrincipal) -> SuppressionSalt:
        salt_id, salt = await self._salt(caller.space.space_id, caller.source_id)
        return SuppressionSalt(salt_id=salt_id, salt=b64url(salt), valid_from=SALT_SINCE)

    async def _entry(self, space_id: UUID, salt: bytes, row: SuppressionRow) -> Suppression:
        canonical = await self._keys.decrypt(
            space_id, row.handle_ct, f"suppressions:{row.suppression_id}:handle_ct".encode(), row.dek_version
        )
        return Suppression(
            id=str(row.suppression_id),
            key=suppression_key(salt, canonical.decode()),
            purpose=row.purpose,
            channel=row.channel,
            since=row.since,
            until=row.until,
            window=ContactWindow.of(Window.decode(row.window)) if row.window else None,
            removed=row.lifted_at is not None,
        )

    async def _salt(self, space_id: UUID, source_id: UUID) -> tuple[str, bytes]:
        salt = await self._keys.hmac(space_id, SALT_HMAC, f"{source_id}:{SALT_VERSION}".encode())
        return f"ss_{hashlib.sha256(salt).hexdigest()[:8]}", salt

    # The durable record.

    async def run_writer(self) -> None:
        """The batch writer: one statement per space of each batch of up to 500; a failed batch goes back to
        the queue whole, and writing it again changes nothing already written."""
        assert self._feed is not None, "the writer runs where the queue is consumed"
        async for batch in self._feed.batches(WRITER_BATCH):
            try:
                await self.write(batch.records)
            except Exception:
                log.exception("coordination batch failed; returned to the queue")
                await batch.retry_later()
            else:
                await batch.ack()

    async def write(self, records: Sequence[CoordinationRecord]) -> None:
        """What an erasure fenced off is left out: a record made before its subject was erased, still in the
        queue while the erasure ran, never brings the subject back (24.5.5). A store that does not answer
        sends the batch back to the queue."""
        spaces: dict[UUID, list[CoordinationRecord]] = {}
        for record in records:
            spaces.setdefault(record.space_id, []).append(record)
        kept: list[CoordinationRecord] = []
        for space_id, items in spaces.items():
            named = {found[0] for found in map(about, items) if found is not None}
            try:
                fenced = await self._erasures.fenced(space_id, list(named))
            except CacheUnavailable:
                raise CoordinationUnavailable("the state store is unavailable", retry_after_s=5) from None
            kept += unfenced(items, {mark: _ms(at) for mark, at in fenced.items()})
        for batch in by_space(kept):
            try:
                async with self._uow(batch.space_id) as uow:
                    await uow.coordinate.write(batch)
            except NotFound:
                log.warning(
                    "coordination records of a space that is gone were dropped", extra={"n": batch.size}
                )

    async def watch_state_store(self) -> bool:
        """Run by the cell's loops process every few seconds: once the state store came back empty, reloads
        every space's live effects, claims and suppressions from the durable record and seals it whole. True
        when it sealed it."""
        now_ms = _ms(self._clock.now())
        try:
            life = await self._store.life(now_ms=now_ms)
            if life.whole:
                return False
            token = uuid4().hex
            if not await self._store.claim_rebuild(token, REBUILD_LEASE_MS):
                return False
        except CacheUnavailable:
            return False
        log.warning("the state store lost coordination; reloading it from the durable record")
        for config in self._config.spaces():
            if Feature.COORDINATION not in config.features.enabled:
                continue
            try:
                await self._reload_space(config.space_id, life, token)
            except (Unavailable, NotFound):
                continue
        return await self._store.seal(life)

    async def _reload_space(self, space_id: UUID, life: StoreLife, token: str) -> None:
        settings = self._settings(space_id)
        now = self._clock.now()
        after: bytes | None = None
        while True:
            async with self._uow(space_id) as uow:
                effects = await uow.coordinate.live_effects(now, after, REBUILD_PAGE)
            for key_hash, snapshot in effects:
                key = self._store.effect_key(space_id, b64url(key_hash))
                await self._store.load_effect(key, snapshot, _left_ms(snapshot, _ms(now)))
            await self._store.extend_rebuild(token, REBUILD_LEASE_MS)
            if len(effects) < REBUILD_PAGE:
                break
            after = effects[-1][0]
        last: tuple[str, bytes] | None = None
        while True:
            async with self._uow(space_id) as uow:
                targets = await uow.coordinate.live_targets(now, last, REBUILD_PAGE)
            for state in targets:
                await self._load(space_id, await self._tag_of(space_id, state), state, life, settings, now)
            await self._store.extend_rebuild(token, REBUILD_LEASE_MS)
            if len(targets) < REBUILD_PAGE:
                break
            last = (targets[-1].kind, targets[-1].target)

    # What the turns report.

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:
        """The effects turn records report after the fact (the coordination spec, 7.6), inside the group's
        transaction: the state store takes each report, and the group's effects are one statement. A group
        that runs again reports the same states and writes the same rows."""
        config = self._config.space(space_id)
        if Feature.COORDINATION not in config.features.enabled:
            return
        reported = [
            (turn, effect)
            for turn in turns
            for effect in turn.record.effects
            if effect.state in ("done", "unknown_outcome")
        ]
        if not reported:
            return
        ttl_ms = _effect_ttl_ms(config.coordination, EffectKind.DELIVERY)
        now_ms = _ms(self._clock.now())
        records: list[CoordinationRecord] = []
        for turn, effect in reported:
            effect_hash = await self._effect_hash(space_id, effect.key)
            snapshot, _ = await self._store.record_effect(
                self._store.effect_key(space_id, effect_hash),
                EffectState(effect.state),
                kind=EffectKind.DELIVERY.value,
                source=str(turn.source_id),
                agent=turn.record.agent.name,
                ttl_ms=ttl_ms,
                now_ms=now_ms,
            )
            records.append(EffectRecord(space_id, unb64url(effect_hash), snapshot, turn.source_id))
        for batch in by_space(records):
            await uow.coordinate.write(batch)

    # What the check and the declarations share.

    async def _ensure(
        self, space_id: UUID, target: _Target, life: StoreLife, settings: CoordinationSettings, now: datetime
    ) -> None:
        """Loads a subject's or an object's claims and suppressions from the durable record, the first time
        it is touched in a life of the store that is not whole yet: one statement, off the steady path."""
        if life.whole:
            return
        async with self._uow(space_id) as uow:
            state = await uow.coordinate.target(target.raw, kind=target.kind, now=now)
        await self._load(space_id, target.tag, state, life, settings, now)

    async def _load(
        self,
        space_id: UUID,
        tag: str,
        state: TargetState,
        life: StoreLife,
        settings: CoordinationSettings,
        now: datetime,
    ) -> None:
        # A reload never brings back what an erasure fenced off, should a straggling row be left. Erasures
        # fence subjects only, by the keyed hash the row carries.
        erased = None
        if state.kind == "subject":
            erased = (await self._erasures.fenced(space_id, [state.target])).get(state.target)
        fenced = -1 if erased is None else _ms(erased)
        state = dataclasses.replace(
            state,
            claims=tuple(c for c in state.claims if c.claimed_at_ms > fenced),
            spends=tuple(s for s in state.spends if _ms(s.spent_at) > fenced),
            commitments=tuple(c for c in state.commitments if _ms(c.made_at) > fenced),
        )
        levels = settings.ownership.levels
        claims = {
            _slot(c): Claim(
                slot=_slot(c),
                rank=levels.index(c.level) if c.level in levels else len(levels),
                level=c.level,
                holder=c.holder,
                kind=ClaimKind(c.kind),
                via=ClaimVia(c.via),
                since_ms=c.claimed_at_ms,
                until_ms=c.valid_until_ms,
                epoch=c.epoch,
                claim_id=str(c.claim_id),
                intents=c.intents,
            )
            for c in sorted(state.claims, key=lambda c: c.claimed_at_ms)
        }
        suppressions = {
            _suppression_field(row.purpose, row.channel, row.source_id): (
                f"{_ms(row.until) if row.until is not None else 0}|{row.suppression_id}"
                + (f"|{row.window}" if row.window else "")
            )
            for row in state.suppressions
        }
        spends = [
            BudgetSpend(
                str(row.spend_id), row.budget, row.slice, row.units, _ms(row.spent_at), _ms(row.until)
            )
            for row in state.spends
        ]
        commitments = {
            row.external_id: CommitmentEntry(
                str(row.commitment_id),
                row.type,
                row.compatibility,
                _ms(row.valid_until),
                row.score,
                row.agent,
            )
            for row in state.commitments
        }
        await self._store.load_target(
            space_id,
            tag,
            life,
            claims=claims,
            suppressions=suppressions,
            spends=spends,
            commitments=commitments,
            now_ms=_ms(now),
        )

    async def _effect(
        self, space_id: UUID, effect_hash: str, life: StoreLife, now_ms: int
    ) -> EffectSnapshot | None:
        """The effect as the store holds it; while the store is not whole, a key it lacks is looked up in the
        durable record once and loaded."""
        key = self._store.effect_key(space_id, effect_hash)
        found, lapsed = await self._store.peek_effect(key, now_ms=now_ms)
        if found is None and not life.whole:
            async with self._uow(space_id) as uow:
                stored = await uow.coordinate.effect(unb64url(effect_hash))
            if stored is not None:
                await self._store.load_effect(key, stored, _left_ms(stored, now_ms))
                found, lapsed = await self._store.peek_effect(key, now_ms=now_ms)
        if lapsed and found is not None:
            record = EffectRecord(space_id, unb64url(effect_hash), found, _source(found))
            await self._publish_quietly([record])
        return found

    async def _publish_quietly(self, records: Sequence[CoordinationRecord]) -> None:
        if not await self._log.publish(records, self._publish_timeout_s):
            log.warning("a coordination record missed the durable record", extra={"n": len(records)})

    async def _once(
        self,
        caller: SourcePrincipal,
        route: str,
        idempotency_key: str,
        model: type[M],
        run: Callable[[], Awaitable[M]],
    ) -> M:
        """The first answer to an `Idempotency-Key`, for ten minutes: a retry after a lost answer gets it
        again instead of acting twice."""
        digest = hashlib.sha256(f"{caller.source_id}\n{route}\n{idempotency_key}".encode()).hexdigest()[:32]
        key = self._store.idempotency_key(caller.space.space_id, digest)
        try:
            if not await self._store.remember(key, b"", IDEMPOTENCY_PENDING_MS):
                seen = await self._store.recall(key)
                if seen:
                    return model.model_validate_json(seen)
                raise Conflict("the same request is still running")
        except CacheUnavailable:
            raise CoordinationUnavailable("the state store is unavailable", retry_after_s=1) from None
        try:
            answer = await run()
        except BaseException:
            with contextlib.suppress(CacheUnavailable):
                await self._store.forget(key)
            raise
        try:
            await self._store.overwrite(key, answer.model_dump_json().encode(), IDEMPOTENCY_TTL_MS)
        except CacheUnavailable:
            log.warning("an idempotency key's answer was not kept")
        return answer

    async def _subject(self, space_id: UUID, handle: Handle) -> _Subject:
        canonical, kind = subject_canonical(handle.type, handle.value, handle.scope, handle.subject_kind)
        digest = await self._keys.hmac(space_id, SUBJECT_HMAC, canonical.encode())
        return _Subject(b64url(digest), digest, canonical, kind)

    async def _destination(self, space_id: UUID, handle: Handle | None) -> _Subject:
        if handle is None:
            raise InvalidInput("a suppression names its subject")
        if handle.type not in DESTINATIONS:
            canonical_destination(handle.type.value, handle.value)  # raises unsupported_type
        return await self._subject(space_id, handle)

    async def _object(self, space_id: UUID, ref: ObjectRef) -> _Target:
        raw = f"{ref.type}\t{ref.namespace}\t{ref.id}".encode()
        return _Target(b64url(await self._keys.hmac(space_id, OBJECT_HMAC, raw)), raw, "object")

    async def _target(self, space_id: UUID, subject: Handle | None, object_: ObjectRef | None) -> _Target:
        if (subject is None) == (object_ is None):
            raise InvalidInput("a claim names a subject or an object")
        if object_ is not None:
            return await self._object(space_id, object_)
        assert subject is not None
        found = await self._subject(space_id, subject)
        return _Target(found.tag, found.hmac)

    async def _tag_of(self, space_id: UUID, state: TargetState) -> str:
        if state.kind == "space":
            return SPACE_TAG
        if state.is_object:
            return b64url(await self._keys.hmac(space_id, OBJECT_HMAC, state.target))
        return b64url(state.target)

    async def _effect_hash(self, space_id: UUID, effect_key: str) -> str:
        return b64url(await self._keys.hmac(space_id, EFFECT_HMAC, effect_key.encode()))

    def _quiet_until_ms(self, space_id: UUID, channel_rule: ChannelRule | None, now: datetime) -> int | None:
        quiet = channel_rule.quiet_hours if channel_rule is not None else None
        if quiet is None:
            return None
        zone = ZoneInfo(quiet.timezone or self._config.space(space_id).settings.timezone)
        until = quiet_until(time.fromisoformat(quiet.start), time.fromisoformat(quiet.end), zone, now)
        return _ms(until) if until is not None else None

    def _settings(self, space_id: UUID) -> CoordinationSettings:
        return self._config.space(space_id).coordination


def _claimed(claim_id: UUID, hold: _Hold, epoch: int) -> OwnershipClaim:
    return OwnershipClaim(
        claim_id=str(claim_id),
        kind=hold.kind,
        holder=hold.holder,
        level=hold.level,
        valid_until=_at(hold.until_ms),
        epoch=epoch,
    )


def _lock(claim: Claim) -> Lock:
    return Lock(
        kind=ClaimKind.TASK_LOCK.value,
        task=claim.slot.removeprefix("t:"),
        holder=claim.holder,
        until=_at(claim.until_ms),
    )


def _slot(claim: OwnershipRecord) -> str:
    """Where the state store keeps a claim of the durable record: by its task, its observer or its source."""
    if claim.task is not None:
        return f"t:{claim.task}"
    if claim.observer is not None:
        return f"o:{claim.observer}"
    return f"d:{claim.source_id}:{claim.holder}"


def _handle(record: HandleRecord) -> Handle:
    return Handle(
        type=record.type, value=record.value, scope=record.scope or None, subject_kind=record.subject_kind
    )


def _contact_budget(purpose: str, portion: BudgetSlice | None = None) -> str:
    """The state store's window of a purpose's contact budget, or of one funnel stage's slice of it."""
    return f"c:{purpose}" if portion is None else f"c:{purpose}:{portion.intent}"


def _paid_budget(operation: str) -> str:
    return f"p:{operation}"


def _slice(slices: Sequence[BudgetSlice], intent: str) -> BudgetSlice | None:
    return next((s for s in slices if s.intent == intent), None)


def _spacing_ms(portion: BudgetSlice | None) -> int:
    """A spread slice gives out its units evenly: one every `spread_days` over `limit`."""
    if portion is None or portion.spread_days is None:
        return 0
    return portion.spread_days * 86_400_000 // portion.limit


def _gateway(settings: CoordinationSettings, request: CheckRequest) -> Gateway | None:
    """The dispatch point a token is for: the one named, or the only one serving the channel. None where no
    gateway serves the channel: nothing there checks tokens."""
    if request.gateway_id is not None:
        named = next((g for g in settings.gateways if g.gateway_id == request.gateway_id), None)
        if named is None or request.channel is None or request.channel not in named.channels:
            raise InvalidInput("unknown gateway, or not one of the check's channel")
        return named
    serving = [g for g in settings.gateways if request.channel is not None and request.channel in g.channels]
    if len(serving) > 1:
        raise InvalidInput("several gateways serve the channel: name the contact's `gateway_id`")
    return serving[0] if serving else None


def _uuid7_ms(value: UUID) -> int:
    return value.int >> 80


def _unknown(what: str, name: str, declared: Iterable[str]) -> str:
    """An unknown name, with the ones the coordination document declares: the fix is in the message."""
    names = ", ".join(declared) or "none"
    return f"unknown {what} {name}: the coordination document declares {names}"


def _purpose(settings: CoordinationSettings, name: str) -> PurposeRule:
    """The rule of a contact purpose. One the document does not declare is treated as `service` (zero touch):
    it fails open and needs no token, as serving the customer does. Only a document without a
    `service` rule refuses it, naming the ones it declares."""
    for rule in settings.purposes:
        if rule.name == name:
            return rule
    for rule in settings.purposes:
        if rule.name == "service":
            return rule
    # A contact's purpose is the coordination document's, not a data purpose (`purposes`): say which.
    declared = ", ".join(sorted(rule.name for rule in settings.purposes))
    raise InvalidInput(f"unknown purpose {name}: the coordination document declares {declared}")


def _blocks(claim: Claim, agent: str, intent: str) -> bool:
    return claim.holder != agent and intent not in claim.intents


def _suppression_field(purpose: str, channel: str | None, source_id: UUID | None) -> str:
    return f"{purpose}|{channel or '*'}|{source_id}"


def _effect_ttl_ms(settings: CoordinationSettings, kind: EffectKind) -> int | None:
    days = settings.effect_key_days.get(kind, 7)
    return None if days is None else days * 86_400_000


def _left_ms(snapshot: EffectSnapshot, now_ms: int) -> int | None:
    if snapshot.ttl_ms is None:
        return None
    return max(snapshot.reserved_at_ms + snapshot.ttl_ms - now_ms, 1)


def _effect_status(found: EffectSnapshot | None) -> EffectStatus:
    if found is None or found.state is EffectState.FAILED:
        return EffectStatus(state="none")
    return EffectStatus(state=_check_state(found.state.value), attempt=found.attempt)


def _check_state(state: str) -> str:
    return {"reserved": "in_flight", "in_flight": "in_flight"}.get(state, state)


def _effect(effect_hash: str, snapshot: EffectSnapshot) -> Effect:
    return Effect(
        effect_id=effect_hash,
        state=snapshot.state.value,
        attempt=snapshot.attempt,
        reserved_at=_at(snapshot.reserved_at_ms),
        done_at=_at(snapshot.done_at_ms),
    )


def _owner(claim: Claim | None) -> Owner | None:
    if claim is None:
        return None
    _, source, _ = [*claim.slot.split(":", 2), "", ""][:3]
    return Owner(
        holder=claim.holder,
        kind=claim.kind,
        level=claim.level,
        source=source or claim.slot,
        via=claim.via,
        since=_at(claim.since_ms),
        valid_until=_at(claim.until_ms),
        epoch=claim.epoch,
    )


def _channel(view: ChannelView | None) -> ChannelState | None:
    if view is None:
        return None
    return ChannelState(
        free_form_until=view.free_form_until,
        template_required=view.template_required,
        quiet_until=view.quiet_until,
    )


def _fact(ref: ObjectRef | None) -> str:
    return f"{ref.type}\t{ref.namespace}\t{ref.id}" if ref is not None else ""


def _source(snapshot: EffectSnapshot) -> UUID:
    try:
        return UUID(snapshot.source)
    except ValueError:
        return UUID(int=0)


def _cursor(cursor: str | None, salt_id: str) -> tuple[int | None, bool]:
    """The change sequence a cursor stands at; a cursor of another salt starts over with `reset`."""
    if cursor is None:
        return None, False
    prefix = f"c_{salt_id}_"
    if not cursor.startswith(prefix):
        return None, True
    try:
        return int(cursor.removeprefix(prefix)), False
    except ValueError:
        raise InvalidInput("not a cursor of this list") from None


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _at(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, UTC) if ms is not None else None


def _terms_aad(commitment_id: UUID) -> bytes:
    return f"commitments:{commitment_id}:terms_ct".encode()


def unfinished_objects(state: StateView | None) -> tuple[list[OpenObject], int]:
    """The subject's objects whose lifecycle has not reached a final outcome, as the `now` view reads them, up
    to what a package carries, and how many more there are."""
    if state is None:
        return [], 0
    unfinished = [
        OpenObject(
            ref=ObjectRef(type=o.ref.type, namespace=o.ref.namespace, id=o.ref.id),
            state=o.state,
            since=o.as_of,
        )
        for o in state.objects
        if o.outcome is None or not o.outcome.final
    ]
    return unfinished[:MAX_OPEN_OBJECTS], max(0, len(unfinished) - MAX_OPEN_OBJECTS)


async def _open_promises(uow: UnitOfWork, handle: Handle) -> list[PromiseRef]:
    """The promises still open for the subject's profile: ids, due dates and owners, never their text."""
    key = normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind).key
    found = await uow.identity.find_handle(key.type, key.scope, key.value)
    profile = await uow.identity.profile_of(found.handle_id) if found is not None else None
    if profile is None:
        return []
    items = await uow.memory.open_items_for(profile.handle_ids, OPEN_STATUSES)
    return [
        PromiseRef(id=str(i.item_id), due=i.due_at, owner=i.owner)
        for i in sorted(items, key=lambda i: (i.due_at is None, i.due_at or i.created_at))
    ]


def _handoff_aad(handoff_id: UUID, column: str) -> bytes:
    return f"handoffs:{handoff_id}:{column}".encode()


def _handoff_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFound("no such handoff") from None


def _shadow_message(record: EventRecord) -> ShadowMessage | None:
    """A message of a conversation, as the shadow run reads it: outbound when an agent or a person of the
    company wrote it, named by the first handle it came under."""
    if record.kind is not EventKind.MESSAGE or not record.handle_ids or record.speaker is Speaker.SYSTEM:
        return None
    return ShadowMessage(
        event_id=record.event_id,
        subject=sorted(record.handle_ids)[0],
        channel=record.channel,
        at=record.occurred_at,
        outbound=record.speaker is not Speaker.CUSTOMER,
        agent=record.speaker_id or str(record.source_id),
        conversation=record.conversation_id,
    )


def _tightest(settings: CoordinationSettings) -> tuple[int, timedelta] | None:
    """The contact budget that would refuse first: the fewest contacts per hour of its window."""
    budgets = [p.budget for p in settings.purposes if p.budget is not None]
    if not budgets:
        return None
    tightest = min(budgets, key=lambda b: b.limit / b.per_hours)
    return tightest.limit, timedelta(hours=tightest.per_hours)


class CoordinationReadBlock:
    """The coordination block as a read's `include` names it (24.2.10): from the state store alone, no
    statement. A first contact, or a read that names no handle, gets an empty block."""

    name = "coordination"
    feature = Feature.COORDINATION
    surface = ReceiptSurface.COORDINATION

    def __init__(self, coordinate: CoordinateService) -> None:
        self._coordinate = coordinate

    async def read(self, caller: SourcePrincipal, target: Target | None, ask: BlockAsk) -> BlockRead:  # noqa: ARG002
        record = target.handle if target is not None else None
        handle = _handle(record) if record is not None and record.value else None
        block = await self._coordinate.subject_block(caller.space.space_id, handle)
        detail = {
            "owner": block.owner.level if block.owner is not None else "",
            "suppressions": str(len(block.suppressions)),
            "commitments": str(len(block.commitments_active)),
        }
        return BlockRead(block, detail=detail)
