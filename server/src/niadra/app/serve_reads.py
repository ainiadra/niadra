"""What every read surface shares (5.11, 16.4): who the target is, the effective level, the control
group, the privacy feed, the policy reader and the receipt that leaves before the response."""

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Coroutine, Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.app.pending_proofs import PendingProofs
from niadra.app.read_state import Change, ReadState, thread_key
from niadra.config.models import SpaceConfig
from niadra.contracts.common import Handle, ObjectRef, SourceCoverage
from niadra.domain.common.errors import AboutWithoutLink, NiadraError, ScopeMissing, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.compile import public_item_id
from niadra.domain.compile.model import profile_target
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.identity.provenance import reader_view
from niadra.domain.identity.rules import PROVISIONAL_READ_CAP
from niadra.domain.measure.experiments import ASSIGNED_KINDS, Arm, assign_arms
from niadra.domain.policy import NOT_PROVEN, EffectiveVerification, Reader, effective_verification
from niadra.domain.receipts import ProducerMark, ReceiptDraft, draft_to_json
from niadra.domain.records import BusinessObject, HandleRecord, ObjectKey, ProfileRecord
from niadra.domain.serve.coverage import LOOKBACK, REFRESH, source_coverage
from niadra.domain.serve.holdout import Assignment, assign
from niadra.domain.serve.privacy_guard import REFRESH_EVERY, PrivacyGuard
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    Feature,
    HandleStatus,
    ReceiptKind,
    ReceiptSurface,
    Scope,
    Verification,
)
from niadra.ports.infra import Clock, ReceiptSink, SpaceDatabases
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

RECEIPT_TIMEOUT_S = 0.02
SETTLING = timedelta(seconds=10)
"""A handle first seen this recently is not kept in the read state: its customer's other handles may still
be joining it."""
UNCONFIRMED_LINK = "unconfirmed_link"
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)


@dataclass(frozen=True, slots=True)
class AboutLink:
    """The organization a person acts for, through an active link (21.4)."""

    handle_id: UUID
    profile: ProfileRecord | None
    role: str
    can_see_contacts: bool
    generation: int | None = None
    """The organization's compiled generation when the read began, None when the read state served the link:
    an `about` pack compiled at an older one holds an account block the organization's memory moved past."""
    valid_to: datetime | None = None
    """When the link ends on its own: an end by date fires no change, so a kept link checks it."""


@dataclass(frozen=True, slots=True)
class Draws:
    """What the draws of a profile's experiments take from the database: its oldest handle, and the group or
    arm it first got in each experiment. Kept in the read state beside the targets and dropped by
    the same changes, so a space with an experiment still reads warm with no statement (6.4)."""

    oldest_handle_id: UUID | None
    kept: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Assigned:
    """A read's place in the space's experiments: its memory group, and its arm in each element or agent
    experiment that covers it."""

    memory: Assignment | None = None
    arms: tuple[Arm, ...] = ()


UNASSIGNED = Assigned()


@dataclass(frozen=True, slots=True)
class Target:
    profile: ProfileRecord
    """The reader's view of the profile (5.3): without the rows of unconfirmed links it may not see."""
    handle: HandleRecord | None = None
    about: AboutLink | None = None
    object: BusinessObject | None = None


