"""Typed state on the read path (24.3; 06, 15.1): `POST /v1/state/read`, the SDK profile, and the copies of
the typed objects the read path serves without a database statement.

The objects worker writes an object's copy to the cache store once its transaction commits (`publish`), under
the row's change sequence; a read opens the copies it finds, and the missing ones (a first read, an eviction)
come from the database in one statement and are written back under the same rule, so a copy never replaces a
newer one. Which source a field serves, how fresh it is and what may be claimed are computed at the read
(`typed_state`), against the declaration in force.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

import orjson

from niadra.app.derived import (
    NOT_DERIVED,
    PIECES_MAX,
    Derivation,
    Lookup,
    Related,
    derive,
    lookups,
    matched,
    named_by,
    private_derived,
    related,
)
from niadra.app.object_memory import final_at, memory_out
from niadra.app.objects import check_purpose, offset_min
from niadra.app.refresh import Ask, RefreshService
from niadra.app.serve import BlockAsk, BlockGate, BlockRead
from niadra.app.serve_reads import Target
from niadra.app.shared_objects import SharedCopy, SharedObjectsService
from niadra.app.typed_state import (
    Purpose,
    ReadRequest,
    TypedObject,
    evaluated,
    field_access,
    newest,
    read,
    resolved_state,
)
from niadra.app.typed_state import wanted as wanted_of
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectType, ObjectTypeRegistry
from niadra.contracts.state import (
    ChangeSinceSeen,
    FieldCoverage,
    InterestState,
    ObjectCoverage,
    ObjectRead,
    SdkProfile,
    StateReadRequest,
    StateReadResponse,
    StateRef,
    StateVerifyRequest,
    StateVerifyResponse,
    StateView,
    TypeCoverage,
    Verdict,
)
from niadra.domain.common.errors import Forbidden, NotFound
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.compile.envelope import NONCE_HEX, nonce_input
from niadra.domain.compile.state_view import (
    Change,
    ObjectLine,
    bookkeeping,
    changes_since_seen,
    render,
    summarized,
)
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.memory.bound import CUSTOMER_FACING, OBJECTS_CEILING, ObjectMemory, OnLine, in_text
from niadra.domain.objects.state import BOOKKEEPING, LIFECYCLE
from niadra.domain.objects.undeclared import as_value, humanized, shown
from niadra.domain.objects.written import distinctive
from niadra.domain.policy.fields import FieldReader, reader_of
from niadra.domain.records import BusinessObject, ObjectKey, ProfileRecord
from niadra.domain.serve.cache_keys import CacheEpoch, gen_key
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.state.coverage import coverage
from niadra.domain.state.expr import anchor
from niadra.domain.state.keys import (
    object_key,
    object_version_key,
    relation_key,
    relation_version_key,
    subject_objects_key,
)
from niadra.domain.state.observed import Holding, decode, encode
from niadra.domain.state.shared import SharedKey, shared_key
from niadra.domain.state.typed import UNTYPED, Typed, meta_part, sealed_part, typed_of
from niadra.domain.vocabulary import Feature, ReceiptSurface
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache, KeyVault
from niadra.ports.state import NoInterests, SeenInterest, SubjectInterests
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

COPY_TTL_S = 24 * 3600
"""A copy nobody wrote again leaves the cache after a day; the database has it."""
ABSENT_TTL_S = 300
"""How long the cache says an object is not known: its first event writes its copy over this one."""
PROFILE_TTL_S = 300
SUBJECT_OBJECTS = 20
"""The objects a subject's state block says, a choice of length and read time, not of memory: every object
whose memory holds an instruction or an open item first, then the open ones, then the newest (`_shown`);
past it the block says how many more the subject has (`render(more=...)`), and the pack and the history
hold them all. The 20 newest it once said left a client's older cases off the block with no word of them.
"""
INTERESTS = 20
"""The newest interests in shared objects a subject's state block says."""
SUBJECT_OBJECTS_TTL_S = 24 * 3600
COVERAGE_SAMPLE = 5000
"""The objects of each kind, customers' and shared, the coverage is measured on: the ones changed last."""
"""How long an SDK keeps the profile before asking again (it sends the ETag, and a 304 costs nothing)."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)
_OPAQUE_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@dataclass(frozen=True, slots=True)
class Copy:
    """An object as the read path keeps it: its observations and owner, and when the copy was made."""

    key: ObjectKey
    object_id: UUID | None
    owner: UUID | None
    as_of: datetime | None
    fields: Holding
    derived_status: str | None
    made_at: datetime
    typed: Typed = UNTYPED

    @property
    def known(self) -> bool:
        return self.object_id is not None


@dataclass(frozen=True, slots=True)
class StateAnswer:
    """What a state read returns, and the objects its receipt names."""

    response: StateReadResponse
    object_ids: tuple[UUID, ...]
    verdicts: StateVerifyResponse | None = None


class StateService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cache: KeyValueCache,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        *,
        epoch: CacheEpoch | None = None,
        shared: SharedObjectsService | None = None,
        refresh: RefreshService | None = None,
    ) -> None:
        self._uow = uow
        self._cache = cache
        self._keys = keys
        self._clock = clock
        self._config = config
        self._epoch = epoch or CacheEpoch()
        self._shared = shared
        self._refresh = refresh
        # The subject's interests, from the signals module's cache; the cell wires it (24.3.13).
        self.interests: SubjectInterests = NoInterests()

    async def publish(self, space_id: UUID, objects: Sequence[BusinessObject]) -> None:
        """After a commit: the new copies of the objects of a declared type. A copy the cache refuses or loses
        is read from the database by the next read, so a failure here only costs that read a statement."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        now = self._clock.now()
        for obj in objects:
            if space.object_types.get(obj.key.type) is None:
                continue
            try:
                await self._put(space_id, _copy_of(obj, now), obj.revision)
                for field, value in obj.typed.matched:
                    # A reverse relation's index of this value is read again from the database.
                    digest = await self._match_digest(space_id, obj.key.type, field, value)
                    version = relation_version_key(self._epoch.value, space_id, digest)
                    await self._cache.bump_generation(version, obj.revision, COPY_TTL_S)
            except _CACHE_ERRORS:
                log.warning("typed state copy not written; the next read takes it from the database")

    async def read(
        self, caller: SourcePrincipal, request: StateReadRequest, guard: PrivacyGuard | None = None
    ) -> StateAnswer:
        """`POST /v1/state/read` (7.7): the objects by reference, each for the read's purpose. Warm, it runs
        no database statement; it never waits for a source, and it never refuses (L19)."""
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        declared = [_declared(space, ref) for ref in request.refs]
        for ref in request.refs:
            check_purpose(space, caller, ref.type)
        copies, degraded = await self._all_copies(space_id, space, request.refs, guard)
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        wanted = ReadRequest(
            request.purpose,
            request.fields,
            tuple(request.readings or ()),
            reader=_reader(space, caller),
            commercial=space.object_types.commercial_purposes,
        )
        typed = [
            (TypedObject(copy.key, copy.fields, copy.as_of, copy.derived_status, copy.typed), copy.object_id)
            for copy in copies
        ]
        offset = offset_min(space, now)
        composites = [
            (obj, declaration, copy.known)
            for (obj, _), declaration, copy in zip(typed, declared, copies, strict=True)
        ]
        derivations = await self._derivations(caller, space, composites, wanted, names, now, offset, guard)
        objects = [
            read(
                obj,
                declaration,
                wanted,
                names=names,
                now=now,
                utc_offset_min=offset,
                derived=derivation.fields,
                derived_withheld=derivation.withheld,
            )
            for (obj, _), declaration, derivation in zip(typed, declared, derivations, strict=True)
        ]
        objects = await self._refetch(space_id, typed, declared, objects, request.purpose, names, now, offset)
        found = tuple(dict.fromkeys(c.object_id for c in copies if c.object_id is not None))
        return StateAnswer(StateReadResponse(objects=objects, degraded=degraded), found)

    async def verify(
        self, caller: SourcePrincipal, request: StateVerifyRequest, guard: PrivacyGuard | None = None
    ) -> StateAnswer:
        """`POST /v1/state/verify` (24.2.4): a verdict per object and field for a claim, from a `claim` read,
        without waiting for the source; a value given is compared with the one held."""
        refs = list(
            {(c.ref.type, c.ref.namespace, c.ref.id, c.ref.variant): c.ref for c in request.checks}.values()
        )
        answer = await self.read(
            caller, StateReadRequest(refs=refs, purpose="claim", subject=request.subject), guard
        )
        by_ref = {(o.ref.type, o.ref.namespace, o.ref.id): o for o in answer.response.objects}
        verdicts = []
        for check in request.checks:
            obj = by_ref[(check.ref.type, check.ref.namespace, check.ref.id)]
            field = obj.fields.get(check.field)
            value = obj.values.get(check.field)
            known = field is not None and field.logic in ("yes", "no")
            held = field.v if field else value.v if value else None
            verdicts.append(
                Verdict(
                    ref=check.ref,
                    field=check.field,
                    claim_safe=field.claim_safe if field else value.claim_safe if value else False,
                    matches=None if check.value is None or not (known or value) else check.value == held,
                    status=field.status if field else value.status if value else "expired",
                    prohibitions=obj.prohibitions,
                    declared_gaps=value.declared_gaps if value else [],
                )
            )
        return StateAnswer(
            StateReadResponse(objects=answer.response.objects, degraded=answer.response.degraded),
            answer.object_ids,
            StateVerifyResponse(verdicts=verdicts),
        )

    async def _refetch(
        self,
        space_id: UUID,
        typed: Sequence[tuple[TypedObject, UUID | None]],
        declared: Sequence[ObjectType],
        objects: list[ObjectRead],
        purpose: str,
        names: Mapping[UUID, str],
        now: datetime,
        offset: int,
    ) -> list[ObjectRead]:
        """Each object's reason to be fetched again, if one holds, with whether its budget admitted a request
        (24.3.5): no statement, and never a wait for the source."""
        if self._refresh is None:
            return objects
        asks: list[tuple[int, Ask]] = []
        for i, ((obj, object_id), declaration) in enumerate(zip(typed, declared, strict=True)):
            if object_id is None or declaration.refetch is None:
                continue
            seen = evaluated(obj, declaration, names, now, utc_offset_min=offset, purpose=purpose)
            found = wanted_of(declaration, seen.env, newest(obj.fields), now)
            if found is not None:
                key = SharedKey(obj.key.type, obj.key.namespace, obj.key.external_id)
                asks.append((i, Ask(object_id, key, declaration, found)))
        if not asks:
            return objects
        answers = await self._refresh.want(space_id, [ask for _, ask in asks])
        for (i, _), answer in zip(asks, answers, strict=True):
            objects[i] = objects[i].model_copy(update={"refetch": answer})
        return objects

    async def subject_view(
        self,
        caller: SourcePrincipal,
        target: Target,
        guard: PrivacyGuard | None = None,
        purpose: str = "display",
        gate: BlockGate | None = None,
        memory: Sequence[ObjectMemory] = (),
        carried: frozenset[str] = frozenset(),
    ) -> tuple[StateView, tuple[UUID, ...]]:
        """The `now` view (24.3.13), the state block of a context read (`include: ["state"]`) and its own
        route: the subject's objects of the declared types, newest first; the shared objects they showed
        interest in, as they stand now, and what changed since they saw them; and the same as lines for the
        turn block. Warm, it runs no database statement: the list of the subject's objects is kept beside the
        pack, under the profile's pack generation, each object is its copy, the interests come from the
        signals module's cache and each shared object from the working set's hot copy. Beside a pack
        (`gate`), each object and each interest goes only where the pack's policy lets an item of its type
        go, by its own attributes. An instruction the served pack already says (`carried`, its lines' ids)
        stays in the JSON and leaves the lines: said twice, an instruction beside a contact preference left an
        attendant refusing a client his own claim amount (juridico-sandbox, 06/10/2026)."""
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        keys, unread = await self._subject_objects(space_id, space, target.profile)
        about = target.about.profile if target.about is not None else None
        if about is not None:
            # A person acting for an organization (`about`) reads the organization's objects beside their own
            # (21.4): a lawyer's case belongs to the firm, a buyer's order to the company.
            theirs, also = await self._subject_objects(space_id, space, about)
            keys += [k for k in theirs if k not in keys]
            unread += also
        readable, more = _shown([key for key in keys if _readable(space, caller, key.type)], memory)
        more += unread
        copies, degraded = await self._copies(space_id, readable, guard)
        if gate is not None:
            copies = [
                c
                for c in copies
                if gate.admits_about(c.key.type, target, (c.owner,) if c.owner is not None else ())
            ]
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        asked = ReadRequest(
            _purpose(purpose),
            reader=_reader(space, caller),
            commercial=space.object_types.commercial_purposes,
        )
        offset = offset_min(space, now)
        typed, declared_of = [], []
        for copy in copies:
            declared = space.object_types.get(copy.key.type)
            if declared is not None and copy.known:
                obj = TypedObject(copy.key, copy.fields, copy.as_of, copy.derived_status, copy.typed)
                typed.append((obj, copy.object_id))
                declared_of.append(declared)
        composites = [(obj, declared, True) for (obj, _), declared in zip(typed, declared_of, strict=True)]
        derivations = await self._derivations(caller, space, composites, asked, names, now, offset, guard)
        objects = [
            read(
                obj,
                declared,
                asked,
                names=names,
                now=now,
                utc_offset_min=offset,
                derived=derivation.fields,
                derived_withheld=derivation.withheld,
            )
            for (obj, _), declared, derivation in zip(typed, declared_of, derivations, strict=True)
        ]
        objects = await self._refetch(space_id, typed, declared_of, objects, purpose, names, now, offset)
        interests, changes, lost = await self._interests(
            caller, space, target, purpose, names, now, offset, gate, guard
        )
        registry = space.object_types
        private = registry.private_fields()
        hidden = private_derived(registry) if registry.derived_fields_in_text else None

        def unsaid(kind: str) -> frozenset[str]:
            return private.get(kind, frozenset()) | (hidden or {}).get(kind, frozenset())

        said = hidden is not None
        # What the owner and participants said about each object, as the pack beside it let it through:
        # on its line every instruction and item and the newest observations
        # (`domain.memory.bound.in_text`), and in its JSON up to `MAX_ROWS` of each kind.
        bound = {m.ref: m for m in memory}
        now_at = self._clock.now()
        objects = [_with_memory(o, bound.get(_ref_of(o)), now_at) for o in objects]
        called = _names(registry, [*objects, *(i.object for i in interests if i.object)], unsaid)
        on_line = [_in_line(bound.get(_ref_of(o)), now_at, carried) for o in objects]
        lines = [
            replace(
                _line(
                    o,
                    private=unsaid(o.ref.type),
                    derived=said,
                    parents=_parents(registry, o, unsaid, called),
                    declared=registry.get(o.ref.type),
                    name=called.get(_spoken_ref(o)),
                ),
                bound=tuple(told.rows),
                more=told.more,
            )
            for o, told in zip(objects, on_line, strict=True)
        ]
        if caller.audience in CUSTOMER_FACING:
            # A customer-facing agent reads a case, not the system's bookkeeping of its files: each child
            # whose line says nothing a person recognises is a count on its parent's line (`bookkeeping`).
            plain = [
                bookkeeping(line, _measured(o, registry.get(o.ref.type), unsaid(o.ref.type)))
                for line, o in zip(lines, objects, strict=True)
            ]
            lines = summarized(lines, plain)
        lines += [
            _line(
                i.object,
                [c for c in changes if c.ref == i.ref],
                unsaid(i.object.ref.type),
                derived=said,
                parents=_parents(registry, i.object, unsaid, called),
                name=called.get(_spoken_ref(i.object)),
            )
            for i in interests
            if i.object
        ]
        locale, timezone = space.settings.locale, space.settings.timezone
        text = render(lines, locale, timezone, more=more)
        if text is not None and any(line.content for line in lines):
            # The envelope's nonce: the space's key over the block's own bytes (context-pack 10.7).
            digest = await self._keys.hmac(space_id, "content_envelope", nonce_input(text))
            text = render(lines, locale, timezone, digest.hex()[:NONCE_HEX], more=more)
        view = StateView(
            objects=objects,
            interests=interests,
            changes_since_seen=changes,
            text=text,
            degraded=degraded or lost,
        )
        found = tuple(c.object_id for c in copies if c.object_id is not None)
        return view, found

    async def _interests(
        self,
        caller: SourcePrincipal,
        space: SpaceConfig,
        target: Target,
        purpose: str,
        names: Mapping[UUID, str],
        now: datetime,
        offset: int,
        gate: BlockGate | None = None,
        guard: PrivacyGuard | None = None,
    ) -> tuple[list[InterestState], list[ChangeSinceSeen], bool]:
        """The subject's newest interests in shared objects of a declared type, each as it stands now, and
        the fields whose value is not the one they were shown; beside a pack, of the types its gate admits."""
        space_id = space.space_id
        try:
            seen = await self.interests.interests(space_id, list(target.profile.handle_ids))
        except _CACHE_ERRORS:
            return [], [], True
        wanted: list[tuple[SeenInterest, SharedKey, ObjectType]] = []
        for interest in sorted(seen, key=lambda i: i.at, reverse=True):
            key = shared_key(interest.ref)
            declared = space.object_types.get(key.type) if key else None
            if key is None or declared is None or declared.ownership != "shared":
                continue
            if gate is not None and not gate.admits_about(key.type, target):
                continue
            if _readable(space, caller, key.type):
                wanted.append((interest, key, declared))
            if len(wanted) == INTERESTS:
                break
        if not wanted or self._shared is None:
            return [], [], False
        copies, lost = await self._shared.copies(space_id, [key for _, key, _ in wanted])
        asked = ReadRequest(
            _purpose(purpose),
            reader=_reader(space, caller),
            commercial=space.object_types.commercial_purposes,
        )
        interests: list[InterestState] = []
        changes: list[ChangeSinceSeen] = []
        typed_held = [
            (_typed_copy(key, copy), declared, copy is not None)
            for (_, key, declared), copy in zip(wanted, copies, strict=True)
        ]
        derivations = await self._derivations(caller, space, typed_held, asked, names, now, offset, guard)
        for (interest, key, declared), copy, (obj, _, _), derivation in zip(
            wanted, copies, typed_held, derivations, strict=True
        ):
            ref = StateRef(
                type=key.type, namespace=key.namespace, id=key.external_id, variant=key.variant or None
            )
            state = None
            if copy is not None:
                state = read(
                    obj,
                    declared,
                    asked,
                    names=names,
                    now=now,
                    utc_offset_min=offset,
                    derived=derivation.fields,
                    derived_withheld=derivation.withheld,
                )
                held = {
                    name: (f.v, f.observed_at)
                    for name, f in state.fields.items()
                    if f.logic in ("yes", "no") and not f.masked
                }
                changes += [
                    ChangeSinceSeen(ref=ref, field=c.field, seen=c.seen, now=c.now, observed_at=c.observed_at)
                    for c in changes_since_seen(interest.ref, interest.shown, held)
                ]
            interests.append(InterestState(ref=ref, reason=interest.reason, at=interest.at, object=state))
        return interests, changes, lost

    async def derived_of(
        self,
        caller: SourcePrincipal,
        obj: TypedObject,
        declared: ObjectType,
        request: ReadRequest,
        guard: PrivacyGuard | None,
    ) -> Derivation:
        """The derived fields of one object read outside these routes (`GET /v1/objects/...`, which reads the
        object itself from the database): its related objects as `read` finds them."""
        space = self._config.space(caller.space.space_id)
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        [derivation] = await self._derivations(
            caller, space, [(obj, declared, True)], request, names, now, offset_min(space, now), guard
        )
        return derivation

    async def _derivations(
        self,
        caller: SourcePrincipal,
        space: SpaceConfig,
        objects: Sequence[tuple[TypedObject, ObjectType, bool]],
        request: ReadRequest,
        names: Mapping[UUID, str],
        now: datetime,
        offset: int,
        guard: PrivacyGuard | None,
    ) -> list[Derivation]:
        """The derived fields of each known object whose type declares some: the related
        objects of every object at once, a shared one from the working set's hot copy and a customer's from
        its copy, each only where the caller may read its type."""
        registry = space.object_types
        pieces = [
            related(declared, obj, names, now) if known and declared.derived_fields else {}
            for obj, declared, known in objects
        ]
        searches = [
            lookups(declared, obj, names, now) if known and declared.derived_fields else {}
            for obj, declared, known in objects
        ]
        readable_searches = [
            s for by in searches for ss in by.values() for s in ss if _readable(space, caller, s.type)
        ]
        matches = await self._matching(space.space_id, readable_searches) if readable_searches else {}
        for of_object, searched in zip(pieces, searches, strict=True):
            for name, ss in searched.items():
                if not _readable(space, caller, ss[0].type):
                    continue  # the caller may not read the related type: the fields over it are not known
                named = dict.fromkeys(k for s in ss for k in matches.get(s, ()))
                of_object[name] = [Related(k.type, k.namespace, k.external_id) for k in named][
                    : PIECES_MAX + 1
                ]
        if not any(pieces):
            return [NOT_DERIVED] * len(objects)
        wanted = {r.key: r for found in pieces for refs in found.values() for r in refs[:PIECES_MAX]}
        readable = [
            r for r in wanted.values() if (t := registry.get(r.type)) and _readable(space, caller, r.type)
        ]
        shared = [r for r in readable if (t := registry.get(r.type)) is not None and t.ownership == "shared"]
        owned = [r for r in readable if (t := registry.get(r.type)) is not None and t.ownership == "subject"]
        copies: dict[tuple[str, str, str, str], TypedObject] = {}
        if shared and self._shared is not None:
            keys = [SharedKey(r.type, r.namespace, r.external_id, r.variant) for r in shared]
            held, _ = await self._shared.copies(space.space_id, keys)
            for ref, found in zip(shared, held, strict=True):
                if found is not None:
                    copies[ref.key] = _typed_copy(found.key, found)
        if owned:
            found_copies, _ = await self._copies(
                space.space_id, [ObjectKey(r.type, r.namespace, r.external_id) for r in owned], guard
            )
            by_key = {c.key: c for c in found_copies if c.known}
            for ref in owned:
                copy = by_key.get(ObjectKey(ref.type, ref.namespace, ref.external_id))
                if copy is not None:
                    copies[ref.key] = TypedObject(
                        copy.key, copy.fields, copy.as_of, copy.derived_status, copy.typed
                    )
        for of_object, searched in zip(pieces, searches, strict=True):
            for name, ss in searched.items():
                if name not in of_object or len(of_object[name]) > PIECES_MAX:
                    continue  # past the bound, the field is not known whatever the related objects hold
                # A related object found by a value it no longer holds, or gone, is not related any more.
                of_object[name] = [
                    r for r in of_object[name] if _holds_value(copies.get(r.key), ss, registry, names, now)
                ]
        return [
            derive(declared, registry, found, copies, request, names=names, now=now, utc_offset_min=offset)
            if found
            else NOT_DERIVED
            for (_, declared, _), found in zip(objects, pieces, strict=True)
        ]

    async def _matching(self, space_id: UUID, searches: Sequence[Lookup]) -> dict[Lookup, list[ObjectKey]]:
        """A reverse relation's related objects, from the index the cache keeps per search,
        valid while no object written since holds the value (its version key); the missing ones in one
        statement, kept for the next reads unless a write moved the version meanwhile."""
        epoch = self._epoch.value
        wanted = list(dict.fromkeys(searches))
        digests = [await self._match_digest(space_id, s.type, s.field, s.value) for s in wanted]
        index = [relation_key(epoch, space_id, d) for d in digests]
        versions = [relation_version_key(epoch, space_id, d) for d in digests]
        try:
            raw = await self._cache.get_many([*index, *versions])
        except _CACHE_ERRORS:
            raw = [None] * (2 * len(wanted))
        found: dict[Lookup, list[ObjectKey]] = {}
        missing: list[tuple[Lookup, str, str, int]] = []
        for i, search in enumerate(wanted):
            kept, version = raw[i], raw[len(wanted) + i]
            current = int(version) if version is not None else 0
            body = orjson.loads(kept) if kept is not None else None
            if body is not None and version is not None and body["v"] == current:
                found[search] = [ObjectKey(*key) for key in body["keys"]]
            else:
                missing.append((search, index[i], versions[i], current))
        if missing:
            async with self._uow(space_id) as uow:
                loaded = await uow.objects.matching(
                    [(s.type, s.field, s.value) for s, _, _, _ in missing], limit=PIECES_MAX + 1
                )
            for search, key, version_key, current in missing:
                found[search] = loaded.get((search.type, search.field, search.value), [])
                body = {"v": current, "keys": [[k.type, k.namespace, k.external_id] for k in found[search]]}
                try:
                    await self._cache.bump_generation(version_key, current, COPY_TTL_S)
                    await self._cache.put_if_generation(
                        key, version_key, orjson.dumps(body), current, COPY_TTL_S
                    )
                except _CACHE_ERRORS:
                    break
        return found

    async def _match_digest(self, space_id: UUID, type_: str, field: str, value: str) -> str:
        raw = f"{type_}\x1f{field}\x1f{value}".encode()
        return (await self._keys.hmac(space_id, "relation_match", raw)).hex()[:32]

    async def coverage(self, principal: Principal) -> ObjectCoverage:
        """`GET /v1/objects/coverage`: each declared type's fields, the share of its objects that carry each
        and how old it is, measured on the objects that changed last (one statement, stamps only)."""
        space_id = principal.space.space_id
        space = self._config.space(space_id)
        declared = {t.type: list(t.fields) for t in space.object_types.types if t.ownership != "agent"}
        stamps: list[tuple[str, dict[str, Any]]] = []
        if declared:
            async with self._uow(space_id) as uow:
                stamps = await uow.typed.field_stamps(list(declared), limit=COVERAGE_SAMPLE)
        shares = coverage(declared, stamps, self._clock.now())
        return ObjectCoverage(
            types=[
                TypeCoverage(
                    type=share.type,
                    objects=share.objects,
                    fields=[
                        FieldCoverage(field=f.field, coverage=f.coverage, age_p50_s=f.age_p50_s)
                        for f in share.fields
                    ],
                )
                for share in shares
            ]
        )

    def profile(self, caller: SourcePrincipal) -> SdkProfile:
        """`GET /v1/sdk/profile` (7.7): what the SDK keeps in its local cache, from the configuration only."""
        space = self._config.space(caller.space.space_id)
        enabled = space.features.enabled
        reader = _reader(space, caller)
        return SdkProfile(
            features=sorted(enabled),
            tool_bindings=[b.public() for b in space.tool_bindings.for_source(caller.source_id)],
            types=[_summary(declared, reader) for declared in space.object_types.types],
            claim_contract=space.claim_contract.summary() if space.claim_contract.categories else None,
            recording=space.recording.summary(caller.source_id) if Feature.TURNS in enabled else None,
            valid_for_s=PROFILE_TTL_S,
        )

    async def _subject_objects(
        self, space_id: UUID, space: SpaceConfig, profile: ProfileRecord
    ) -> tuple[list[ObjectKey], int]:
        """A profile's typed objects, the open ones first, then the newest, up to the runaway guard, and how
        many past it were not listed: kept under its pack generation, so the compilation that follows a
        change to its objects moves the generation, and the list is read again once."""
        profile_id = profile.profile_id
        epoch = self._epoch.value
        index_key = subject_objects_key(epoch, space_id, profile_id)
        try:
            raw, generation = await self._cache.get_many([index_key, gen_key(epoch, space_id, profile_id)])
        except _CACHE_ERRORS:
            raw, generation = None, None
        current = int(generation) if generation is not None else None
        if raw is not None and current is not None:
            kept = orjson.loads(raw)
            if kept["generation"] == current:
                return [ObjectKey(*key) for key in kept["keys"]], int(kept.get("unread") or 0)
        async with self._uow(space_id) as uow:
            owned = await uow.objects.objects_for(list(profile.handle_ids), limit=OBJECTS_CEILING + 1)
        unread = max(0, len(owned) - OBJECTS_CEILING)
        typed = [o for o in owned[:OBJECTS_CEILING] if space.object_types.get(o.key.type) is not None]
        # Open first, newest first within each: the list was read newest first, and the sort is stable.
        keys = [o.key for o in sorted(typed, key=lambda o: final_at(o, space) is not None)]
        if current is not None:
            body = {
                "generation": current,
                "keys": [[k.type, k.namespace, k.external_id] for k in keys],
                "unread": unread,
            }
            try:
                await self._cache.set(index_key, orjson.dumps(body), SUBJECT_OBJECTS_TTL_S)
            except _CACHE_ERRORS:
                log.warning("the subject's typed objects not kept; the next read lists them again")
        return keys, unread

    async def _all_copies(
        self, space_id: UUID, space: SpaceConfig, refs: Sequence[StateRef], guard: PrivacyGuard | None
    ) -> tuple[list[Copy], bool]:
        """A subject's objects from their copies; a shared type's from the working set's hot copy."""
        shared = [i for i, ref in enumerate(refs) if _declared(space, ref).ownership == "shared"]
        rest = [i for i in range(len(refs)) if i not in set(shared)]
        found: dict[int, Copy] = {}
        copies, degraded = await self._copies(space_id, [_key_of(refs[i]) for i in rest], guard)
        found.update(zip(rest, copies, strict=True))
        if shared:
            now = self._clock.now()
            keys = [
                SharedKey(refs[i].type, refs[i].namespace, refs[i].id, refs[i].variant or "") for i in shared
            ]
            held: list[SharedCopy | None] = [None] * len(keys)
            if self._shared is not None:
                held, lost = await self._shared.copies(space_id, keys)
                degraded = degraded or lost
            for i, copy in zip(shared, held, strict=True):
                found[i] = _shared_copy(_key_of(refs[i]), copy, now)
        return [found[i] for i in range(len(refs))], degraded

    async def _copies(
        self, space_id: UUID, keys: Sequence[ObjectKey], guard: PrivacyGuard | None
    ) -> tuple[list[Copy], bool]:
        epoch = self._epoch.value
        digests = [await self._digest(space_id, key) for key in keys]
        cache_keys = [object_key(epoch, space_id, digest) for digest in digests]
        degraded = False
        try:
            raw = await self._cache.get_many(cache_keys)
        except _CACHE_ERRORS:
            raw, degraded = [None] * len(keys), True
        now = self._clock.now()
        copies: list[Copy | None] = [
            await self._opened(space_id, cache_key, value) if value is not None else None
            for cache_key, value in zip(cache_keys, raw, strict=True)
        ]
        copies = [None if c is not None and _guarded(c, guard, now) else c for c in copies]
        missing = [key for key, copy in zip(keys, copies, strict=True) if copy is None]
        if missing:
            loaded = await self._load(space_id, missing, write_back=not degraded)
            copies = [
                copy if copy is not None else loaded[key] for key, copy in zip(keys, copies, strict=True)
            ]
        return [copy for copy in copies if copy is not None], degraded

    async def _load(
        self, space_id: UUID, keys: Sequence[ObjectKey], *, write_back: bool
    ) -> dict[ObjectKey, Copy]:
        """The objects the cache did not have, in one statement, written back for the next reads."""
        async with self._uow(space_id) as uow:
            stored = {obj.key: obj for obj in await uow.objects.get_objects(list(dict.fromkeys(keys)))}
        now = self._clock.now()
        loaded: dict[ObjectKey, Copy] = {}
        for key in keys:
            obj = stored.get(key)
            loaded[key] = _copy_of(obj, now) if obj else Copy(key, None, None, None, {}, None, now)
            if write_back:
                try:
                    await self._put(space_id, loaded[key], obj.revision if obj else 0)
                except _CACHE_ERRORS:
                    write_back = False
        return loaded

    async def _put(self, space_id: UUID, copy: Copy, version: int) -> None:
        epoch = self._epoch.value
        digest = await self._digest(space_id, copy.key)
        key, version_key = object_key(epoch, space_id, digest), object_version_key(epoch, space_id, digest)
        ttl = COPY_TTL_S if copy.known else ABSENT_TTL_S
        sealed, dek = await self._keys.encrypt(space_id, _document(copy), key.encode())
        await self._cache.bump_generation(version_key, version, ttl)
        await self._cache.put_if_generation(key, version_key, pack_envelope(dek, sealed), version, ttl)

    async def _opened(self, space_id: UUID, key: str, value: bytes) -> Copy | None:
        try:
            dek, sealed = unpack_envelope(value)
            return _from_document(await self._keys.decrypt(space_id, sealed, key.encode(), dek))
        except Exception:  # a copy that does not open is a miss; the database has the object
            log.warning("typed state copy unreadable; reading the object from the database")
            return None

    async def _digest(self, space_id: UUID, key: ObjectKey) -> str:
        raw = f"{key.type}\x1f{key.namespace}\x1f{key.external_id}".encode()
        return (await self._keys.hmac(space_id, "object_state", raw)).hex()[:32]


