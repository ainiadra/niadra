"""Identity resolution: reversible identity as a log of assertions (2.5, 5.3).

A profile is the connected component of the live assertions between handles. Ingestion only
attaches handles; merges, unmerges, retractions and blocks run in `identity_apply` under the
lease of the profiles they touch, so a merge never happens inside an ingestion transaction. The
merge an `identify` asks for runs the same way right after its batch commits, before the answer,
with the task as a safety net.
"""

import asyncio
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.app.collaborators import AttachResult, PendingMerge
from niadra.app.notify import enqueue_event
from niadra.app.profile_names import Shown, shown_profiles
from niadra.app.profile_view import ConsoleReading
from niadra.contracts.common import Handle
from niadra.domain.common.errors import Conflict, InvalidInput, NiadraError, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal
from niadra.domain.compile import MemorySnapshot
from niadra.domain.identity.blocked import is_fixed_blocked, is_tenant_blocked
from niadra.domain.identity.display import NAME_PREDICATES
from niadra.domain.identity.graph import connected_components
from niadra.domain.identity.handles import (
    PHONE_FAMILY,
    HandleKey,
    NewHandle,
    NormalizedHandle,
    normalize_handle,
)
from niadra.domain.identity.listing import HANDLES_SHOWN, ListCursor, ListedProfile, ListOrder
from niadra.domain.identity.model import (
    ApplyOp,
    AssertionRecord,
    AssertionState,
    MergeLogEntry,
    MergeOp,
    SuggestionRecord,
)
from niadra.domain.identity.provenance import governance_view
from niadra.domain.identity.rules import (
    CLAIM_METHODS,
    FANOUT_LONG_LIMIT,
    FANOUT_LONG_WINDOW,
    FANOUT_WINDOW,
    LINK_METHODS,
    METHOD_CONFIDENCE,
    ONE_PER_SCOPE,
    PHONE_LIMITED,
    RECONFIRMING_METHODS,
    RECORD_TYPES,
    Rejection,
    can_assert,
    choose_anchor,
    fanout_exceeded,
    is_strong,
    looks_recycled,
    merge_rejection,
    one_per_scope_conflict,
    phones_to_demote,
    profile_kind,
    vouches_for,
)
from niadra.domain.identity.search import is_name_query, is_pseudonym, search_keys
from niadra.domain.identity.suggest import THRESHOLD as SUGGESTION_THRESHOLD
from niadra.domain.identity.suggest import Candidate, suggest
from niadra.domain.memory.facts import normalize_value
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.lease import LEASE_TTL_S, LeaseGrant, profile_unit
from niadra.domain.policy import Effect
from niadra.domain.policy.console import reads_memory
from niadra.domain.records import HandleRecord, LinkRecord, ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.repos.pipeline import LeaseStore
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

SYSTEM_ACTOR = "niadra:identity"
CO_OCCURRENCE_ROLE = "contact"
SUGGEST_PAGE = 1000
NAMED_FACTS = 200
"""Name facts a name search reads at most: a name many customers share finds the latest confirmed ones."""
# A record id in the event brings a number back to its previous holder when the tenant's systems, a login
# or an ordinary event of a system of record carry it (the same rule as `_suspend_silent`).
_VOUCHING_METHODS = RECONFIRMING_METHODS | {AssertionMethod.SAME_EVENT}
DISMISSED = "rejected"
SHARED_HANDLE = "shared_handle"
ACCEPTED = "accepted"
MERGE_SAFETY_NET = timedelta(seconds=10)
"""When the `identity_apply` task of a merge its caller runs after the commit is published: only if that
run failed does the task find anything left to merge."""
MERGE_LEASE_WAIT_S = 2.0
MERGE_LEASE_POLL_S = 0.1
"""A merge run before the answer waits this long for a worker's lease on its profiles (a compilation holds
one for about a second), then leaves the merge to its task."""


@dataclass(frozen=True, slots=True)
class Operation:
    """The answer to a governance call: the change is queued for `identity_apply` or already done."""

    op_id: UUID
    status: str
    """`queued`, `applied` (nothing to move), `linked` (converted into a link) or `blocked`."""
    assertion_id: UUID | None = None
    link_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile: ProfileRecord
    handles: tuple[HandleRecord, ...]
    links: tuple[LinkRecord, ...]

    @property
    def last_seen(self) -> datetime | None:
        return max((h.last_seen for h in self.handles), default=None)


@dataclass(frozen=True, slots=True)
class SuggestionView:
    """A suggestion with what a reviewer needs to judge it: both handles and their current profiles."""

    suggestion: SuggestionRecord
    handles: dict[UUID, HandleRecord]
    profiles: dict[UUID, UUID]


@dataclass(frozen=True, slots=True)
class _Event:
    """What one `attach` call knows while it places the handles of an event."""

    space_id: UUID
    method: AssertionMethod
    origin_event_id: UUID | None
    level: Verification
    at: datetime
    demoted: set[UUID] = field(default_factory=set)
    """Handles this attach made ambiguous or suspected recycled, which its records do not show."""
    created: set[UUID] = field(default_factory=set)
    """Handles this attach inserted: no profile, no assertion and nothing to touch yet."""