class ReadSupport:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        receipts: ReceiptSink,
        clock: Clock,
        producer_id: str,
        *,
        guard: PrivacyGuard | None = None,
        buffer: ReceiptBuffer | None = None,
        receipt_timeout_s: float = RECEIPT_TIMEOUT_S,
        databases: SpaceDatabases | None = None,
        state: ReadState | None = None,
        pending: PendingProofs | None = None,
    ) -> None:
        """`pending` holds what turns the write path acknowledged prove before they are stored."""
        self._uow = uow
        self.state = state if state is not None else ReadState()
        self._pending = pending
        self._databases = databases
        self._receipts = receipts
        self._clock = clock
        self.producer_id = producer_id
        self.guard = guard if guard is not None else PrivacyGuard()
        self.buffer = buffer if buffer is not None else ReceiptBuffer()
        self._timeout = receipt_timeout_s
        self._seq = itertools.count(1)
        self._last_seq = 0
        self._guard_lock = asyncio.Lock()
        self._coverage: dict[UUID, tuple[datetime, list[SourceCoverage]]] = {}
        self._jobs: dict[Hashable, asyncio.Task[None]] = {}

    @staticmethod
    def require(caller: SourcePrincipal, scope: Scope) -> None:
        if not caller.allows(scope):
            raise ScopeMissing(f"the key lacks the scope {scope.value}")

    # Targets.

    async def find_handle(self, uow: UnitOfWork, handle: Handle) -> HandleRecord | None:
        key = normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind).key
        return await uow.identity.find_handle(key.type, key.scope, key.value)

    def cached_subject(self, space_id: UUID, handle: Handle) -> Target | None:
        """The target a read by this handle found, while nothing under it changed (`app.read_state`)."""
        found: Target | None = self.state.target(space_id, _subject_key(handle))
        return found

    def cached_profile(self, space_id: UUID, profile_id: UUID) -> Target | None:
        found: Target | None = self.state.target(space_id, ("profile", profile_id))
        return found

    async def resolve_subject(self, uow: UnitOfWork, handle: Handle) -> Target | None:
        """The profile behind a handle; a handle in doubt (maybe recycled, shared or blocked) resolves to
        nobody, so a number that changed hands reads as a first contact (5.3). In doubt, memory tells
        nothing; the agent reads again by a handle that is not in doubt. Only an active handle's target
        is kept for the next reads, and the change feed drops it when the handle or its profile moves."""
        started = self.state.started()
        record = await self.find_handle(uow, handle)
        if record is None or record.status is not HandleStatus.ACTIVE:
            return None
        profile = await uow.identity.profile_of(record.handle_id)
        if profile is None:
            return None
        # Through a confirmed handle, the confirmed rows; through an unconfirmed link, its own rows too.
        target = Target(reader_view(profile, (record.handle_id,)), record)
        if record.first_seen > self._clock.now() - SETTLING:
            # A customer's first seconds are when its handles join: the handle a first message brings (a
            # WhatsApp id beside the verified phone) is stored by a worker after this read, and a target kept
            # now would read without it until the change feed's next poll, up to 2 s (28/09/2026).
            return target
        self.state.store_target(
            uow.space_id,
            _subject_key(handle),
            target,
            handle_ids=(*profile.handle_ids, record.handle_id),
            profile_id=profile.profile_id,
            started=started,
        )
        return target

    async def resolve_object(self, uow: UnitOfWork, ref: ObjectRef) -> Target | None:
        found = await uow.objects.get_object(ObjectKey(ref.type, ref.namespace, ref.id))
        if found is None or found.origin_handle_id is None:
            return None
        profile = await uow.identity.profile_of(found.origin_handle_id)
        # No handle to read through: the rows of unconfirmed links stay out, as for a read by profile.
        return Target(reader_view(profile), object=found) if profile else None

    async def resolve_profile(self, uow: UnitOfWork, profile_id: UUID) -> Target | None:
        """A read by profile id carries no handle: it sees the confirmed rows only, whatever the source
        (5.3). Internal agents and governance keys read this way; no source key reads unconfirmed links."""
        started = self.state.started()
        profile = await uow.identity.get_profile(profile_id)
        if profile is None:
            return None
        target = Target(reader_view(profile))
        self.state.store_target(
            uow.space_id,
            ("profile", profile_id),
            target,
            handle_ids=profile.handle_ids,
            profile_id=profile_id,
            started=started,
        )
        return target

    async def attach_about(self, uow: UnitOfWork, target: Target | None, about: Handle) -> Target | None:
        """`about` needs an active person-organization link; the error never says if the org exists.

        A read by object has no handle: the object's owner reads. When `about` names that owner itself (a
        drafting task on the firm's case, about the firm), the read already is the organization's memory and
        needs no link (B20, 03/10/2026); when the owner is a person, the person's link counts."""
        started = self.state.started()
        org = await self.find_handle(uow, about)
        if target is None or org is None:
            raise AboutWithoutLink("no active link between the subject and that organization")
        org_profile = await uow.identity.profile_of(org.handle_id)
        orgs = set(org_profile.handle_ids) if org_profile is not None else {org.handle_id}
        if target.handle is None and orgs & set(target.profile.handle_ids):
            return target
        # The link is the person's and the organization's, whichever of their handles it was made on: a
        # link made on the e-mail holds for a read by the phone.
        people = set(target.profile.handle_ids) | ({target.handle.handle_id} if target.handle else set())
        link = next(
            (
                link
                for link in await uow.identity.active_links(sorted(people))
                if link.person_handle_id in people and link.org_handle_id in orgs
            ),
            None,
        )
        if link is None:
            raise AboutWithoutLink("no active link between the subject and that organization")
        generation = (
            await uow.compile.current_generation(profile_target(org_profile.profile_id))
            if org_profile is not None
            else 0
        )
        found = AboutLink(
            org.handle_id, org_profile, link.role, link.can_see_contacts, generation, link.valid_to
        )
        if org_profile is not None and target.handle is not None:
            # The next reads of this person about this organization resolve it with no statement, until a
            # link, a handle or either profile changes (the change feed) or the link's own end comes.
            self.state.store_target(
                uow.space_id,
                _about_key(target, about),
                replace(found, generation=None),
                handle_ids=(*people, *orgs, org_profile.profile_id),
                profile_id=target.profile.profile_id,
                started=started,
            )
        return replace(target, about=found)

    def cached_about(self, space_id: UUID, target: Target, about: Handle) -> AboutLink | None:
        """The organization a read of this person about it found, while its link and both sides hold."""
        if target.handle is None:
            return None
        found: AboutLink | None = self.state.target(space_id, _about_key(target, about))
        if found is None or (found.valid_to is not None and found.valid_to <= self._clock.now()):
            return None
        return found

    # Verification, control group, reader.

    async def verification(
        self,
        uow: UnitOfWork,
        caller: SourcePrincipal,
        requested: Verification,
        *,
        conversation_id: str | None,
        task_id: str | None,
        target: Target | None = None,
        held: Verification | None = None,
        inactivity: Mapping[str, int] | None = None,
    ) -> EffectiveVerification:
        """min(requested, source ceiling, proven in this conversation or task) (2.2).

        Proven is only what the conversation or the task proved: its highest `verify` still valid and
        the channel's own attestation. A handle's past never counts: the next call from the same
        number, maybe spoofed or recycled, starts over at what that call proves. A read through a
        provisional handle (joined by a claim nobody confirmed) stays at what its channel shows, V1:
        proving the handle is not proving that the profile it was joined to is its holder's (5.3).

        `held` is what the conversation's turns waiting to be stored prove (`held`, read before the
        transaction): it counts when the database proves less, and is never kept in the read state, which
        holds only what is stored. `inactivity`, the space's minutes per channel, is how long a conversation
        closed on a quiet keeps its proof (`held_proof`)."""
        cached = self.cached_verification(
            caller, requested, conversation_id=conversation_id, task_id=task_id, target=target
        )
        if cached is not None:
            return cached
        started = self.state.started()
        found = await uow.sessions.proof(
            conversation_id=conversation_id,
            task_id=task_id,
            at=self._clock.now(),
            minutes_by_channel=inactivity,
        )
        proven = Verification.V0
        if found is not None and found[0] is not Verification.NO_CUSTOMER:
            proven = found[0]
            thread = thread_key(conversation_id, task_id)
            assert thread is not None
            self.state.store_proof(uow.space_id, thread, proven, found[1], started=started)
        return self._raised(caller, requested, self._level(caller, requested, proven, target), held, target)

    async def held(
        self,
        caller: SourcePrincipal,
        requested: Verification,
        *,
        conversation_id: str | None,
        task_id: str | None,
    ) -> Verification | None:
        """What the conversation's turns acknowledged and not stored yet prove (`app.pending_proofs`), read
        outside any transaction: None, with no I/O, when the read needs no proof or the read state already
        holds one that covers it."""
        thread = thread_key(conversation_id, task_id)
        needed = _needed(requested, caller.verification_ceiling)
        if (
            self._pending is None
            or requested is Verification.NO_CUSTOMER
            or thread is None
            or needed.rank <= 0
        ):
            return None
        space_id = caller.space.space_id
        if self.state.proof(space_id, thread, needed, self._clock.now()) is not None:
            return None
        return await self._pending.level(space_id, thread)

    async def with_held(
        self,
        caller: SourcePrincipal,
        requested: Verification,
        level: EffectiveVerification,
        *,
        conversation_id: str | None,
        task_id: str | None,
        target: Target | None = None,
    ) -> EffectiveVerification:
        """After the transaction: a level the database proved less than asked for, raised by what the
        conversation's waiting turns prove. One round trip to the state store, only on that path."""
        if level.reason != NOT_PROVEN:
            return level
        held = await self.held(caller, requested, conversation_id=conversation_id, task_id=task_id)
        return self._raised(caller, requested, level, held, target)

    def _raised(
        self,
        caller: SourcePrincipal,
        requested: Verification,
        level: EffectiveVerification,
        held: Verification | None,
        target: Target | None,
    ) -> EffectiveVerification:
        # Lowered for the proof alone, the effective level is what the conversation proved.
        if level.reason != NOT_PROVEN or held is None or held.rank <= level.effective.rank:
            return level
        return self._level(caller, requested, held, target)

    def cached_verification(
        self,
        caller: SourcePrincipal,
        requested: Verification,
        *,
        conversation_id: str | None,
        task_id: str | None,
        target: Target | None = None,
    ) -> EffectiveVerification | None:
        """The level without the database, when nothing a proof could add is missing: a read that asks
        for no more than V0 (or than its source's ceiling) needs no proof, and a conversation's proof kept
        from an earlier read serves a read that needs no more than it proves. None: ask the database."""
        thread = thread_key(conversation_id, task_id)
        needed = _needed(requested, caller.verification_ceiling)
        if requested is Verification.NO_CUSTOMER or thread is None or needed.rank <= 0:
            return self._level(caller, requested, Verification.V0, target)
        proven = self.state.proof(caller.space.space_id, thread, needed, self._clock.now())
        return self._level(caller, requested, proven, target) if proven is not None else None

    @staticmethod
    def _level(
        caller: SourcePrincipal, requested: Verification, proven: Verification, target: Target | None
    ) -> EffectiveVerification:
        level = effective_verification(
            requested, ceiling=caller.verification_ceiling, proven=proven, audience=caller.audience
        )
        handle = target.handle if target is not None else None
        if (
            handle is not None
            and handle.provisional
            and level.effective is not Verification.NO_CUSTOMER
            and level.effective.rank > PROVISIONAL_READ_CAP.rank
        ):
            return EffectiveVerification(requested, PROVISIONAL_READ_CAP, UNCONFIRMED_LINK)
        return level

    async def assignment(
        self, uow: UnitOfWork, caller: SourcePrincipal, config: SpaceConfig, target: Target
    ) -> Assignment | None:
        return (await self.assigned(uow, caller, config, target)).memory

    async def assigned(
        self, uow: UnitOfWork, caller: SourcePrincipal, config: SpaceConfig, target: Target
    ) -> Assigned:
        """The memory group and the element and agent arms of this read, drawn from the profile's draws: from
        the read state, else one statement for the handles and one for what the profile was assigned before,
        kept for the next reads (6.4)."""
        if not _experimenting(config):
            return UNASSIGNED
        draws = self._cached_draws(uow.space_id, target)
        if draws is None:
            started = self.state.started()
            handles = await uow.identity.get_handles(target.profile.handle_ids)
            oldest = min(handles, key=lambda h: (h.first_seen, str(h.handle_id))) if handles else None
            kept = await uow.serve.get_assignments(target.profile.profile_id)
            draws = Draws(oldest.handle_id if oldest else None, kept)
            self.state.store_target(
                uow.space_id,
                _draws_key(target),
                draws,
                handle_ids=target.profile.handle_ids,
                profile_id=target.profile.profile_id,
                started=started,
            )
        return self._assigned(caller, config, draws)

    def cached_assigned(
        self, caller: SourcePrincipal, config: SpaceConfig, target: Target
    ) -> Assigned | None:
        """`assigned` without the database, when the read state holds the profile's draws; None: ask it."""
        if not _experimenting(config):
            return UNASSIGNED
        draws = self._cached_draws(caller.space.space_id, target)
        return self._assigned(caller, config, draws) if draws is not None else None

    def _cached_draws(self, space_id: UUID, target: Target) -> "Draws | None":
        found: Draws | None = self.state.target(space_id, _draws_key(target))
        return found

    def _assigned(self, caller: SourcePrincipal, config: SpaceConfig, draws: "Draws") -> Assigned:
        if draws.oldest_handle_id is None:
            return UNASSIGNED
        source = config.source(caller.source_id)
        critical = bool(source and source.critical)
        now = self._clock.now()
        drawn = assign(
            config.experiments,
            source_id=caller.source_id,
            critical_source=critical,
            oldest_handle_id=draws.oldest_handle_id,
            now=now,
        )
        if drawn is not None and drawn.group != "shadow":
            # A profile keeps the group it first got: a union may change its oldest handle.
            kept = draws.kept.get(drawn.experiment)
            drawn = Assignment(drawn.experiment, kept) if kept else drawn
        arms: tuple[Arm, ...] = ()
        if Feature.MEASUREMENT in config.features.enabled:
            arms = assign_arms(
                config.measurement.experiments,
                source_id=caller.source_id,
                source_name=source.name if source else None,
                critical_source=critical,
                oldest_handle_id=draws.oldest_handle_id,
                kept=draws.kept,
                now=now,
            )
        return Assigned(drawn, arms)

    @staticmethod
    def reader(caller: SourcePrincipal) -> Reader:
        return Reader(caller.audience, caller.purposes, caller.source_id)

    # Coverage.

    def cached_coverage(self, config: SpaceConfig) -> list[SourceCoverage] | None:
        """Per space: it describes the space's sources, not one customer (5.2). Past `REFRESH` the last
        answer still serves while one background read renews it; None only before the first."""
        cached = self._coverage.get(config.space_id)
        if cached is None:
            return None
        if self._clock.now() - cached[0] >= REFRESH:
            self.later(("coverage", config.space_id), self._renew_coverage(config))
        return cached[1]

    async def coverage(self, uow: UnitOfWork, config: SpaceConfig) -> list[SourceCoverage]:
        cached = self.cached_coverage(config)
        return cached if cached is not None else await self._load_coverage(uow, config)

    async def _renew_coverage(self, config: SpaceConfig) -> None:
        async with self._uow(config.space_id) as uow:
            await self._load_coverage(uow, config)

    async def _load_coverage(self, uow: UnitOfWork, config: SpaceConfig) -> list[SourceCoverage]:
        now = self._clock.now()
        last = await uow.ingest.source_last_event_at(now - LOOKBACK)
        found = [
            SourceCoverage(source_id=str(s.source_id), status=s.status, last_event_at=s.last_event_at)
            for s in source_coverage(last, {s.source_id for s in config.sources}, now)
        ]
        self._coverage[config.space_id] = (now, found)
        return found

    # Work a read starts and never waits for.

    def later(self, key: Hashable, work: Coroutine[Any, Any, object]) -> None:
        """Runs `work` in the background, one at a time per `key`; a failure is logged, never raised."""
        running = self._jobs.get(key)
        if running is not None and not running.done():
            work.close()
            return
        task = asyncio.create_task(_logged(work, key))
        self._jobs[key] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._jobs.get(key) is done:
                del self._jobs[key]

        task.add_done_callback(forget)

    async def drain(self) -> None:
        """Waits for the background work in flight (tests, and a stopping process)."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)
            # A gather of jobs already done returns without yielding: their callbacks, which forget them,
            # run only once the loop gets a turn.
            await asyncio.sleep(0)

    # Privacy feed.

    async def follow_feeds(self, space_id: UUID) -> None:
        """The start of every read: the space stays polled, and the feeds are fresh enough."""
        self.state.read(space_id)
        if (waiting := self.refresh_privacy_ahead()) is not None:
            await waiting

    def refresh_privacy_ahead(self) -> Awaitable[None] | None:
        """What a read does about the feeds: the process's first read waits for them (nothing guarded is
        known yet); after it, a due poll runs in the background and the read goes on with the guard as it
        is, whose age the answer's `degraded` reports, and with the state `read_state` still vouches for.
        A poll two periods late makes the read wait, as the first one does. Returns the poll to await when
        the read must wait for it."""
        refreshed = self.guard.refreshed_at
        if refreshed is None or self._clock.now() - refreshed > 2 * REFRESH_EVERY:
            # The process's first read, or a poll two periods late (the loop stalled): the read waits.
            return self.refresh_privacy()
        if self.guard.due(self._clock.now()):
            self.later("privacy", self.refresh_privacy())
        return None

    async def refresh_privacy(self) -> None:
        """Polls the feeds on the writer every 2 s: the privacy feed, for the guard, and the changes under
        this process's read state (`read_invalidations`), in one transaction per space.

        Each space keeps its feeds in its own database, so this reads the spaces with an open connection
        pool and the ones this process read recently: a warm read never opens a pool, and its space must
        still be polled while its packs and targets are served from memory.
        """
        now = self._clock.now()
        # Nobody waits behind a refresh in flight: it updates the guard for the whole process, and a
        # queue of readers behind it is how a slow refresh became every read's wait.
        if not self.guard.due(now) or self._guard_lock.locked():
            return
        async with self._guard_lock:
            if not self.guard.due(now):
                return
            since = self.guard.window_start(now)
            active = self._databases.active_spaces() if self._databases else ()
            rows = []
            failed = False
            for space_id in self.state.spaces_to_poll(active):
                try:
                    async with self._uow(space_id) as uow:
                        feed = await uow.privacy.feed_since(since)
                        changes = await uow.serve.invalidations_since(since)
                except (NiadraError, *_CACHE_ERRORS):
                    log.warning("privacy feed unavailable; the guard goes stale")
                    failed = True
                    continue
                rows.extend(feed)
                for row in feed:
                    # Belt and braces: an erasure or a split also reaches the read state by its own rows.
                    self.state.invalidate(space_id, Change.of_row(row))
                self.state.polled(space_id, changes)
            # A space that failed leaves the guard's clock where it was: it goes stale (L10).
            self.guard.apply(rows, now, complete=not failed)

    def required_privacy(self, profile: ProfileRecord) -> int:
        """A pack must be at least as new as the profile's privacy version on the replica and in the feed."""
        return max(profile.privacy_version, self.guard.required_version(profile.profile_id) or 0)

    # Receipts.

    @property
    def deny_sensitive(self) -> bool:
        """Receipts piling up in memory: stop serving sensitive categories until they drain (5.11, L10)."""
        return self.buffer.full

    async def ensure_can_record(self) -> None:
        if self.buffer.exhausted and not await self.flush():
            raise Unavailable("reads are paused until receipts can be recorded")

    def draft(
        self,
        caller: SourcePrincipal,
        kind: ReceiptKind,
        surface: ReceiptSurface,
        **fields: object,
    ) -> ReceiptDraft:
        seq = next(self._seq)
        self._last_seq = seq
        item_ids = fields.pop("item_ids", ())
        assert isinstance(item_ids, tuple | list)
        return ReceiptDraft(
            receipt_id=uuid7(),
            space_id=caller.space.space_id,
            producer_id=self.producer_id,
            producer_seq=seq,
            kind=kind,
            surface=surface,
            audience=caller.audience,
            read_at=self._clock.now(),
            source_id=caller.source_id,
            purpose=",".join(sorted(caller.purposes)) or None,
            item_ids=tuple(public_item_id(str(i)) for i in item_ids),
            **fields,  # type: ignore[arg-type]
        )

    async def emit(self, drafts: Sequence[ReceiptDraft]) -> None:
        """The receipt leaves before the response (5.11): confirmed by the queue within 20 ms, or else
        committed to the outbox, which the relay delivers at least once. Only when both fail does it
        wait in memory, and a full buffer stops sensitive reads, then all reads."""
        pending = [*self.buffer.take(), *drafts]
        if not pending or await self._send(pending):
            return
        self.buffer.add(await self._spool(pending))

    async def flush(self) -> bool:
        pending = self.buffer.take()
        if not pending or await self._send(pending):
            return True
        left = await self._spool(pending)
        self.buffer.add(left)
        return not left

    async def _spool(self, drafts: Sequence[ReceiptDraft]) -> list[ReceiptDraft]:
        """Commits the drafts as `record_receipts` tasks, one per space; returns what it could not."""
        by_space: dict[UUID, list[ReceiptDraft]] = {}
        for draft in drafts:
            by_space.setdefault(draft.space_id, []).append(draft)
        left: list[ReceiptDraft] = []
        for space_id, own in by_space.items():
            task = Task(TaskName.RECORD_RECEIPTS, space_id, {"drafts": [draft_to_json(d) for d in own]})
            try:
                async with self._uow(space_id) as uow:
                    await uow.pipeline.enqueue([task])
            except Exception:  # the database is the last durable place; failing it, memory holds them
                log.warning("receipts could not be spooled to the outbox; buffering in memory")
                left += own
        return left

    async def heartbeat(self, *, final: bool = False) -> None:
        """An idle or stopping producer still moves the cell watermark forward (5.11)."""
        await self._receipts.mark(ProducerMark(self.producer_id, self._last_seq, self._clock.now(), final))

    async def _send(self, drafts: Sequence[ReceiptDraft]) -> bool:
        try:
            # The grace covers a sink that does not honour its own timeout.
            return await asyncio.wait_for(self._receipts.emit(drafts, self._timeout), self._timeout * 2)
        except (TimeoutError, *_CACHE_ERRORS):
            return False


def _about_key(target: Target, about: Handle) -> tuple[Any, ...]:
    key = normalize_handle(about.type, about.value, about.scope, about.subject_kind).key
    return ("about", target.profile.profile_id, target.handle.handle_id if target.handle else None, key)


def _subject_key(handle: Handle) -> tuple[Any, ...]:
    return ("subject", normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind).key)


def _needed(requested: Verification, ceiling: Verification) -> Verification:
    """The level a read gets before its proof counts: min(requested, ceiling) (2.2)."""
    if ceiling is not Verification.NO_CUSTOMER and ceiling.rank < requested.rank:
        return ceiling
    return requested


async def _logged(work: Coroutine[Any, Any, object], key: Hashable) -> None:
    try:
        await work
    except Exception:
        log.warning("background read work failed", extra={"work": str(key)[:40]})


def _experimenting(config: SpaceConfig) -> bool:
    return bool(config.experiments) or (
        Feature.MEASUREMENT in config.features.enabled
        and any(e.kind in ASSIGNED_KINDS for e in config.measurement.experiments)
    )


def _draws_key(target: Target) -> tuple[str, UUID]:
    return ("draws", target.profile.profile_id)