class StateBlock:
    """`include: ["state"]` on the context read (24.2.10, 24.3.13): the subject's typed objects beside the
    pack, with a receipt of surface `state` naming them."""

    name = "state"
    feature = Feature.STATE
    surface = ReceiptSurface.STATE

    def __init__(self, state: StateService, guard: Callable[[UUID], Awaitable[PrivacyGuard]] | None = None):
        self._state = state
        self._guard = guard

    async def read(
        self,
        caller: SourcePrincipal,
        target: Target | None,
        ask: BlockAsk,
    ) -> BlockRead:
        if target is None:
            return BlockRead(StateView())  # a first contact has no objects yet
        guard = await self._guard(caller.space.space_id) if self._guard else None
        view, object_ids = await self._state.subject_view(
            caller, target, guard, ask.purpose, ask.gate, ask.memory, ask.carried
        )
        return BlockRead(view, item_ids=tuple(str(i) for i in object_ids))


def _ref_of(obj: ObjectRead) -> str:
    return f"{obj.ref.type}:{obj.ref.namespace}:{obj.ref.id}"


def _shown(keys: Sequence[ObjectKey], memory: Sequence[ObjectMemory]) -> tuple[list[ObjectKey], int]:
    """The objects the block says, up to `SUBJECT_OBJECTS`, and how many of `keys` it leaves out: the ones
    whose memory holds an instruction or an open item first, in their order, then the rest in theirs (the
    open first, then the newest). A case a client has an open promise on is never pushed off the block by
    a newer bookkeeping record."""
    held = {m.ref for m in memory if m.of("instruction") or m.open_items()}

    def ref(key: ObjectKey) -> str:
        return f"{key.type}:{key.namespace}:{key.external_id}"

    ranked = [k for k in keys if ref(k) in held] + [k for k in keys if ref(k) not in held]
    return ranked[:SUBJECT_OBJECTS], max(0, len(ranked) - SUBJECT_OBJECTS)