class IdentityService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: ConfigProvider,
        *,
        leases: LeaseStore | None = None,
    ) -> None:
        """`leases` takes and gives back a lease in one statement each; without it, each is a transaction
        of the unit of work."""
        self._uow = uow
        self._clock = clock
        self._config = config
        self._leases = leases

    # The synchronous path, inside the ingestion transaction (5.3).

    async def attach(
        self,
        uow: UnitOfWork,
        handles: Sequence[Handle],
        *,
        subject_kind: SubjectKind,
        method: AssertionMethod,
        origin_event_id: UUID | None,
        level: Verification,
        at: datetime,
        merge_after_commit: bool = False,
    ) -> AttachResult:
        if not handles:
            raise InvalidInput("attach needs at least one handle")
        event = _Event(await _space_of(uow), method, origin_event_id, level, at)
        normalized = [self._normalize(h, subject_kind) for h in handles]
        inputs = list(dict.fromkeys(n.key for n in normalized))
        records = await self._ensure_handles(
            uow, event.space_id, _with_derived(normalized), at, event.created
        )
        members = await uow.identity.memberships(
            [r.handle_id for r in records.values() if r.handle_id not in event.created]
        )
        await self._reconfirm(uow, event, records, members)
        before = {key: r.status for key, r in records.items()}
        await self._suspend_silent(uow, event, records, members)
        await self._demote_recycled(uow, records, members, at)
        await self._suspend_on_rupture(uow, event, records, members)
        # Only what this event judged to have changed hands leaves its handle: a phone demoted for the
        # profile's limit (5.3) stays its customer's, and a replay finds the new handle already there.
        changed_hands = {
            key
            for key, r in records.items()
            if r.status is HandleStatus.SUSPECTED_RECYCLED
            and before[key] is not HandleStatus.SUSPECTED_RECYCLED
        }
        barred = await self._retire_changed_hands(uow, event, records, members, changed_hands)
        if method not in CLAIM_METHODS:
            # A claim proves nothing about the handles it names: the conversation's level belongs to the
            # handle it verified, which its own `verify` already raised.
            await self._raise_levels(uow, records, level)

        anchors: dict[SubjectKind, HandleRecord] = {}
        pending: list[AssertionRecord] = []
        merge_due = False
        joined: set[UUID] = set()
        for kind in dict.fromkeys(r.subject_kind for r in records.values()):
            group = [r for r in records.values() if r.subject_kind is kind and can_assert(r)]
            group = _unbarred(event, group, members, barred)
            placed = await self._place_group(uow, event, group, members)
            if placed is not None:
                anchors[kind], new_pending, spans, flagged = placed
                pending += new_pending
                merge_due = merge_due or spans
                records |= {
                    k: replace(r, provisional=True) for k, r in records.items() if r.handle_id in flagged
                }
                joined |= flagged
        confirmed = await self._confirm_claimed(uow, event, records, members)
        await _prewarm_readers(uow, event.space_id, members, joined - confirmed)
        await _request_recompute(uow, event.space_id, sorted({members[h] for h in confirmed}))
        await self._place_singletons(uow, records.values(), members, at)
        await self._link_co_occurrence(uow, event, anchors)
        await uow.identity.touch_handles(
            [r.handle_id for r in records.values() if r.handle_id not in event.created], at
        )
        merge: PendingMerge | None = None
        if pending:
            ids = [a.assertion_id for a in pending]
            due = at + MERGE_SAFETY_NET if merge_after_commit else None
            op_id = await self._enqueue_apply(uow, event.space_id, ids, members, due=due)
            merge = PendingMerge(op_id, tuple(ids), tuple(sorted(set(members.values()))))

        event_anchor = anchors.get(subject_kind) or choose_anchor(
            [r for r in records.values() if r.subject_kind is subject_kind] or records.values()
        )
        # A test or placeholder value (5.3) keeps its own profile, as every handle does, but an event that
        # names nobody else belongs to no customer: many people type the same fake number.
        blocked = event_anchor.status is HandleStatus.BLOCKED
        anchor = anchors.get(subject_kind)
        return AttachResult(
            handle_ids=tuple(records[k].handle_id for k in inputs),
            profile_id=None if blocked else members[event_anchor.handle_id],
            pending_merge=merge_due,
            anchor_handle_id=anchor.handle_id if anchor else None,
            evidence={}
            if blocked
            else {
                k: r.handle_id
                for k, r in records.items()
                if can_assert(r)
                and r.handle_id not in event.demoted
                and members.get(r.handle_id) == members[event_anchor.handle_id]
            },
            merge=merge,
        )

    async def merge_now(self, space_id: UUID, merge: PendingMerge) -> bool:
        """The merge an `identify` asked for, run before its answer: the same `_apply` as
        `identity_apply`, with the same rules, under the lease of the same profiles, so no compilation or
        reconciliation of either profile runs in between. A replay of the task afterwards finds the
        assertions decided and changes nothing."""
        units = sorted(profile_unit(p) for p in merge.profile_ids)
        grant = await self._wait_for_lease(space_id, units, f"identify:{merge.op_id}")
        if grant is None:
            return False
        try:
            async with self._uow(space_id) as uow:
                await self._apply(uow, space_id, merge.op_id, merge.assertion_ids, force=False)
        except NiadraError:
            return False  # a membership that moved while locking: the task retries it
        finally:
            await self._release(grant)
        return True

    # Governance (7.1): every change to membership is queued for `identity_apply`.

    async def create_assertion(
        self,
        space_id: UUID,
        handles: Sequence[Handle],
        *,
        method: AssertionMethod,
        subject_kind: SubjectKind,
        actor: str,
        reason: str | None,
    ) -> Operation:
        if len(handles) != 2:
            raise InvalidInput("an assertion links exactly two handles")
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            first, second = await self._resolve(uow, space_id, handles, subject_kind, now)
            return await self._assert(uow, space_id, first, second, method, now, actor=actor, reason=reason)

    async def list_assertions(
        self,
        space_id: UUID,
        *,
        profile_id: UUID | None,
        state: AssertionState | None,
        after: UUID | None,
        limit: int,
    ) -> tuple[list[AssertionRecord], UUID | None]:
        async with self._uow(space_id) as uow:
            handle_ids: tuple[UUID, ...] | None = None
            if profile_id is not None:
                handle_ids = (await self._profile(uow, profile_id)).handle_ids
            rows = await uow.identity.list_assertions(handle_ids, state, after, limit + 1)
        return rows[:limit], rows[limit - 1].assertion_id if len(rows) > limit else None

    async def retract(
        self, space_id: UUID, assertion_id: UUID, *, actor: str, reason: str | None
    ) -> Operation:
        async with self._uow(space_id) as uow:
            found = await uow.identity.get_assertions([assertion_id])
            if not found:
                raise NotFound("unknown assertion")
            if not found[0].live and found[0].retracted_at is None:
                # Not in the graph yet: retracting now stops a queued merge from applying it.
                await uow.identity.retract_assertions([assertion_id], self._clock.now())
            op_id = await self._enqueue_split(
                uow, space_id, ApplyOp.RETRACT, actor, reason, assertions=[assertion_id]
            )
        return Operation(op_id, "queued", assertion_id=assertion_id)

    async def merge(
        self, space_id: UUID, profile_ids: Sequence[UUID], *, force: bool, actor: str, reason: str | None
    ) -> Operation:
        if len(set(profile_ids)) != 2:
            raise InvalidInput("a merge takes two different profiles")
        if force and not reason:
            raise InvalidInput("a forced merge needs a reason")
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            first, second = [choose_anchor(await self._profile_handles(uow, p)) for p in profile_ids]
            if first.subject_kind is not second.subject_kind:
                raise InvalidInput("profiles of different subject kinds never merge")
            method = AssertionMethod.EXPLICIT_IDENTIFY
            assertion = _assertion(
                first, second, method, AssertionState.PENDING, now, actor=actor, reason=reason
            )
            await uow.identity.add_assertions([assertion])
            members = {first.handle_id: profile_ids[0], second.handle_id: profile_ids[1]}
            op_id = await self._enqueue_apply(uow, space_id, [assertion.assertion_id], members, force=force)
        return Operation(op_id, "queued", assertion_id=assertion.assertion_id)

    async def unmerge(
        self,
        space_id: UUID,
        profile_id: UUID,
        *,
        handle_ids: Sequence[UUID] = (),
        assertion_ids: Sequence[UUID] = (),
        actor: str,
        reason: str | None,
    ) -> Operation:
        """Splits `handle_ids` off the profile, or retracts `assertion_ids` and lets the graph decide."""
        if bool(handle_ids) == bool(assertion_ids):
            raise InvalidInput("an unmerge takes either handle_ids or assertion_ids")
        async with self._uow(space_id) as uow:
            profile = await self._profile(uow, profile_id)
            if not set(handle_ids) <= set(profile.handle_ids):
                raise InvalidInput("every handle must belong to the profile")
            op_id = await self._enqueue_split(
                uow, space_id, ApplyOp.UNMERGE, actor, reason, handles=handle_ids, assertions=assertion_ids
            )
        return Operation(op_id, "queued")

    async def block(self, space_id: UUID, handle: Handle, *, actor: str, reason: str | None) -> Operation:
        """Blocks the value for good and detaches the handle, if it exists, from whoever it joined."""
        key = self._normalize(handle, SubjectKind.PERSON).key
        async with self._uow(space_id) as uow:
            await uow.identity.add_blocked(key.type, key.value, reason)
            existing = await uow.identity.find_handle(key.type, key.scope, key.value)
            if existing is None:
                return Operation(uuid7(), "blocked")
            await uow.identity.set_status([existing.handle_id], HandleStatus.BLOCKED)
            op_id = await self._enqueue_split(
                uow, space_id, ApplyOp.BLOCK, actor, reason, handles=[existing.handle_id]
            )
        return Operation(op_id, "queued")

    async def create_link(
        self,
        space_id: UUID,
        person: Handle,
        organization: Handle,
        *,
        role: str,
        can_see_contacts: bool = False,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        method: AssertionMethod = AssertionMethod.DECLARED,
        origin_event_id: UUID | None = None,
    ) -> LinkRecord:
        if method not in LINK_METHODS:
            raise InvalidInput("a link is born by system_import, co_occurrence, declared or login")
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            person_record = (await self._resolve(uow, space_id, [person], SubjectKind.PERSON, now))[0]
            org_record = (await self._resolve(uow, space_id, [organization], SubjectKind.ACCOUNT, now))[0]
            if person_record.subject_kind.is_organization or not org_record.subject_kind.is_organization:
                raise InvalidInput("a link joins a person handle to an organization handle")
            # No cap: a broker or an accountant speaks for any number of companies, and memory keeps every
            # link. A read that bounds them counts the rest.
            link = LinkRecord(
                uuid7(),
                person_record.handle_id,
                org_record.handle_id,
                role,
                can_see_contacts,
                valid_from or now,
                valid_to,
            )
            await uow.identity.add_link(
                link, method=method, origin_event_id=origin_event_id, confidence=METHOD_CONFIDENCE[method]
            )
            # The person's packs about the organization are compiled now, so the first read of a contact
            # just linked finds one instead of compiling it (P6).
            members = await uow.identity.memberships([person_record.handle_id])
            if (person_profile := members.get(person_record.handle_id)) is not None:
                payload = {"reason": "link", "links": [str(link.link_id)]}
                await uow.pipeline.enqueue(
                    [Task(TaskName.COMPILE, space_id, payload, profile_id=person_profile)]
                )
        return link

    async def end_link(self, space_id: UUID, link_id: UUID, at: datetime | None = None) -> LinkRecord:
        async with self._uow(space_id) as uow:
            link = await uow.identity.get_link(link_id)
            if link is None:
                raise NotFound("unknown link")
            if link.valid_to is not None:
                return link
            ended_at = at or self._clock.now()
            await uow.identity.end_link(link_id, ended_at)
            # The person's packs about the organization go: a new generation leaves the ones in the cache
            # behind, and the compilation keeps no pack of a link that no longer holds.
            members = await uow.identity.memberships([link.person_handle_id])
            if (person_profile := members.get(link.person_handle_id)) is not None:
                await uow.pipeline.request_work(
                    Task(TaskName.COMPILE, space_id, {"reason": "link_ended"}, profile_id=person_profile)
                )
        return replace(link, valid_to=ended_at)

    async def active_links(self, space_id: UUID, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        async with self._uow(space_id) as uow:
            return await uow.identity.active_links(handle_ids)

    async def profile_summary(self, space_id: UUID, profile_id: UUID) -> ProfileSummary:
        async with self._uow(space_id) as uow:
            profile = await self._profile(uow, profile_id)
            handles = await uow.identity.get_handles(profile.handle_ids)
            links = await uow.identity.active_links(profile.handle_ids)
        return ProfileSummary(profile, tuple(handles), tuple(links))

    async def search_profiles(
        self,
        space_id: UUID,
        query: str,
        *,
        type: HandleType | None = None,
        scope: str | None = None,
        limit: int = 20,
        viewer: Principal | None = None,
    ) -> list[ProfileSummary]:
        """The Console's customer search (5.22): an exact pseudonym, or an identifier normalized like
        ingestion and found by its keyed hash. Unknown values find nothing, and say nothing.

        For a `viewer` who reads customer memory, a value that is no identifier is a name: the profiles
        whose memory holds it as a name fact (`NAME_PREDICATES`), exactly, but for case, accents and
        spacing, found by the fact's keyed hash; a profile is found only when the viewer's policy lets them
        see that fact. Any other viewer gets the refusal an unrecognizable value always got."""
        async with self._uow(space_id) as uow:
            profiles: list[ProfileRecord] = []
            if type is None and is_pseudonym(query):
                found = await uow.identity.find_profile_by_pseudonym(query.strip())
                profiles = [found] if found else []
            elif type is None and viewer is not None and is_name_query(query) and reads_memory(viewer):
                profiles = await self._named(uow, viewer, query, limit)
            else:
                keys = search_keys(query, type, scope)
                # A number that changed hands finds its new holder and, retired, the previous one.
                handles = [*await uow.identity.find_handles(keys), *await uow.identity.retired_handles(keys)]
                members = await uow.identity.memberships([h.handle_id for h in handles])
                for profile_id in dict.fromkeys(members.values()):
                    if (profile := await uow.identity.get_profile(profile_id)) is not None:
                        profiles.append(profile)
            summaries = []
            for profile in profiles[:limit]:
                handles = await uow.identity.get_handles(profile.handle_ids)
                links = await uow.identity.active_links(profile.handle_ids)
                summaries.append(ProfileSummary(profile, tuple(handles), tuple(links)))
        return summaries

    async def _named(self, uow: UnitOfWork, viewer: Principal, query: str, limit: int) -> list[ProfileRecord]:
        """The profiles whose memory holds `query` as a name the viewer may see (`search_profiles`)."""
        facts = await uow.memory.facts_valued(NAME_PREDICATES, normalize_value(query), limit=NAMED_FACTS)
        facts = [f for f in facts if f.about_handle_id is None]
        if not facts:
            return []
        members = await uow.identity.memberships(sorted({f.origin_handle_id for f in facts}))
        profiles = await uow.identity.get_profiles(list(dict.fromkeys(members.values())))
        reading = ConsoleReading.of(viewer, self._config.space(uow.space_id))
        found: list[ProfileRecord] = []
        for profile in profiles:
            view = governance_view(viewer, profile)
            said = tuple(f for f in facts if f.origin_handle_id in view.handle_ids)
            policy = reading.view(MemorySnapshot(profile=view, facts=said)) if said else None
            if policy is not None and any(policy.effect(f"fact:{f.fact_id}") is Effect.ALLOW for f in said):
                found.append(profile)
        return found[:limit]

    async def shown(self, principal: Principal, profile_ids: Sequence[UUID]) -> dict[UUID, Shown]:
        """How `principal` sees each of these profiles named (`app.profile_names`); a retired one is left
        out."""
        if not profile_ids:
            return {}
        async with self._uow(principal.space.space_id) as uow:
            profiles = await uow.identity.get_profiles(list(dict.fromkeys(profile_ids)))
            return await shown_profiles(uow, principal, self._config.space(uow.space_id), profiles)

    async def list_profiles(
        self, space_id: UUID, *, kind: SubjectKind | None, order: ListOrder, cursor: str | None, limit: int
    ) -> tuple[list[ListedProfile], str | None]:
        """The Console's customer list (5.22): live profiles by their latest activity, a page at a time.
        An erased or merged profile is retired, and never listed. A page may hold fewer rows than `limit`
        and still have a cursor: the list ends where the cursor is None."""
        after = ListCursor.decode(cursor) if cursor else None
        async with self._uow(space_id) as uow:
            rows, more = await uow.identity.list_profiles(
                kind=kind,
                after=after,
                ascending=order is ListOrder.LAST_SEEN_ASC,
                limit=limit,
                handles_shown=HANDLES_SHOWN,
            )
        return rows, more.encode() if more else None

    async def open_suggestions(
        self, space_id: UUID, *, after: UUID | None, limit: int
    ) -> tuple[list[SuggestionView], UUID | None]:
        async with self._uow(space_id) as uow:
            rows = await uow.identity.open_suggestions(after=after, limit=limit + 1)
            views = await _views(uow, rows[:limit])
        return views, rows[limit - 1].suggestion_id if len(rows) > limit else None

    async def accept_suggestion(
        self, space_id: UUID, suggestion_id: UUID, *, actor: str, reason: str | None
    ) -> Operation:
        """A reviewer confirms the pair: it becomes an `accepted_suggestion` assertion that goes through
        `identity_apply` and every merge protection, like any other; nothing merges here (5.3)."""
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            suggestion = await uow.identity.get_suggestion(suggestion_id)
            if suggestion is None:
                raise NotFound("unknown suggestion")
            if suggestion.status != "open":
                raise Conflict("the suggestion was already reviewed")
            handles = await uow.identity.get_handles([suggestion.handle_a, suggestion.handle_b])
            if len(handles) != 2:
                raise Conflict("a handle of the suggestion no longer exists")
            await uow.identity.set_suggestion_status(suggestion_id, ACCEPTED)
            first, second = handles
            method = AssertionMethod.ACCEPTED_SUGGESTION
            return await self._assert(uow, space_id, first, second, method, now, actor=actor, reason=reason)

    async def dismiss_suggestion(self, space_id: UUID, suggestion_id: UUID) -> SuggestionView:
        """A reviewer says the pair is two people; the suggester never reopens a dismissed pair."""
        async with self._uow(space_id) as uow:
            suggestion = await uow.identity.get_suggestion(suggestion_id)
            if suggestion is None:
                raise NotFound("unknown suggestion")
            if suggestion.status == "open":
                await uow.identity.set_suggestion_status(suggestion_id, DISMISSED)
                suggestion = replace(suggestion, status=DISMISSED)
            [view] = await _views(uow, [suggestion])
        return view

    # Worker tasks.

    async def identity_apply(self, task: Task) -> None:
        payload = task.payload
        op = ApplyOp(payload["op"])
        op_id = UUID(payload["op_id"])
        assertion_ids = [UUID(a) for a in payload.get("assertion_ids", ())]
        handle_ids = [UUID(h) for h in payload.get("handle_ids", ())]
        async with self._uow(task.space_id) as uow:
            if op is ApplyOp.APPLY:
                await self._apply(uow, task.space_id, op_id, assertion_ids, force=bool(payload.get("force")))
            else:
                await self._split(uow, task.space_id, op_id, assertion_ids, handle_ids, payload)

    async def identity_suggest(self, task: Task) -> None:
        """Offline candidates for review; never applies anything (5.3)."""
        now = self._clock.now()
        async with self._uow(task.space_id) as uow:
            candidates: list[Candidate] = []
            after: UUID | None = None
            while page := await uow.identity.list_handles(
                [HandleType.EMAIL, HandleType.PHONE_E164], after, SUGGEST_PAGE
            ):
                members = await uow.identity.memberships([h.handle_id for h in page])
                candidates += [
                    Candidate(h.handle_id, members[h.handle_id], h.type, h.value)
                    for h in page
                    if h.handle_id in members
                ]
                after = page[-1].handle_id
            # A pair of profiles already up for review, or dismissed, is not suggested again by other handles.
            pairs = await uow.identity.unsettled_suggestion_pairs()
            owners = await uow.identity.memberships(sorted({h for pair in pairs for h in pair}))
            settled = {frozenset((owners.get(a), owners.get(b))) for a, b in pairs}
            profile_of = {c.handle_id: c.profile_id for c in candidates}
            config = self._config.space(task.space_id)
            for found in suggest(candidates):
                if frozenset((profile_of[found.handle_a], profile_of[found.handle_b])) in settled:
                    continue
                suggestion = SuggestionRecord(
                    uuid7(), found.handle_a, found.handle_b, found.score, now, {"agree": list(found.features)}
                )
                if await uow.identity.add_suggestion(suggestion):
                    await enqueue_event(
                        uow, config, "identity.suggestion", None, _suggestion_event(suggestion)
                    )

    # Attach internals.

    def _normalize(self, handle: Handle, default_kind: SubjectKind) -> NormalizedHandle:
        return normalize_handle(
            handle.type, handle.value, handle.scope, handle.subject_kind, default_kind=default_kind
        )

    async def _ensure_handles(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        normalized: Sequence[NormalizedHandle],
        at: datetime,
        created: set[UUID],
    ) -> dict[HandleKey, HandleRecord]:
        """Finds the handles and inserts the missing ones in one statement, adding to `created` the ids this
        call inserted. A handle another transaction inserted meanwhile is read back."""
        records = {_key(r): r for r in await uow.identity.find_handles([n.key for n in normalized])}
        missing = [n for n in normalized if n.key not in records]
        if missing:
            statuses = await self._initial_statuses(uow, space_id, missing)
            new = [NewHandle(n.key, n.subject_kind, statuses[n.key]) for n in missing]
            inserted = await uow.identity.add_handles(new, at)
            records |= {_key(r): r for r in inserted}
            created.update(r.handle_id for r in inserted)
            if lost := [n.key for n in missing if n.key not in records]:
                records |= {_key(r): r for r in await uow.identity.find_handles(lost)}
        return {n.key: records[n.key] for n in normalized}

    async def _initial_statuses(
        self, uow: UnitOfWork, space_id: UUID, handles: Sequence[NormalizedHandle]
    ) -> dict[HandleKey, HandleStatus]:
        tenant_list = self._config.space(space_id).settings.blocked_values
        fixed = {h.key for h in handles if is_fixed_blocked(h.key) or is_tenant_blocked(h.key, tenant_list)}
        blocked = fixed | await uow.identity.blocked_keys([h.key for h in handles if h.key not in fixed])
        statuses: dict[HandleKey, HandleStatus] = {}
        for handle in handles:
            if handle.key in blocked:
                statuses[handle.key] = HandleStatus.BLOCKED
            elif handle.type in PHONE_FAMILY and handle.subject_kind.is_organization:
                statuses[handle.key] = HandleStatus.AMBIGUOUS  # a company line reaches many people (21.2)
            else:
                statuses[handle.key] = HandleStatus.ACTIVE
        return statuses

    async def _suspend_silent(
        self,
        uow: UnitOfWork,
        event: _Event,
        records: dict[HandleKey, HandleRecord],
        members: dict[UUID, UUID],
    ) -> None:
        """A number silent for months may belong to someone else now (5.3): the link is suspended, so
        reads by it reveal nothing, unless the same event carries a record id of the same profile."""
        if event.method is not AssertionMethod.SAME_EVENT:
            return
        for key, record in records.items():
            if record.handle_id not in members or record.status is not HandleStatus.ACTIVE:
                continue
            if not looks_recycled(record, event.at):
                continue
            if any(vouches_for(record, other, members) for other in records.values()):
                continue
            await uow.identity.set_status([record.handle_id], HandleStatus.SUSPECTED_RECYCLED)
            records[key] = replace(record, status=HandleStatus.SUSPECTED_RECYCLED)

    async def _reconfirm(
        self,
        uow: UnitOfWork,
        event: _Event,
        records: dict[HandleKey, HandleRecord],
        members: dict[UUID, UUID],
    ) -> None:
        """A suspended number comes back when the tenant's systems or a login tie it again to a record id
        of the same profile; an OTP to the number itself never does."""
        if event.method not in RECONFIRMING_METHODS:
            return
        for key, record in records.items():
            if record.status is not HandleStatus.SUSPECTED_RECYCLED or record.handle_id not in members:
                continue
            if any(vouches_for(record, other, members) for other in records.values()):
                await uow.identity.set_status([record.handle_id], HandleStatus.ACTIVE)
                records[key] = replace(record, status=HandleStatus.ACTIVE)

    async def _demote_recycled(
        self, uow: UnitOfWork, records: dict[HandleKey, HandleRecord], members: dict[UUID, UUID], at: datetime
    ) -> None:
        for key, record in records.items():
            if record.handle_id not in members or not looks_recycled(record, at):
                continue
            others = {
                members[r.handle_id]
                for r in records.values()
                if r.handle_id in members
                and r is not record
                and is_strong(r)
                and r.subject_kind is record.subject_kind
            }
            if others - {members[record.handle_id]}:
                await uow.identity.set_status([record.handle_id], HandleStatus.SUSPECTED_RECYCLED)
                records[key] = replace(record, status=HandleStatus.SUSPECTED_RECYCLED)

    async def _suspend_on_rupture(
        self,
        uow: UnitOfWork,
        event: _Event,
        records: dict[HandleKey, HandleRecord],
        members: dict[UUID, UUID],
    ) -> None:
        """A recent number that the tenant's systems or a login tie to a record id of another profile,
        while its own profile holds a different id of that kind (another document of the country, another
        customer id of the same system), has changed hands before the silence could tell. An
        ordinary event never counts: whoever holds the phone can type someone else's document into it."""
        if event.method not in RECONFIRMING_METHODS:
            return
        for key, record in records.items():
            if record.type not in PHONE_LIMITED or record.status is not HandleStatus.ACTIVE:
                continue
            own = members.get(record.handle_id)
            if own is None:
                continue
            strangers = [
                r
                for r in records.values()
                if r.type in RECORD_TYPES
                and r.status is HandleStatus.ACTIVE
                and members.get(r.handle_id) not in (None, own)
            ]
            if strangers and one_per_scope_conflict(await self._profile_handles(uow, own), strangers):
                await uow.identity.set_status([record.handle_id], HandleStatus.SUSPECTED_RECYCLED)
                records[key] = replace(record, status=HandleStatus.SUSPECTED_RECYCLED)

    async def _retire_changed_hands(
        self,
        uow: UnitOfWork,
        event: _Event,
        records: dict[HandleKey, HandleRecord],
        members: dict[UUID, UUID],
        changed_hands: set[HandleKey],
    ) -> dict[UUID, set[UUID]]:
        """A number that changed hands leaves its old handle, which stays suspended in the old profile
        with that person's memory, and starts over as a new handle: the new holder's events build a
        provisional profile of their own instead of landing in someone else's.

        Returns, per new handle of a retired number, the profiles that hold its predecessors: the new
        handle joins one of them only on a record id of that profile (see `_unbarred`)."""
        for key in sorted(changed_hands, key=lambda k: (k.type.value, k.scope, k.value)):
            record = records[key]
            if record.type not in PHONE_LIMITED or record.handle_id not in members:
                continue
            await uow.identity.retire_handle(record.handle_id)
            normalized = _normalized(record)
            statuses = await self._initial_statuses(uow, event.space_id, [normalized])
            created = await uow.identity.add_handles(
                [NewHandle(key, record.subject_kind, statuses[key])], event.at
            )
            event.created.update(r.handle_id for r in created)
            records[key] = created[0] if created else (await uow.identity.find_handles([key]))[0]
        numbers = [key for key, record in records.items() if record.type in PHONE_LIMITED]
        retired = await uow.identity.retired_handles(numbers) if numbers else []
        if not retired:
            return {}
        holders = await uow.identity.memberships([r.handle_id for r in retired])
        barred: dict[UUID, set[UUID]] = {}
        for old in retired:
            current = records.get(_key(old))
            if current is not None and old.handle_id in holders:
                barred.setdefault(current.handle_id, set()).add(holders[old.handle_id])
        return barred

    async def _place_group(
        self, uow: UnitOfWork, event: _Event, group: list[HandleRecord], members: dict[UUID, UUID]
    ) -> tuple[HandleRecord, list[AssertionRecord], bool, set[UUID]] | None:
        """Joins the group's handles of one subject kind. Returns its anchor, the pending assertions it
        wrote, whether the group spans several profiles (a merge is due, now or already queued) and the
        handles it joined as provisional."""
        group = await self._drop_shared(uow, event, group, members)
        if not group:
            return None
        known = [h for h in group if h.handle_id in members]
        profiles = {members[h.handle_id] for h in known}
        spans = len(profiles) > 1
        anchor = choose_anchor(known or group)
        joiners = [h for h in group if h.handle_id not in members and h is not anchor]
        if event.method in CLAIM_METHODS and not any(_vouched(event, group, members, p) for p in profiles):
            # A claim merges two profiles only on a record id of one of them; otherwise a person reviews
            # the pair (`declared` never merges at all, since it is never vouched).
            spans = False
        if event.method is AssertionMethod.DECLARED and not known:
            # A declaration that names nothing known binds nothing: the values may be someone else's,
            # and whoever uses them next would write into the declarer's profile. A person reviews it.
            for joiner in joiners:
                await self._suggest_claim(uow, event, anchor, joiner)
            joiners = []
        fresh: ProfileRecord | None = None
        if not known:
            # A new subject: its profile is born with the anchor and every joiner, in one membership write.
            fresh = await uow.identity.create_profile(uuid7(), anchor.subject_kind, event.at)
            members[anchor.handle_id] = fresh.profile_id
        flagged: set[UUID] = set()
        if joiners or fresh is not None:
            home = members[anchor.handle_id]
            own = [h for h in known if members[h.handle_id] == home]
            provisional = not _vouched(event, group, members, home) and (
                event.method in CLAIM_METHODS or (bool(own) and all(h.provisional for h in own))
            )
            flagged = await self._join(
                uow, event, anchor, joiners, members, provisional=provisional, fresh=fresh
            )
        pending, apart = await self._cross_profile(uow, event, anchor, known, members, group)
        spans = spans and len(profiles - apart) > 1
        return anchor, pending, spans and event.method is not AssertionMethod.CHANNEL_ROTATION, flagged

    async def _drop_shared(
        self, uow: UnitOfWork, event: _Event, group: list[HandleRecord], members: dict[UUID, UUID]
    ) -> list[HandleRecord]:
        """Marks as ambiguous the known handles this event would join to too many ids (5.3, protection 4)."""
        profiles = {members.get(h.handle_id) for h in group}
        if len(group) < 2 or (None not in profiles and len(profiles) == 1):
            return group
        kept: list[HandleRecord] = []
        for handle in group:
            # A known handle counts every partner outside its profile. A new one counts the partners
            # that already belong to someone, so replaying an event finds the state its first
            # delivery left (attach is idempotent), and one person's new ids never look shared.
            own = members.get(handle.handle_id)
            partners = [
                h
                for h in group
                if h.handle_id != handle.handle_id
                and members.get(h.handle_id) != own
                and (own is not None or members.get(h.handle_id) is not None)
            ]
            if await self._shared(uow, event, handle, partners):
                await uow.identity.set_status([handle.handle_id], HandleStatus.AMBIGUOUS)
                event.demoted.add(handle.handle_id)
                if own is not None:
                    # What it joined before the third person came is undone too: the handle
                    # leaves its profile, and the people it bridged split apart, merge by merge.
                    await self._enqueue_split(
                        uow,
                        event.space_id,
                        ApplyOp.UNMERGE,
                        SYSTEM_ACTOR,
                        SHARED_HANDLE,
                        handles=[handle.handle_id],
                    )
                continue
            kept.append(handle)
        return kept

    async def _shared(
        self, uow: UnitOfWork, event: _Event, handle: HandleRecord, partners: Sequence[HandleRecord]
    ) -> bool:
        """Three different ids of one type in a day, or five in a month (5.3, protection 4). A handle this
        attach inserted has no assertion yet: only the event's own partners count."""
        if handle.handle_id in event.created:
            return fanout_exceeded(partners)
        month = await uow.identity.recent_counterparts(handle.handle_id, event.at - FANOUT_LONG_WINDOW)
        if fanout_exceeded([*month, *partners], FANOUT_LONG_LIMIT):
            return True
        if not fanout_exceeded([*month, *partners]):
            return False
        day = await uow.identity.recent_counterparts(handle.handle_id, event.at - FANOUT_WINDOW)
        return fanout_exceeded([*day, *partners])

    async def _join(
        self,
        uow: UnitOfWork,
        event: _Event,
        anchor: HandleRecord,
        joiners: list[HandleRecord],
        members: dict[UUID, UUID],
        *,
        provisional: bool,
        fresh: ProfileRecord | None = None,
    ) -> set[UUID]:
        """New handles join the anchor's profile; a second document of one country does not (5.3).

        With `provisional` (a claim, or an event that reaches the profile only through provisional
        handles) they join for continuity, but reads through them stay capped until a system of record,
        a login or an operator confirms the join. Returns the handles it marked.

        `fresh` is the anchor's profile when this attach just created it: nobody else sees it yet, so it
        needs no lock and no new version, and the anchor's membership is written with the joiners'."""
        if fresh is not None:
            profile, current = fresh, [anchor]
        else:
            profile = await self._lock_current(uow, anchor, members)
            # The members count only for a joiner with a per-scope limit or a phone (5.3).
            limited = any(j.type in ONE_PER_SCOPE or j.type in PHONE_LIMITED for j in joiners)
            current = await self._profile_handles(uow, profile.profile_id) if limited else []
        accepted: list[HandleRecord] = []
        assertions: list[AssertionRecord] = []
        for joiner in joiners:
            if joiner.type in ONE_PER_SCOPE and one_per_scope_conflict([*current, *accepted], [joiner]):
                assertions.append(
                    _assertion(
                        anchor,
                        joiner,
                        event.method,
                        AssertionState.REJECTED,
                        event.at,
                        event=event,
                        reason=Rejection.ONE_PER_SCOPE.value,
                    )
                )
                continue
            accepted.append(joiner)
            assertions.append(
                _assertion(anchor, joiner, event.method, AssertionState.APPLIED, event.at, event=event)
            )
        await uow.identity.add_assertions(assertions)
        if fresh is not None:
            await uow.identity.set_membership(
                [anchor.handle_id, *(h.handle_id for h in accepted)],
                fresh.profile_id,
                fresh.membership_version,
            )
        elif accepted:
            bumped = await uow.identity.bump_profile(
                profile.profile_id, profile.membership_version, privacy=False
            )
            await uow.identity.set_membership(
                [h.handle_id for h in accepted], bumped.profile_id, bumped.membership_version
            )
        if not accepted:
            return set()
        members.update({h.handle_id: profile.profile_id for h in accepted})
        if provisional:
            await uow.identity.set_provisional([h.handle_id for h in accepted], True)
            accepted = [replace(h, provisional=True) for h in accepted]
        if any(h.type in PHONE_LIMITED for h in accepted):
            event.demoted.update(await self._demote_phones(uow, [*current, *accepted]))
        return {h.handle_id for h in accepted} if provisional else set()

    async def _lock_current(
        self, uow: UnitOfWork, anchor: HandleRecord, members: dict[UUID, UUID]
    ) -> ProfileRecord:
        """A merge may retire the anchor's profile between our read and our lock: follow it once. The
        record has no members; the lock leaves out a retired profile."""
        for _ in range(2):
            locked = await uow.identity.lock_profiles([members[anchor.handle_id]])
            if locked:
                return locked[0]
            members.update(await uow.identity.memberships([anchor.handle_id]))
        raise Conflict("the profile changed during attach; retry")

    async def _cross_profile(
        self,
        uow: UnitOfWork,
        event: _Event,
        anchor: HandleRecord,
        known: list[HandleRecord],
        members: dict[UUID, UUID],
        group: list[HandleRecord],
    ) -> tuple[list[AssertionRecord], set[UUID]]:
        """Strong handles of other profiles become pending assertions; the merge runs later (5.3).

        A claim is weak evidence (it "counts little until confirmed"): a `declared` identify never merges
        two profiles that exist, and an `explicit_identify` merges them only on a record id of one of them
        in the same call. Otherwise the pair goes to the review queue, and only a person who accepts the
        suggestion merges them: whoever types someone's e-mail must not reach their profile.

        Two profiles someone split stay apart (`separated`): the record that joined them wrongly comes
        again, and only a person who accepts the suggestion joins them back. Returns the pending
        assertions and the profiles kept apart."""
        by_profile: dict[UUID, list[HandleRecord]] = {}
        for handle in known:
            by_profile.setdefault(members[handle.handle_id], []).append(handle)
        home = members[anchor.handle_id]
        by_profile.pop(home, None)
        pending: list[AssertionRecord] = []
        written: list[AssertionRecord] = []
        apart: set[UUID] = set()
        for profile_id, handles in by_profile.items():
            other = choose_anchor(handles)
            if await uow.identity.pair_asserted(anchor.handle_id, other.handle_id):
                continue
            if event.method in CLAIM_METHODS and not (
                _vouched(event, group, members, home) or _vouched(event, group, members, profile_id)
            ):
                await self._suggest_claim(uow, event, anchor, other)
                continue
            rotation = event.method is AssertionMethod.CHANNEL_ROTATION
            if not rotation and await uow.identity.separated(home, profile_id):
                await self._suggest_claim(uow, event, anchor, other, after_split=True)
                apart.add(profile_id)
                continue
            state = AssertionState.REJECTED if rotation else AssertionState.PENDING
            note = Rejection.ROTATION.value if rotation else None
            assertion = _assertion(anchor, other, event.method, state, event.at, event=event, reason=note)
            written.append(assertion)
            if not rotation:
                pending.append(assertion)
        await uow.identity.add_assertions(written)
        return pending, apart

    async def _suggest_claim(
        self,
        uow: UnitOfWork,
        event: _Event,
        anchor: HandleRecord,
        other: HandleRecord,
        *,
        after_split: bool = False,
    ) -> None:
        features: dict[str, Any] = {"agree": [event.method.value]}
        if after_split:
            features["after_split"] = True
        suggestion = SuggestionRecord(
            uuid7(), anchor.handle_id, other.handle_id, SUGGESTION_THRESHOLD, event.at, features
        )
        if await uow.identity.add_suggestion(suggestion):
            config = self._config.space(event.space_id)
            await enqueue_event(uow, config, "identity.suggestion", None, _suggestion_event(suggestion))

    async def _confirm_claimed(
        self,
        uow: UnitOfWork,
        event: _Event,
        records: dict[HandleKey, HandleRecord],
        members: dict[UUID, UUID],
    ) -> set[UUID]:
        """A provisional handle is confirmed when the tenant's systems or a login tie it again to a
        confirmed record id of its profile, the rule that brings a recycled number back. A
        declaration, an OTP to the handle itself or another provisional id never does. Returns the
        handles it confirmed: their rows count again in what the profile derives (5.3)."""
        if event.method not in _VOUCHING_METHODS:
            return set()
        group = list(records.values())
        confirmed: set[UUID] = set()
        for key, record in records.items():
            if record.provisional and any(vouches_for(record, other, members) for other in group):
                await uow.identity.set_provisional([record.handle_id], False)
                records[key] = replace(record, provisional=False)
                confirmed.add(record.handle_id)
        return confirmed

    async def _place_singletons(
        self, uow: UnitOfWork, records: Iterable[HandleRecord], members: dict[UUID, UUID], at: datetime
    ) -> None:
        """Every handle belongs to some profile; the ones nothing joined get their own."""
        for record in records:
            if record.handle_id not in members:
                profile = await uow.identity.create_profile(uuid7(), record.subject_kind, at)
                await uow.identity.set_membership(
                    [record.handle_id], profile.profile_id, profile.membership_version
                )
                members[record.handle_id] = profile.profile_id

    async def _link_co_occurrence(
        self, uow: UnitOfWork, event: _Event, anchors: dict[SubjectKind, HandleRecord]
    ) -> None:
        """A person and an organization in one event are linked, never merged (5.3, 21.2)."""
        person = anchors.get(SubjectKind.PERSON)
        if person is None:
            return
        orgs = [org.handle_id for kind, org in anchors.items() if kind.is_organization]
        await self.link_subjects(
            uow, person.handle_id, orgs, origin_event_id=event.origin_event_id, at=event.at
        )

    async def link_subjects(
        self,
        uow: UnitOfWork,
        person_handle_id: UUID,
        org_handle_ids: Sequence[UUID],
        *,
        origin_event_id: UUID | None,
        at: datetime,
    ) -> None:
        """A person and the organizations one event names, each attached as its own subject, are linked
        by co-occurrence (21.2); a link already active is left as it is, and none ever ends to make room."""
        if not org_handle_ids:
            return
        active = [
            link
            for link in await uow.identity.active_links([person_handle_id])
            if link.person_handle_id == person_handle_id
        ]
        linked = {link.org_handle_id for link in active}
        for org in dict.fromkeys(org_handle_ids):
            if org in linked:
                continue
            link = LinkRecord(uuid7(), person_handle_id, org, CO_OCCURRENCE_ROLE, False, at, None)
            await uow.identity.add_link(
                link,
                method=AssertionMethod.CO_OCCURRENCE,
                origin_event_id=origin_event_id,
                confidence=METHOD_CONFIDENCE[AssertionMethod.CO_OCCURRENCE],
            )

    async def _raise_levels(
        self, uow: UnitOfWork, records: dict[HandleKey, HandleRecord], level: Verification
    ) -> None:
        """The event's level counts before anchoring, so replaying the event picks the same anchor."""
        raised = {key: r for key, r in records.items() if level.rank > r.level.rank}
        if raised:
            await uow.identity.raise_level([r.handle_id for r in raised.values()], level)
            records |= {key: replace(r, level=level) for key, r in raised.items()}

    async def _demote_phones(self, uow: UnitOfWork, handles: Sequence[HandleRecord]) -> set[UUID]:
        demoted = phones_to_demote(handles)
        if demoted:
            await uow.identity.set_status([h.handle_id for h in demoted], HandleStatus.SUSPECTED_RECYCLED)
        return {h.handle_id for h in demoted}

    # identity_apply internals.

    async def _apply(
        self, uow: UnitOfWork, space_id: UUID, op_id: UUID, assertion_ids: Sequence[UUID], *, force: bool
    ) -> None:
        """Merges the profiles joined by pending assertions, one lock pair at a time (5.3)."""
        for assertion in sorted(
            await uow.identity.get_assertions(assertion_ids), key=lambda a: a.assertion_id
        ):
            if assertion.state is not AssertionState.PENDING or assertion.retracted_at is not None:
                continue  # already decided: a replay of this task is a no-op
            await self._apply_one(uow, space_id, op_id, assertion, force=force)

    async def _apply_one(
        self, uow: UnitOfWork, space_id: UUID, op_id: UUID, assertion: AssertionRecord, *, force: bool
    ) -> None:
        ends = [assertion.handle_a, assertion.handle_b]
        members = await uow.identity.memberships(ends)
        first, second = members[assertion.handle_a], members[assertion.handle_b]
        if first == second:
            await uow.identity.set_assertion_state([assertion.assertion_id], AssertionState.APPLIED, None)
            return
        locked = {p.profile_id: p for p in await uow.identity.lock_profiles([first, second])}
        if len(locked) != 2 or await uow.identity.memberships(ends) != members:
            raise Conflict("membership moved while locking; the task retries")
        side_a = await self._profile_handles(uow, first)
        side_b = await self._profile_handles(uow, second)
        endpoints = await uow.identity.get_handles(ends)
        # Evidence a system sends again never undoes a split; a person's assertion or merge may (5.3).
        separated = assertion.actor is None and await uow.identity.separated(first, second)
        rejection = merge_rejection(
            assertion.method, endpoints, side_a, side_b, force=force, separated=separated
        )
        if rejection is not None:
            await uow.identity.set_assertion_state(
                [assertion.assertion_id], AssertionState.REJECTED, rejection.value
            )
            return
        survivor, absorbed = (
            (first, second) if choose_anchor([*side_a, *side_b]) in side_a else (second, first)
        )
        moved = [h.handle_id for h in (side_b if absorbed == second else side_a)]
        kept = await uow.identity.bump_profile(survivor, locked[survivor].membership_version, privacy=True)
        gone = await uow.identity.bump_profile(absorbed, locked[absorbed].membership_version, privacy=True)
        await uow.identity.set_membership(moved, survivor, kept.membership_version)
        await uow.identity.retire_profile(absorbed, self._clock.now())
        await uow.identity.set_assertion_state([assertion.assertion_id], AssertionState.APPLIED, None)
        if assertion.actor is not None:
            await _confirm_pair(uow, *endpoints)  # an operator named this pair
        await self._demote_phones(uow, [*side_a, *side_b])
        await uow.identity.add_merge_log(
            MergeLogEntry(
                merge_id=uuid.uuid5(op_id, str(assertion.assertion_id)),
                op=MergeOp.MERGE,
                from_profiles=(absorbed,),
                to_profiles=(survivor,),
                handles_moved=tuple(moved),
                actor=assertion.actor or SYSTEM_ACTOR,
                created_at=self._clock.now(),
                assertion_id=assertion.assertion_id,
                reason=assertion.reason,
            )
        )
        versions = {survivor: kept.privacy_version, absorbed: gone.privacy_version}
        await _record_feed(uow, "merge", versions, moved)
        await _request_recompute(uow, space_id, [survivor])
        await enqueue_event(
            uow,
            self._config.space(space_id),
            "identity.merged",
            WebhookSubject(kept.pseudonym, kept.kind.value),
            {
                "merge_id": str(uuid.uuid5(op_id, str(assertion.assertion_id))),
                "profile_id": str(survivor),
                "merged_profile_ids": [str(absorbed)],
                "handles_moved": [str(h) for h in moved],
                "assertion_id": str(assertion.assertion_id),
                "privacy_version": kept.privacy_version,
                "as_of": self._clock.now().isoformat(),
            },
        )

    async def _split(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        op_id: UUID,
        assertion_ids: Sequence[UUID],
        split_ids: Sequence[UUID],
        payload: dict[str, Any],
    ) -> None:
        """Retract, unmerge and block: retract the evidence, then recompute the components (5.3).
        A bridge handle can split one profile into three or more."""
        if await uow.identity.merge_logged(op_id):
            return
        targets = await uow.identity.get_assertions(assertion_ids)
        seeds = sorted({*split_ids, *(h for a in targets for h in (a.handle_a, a.handle_b))})
        profiles = await self._lock_members(uow, seeds)
        inside = {h.handle_id for _, own in profiles for h in own}
        live = [
            a
            for a in await uow.identity.live_assertions(sorted(inside))
            if {a.handle_a, a.handle_b} <= inside
        ]
        doomed = [a for a in live if a.assertion_id in assertion_ids or _crosses(a, set(split_ids))]
        undecided = [a.assertion_id for a in targets if not a.live and a.retracted_at is None]
        remaining = [(a.handle_a, a.handle_b) for a in live if a not in doomed]
        # Membership is a view of the graph: recompute even when the evidence was already retracted.
        plan = [(profile, own, _strays(own, remaining, set(split_ids))) for profile, own in profiles]
        if not doomed and not undecided and not any(strays for _, _, strays in plan):
            return
        now = self._clock.now()
        await uow.identity.retract_assertions([*(a.assertion_id for a in doomed), *undecided], now)
        reversed_merges = await uow.identity.merges_by_assertions([a.assertion_id for a in doomed])
        revivable = {frozenset(m.handles_moved): m.from_profiles[0] for m in reversed_merges}
        versions, moved = await self._recompute(uow, plan, revivable, now)
        await _settle_provisional(uow, versions)
        if moved:
            await uow.identity.add_merge_log(
                MergeLogEntry(
                    merge_id=op_id,
                    op=MergeOp.UNMERGE,
                    from_profiles=tuple(p.profile_id for p, _ in profiles),
                    to_profiles=tuple(versions),
                    handles_moved=tuple(moved),
                    actor=str(payload.get("actor") or SYSTEM_ACTOR),
                    created_at=now,
                    assertion_id=doomed[0].assertion_id if doomed else None,
                    reason=payload.get("reason"),
                )
            )
        await uow.identity.mark_reversed([m.merge_id for m in reversed_merges], now)
        kind = "unmerge" if moved else "retraction"
        await _record_feed(uow, kind, versions, moved or seeds)
        await _request_recompute(uow, space_id, versions)
        if moved:
            split = profiles[0][0]
            await enqueue_event(
                uow,
                self._config.space(space_id),
                "identity.unmerged",
                WebhookSubject(split.pseudonym, split.kind.value),
                {
                    "merge_id": str(op_id),
                    "from_profiles": [str(p.profile_id) for p, _ in profiles],
                    "to_profiles": [str(p) for p in versions],
                    "handles_moved": [str(h) for h in moved],
                    "reversed_merge_ids": [str(m.merge_id) for m in reversed_merges],
                    "as_of": now.isoformat(),
                },
            )

    async def _lock_members(
        self, uow: UnitOfWork, handle_ids: Sequence[UUID]
    ) -> list[tuple[ProfileRecord, list[HandleRecord]]]:
        """Locks the profiles of the handles in id order, then checks nothing moved meanwhile."""
        members = await uow.identity.memberships(handle_ids)
        wanted = sorted(set(members.values()))
        locked = await uow.identity.lock_profiles(wanted)
        if len(locked) != len(wanted) or await uow.identity.memberships(handle_ids) != members:
            raise Conflict("membership moved while locking; the task retries")
        return [(p, await self._profile_handles(uow, p.profile_id)) for p in locked]

    async def _recompute(
        self,
        uow: UnitOfWork,
        plan: Sequence[tuple[ProfileRecord, list[HandleRecord], list[frozenset[UUID]]]],
        revivable: dict[frozenset[UUID], UUID],
        at: datetime,
    ) -> tuple[dict[UUID, int], list[UUID]]:
        """Moves each stray component to a profile of its own; returns the new privacy version per
        profile and the handles that moved."""
        versions: dict[UUID, int] = {}
        moved: list[UUID] = []
        for profile, own, strays in plan:
            bumped = await uow.identity.bump_profile(
                profile.profile_id, profile.membership_version, privacy=True
            )
            versions[profile.profile_id] = bumped.privacy_version
            by_id = {h.handle_id: h for h in own}
            for part in strays:
                home = await self._new_home(uow, part, by_id, revivable, at)
                await uow.identity.set_membership(sorted(part), home.profile_id, home.membership_version)
                versions[home.profile_id] = home.privacy_version
                moved += sorted(part)
        return versions, moved

    async def _new_home(
        self,
        uow: UnitOfWork,
        part: frozenset[UUID],
        by_id: dict[UUID, HandleRecord],
        revivable: dict[frozenset[UUID], UUID],
        at: datetime,
    ) -> ProfileRecord:
        """The profile a split-off component moves to: the one a reversed merge retired, when the
        component is exactly what that merge moved, so unmerge undoes merge down to the ids."""
        if part in revivable:
            revived = await uow.identity.revive_profile(revivable[part])
            if revived is not None:
                return await uow.identity.bump_profile(
                    revived.profile_id, revived.membership_version, privacy=True
                )
        return await uow.identity.create_profile(uuid7(), profile_kind(by_id[h] for h in part), at)

    # Shared helpers.

    async def _resolve(
        self, uow: UnitOfWork, space_id: UUID, handles: Sequence[Handle], kind: SubjectKind, at: datetime
    ) -> list[HandleRecord]:
        """Finds or creates the handles, each in its own profile if it had none."""
        normalized = [self._normalize(h, kind) for h in handles]
        records = await self._ensure_handles(uow, space_id, normalized, at, set())
        members = await uow.identity.memberships([r.handle_id for r in records.values()])
        await self._place_singletons(uow, records.values(), members, at)
        return [records[n.key] for n in normalized]

    async def _assert(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        first: HandleRecord,
        second: HandleRecord,
        method: AssertionMethod,
        at: datetime,
        *,
        actor: str,
        reason: str | None,
    ) -> Operation:
        """An explicit assertion: applied at once inside one profile, otherwise queued for the merge."""
        if first.subject_kind is not second.subject_kind:
            return await self._assertion_as_link(uow, first, second, method, at)
        members = await uow.identity.memberships([first.handle_id, second.handle_id])
        same_profile = members[first.handle_id] == members[second.handle_id]
        state = AssertionState.APPLIED if same_profile else AssertionState.PENDING
        assertion = _assertion(first, second, method, state, at, actor=actor, reason=reason)
        await uow.identity.add_assertions([assertion])
        if same_profile:
            if await _confirm_pair(uow, first, second):
                await _request_recompute(uow, space_id, [members[first.handle_id]])
            return Operation(uuid7(), "applied", assertion_id=assertion.assertion_id)
        op_id = await self._enqueue_apply(uow, space_id, [assertion.assertion_id], members)
        return Operation(op_id, "queued", assertion_id=assertion.assertion_id)

    async def _assertion_as_link(
        self,
        uow: UnitOfWork,
        first: HandleRecord,
        second: HandleRecord,
        method: AssertionMethod,
        at: datetime,
    ) -> Operation:
        """Identity never crosses subject kinds: a person and an organization become a link (5.3)."""
        people = [h for h in (first, second) if not h.subject_kind.is_organization]
        if len(people) != 1:
            raise InvalidInput("an account and a partner are different organizations and never merge")
        person = people[0]
        org = second if person is first else first
        active = [
            x
            for x in await uow.identity.active_links([person.handle_id])
            if x.person_handle_id == person.handle_id
        ]
        existing = next((x for x in active if x.org_handle_id == org.handle_id), None)
        if existing is not None:
            return Operation(uuid7(), "linked", link_id=existing.link_id)
        link_method = method if method in LINK_METHODS else AssertionMethod.DECLARED
        link = LinkRecord(uuid7(), person.handle_id, org.handle_id, CO_OCCURRENCE_ROLE, False, at, None)
        await uow.identity.add_link(
            link, method=link_method, origin_event_id=None, confidence=METHOD_CONFIDENCE[link_method]
        )
        return Operation(uuid7(), "linked", link_id=link.link_id)

    async def _profile(self, uow: UnitOfWork, profile_id: UUID) -> ProfileRecord:
        profile = await uow.identity.get_profile(profile_id)
        if profile is None:
            raise NotFound("unknown profile")
        return profile

    async def _profile_handles(self, uow: UnitOfWork, profile_id: UUID) -> list[HandleRecord]:
        return await uow.identity.get_handles((await self._profile(uow, profile_id)).handle_ids)

    async def _enqueue_apply(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        assertion_ids: Sequence[UUID],
        members: dict[UUID, UUID],
        *,
        force: bool = False,
        due: datetime | None = None,
    ) -> UUID:
        """Queues the merge, or with `due` schedules it then, as the safety net of a merge its caller runs
        after the commit."""
        op_id = uuid7()
        payload: dict[str, Any] = {
            "op": ApplyOp.APPLY.value,
            "op_id": str(op_id),
            "assertion_ids": [str(a) for a in assertion_ids],
            "profile_ids": sorted({str(p) for p in members.values()}),
        }
        if force:
            payload["force"] = True
        task = Task(TaskName.IDENTITY_APPLY, space_id, payload)
        if due is None:
            await uow.pipeline.enqueue([task])
        else:
            await uow.pipeline.schedule(task, due, DelayReason.SAFETY_NET)
        return op_id

    async def _wait_for_lease(self, space_id: UUID, units: Sequence[str], owner: str) -> LeaseGrant | None:
        deadline = time.monotonic() + MERGE_LEASE_WAIT_S
        while True:
            now = self._clock.now()
            if self._leases is not None:
                grant = await self._leases.acquire(space_id, list(units), owner, LEASE_TTL_S, now)
            else:
                async with self._uow(space_id) as uow:
                    grant = await uow.pipeline.acquire(space_id, units, owner, LEASE_TTL_S, now)
            if grant is not None or time.monotonic() >= deadline:
                return grant
            await asyncio.sleep(MERGE_LEASE_POLL_S)

    async def _release(self, grant: LeaseGrant) -> None:
        if self._leases is not None:
            await self._leases.release(grant)
            return
        async with self._uow(grant.space_id) as uow:
            await uow.pipeline.release(grant)

    async def _enqueue_split(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        op: ApplyOp,
        actor: str,
        reason: str | None,
        *,
        handles: Sequence[UUID] = (),
        assertions: Sequence[UUID] = (),
    ) -> UUID:
        op_id = uuid7()
        payload = {
            "op": op.value,
            "op_id": str(op_id),
            "handle_ids": [str(h) for h in handles],
            "assertion_ids": [str(a) for a in assertions],
            "actor": actor,
            "reason": reason,
        }
        await uow.pipeline.enqueue([Task(TaskName.IDENTITY_APPLY, space_id, payload)])
        return op_id


def _unbarred(
    event: _Event, group: list[HandleRecord], members: dict[UUID, UUID], barred: dict[UUID, set[UUID]]
) -> list[HandleRecord]:
    """Leaves out of the group the new handle of a number that changed hands when the group would take it
    to the profile of the previous holder: only that profile's record id, from the tenant's systems or a
    login, brings it back, never a typed e-mail or a code sent to the number."""
    if not barred:
        return group

    def blocked(handle: HandleRecord) -> bool:
        previous = barred.get(handle.handle_id, set())
        profiles = {members.get(h.handle_id) for h in group if h is not handle} - {None}
        return any(
            p in previous and not _vouched(event, group, members, p) for p in profiles if p is not None
        )

    return [h for h in group if not blocked(h)]


async def _confirm_pair(uow: UnitOfWork, first: HandleRecord, second: HandleRecord) -> bool:
    """An operator who asserts a pair inside one profile confirms the provisional end by the confirmed one.
    Returns whether it confirmed one: the profile's derivations then count its rows (5.3)."""
    if first.provisional == second.provisional:
        return False
    await uow.identity.set_provisional([h.handle_id for h in (first, second) if h.provisional], False)
    return True


async def _settle_provisional(uow: UnitOfWork, profile_ids: Iterable[UUID]) -> None:
    """After a split, a profile left with provisional handles only is nobody else's: nothing is capped."""
    for profile_id in profile_ids:
        profile = await uow.identity.get_profile(profile_id)
        if profile is None:
            continue
        handles = await uow.identity.get_handles(profile.handle_ids)
        if handles and all(h.provisional for h in handles):
            await uow.identity.set_provisional([h.handle_id for h in handles], False)


def _vouched(
    event: _Event, group: Sequence[HandleRecord], members: dict[UUID, UUID], profile_id: UUID
) -> bool:
    """The event carries a confirmed record id of the profile (a system id, a document, an app login),
    from the tenant's systems, a login or an ordinary event of a system of record."""
    return event.method in _VOUCHING_METHODS and any(
        h.type in RECORD_TYPES
        and h.status is HandleStatus.ACTIVE
        and not h.provisional
        and members.get(h.handle_id) == profile_id
        for h in group
    )


def _normalized(record: HandleRecord) -> NormalizedHandle:
    return NormalizedHandle(_key(record), record.subject_kind)


def _strays(
    own: Sequence[HandleRecord], edges: Sequence[tuple[UUID, UUID]], leaving: set[UUID]
) -> list[frozenset[UUID]]:
    """The components that must leave the profile: all but the one holding the anchor of the
    handles that stay, so the profile id stays with them (a bridge can leave two or more)."""
    keeper = choose_anchor([h for h in own if h.handle_id not in leaving] or own).handle_id
    return [part for part in connected_components((h.handle_id for h in own), edges) if keeper not in part]


def _crosses(assertion: AssertionRecord, split: set[UUID]) -> bool:
    """An edge with exactly one end in the split set is what holds the set to the rest."""
    return (assertion.handle_a in split) != (assertion.handle_b in split)


async def _record_feed(
    uow: UnitOfWork, kind: str, versions: dict[UUID, int], handle_ids: Sequence[UUID]
) -> None:
    """One row per profile, each with its own new privacy version (06, 3)."""
    for profile_id, version in versions.items():
        await uow.privacy.record_feed(
            kind=kind, profile_ids=[profile_id], handle_ids=handle_ids, privacy_version=version
        )


RECOMPUTED_ON_IDENTITY = (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.TRIGGER, TaskName.CONSOLIDATE)
"""What a merge, a split or a confirmation asks again of each profile involved: the packs, the patterns,
the warnings (a split retracts the firings whose evidence left) and the stored summaries, so the rows that
moved or became confirmed count, or stop counting, right away instead of at night (5.3)."""


async def _request_recompute(uow: UnitOfWork, space_id: UUID, profile_ids: Iterable[UUID]) -> None:
    for profile_id in dict.fromkeys(profile_ids):
        for name in RECOMPUTED_ON_IDENTITY:
            await uow.pipeline.request_work(
                Task(name, space_id, {"reason": "identity"}, profile_id=profile_id)
            )


async def _prewarm_readers(
    uow: UnitOfWork, space_id: UUID, members: dict[UUID, UUID], handle_ids: Iterable[UUID]
) -> None:
    """A handle a claim just joined reads packs of its own (5.3): they are compiled now, so its first read
    finds them instead of compiling on demand."""
    by_profile: dict[UUID, list[str]] = {}
    for handle_id in sorted(handle_ids):
        by_profile.setdefault(members[handle_id], []).append(str(handle_id))
    for profile_id, readers in by_profile.items():
        payload = {"reason": "unconfirmed_link", "readers": readers}
        await uow.pipeline.enqueue([Task(TaskName.COMPILE, space_id, payload, profile_id=profile_id)])


async def _views(uow: UnitOfWork, suggestions: Sequence[SuggestionRecord]) -> list[SuggestionView]:
    ids = sorted({h for s in suggestions for h in (s.handle_a, s.handle_b)})
    handles = {h.handle_id: h for h in await uow.identity.get_handles(ids)}
    profiles = await uow.identity.memberships(ids)
    return [SuggestionView(s, handles, profiles) for s in suggestions]


def _suggestion_event(suggestion: SuggestionRecord) -> dict[str, Any]:
    """Ids and score only: the features name which values agreed, which says too much outside the cell."""
    return {
        "suggestion_id": str(suggestion.suggestion_id),
        "handle_ids": [str(suggestion.handle_a), str(suggestion.handle_b)],
        "score": suggestion.score,
        "as_of": suggestion.created_at.isoformat(),
    }


async def _space_of(uow: UnitOfWork) -> UUID:
    if uow.space_id is None:
        raise RuntimeError("attach runs inside a space's transaction, never a cell-wide one")
    return uow.space_id


def _key(record: HandleRecord) -> HandleKey:
    return HandleKey(record.type, record.scope, record.value)


def _with_derived(normalized: Iterable[NormalizedHandle]) -> list[NormalizedHandle]:
    unique: dict[HandleKey, NormalizedHandle] = {}
    for handle in normalized:
        for item in (handle, *handle.derived):
            unique.setdefault(item.key, item)
    return list(unique.values())


def _assertion(
    first: HandleRecord,
    second: HandleRecord,
    method: AssertionMethod,
    state: AssertionState,
    at: datetime,
    *,
    event: _Event | None = None,
    actor: str | None = None,
    reason: str | None = None,
) -> AssertionRecord:
    return AssertionRecord(
        assertion_id=uuid7(),
        handle_a=first.handle_id,
        handle_b=second.handle_id,
        method=method,
        confidence=METHOD_CONFIDENCE[method],
        level_at_capture=event.level if event else max(first.level, second.level, key=lambda lv: lv.rank),
        state=state,
        created_at=at,
        origin_event_id=event.origin_event_id if event else None,
        actor=actor,
        reason=reason,
    )