def _in_line(memory: ObjectMemory | None, now: datetime, carried: frozenset[str] = frozenset()) -> OnLine:
    """What an object's line says of its memory, without the instructions the served pack already says."""
    if memory is None:
        return OnLine([], {})
    told = in_text(memory, now)
    rows = [r for r in told.rows if r.kind != "instruction" or f"instruction:{r.row_id}" not in carried]
    return OnLine(rows, told.more)


def _with_memory(obj: ObjectRead, memory: ObjectMemory | None, now: datetime) -> ObjectRead:
    if memory is None or not memory.rows:
        return obj
    said = memory_out(memory, now)
    return obj.model_copy(update={"memory": said})


def _purpose(purpose: str) -> Purpose:
    return "claim" if purpose == "claim" else "decide" if purpose == "decide" else "display"


def _line(
    obj: ObjectRead,
    changes: Sequence[ChangeSinceSeen] | None = None,
    private: frozenset[str] = frozenset(),
    *,
    derived: bool = False,
    parents: Sequence[tuple[str, str | None]] = (),
    declared: ObjectType | None = None,
    name: str | None = None,
) -> ObjectLine:
    """What the turn block says of an object: of an interest (`changes` given), only what changed, and with
    `derived` its known derived fields. The fields its type holds private, and the derived fields that read a
    related type's private field, never reach the text. With its `declared` type, an object's own line also
    says the dates its timers count from and the fields whose changes the type tracks. Its `name` is said
    beside its ref, so the field it comes from is not said again."""
    ref = _spoken_ref(obj)
    named = frozenset(_key_named(declared, obj, private)[:1]) if declared and name else frozenset()
    due = [(t.name, t.due_at) for t in obj.timers if t.state == "armed" and t.due_at is not None]
    own = declared is not None and changes is None
    return ObjectLine(
        ref=ref,
        state=declared.value_word(obj.state) if declared is not None else obj.state,
        outcome=obj.outcome.state if obj.outcome else None,
        final=bool(obj.outcome and obj.outcome.final),
        next_due=min(due, key=lambda d: d[1]) if due else None,
        dates=_anchor_dates(obj, declared, private) if own and declared else [],
        fields=_said_fields(obj, declared, private, _anchors(declared) | named) if own and declared else [],
        expired_by=obj.expired_by,
        prohibitions=obj.prohibitions,
        changes=[
            Change(ref, c.field, c.seen, c.now, c.observed_at)
            for c in changes or ()
            if c.field not in private
        ],
        content=[] if "model_read" in obj.blocked else [c for c in _cleared(obj) if c[0] not in private],
        interest=changes is not None,
        derived=[
            (name, field.v, [_spoken(piece) for piece in field.pieces_out])
            for name, field in sorted(obj.derived.items())
            if field.logic != "unobserved" and name not in private
        ]
        if derived
        else [],
        parents=parents,
        name=name,
    )


def _spoken_ref(obj: ObjectRead) -> str:
    """An object as a line names it: `type namespace:id`."""
    return f"{obj.ref.type} {obj.ref.namespace}:{obj.ref.id}"


def _key_named(declared: ObjectType, obj: ObjectRead, private: frozenset[str]) -> list[str]:
    """The identifiers a conversation writes (`ObjectType.written_keys`) the object holds and the text may
    say, in that order: one `key.match` declares with any value, any other only with a value specific enough
    to name it (`domain.objects.written`), never an opaque id. A short natural key that only repeats the ref
    the line already says ("court:file-1 [file_id file-1]") names nothing new."""
    shaped = set(declared.key.match) if declared.key else set()
    natural = set(declared.key.natural) if declared.key else set()
    out = []
    for name in declared.written_keys():
        value = _known(obj, name)
        if name in private or value is None or str(value) == "":
            continue
        if name in shaped or (
            distinctive(str(value), natural=name in natural and str(value) != obj.ref.id)
            and not _OPAQUE_ID.fullmatch(str(value))
        ):
            out.append(name)
    return out


def _names(
    registry: ObjectTypeRegistry, objects: Iterable[ObjectRead], unsaid: Callable[[str], frozenset[str]]
) -> dict[str, str]:
    """What each object of the view goes by, its first identifier a conversation writes with its label
    ("Número CNJ 0801234-67.2026.8.26.0100"), by its spoken ref; an object that holds none the text may say
    goes by its ref alone. From the objects in hand: no statement."""
    names: dict[str, str] = {}
    for obj in objects:
        declared = registry.get(obj.ref.type)
        if declared is None:
            continue
        keys = _key_named(declared, obj, unsaid(obj.ref.type))
        if keys:
            names[_spoken_ref(obj)] = f"{_label(declared, keys[0])} {_known(obj, keys[0])}"
    return names


def _anchors(declared: ObjectType) -> frozenset[str]:
    return frozenset(name for timer in declared.timers.values() if (name := anchor(timer.due)) is not None)


def _known(obj: ObjectRead, name: str) -> Any:
    """What the object holds as `name`: a field, a computed value or a time axis; None when not known."""
    field = obj.fields.get(name)
    if field is not None:
        return None if field.masked or field.logic not in ("yes", "no") else field.v
    value = obj.values.get(name)
    if value is not None:
        return value.v if value.logic == "yes" else None
    return obj.axes.get(name)


def _anchor_dates(
    obj: ObjectRead, declared: ObjectType, private: frozenset[str]
) -> list[tuple[str | None, Any]]:
    """The dates the type's timers count from, once each, earliest first: a deadline's due date, a hearing's
    date and time. They are the object's own dates, said whether a timer is still armed or not: a deadline
    met is still due on its day, and a line that says only a reminder's time, or no date at all, lets a
    model read another date (a promise's) as the deadline (juridico-sandbox, 05/10/2026)."""
    dates: dict[str, Any] = {}
    for timer in declared.timers.values():
        name = anchor(timer.due)
        if name is None or name in private or name in dates:
            continue
        if (value := _known(obj, name)) is not None:
            dates[name] = _dated(value)
    ordered = sorted(dates.items(), key=lambda kv: (_sort_key(kv[1]), kv[0]))
    return [(_date_label(declared, name), value) for name, value in ordered]


def _date_label(declared: ObjectType, name: str) -> str | None:
    """How a line names a date a timer counts from: its field's label; else, a name that says the object is
    due (`due_on`, `due_at`, `due`) is the object's due date (None, which the line says in the space's words);
    else the name made readable. Field and value names are English ASCII by the contract."""
    spec = declared.fields.get(name)
    if spec is not None and spec.label:
        return spec.label
    return None if "due" in name.split("_") else name.replace("_", " ")


def _said_fields(
    obj: ObjectRead, declared: ObjectType, private: frozenset[str], dated: Collection[str]
) -> list[tuple[str, Any]]:
    """What the line says of the object's own fields: every one it holds a value for, in the type's
    `line_order` (tracked, then claimable, then as declared). No count caps it: a cap cut the claim amount
    (round 2) and the client's side of a case (round 3, "improcedente, ou seja, não ganhamos" said to a
    defendant), and the block is already bounded by its objects (`SUBJECT_OBJECTS`). Never a private or
    content field (a content field goes inside the envelope, once its scan cleared it), a date the line
    already says, nor an opaque id: a model can say nothing of one, and the objects it points to have
    lines of their own."""
    content = set(declared.content.fields) if declared.content else set()
    ruled = declared.ruled()
    out: list[tuple[str, Any]] = []
    for name in declared.line_order():
        if name in private or name in content or name in dated:
            continue
        value = _known(obj, name)
        if value is None or value == "" or (isinstance(value, str) and _OPAQUE_ID.fullmatch(value)):
            continue
        if not shown(name, value, ruled):
            continue
        if declared.fields[name].type in ("date", "datetime"):
            value = _dated(value)
        out.append((_label(declared, name), value))
    # Then what the company's system sent that the type does not declare, by its name made readable: served
    # with no configuration, unless it may be personal data or free text (`domain.objects.undeclared`). The
    # system's own bookkeeping and the lifecycle state are said elsewhere or not at all.
    known = set(declared.fields) | set(declared.values) | set(declared.derived_fields) | set(declared.axes)
    for name in sorted(obj.fields):
        if name in known or name in BOOKKEEPING or name == LIFECYCLE or name in private or name in dated:
            continue
        value = _known(obj, name)
        if value is None or value == "" or (isinstance(value, str) and _OPAQUE_ID.fullmatch(value)):
            continue
        if shown(name, value, ruled):
            out.append((declared.field_word(name) or humanized(name), declared.value_word(as_value(value))))
    return out


def _measured(obj: ObjectRead, declared: ObjectType | None, private: frozenset[str]) -> bool:
    """Whether the object's line says a date or an amount of its own fields: what a person recognises."""
    if declared is None:
        return False
    return any(
        spec.type in ("date", "datetime", "money") and name not in private and _known(obj, name) is not None
        for name, spec in declared.fields.items()
    )


def _dated(value: Any) -> Any:
    """An ISO date or instant as a date or datetime, so the line writes it as the space writes dates."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
        except ValueError:
            return value
    return value


def _sort_key(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _label(declared: ObjectType, name: str) -> str:
    spec = declared.fields.get(name)
    return spec.label if spec is not None and spec.label else name


def _parents(
    registry: ObjectTypeRegistry,
    obj: ObjectRead,
    unsaid: Callable[[str], frozenset[str]],
    names: Mapping[str, str],
) -> list[tuple[str, str | None]]:
    """The objects `obj` belongs to by its declared type's to-one relations, as a line names them, each with
    the name it goes by when its own line is in the view; a relation whose path starts at a field the text
    may not say names nothing."""
    declared = registry.get(obj.ref.type)
    if declared is None:
        return []
    private = unsaid(obj.ref.type)
    state = {name: field.v for name, field in obj.fields.items()}
    found = [
        f"{r.type} {r.namespace}:{r.external_id}"
        for relation in declared.relations.values()
        if not relation.many and relation.via.split(".", 1)[0] not in private
        for r in named_by(relation, obj.ref.namespace, state)
    ]
    return [(ref, names.get(ref)) for ref in dict.fromkeys(found)]


def _spoken(ref: str) -> str:
    """`type:namespace:id` as a line names an object: `type namespace:id`."""
    kind, _, rest = ref.partition(":")
    return f"{kind} {rest}"


def _cleared(obj: ObjectRead) -> list[tuple[str, str]]:
    """The content the scan cleared and Niadra holds: what a model may read, inside the envelope."""
    return [
        (name, field.v)
        for name, field in sorted(obj.fields.items())
        if field.content is not None
        and field.content.scan == "clean"
        and field.content.mode == "full"
        and isinstance(field.v, str)
    ]


def _holds_value(
    copy: TypedObject | None,
    searches: Sequence[Lookup],
    registry: ObjectTypeRegistry,
    names: Mapping[UUID, str],
    now: datetime,
) -> bool:
    if copy is None:
        return False
    declared = registry.get(copy.key.type)
    if declared is None:
        return False
    held = resolved_state(copy.fields, declared, names, now)
    return any((s.field, s.value) in matched(held, (s.field,)) for s in searches if s.type == copy.key.type)


def _readable(space: SpaceConfig, caller: SourcePrincipal, object_type: str) -> bool:
    try:
        check_purpose(space, caller, object_type)
    except Forbidden:
        return False
    return True


def _declared(space: SpaceConfig, ref: StateRef) -> ObjectType:
    """The type a read takes a ref by. A type the space neither declared nor has inferred yet (the
    inference runs a minute after its first objects) reads as a customer's type with nothing declared, as
    `ObjectsService` reads one: its fields are served by the rules for undeclared fields, held ones held,
    and the read never refuses (L19)."""
    return space.object_types.get(ref.type) or ObjectType.model_construct(type=ref.type, ownership="subject")


def _key_of(ref: StateRef) -> ObjectKey:
    return ObjectKey(ref.type, ref.namespace, ref.id)


def _guarded(copy: Copy, guard: PrivacyGuard | None, now: datetime) -> bool:
    """A copy of an object whose owner the privacy feed marked since it was made (an erasure, a split) is
    read again from the database, and so is every owned copy while the feed is not being followed (L10)."""
    if guard is None or copy.owner is None:
        return False
    if guard.stale(now):
        return True
    mark = guard.handle_mark(copy.owner)
    return mark is not None and mark.at >= copy.made_at


def _typed_copy(key: SharedKey, copy: SharedCopy | None) -> TypedObject:
    """A shared object as the typed reads see it; one the working set does not hold has no observation."""
    object_key = ObjectKey(key.type, key.namespace, key.external_id)
    if copy is None:
        return TypedObject(object_key, {})
    return TypedObject(object_key, copy.fields, derived_status=copy.derived_status, typed=copy.typed)


def _shared_copy(key: ObjectKey, copy: SharedCopy | None, now: datetime) -> Copy:
    """A shared object as a read serves it: it has no owner, and it is as new as its newest observation."""
    if copy is None:
        return Copy(key, None, None, None, {}, None, now)
    as_of = max((o.valid_at for by in copy.fields.values() for o in by.values()), default=None)
    return Copy(key, copy.object_id, None, as_of, copy.fields, copy.derived_status, now, copy.typed)


def _copy_of(obj: BusinessObject, now: datetime) -> Copy:
    return Copy(
        obj.key,
        obj.object_id,
        obj.origin_handle_id,
        obj.as_of,
        obj.fields,
        obj.derived_status,
        now,
        obj.typed,
    )


def _document(copy: Copy) -> bytes:
    body: dict[str, Any] = {
        "key": [copy.key.type, copy.key.namespace, copy.key.external_id],
        "object_id": str(copy.object_id) if copy.object_id else None,
        "owner": str(copy.owner) if copy.owner else None,
        "as_of": copy.as_of.isoformat() if copy.as_of else None,
        "fields": encode(copy.fields),
        "derived_status": copy.derived_status,
        "made_at": copy.made_at.isoformat(),
        "typed": {"m": meta_part(copy.typed), "s": sealed_part(copy.typed)},
    }
    return orjson.dumps(body)


def _from_document(raw: bytes) -> Copy:
    body = orjson.loads(raw)
    return Copy(
        ObjectKey(*body["key"]),
        UUID(body["object_id"]) if body["object_id"] else None,
        UUID(body["owner"]) if body["owner"] else None,
        datetime.fromisoformat(body["as_of"]) if body["as_of"] else None,
        decode(body["fields"]),
        body["derived_status"],
        datetime.fromisoformat(body["made_at"]),
        typed_of(body["typed"]["m"], body["typed"]["s"]) if "typed" in body else UNTYPED,
    )


def _reader(space: SpaceConfig, caller: SourcePrincipal) -> FieldReader:
    """The calling key's source as field access and licences name it (24.2.11)."""
    name = next((s.name for s in space.sources if s.source_id == caller.source_id), "")
    return reader_of(name, caller.purposes)


def _summary(declared: ObjectType, reader: FieldReader) -> dict[str, Any]:
    """The registry as the SDK keeps it: what reading, claiming and deciding need, locally, and what the
    calling key may not read, which the SDK masks in a tool's output before the model sees it."""
    summary = declared.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        include={
            "type",
            "version",
            "ownership",
            "nature",
            "mirror_of",
            "key",
            "inputs",
            "fields",
            "values",
            "sources",
            "union",
            "states",
            "purposes",
            "readings",
            "agent_state",
        },
    )
    for spec in summary.get("fields", {}).values():
        # The rules stay with the server; the SDK gets their outcome for its own key.
        spec.pop("access", None)
    if hidden := field_access(declared, reader):
        summary["field_access"] = {name: effect.value for name, effect in hidden.items()}
    return summary
