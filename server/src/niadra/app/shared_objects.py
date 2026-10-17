"""Shared objects and their working set (24.3.12; 06, 15.3): push, observation, the consolidator and reads.

A push is decided against the hot copy of the working set, in the cache store, with no database statement:
each field of a source moves when the push's version is greater than the version that last wrote it
(object-state 10.1). The objects it moved wait in the space's dirty set; the consolidator, a per-space loop,
writes them once a second in one statement by the same rule, so the stored object reaches the state the hot
copy served, whatever the order and grouping of the writes (`domain.state.push`). A tool's observation of a
shared object from a turn takes the same path, its version the time the source observed it.

An object enters the working set when it is presented, engaged or watched (as its type allows), and leaves
after its type's `leave_after` without a reference. Reads take the hot copy; an object the hot copy lost is
read from the database in one statement and seeded back.

What a typed shared type declares besides its fields runs in its own pass: the consolidator
marks the objects of such types it wrote, and a per-space loop plans them every few seconds with the same pure
plan a customer's object gets, keeps the values' versions and the timers, binds a derived shared object to its
inputs, brings a composite's pieces into the working set, and expires the objects, customers' and shared,
derived from a field that changed. The typed stamps go under `META_KEY` of the row and to the hot copy, so a
read serves them with no statement. The pass and the timers' sweep both write those stamps: each merges with
the row as it stands when its statement reaches it, and the hot copy keeps the greater stamp sequence, so a
firing is never undone by a pass that planned the object before it.
"""

import base64
import hashlib
import logging
import uuid
from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jmespath
import orjson
from pydantic import ValidationError

from niadra.app.derived import PIECES_MAX, related
from niadra.app.notify import enqueue_event
from niadra.app.objects import INPUT_REFS, SCHEDULE_AHEAD, offset_min, timer_data
from niadra.app.observations import ObservationsConsumer, Watched
from niadra.app.refresh import Ask, RefreshService
from niadra.app.typed_changes import ARMED, shared_plan, value_names
from niadra.app.typed_state import (
    DEFAULT_PERIOD_S,
    WATCH_REVALIDATION,
    Attribution,
    Evaluated,
    TypedObject,
    Wanted,
    evaluated,
    lifecycle_stage,
    revalidation,
)
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectType
from niadra.contracts.state import (
    ObjectPush,
    ObjectPushRequest,
    ObjectPushResponse,
    ObjectSnapshotResponse,
)
from niadra.domain.common.errors import InvalidInput, NotFound, ObjectTypeUnknown, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.objects.state import CLOCK_SOURCE, system_source
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.privacy.holds import Held
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.state.expr import ExprError, duration_ms, epoch_ms, evaluate, parse
from niadra.domain.state.logic import Logic
from niadra.domain.state.observed import Holding, Observation, Report, observation_of
from niadra.domain.state.push import PushOutcome
from niadra.domain.state.shared import SharedKey, shared_key, shared_object_id, slot_name, slot_of
from niadra.domain.state.typed import META_KEY, UNTYPED, Typed, shared_part, shared_typed, stamp_seq
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.ports.infra import Clock, ConfigProvider, KeyVault, ObjectStore
from niadra.ports.repos.typed import SharedRow, SharedStamps, SharedWrite, WatchRow
from niadra.ports.state import DriftNoticed, SharedDrift, SharedHot
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

CONSOLIDATE_MAX = 1000
"""Objects one consolidation writes in its statement; a burst larger than this takes more seconds."""
LOAD_PAGE = 1000
"""Objects of the working set per statement when the hot copy is loaded again."""
MAX_VERSION = 2**53 - 1
"""The greatest version the hot copy compares exactly."""
WATCH_EVERY = timedelta(hours=1)
"""A watch tells its subject at most once in this long, whatever the pushes do."""
WATCH_FOR = timedelta(days=90)
"""How long a watch that names no expiry lasts."""
SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
SNAPSHOT_PART_LINES = 1000
"""Lines per staged part of a snapshot, and per step of its task."""
DRIFT_MAX = 100
"""Drift findings one consolidation opens; findings are names only, throttled to one an hour per field."""
TYPED_PAGE = 500
"""Objects one typed pass plans."""
FIRE_PAGE = 500
"""Timers one statement of the sweep fires."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)


@dataclass(frozen=True, slots=True)
class SharedObserved:
    """What a tool's result showed of a shared object, as a turn carried it."""

    key: SharedKey
    report: Report


@dataclass(frozen=True, slots=True)
class SharedCopy:
    """A shared object as a read serves it."""

    key: SharedKey
    object_id: UUID
    fields: Holding
    typed: Typed = UNTYPED
    derived_status: str | None = None


class SharedObjectsService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        hot: SharedHot,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        observations: ObservationsConsumer,
        store: ObjectStore,
        refresh: RefreshService | None = None,
    ) -> None:
        self._uow = uow
        self._hot = hot
        self._keys = keys
        self._clock = clock
        self._config = config
        self._observations = observations
        self._store = store
        self._refresh = refresh
        # Drift by observation; the cell wires the type drift service (object-type spec 3.2.2).
        self.drift: SharedDrift | None = None

    # Pushes and observations

    async def push(self, caller: SourcePrincipal, request: ObjectPushRequest) -> ObjectPushResponse:
        """`POST /v1/objects/push` (object-state 10): a shared object's items against the hot copy, with no
        database statement; a subject's items, kept for the objects worker in the request's one statement."""
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        shared: list[tuple[ObjectPush, ObjectType]] = []
        subject: list[ObjectPush] = []
        for item in request.objects:
            declared = space.object_types.get(item.ref.type)
            if declared is None:
                # A type nobody declared and the inference has not seen yet is a customer's
                # type until the inference says otherwise: kept for the objects worker as a system event's
                # fields are, its fields served by the rules for undeclared fields, and the inference's next
                # run reads it as a system's object (`observed_config.from_systems`).
                subject.append(item)
                continue
            if declared.ownership == "shared":
                if item.provenance.scope != "global":
                    raise InvalidInput(
                        "a value scoped to a customer or a context never feeds a shared object"
                    )
                shared.append((item, declared))
            elif declared.ownership == "subject":
                subject.append(item)
            else:
                raise InvalidInput("an agent's working state is written through /v1/agent-state")
        now = self._clock.now()
        source = system_source(caller.source_id)
        items = []
        for item, declared in shared:
            key = SharedKey(item.ref.type, item.ref.namespace, item.ref.id, item.ref.variant or "")
            items.append((key, item.version, _shared_report(item, declared, source, now)))
        try:
            outcomes, prepared = await self._write(space_id, items)
        except _CACHE_ERRORS:
            # The hot copy decides a shared push, and nothing was kept: the sender retries it, harmlessly,
            # since a field moves only for a newer version.
            raise Unavailable("the working set's store did not answer", retry_after_s=1) from None
        if subject:
            await self._observations.record(
                space_id, caller.source_id, [_subject_report(i, source, now) for i in subject]
            )
        if self._refresh is not None:
            answers = [
                _answer(item.request_id, space_id, key, version, fields)
                for (item, _), (key, _, _), (_, version, fields) in zip(shared, items, prepared, strict=True)
                if item.request_id
            ]
            answers += [{"request_id": i.request_id} for i in subject if i.request_id]
            await self._refresh.answered(space_id, answers)
        return ObjectPushResponse(
            applied=outcomes.count(PushOutcome.APPLIED),
            stale_version=outcomes.count(PushOutcome.STALE_VERSION),
            out_of_set=outcomes.count(PushOutcome.OUT_OF_SET),
            recorded=len(subject),
        )

    async def observe(self, space_id: UUID, observed: Sequence[SharedObserved]) -> list[PushOutcome]:
        """What the tools of applied turns showed of shared objects: each tool is a source whose version is
        the time it observed, so a late observation never moves a field back."""
        items = [(o.key, epoch_ms(o.report.observed_at), o.report) for o in observed]
        outcomes, _ = await self._write(space_id, items)
        return outcomes

    async def _write(
        self, space_id: UUID, items: Sequence[tuple[SharedKey, int, Report]]
    ) -> tuple[list[PushOutcome], list[tuple[str, int, dict[str, bytes]]]]:
        """The decision of each item against the hot copy, and what it offered: digest, version and the
        payload of each field."""
        if not items:
            return [], []
        prepared = []
        for key, version, report in items:
            object_id = shared_object_id(space_id, key)
            fields = {
                slot_name(name, report.source): await self._payload(space_id, object_id, name, report, value)
                for name, value in report.fields.items()
            }
            prepared.append((await self._digest(space_id, key), min(version, MAX_VERSION), fields))
        outcomes = [PushOutcome(outcome) for outcome in await self._hot.push(space_id, prepared)]
        await self._notice_drift(space_id, items, outcomes)
        return outcomes, prepared

    async def _notice_drift(
        self, space_id: UUID, items: Sequence[tuple[SharedKey, int, Report]], outcomes: Sequence[PushOutcome]
    ) -> None:
        """What an applied report shows that its type does not declare: noticed here, with no statement, and
        opened as a `drift` issue by the consolidator's next transaction (object-type spec 3.2.2)."""
        if self.drift is None:
            return
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        names = {source.source_id: source.name for source in space.sources}
        noticed: list[DriftNoticed] = []
        for (key, _, report), outcome in zip(items, outcomes, strict=True):
            if outcome is PushOutcome.APPLIED:
                noticed += await self.drift.noticed(space, key.type, key.ref, report, names)
        if not noticed:
            return
        try:
            await self._hot.note_drift(space_id, [_drift_entry(n) for n in noticed])
        except _CACHE_ERRORS:
            log.warning("drift noticed on a shared object not kept; the next report of it notices it again")

    async def _payload(
        self, space_id: UUID, object_id: UUID, field: str, report: Report, value: Any
    ) -> bytes:
        """One field of one source, as the hot copy and the database keep it: the value sealed on its own, the
        stamps in the clear."""
        sealed, dek = await self._keys.encrypt(
            space_id, orjson.dumps(value), _aad(object_id, field, report.source)
        )
        stamps = {
            "valid_at": report.valid_at.isoformat(),
            "observed_at": report.observed_at.isoformat(),
            "known_at": report.received_at.isoformat(),
        }
        stamps |= {k: v for k, v in (("version", report.version), ("provenance", report.provenance)) if v}
        return orjson.dumps({"c": [dek, base64.b64encode(sealed).decode()], "m": stamps})

    async def take_snapshot(
        self, caller: SourcePrincipal, type_: str, chunks: AsyncIterable[bytes]
    ) -> ObjectSnapshotResponse:
        """`POST /v1/objects/snapshot` (24.3.12): a shared type's objects as NDJSON, one push item per line,
        staged in object storage while the body streams and applied by `apply_snapshot` as pushes of
        provenance `snapshot`. One statement: the task."""
        space_id = caller.space.space_id
        declared = self._config.space(space_id).object_types.get(type_)
        if declared is None:
            raise ObjectTypeUnknown("the space declares no object type of this name")
        if declared.ownership != "shared":
            raise InvalidInput("a snapshot reconciles a shared type")
        import_id, size, lines, parts = uuid7(), 0, 0, 0
        pending: list[bytes] = []
        rest = b""
        async for chunk in chunks:
            size += len(chunk)
            if size > SNAPSHOT_MAX_BYTES:
                await self._store.delete_prefix(_snapshot_prefix(space_id, import_id))
                raise InvalidInput("the snapshot exceeds 64 MB")
            *complete, rest = (rest + chunk).split(b"\n")
            pending += [line for line in complete if line.strip()]
            while len(pending) >= SNAPSHOT_PART_LINES:
                await self._store.put(
                    _snapshot_part(space_id, import_id, parts), b"\n".join(pending[:SNAPSHOT_PART_LINES])
                )
                pending, parts, lines = pending[SNAPSHOT_PART_LINES:], parts + 1, lines + SNAPSHOT_PART_LINES
        if rest.strip():
            pending.append(rest)
        if pending:
            await self._store.put(_snapshot_part(space_id, import_id, parts), b"\n".join(pending))
            parts, lines = parts + 1, lines + len(pending)
        if not lines:
            raise InvalidInput("the snapshot has no lines")
        payload = {
            "import_id": str(import_id),
            "type": type_,
            "parts": parts,
            "source_id": str(caller.source_id),
        }
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue([Task(TaskName.APPLY_SNAPSHOT, space_id, payload)])
        return ObjectSnapshotResponse(import_id=str(import_id), objects=lines)

    async def apply_snapshot(self, task: Task) -> None:
        """Each staged part as one push of provenance `snapshot`: a line that is not a push of the snapshot's
        type is left out. A retried task applies the parts again, which the version rule makes harmless."""
        space_id, import_id = task.space_id, UUID(task.payload["import_id"])
        type_, source = task.payload["type"], system_source(UUID(task.payload["source_id"]))
        declared = self._config.space(space_id).object_types.get(type_)
        if declared is None:
            await self._store.delete_prefix(_snapshot_prefix(space_id, import_id))
            return
        now = self._clock.now()
        for part in range(int(task.payload["parts"])):
            try:
                data = await self._store.get(_snapshot_part(space_id, import_id, part))
            except NotFound:
                continue  # applied by an earlier attempt
            items = []
            for line in data.split(b"\n"):
                try:
                    item = ObjectPush.model_validate_json(line)
                except ValidationError:
                    continue
                if item.ref.type != type_:
                    continue
                report = _shared_report(item, declared, source, now, provenance="snapshot")
                key = SharedKey(item.ref.type, item.ref.namespace, item.ref.id, item.ref.variant or "")
                items.append((key, item.version, report))
            await self._write(space_id, items)
        await self._store.delete_prefix(_snapshot_prefix(space_id, import_id))

    # The working set

    async def enter_working_set(
        self,
        space_id: UUID,
        refs: Sequence[str],
        reason: str,
        *,
        at: datetime | None = None,
        with_pieces: bool = True,
    ) -> int:
        """`WorkingSet` (24.3.12): the objects of shared types whose working set this reason enters join it,
        or stay with the later reference, in one statement; the hot copy holds them from now on. A composite
        (a look) brings the related objects its derived fields read along as `related`, in one more
        statement, so it keeps them while it is referenced."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        if Feature.STATE not in space.features.enabled:
            return 0
        now = at or self._clock.now()
        wanted: dict[UUID, tuple[UUID, SharedKey, datetime, datetime]] = {}
        for ref in refs:
            key = shared_key(ref)
            declared = space.object_types.get(key.type) if key else None
            if key is None or declared is None or not _enters(declared, reason):
                continue
            object_id = shared_object_id(space_id, key)
            wanted[object_id] = (object_id, key, now, now + _leave_after(declared))
        if not wanted:
            return 0
        async with self._uow(space_id) as uow:
            rows = await uow.typed.enter_working_set(list(wanted.values()))
        await self._seed(space_id, space, rows, now)
        if with_pieces:
            pieces: set[str] = set()
            for row in rows:
                declared = space.object_types.get(row.key.type)
                if declared is not None and declared.derived_fields:
                    payloads, versions = _stored(row)
                    holding = await self._holding(space_id, row.object_id, payloads, versions)
                    pieces |= _pieces(space, declared, row.key, holding, now)
            if pieces:
                await self.enter_working_set(space_id, sorted(pieces), "related", at=now, with_pieces=False)
        return len(rows)

    async def enter_related(self, space_id: UUID, written: Sequence[BusinessObject]) -> int:
        """The shared objects the customers' objects just written relate to (24.2.12): each relation of a
        written object's type to a shared type whose working set `related` enters, read at its `via` in the
        object's state. A bare id is taken in the object's own namespace; a `type:namespace:id` as it is. A
        derived object's shared inputs relate to it too, so their pushes are applied and can expire it."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        if Feature.STATE not in space.features.enabled:
            return 0
        refs: set[str] = set()
        for obj in written:
            declared = space.object_types.get(obj.key.type)
            if declared is None or declared.ownership != "subject":
                continue
            for relation in declared.relations.values():
                target = space.object_types.get(relation.type)
                if target is None or not _enters(target, "related"):
                    continue
                refs |= {
                    _related_ref(relation.type, obj.key.namespace, v) for v in _at(obj.state, relation.via)
                }
            named = obj.state.get(INPUT_REFS) if declared.nature == "derived" else None
            for ref in named.values() if isinstance(named, Mapping) else ():
                key = shared_key(ref) if isinstance(ref, str) else None
                target = space.object_types.get(key.type) if key else None
                if target is not None and _enters(target, "related"):
                    refs.add(ref)
        return await self.enter_working_set(space_id, sorted(refs), "related") if refs else 0

    async def _seed(
        self, space_id: UUID, space: SpaceConfig, rows: Sequence[SharedRow], now: datetime
    ) -> None:
        entries = []
        for row in rows:
            declared = space.object_types.get(row.key.type)
            until = row.hot_until or now
            if declared is None or until <= now:
                continue
            fields = _fields_of(row)
            ttl = max(1, int((until - now).total_seconds()))
            typed = row.meta.get(META_KEY)
            entries.append(
                (
                    await self._digest(space_id, row.key),
                    row.object_id,
                    fields,
                    ttl,
                    orjson.dumps(typed) if typed else None,
                    stamp_seq(typed),
                )
            )
        try:
            await self._hot.enter(space_id, entries)
        except _CACHE_ERRORS:
            log.warning("working set not seeded in the cache store; the consolidator loads it again")

    async def consolidate(self, space_id: UUID) -> int:
        """A per-space loop step, every second (24.3.12): what the pushes and observations moved since the
        last step, in one statement. A store that came back empty gets the working set loaded again first."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        if Feature.STATE not in space.features.enabled or not _shares(space):
            return 0
        if not await self._hot.loaded(space_id):
            await self._reload(space_id, space)
        moved = await self._consolidate(space_id, space)
        await self._revalidate(space_id, space)
        return moved

    async def _consolidate(self, space_id: UUID, space: SpaceConfig) -> int:
        digests = await self._hot.take_dirty(space_id, CONSOLIDATE_MAX)
        noticed = await self._hot.take_drift(space_id, DRIFT_MAX) if self.drift is not None else []
        if not digests and not noticed:
            return 0
        held = await self._hot.load(space_id, digests) if digests else []
        objects = []
        for found in held:
            if found is None:
                continue  # left the cache before it was written: the next push or observation repairs it
            object_id, fields, _ = found
            state: dict[str, dict[str, Any]] = {}
            meta: dict[str, dict[str, Any]] = {}
            for name, (version, payload) in fields.items():
                field, source = slot_of(name)
                body = orjson.loads(payload)
                state.setdefault(field, {})[source] = body["c"]
                meta.setdefault(field, {})[source] = {**body["m"], "version": version}
            objects.append((object_id, state, meta))
        try:
            async with self._uow(space_id) as uow:
                moved = await uow.typed.consolidate(objects) if objects else []
                if noticed and self.drift is not None:
                    await self.drift.open_noticed(uow, space, [_drift_of(entry) for entry in noticed])
        except Exception:
            # Nothing was written: the next step takes them.
            await self._hot.mark_dirty(space_id, digests)
            if noticed:
                await self._hot.note_drift(space_id, noticed)
            raise
        typed = _typed_types(space)
        marked = [object_id for object_id, _, type_ in moved if type_ in typed]
        if marked:
            await self._hot.mark_typed(space_id, marked)
        if self._refresh is not None:
            await self._refresh.refreshed(space_id, [object_id for object_id, _, _ in moved])
        watched = {object_id for object_id, count, _ in moved if count}
        if watched:
            payloads = {found[0]: found[1] for found in held if found is not None and found[0] in watched}
            await self._tell_watchers(space_id, space, payloads)
        return len(moved)

    async def _revalidate(self, space_id: UUID, space: SpaceConfig) -> None:
        """What the resolver worker answered: the requests settle, and the watches that asked for a fresh
        value are decided on the values it pushed, even when they were not newer than the ones held."""
        if self._refresh is None:
            return
        answered: dict[UUID, dict[str, tuple[int, bytes]]] = {}
        for row, body in await self._refresh.settle_answered(space_id):
            if row.reason != WATCH_REVALIDATION or body.get("object_id") != str(row.object_id):
                continue
            answered[row.object_id] = {
                slot: (int(version), payload.encode()) for slot, (version, payload) in body["fields"].items()
            }
        if answered:
            await self._tell_watchers(space_id, space, answered, verdict="revalidated")

    async def unconfirmed(self, space_id: UUID, object_ids: Sequence[UUID]) -> None:
        """`WatchRevalidation`: no fresh value came for these objects' watches; each type's
        `unconfirmed_watch` decides."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        await self._tell_watchers(
            space_id, space, {object_id: {} for object_id in object_ids}, verdict="unconfirmed"
        )

    async def _tell_watchers(
        self,
        space_id: UUID,
        space: SpaceConfig,
        held: Mapping[UUID, Mapping[str, tuple[int, bytes]]],
        *,
        verdict: Literal["consolidated", "revalidated", "unconfirmed"] = "consolidated",
    ) -> None:
        """The watches of these objects whose condition holds (24.3.12): each tells its subject once an hour
        at most, with ids and the condition's name, never a value.

        - `consolidated`, on the hot copy of what a consolidation moved: `revalidated: true` when the values
          came live from their source. Otherwise the resolver worker is asked for them again
          (`watch_revalidation`), and the watch waits; with no worker, or no budget, the type's
          `unconfirmed_watch` decides.
        - `revalidated`, on the stored object overlaid with what the worker pushed: fires when it still
          holds on live values.
        - `unconfirmed`, on the stored object: `unconfirmed_watch` fires it with `revalidated: false`, or
          drops it."""
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            watches = await uow.typed.watches_of(list(held), now)
            due = [w for w in watches if w.last_fired_at is None or now - w.last_fired_at >= WATCH_EVERY]
            if not due:
                return
            rows = {
                row.object_id: row for row in await uow.typed.shared_by_id(list({w.object_id for w in due}))
            }
            names = {source.source_id: source.name for source in space.sources}
            fired: list[WatchRow] = []
            for watch in due:
                row = rows.get(watch.object_id)
                declared = space.object_types.get(row.key.type) if row else None
                if row is None or declared is None:
                    continue
                fields = dict(held[watch.object_id])
                if verdict != "consolidated":
                    fields = {**_fields_of(row), **fields}
                payloads = {slot_of(n): orjson.loads(p) for n, (_, p) in fields.items()}
                versions = {slot_of(n): v for n, (v, _) in fields.items()}
                holding = await self._holding(space_id, watch.object_id, payloads, versions)
                key = ObjectKey(row.key.type, row.key.namespace, row.key.external_id)
                seen = evaluated(TypedObject(key, holding), declared, names, now)
                try:
                    holds = evaluate(parse(watch.condition), seen.env).logic is Logic.YES
                except ExprError:
                    holds = False
                if not holds:
                    continue
                live = verdict != "unconfirmed" and all(
                    o.provenance in (None, "live") for by in holding.values() for o in by.values()
                )
                if not live:
                    if verdict == "consolidated" and await self._ask_again(space_id, row, declared):
                        continue
                    if declared.refetch is not None and declared.refetch.unconfirmed_watch == "drop":
                        continue
                profile = await uow.identity.profile_of(watch.handle_id)
                subject = WebhookSubject(profile.pseudonym, profile.kind.value) if profile else None
                data = {
                    "object": {"type": key.type, "namespace": key.namespace, "id": key.external_id},
                    "watch_id": str(watch.watch_id),
                    "consent_event_key": watch.consent_event_key,
                    "revalidated": live,
                }
                await enqueue_event(uow, space, "object.watch_fired", subject, data)
                fired.append(watch)
            await uow.typed.watches_fired([w.watch_id for w in fired], now)

    async def _ask_again(self, space_id: UUID, row: SharedRow, declared: ObjectType) -> bool:
        """Whether the resolver worker was asked for the object's values again, and the watch should wait for
        them: a worker took requests lately, and the type's `interest_alert` budget admits it."""
        wanted = revalidation(declared)
        if self._refresh is None or wanted is None or not await self._refresh.has_worker(space_id):
            return False
        [answer] = await self._refresh.want(space_id, [Ask(row.object_id, row.key, declared, wanted)])
        return answer.admitted

    async def watch(self, space_id: UUID, watches: Sequence[Watched]) -> None:
        """A turn's `watch` and `unwatch` of shared objects, for the conversation's customer: a watch brings
        its object into the working set and is counted on it."""
        if not watches:
            return
        now = self._clock.now()
        conversations = sorted({w.conversation_id for w in watches if w.conversation_id})
        async with self._uow(space_id) as uow:
            handles = await uow.ingest.primary_handles(conversations) if conversations else {}
            kept, gone = [], []
            for w in watches:
                handle = handles.get(w.conversation_id or "")
                key = shared_key(w.ref)
                if handle is None or key is None:
                    continue
                object_id = shared_object_id(space_id, key)
                if w.condition is None:
                    gone.append((object_id, handle))
                    continue
                kept.append(
                    WatchRow(
                        uuid5_of(object_id, handle),
                        object_id,
                        handle,
                        w.condition,
                        w.consent_event_key or "",
                        w.expires_at or now + WATCH_FOR,
                    )
                )
            await uow.typed.watch(kept, now)
            await uow.typed.unwatch(gone)

    async def _reload(self, space_id: UUID, space: SpaceConfig) -> None:
        now = self._clock.now()
        after = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.typed.working_set(now, after=after, limit=LOAD_PAGE)
            await self._seed(space_id, space, page, now)
            if len(page) < LOAD_PAGE:
                break
            after = page[-1].object_id
        await self._hot.mark_loaded(space_id)

    async def leave(self, space_id: UUID, held: Held | None = None) -> int:
        """A per-space loop step: objects past their type's `leave_after` without a reference (and with no
        watch) leave the working set, a page per type and step; an object under a legal hold stays."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        now, gone = self._clock.now(), 0
        for declared in space.object_types.types:
            if declared.ownership != "shared" or declared.working_set is None:
                continue
            async with self._uow(space_id) as uow:
                left = await uow.typed.leave_working_set(
                    declared.type,
                    now - _leave_after(declared),
                    limit=LOAD_PAGE,
                    keep=frozenset(held.objects) if held else frozenset(),
                )
            gone += len(left)
        return gone

    # The typed pass

    async def plan_typed(self, space_id: UUID) -> list[BusinessObject]:
        """A per-space loop step, every 5 s: the objects of typed shared types the
        consolidator wrote since the last step, read in one statement and planned in process; their typed
        stamps, values' versions, timers and bindings with the derived shared objects that expire, then the
        customers' objects derived from a field that changed, in one statement each. Returns the customers'
        objects it expired, for their copies."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return []
        if Feature.STATE not in space.features.enabled or not _typed_types(space):
            return []
        object_ids = await self._hot.take_typed(space_id, TYPED_PAGE)
        if not object_ids:
            return []
        try:
            return await self._plan(space_id, space, object_ids)
        except Exception:
            await self._hot.mark_typed(space_id, object_ids)  # nothing was written: the next step takes them
            raise

    async def _plan(
        self, space_id: UUID, space: SpaceConfig, object_ids: Sequence[UUID]
    ) -> list[BusinessObject]:
        now = self._clock.now()
        names = {source.source_id: source.name for source in space.sources}
        offset = offset_min(space, now)
        inputs = input_fields(space)
        typed_types = _typed_types(space)
        async with self._uow(space_id) as uow:
            rows = await uow.typed.shared_by_id(list(object_ids))
        writes: dict[UUID, SharedWrite] = {}
        values, timers, soon = {}, {}, []
        revised: list[tuple[SharedKey, Any]] = []
        changed: dict[UUID, list[str]] = {}
        keys: dict[UUID, SharedKey] = {}
        related: set[str] = set()
        for row in rows:
            declared = space.object_types.get(row.key.type)
            if declared is None or row.key.type not in typed_types:
                continue
            payloads, versions = _stored(row)
            holding = await self._holding(space_id, row.object_id, payloads, versions)
            key = ObjectKey(row.key.type, row.key.namespace, row.key.external_id)
            typed = shared_typed(row.meta.get(META_KEY))
            obj = TypedObject(key, holding, derived_status=derived_status_of(declared, typed), typed=typed)
            seen = evaluated(obj, declared, names, now, utc_offset_min=offset)
            own = [name for name in inputs if name in declared.fields or name in declared.values]
            digests = {}
            for name in {*value_names(declared), *own}:
                found = seen.resolved.get(name)
                if found is not None and found.logic.known:
                    digests[name] = await self._value_digest(space_id, found.value)
            planned = shared_plan(obj, seen, declared, digests, own, now)
            keys[row.object_id] = row.key
            stamps, since, bound = planned.typed, None, None
            if declared.nature == "derived":
                # Computed again by the push that brought it here: current, unless an input changed after.
                since = max((o.valid_at for by in holding.values() for o in by.values()), default=None)
                pairs, refs = _bindings(space, declared, seen)
                digest = _bound_digest(pairs)
                if digest != typed.bound:
                    bound, stamps = pairs, replace(stamps, bound=digest)
                    related |= refs
            if declared.derived_fields:
                pieces = _pieces(space, declared, row.key, holding, now)
                digest = _refs_digest(pieces)
                if digest != typed.pieces:
                    stamps = replace(stamps, pieces=digest)
                    related |= pieces
            if stamps != obj.typed or since is not None or bound is not None:
                writes[row.object_id] = SharedWrite(shared_part(stamps), since, bound)
            if planned.values:
                values[row.object_id] = planned.values
            if planned.timers:
                timers[row.object_id] = planned.timers
            soon += [
                t.due_at
                for t in planned.timers
                if t.state == ARMED and t.due_at and t.due_at <= now + SCHEDULE_AHEAD
            ]
            revised += [(row.key, v) for v in planned.revised]
            if planned.changed:
                changed[row.object_id] = [name for field in planned.changed for name in inputs[field]]
        for object_id, write in list(writes.items()):
            hit = sorted({name for i, name in write.bound or () if name in changed.get(i, ())})
            if hit:
                writes[object_id] = replace(write, expired_by=hit)
        if not (writes or values or timers or changed):
            return []
        async with self._uow(space_id) as uow:
            stored = await uow.typed.write_shared(writes, values, timers, changed, now)
            expired = await uow.objects.expire_dependents(changed, now) if changed else []
            for shared_ref, value in revised:
                data = {
                    "object": _shared_json(shared_ref),
                    "name": value.name,
                    "version": value.version,
                    "supersedes_version": value.supersedes,
                    "rule": value.rule,
                    "computed_at": value.computed_at.isoformat(),
                }
                await enqueue_event(uow, space, "object.value_revised", None, data)
            for dependent in expired:
                data = {
                    "object": {
                        "type": dependent.key.type,
                        "namespace": dependent.key.namespace,
                        "id": dependent.key.external_id,
                    },
                    "inputs": list(dependent.typed.expired_by),
                    "input": _cause(keys, changed, dependent.typed.expired_by),
                }
                await enqueue_event(uow, space, "object.expired_by_input", None, data)
            for written in stored:
                if written.expired:
                    by = list(written.stamps.get("by") or ())
                    data = {
                        "object": _shared_json(written.key),
                        "inputs": by,
                        "input": _cause(keys, changed, by),
                    }
                    await enqueue_event(uow, space, "object.expired_by_input", None, data)
            if soon:
                task = Task(TaskName.FIRE_TIMERS, space_id, {"reason": "due"})
                await uow.pipeline.schedule(task, max(min(soon), now), DelayReason.VALID_UNTIL)
        await self._publish_stamps(space_id, stored)
        if related:
            await self.enter_working_set(space_id, sorted(related), "related", at=now, with_pieces=False)
        return expired

    async def _publish_stamps(self, space_id: UUID, stored: Sequence[SharedStamps]) -> None:
        """The stamps as the database stored them, onto the hot copy: a read serves them with no statement."""
        try:
            await self._hot.set_typed(
                space_id,
                [
                    (
                        await self._digest(space_id, found.key),
                        orjson.dumps(found.stamps),
                        stamp_seq(found.stamps),
                    )
                    for found in stored
                ],
            )
        except _CACHE_ERRORS:
            log.warning(
                "typed stamps not copied to the cache store; reads serve the older ones until the next write"
            )

    async def _value_digest(self, space_id: UUID, value: Any) -> str:
        """The space's keyed digest of a value: equal values give equal digests, and a price never shows."""
        raw = orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=str)
        return (await self._keys.hmac(space_id, "shared_value", raw)).hex()[:32]

    async def fire_all_timers(self, now: datetime | None = None) -> int:
        """The five-minute sweep's part for shared objects: the due timers of every space that has them."""
        fired = 0
        for space in self._config.spaces():
            try:
                fired += await self.fire_timers(space.space_id, now)
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return fired

    async def fire_timers(self, space_id: UUID, now: datetime | None = None) -> int:
        """Fires the space's due timers of shared objects (24.3.8): each fires once per firing, tells the
        company when its type says `notify`, asks the resolver worker again for `refresh(field)`, and moves
        the state for `transition(a->b)` as an observation of the clock through the hot copy, which the
        consolidator writes like any push. One statement fires a page and marks it on the objects' stamps; one
        reads the objects back, as stored, for the actions and the hot copy."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        if Feature.STATE not in space.features.enabled or not any(
            t.ownership == "shared" and t.timers for t in space.object_types.types
        ):
            return 0
        now = now or self._clock.now()
        names = {source.source_id: source.name for source in space.sources}
        asks: list[Ask] = []
        moves: list[tuple[SharedKey, int, Report]] = []
        async with self._uow(space_id) as uow:
            fired = await uow.typed.fire_shared(now, limit=FIRE_PAGE)
            if not fired:
                return 0
            rows = {r.object_id: r for r in await uow.typed.shared_by_id(list({t.object_id for t in fired}))}
            for timer in sorted(fired, key=lambda t: (t.due_at or now, t.timer_id)):
                row = rows.get(timer.object_id)
                declared = space.object_types.get(row.key.type) if row else None
                if row is None or declared is None:
                    continue
                mark = shared_typed(row.meta.get(META_KEY)).timers.get(timer.name)
                if mark is None or mark.timer_id != timer.timer_id:
                    continue  # a firing its object no longer names: re-armed since it was written
                spec = declared.timers.get(timer.name)
                for action in spec.on_fire if spec else ():
                    if action == "notify":
                        await enqueue_event(
                            uow,
                            space,
                            "object.timer_due",
                            None,
                            timer_data(_shared_json(row.key), timer, now),
                        )
                    elif action.startswith("refresh("):
                        wanted = Wanted(timer.name, "high", None, False, DEFAULT_PERIOD_S, False)
                        asks.append(Ask(row.object_id, row.key, declared, wanted))
                    elif move := await self._moved(space_id, row, declared, names, action):
                        # The clock moves the state from the one it is in: never before that one began.
                        target, since = move
                        at = max(timer.due_at or now, since)
                        report = Report(CLOCK_SOURCE, {"state": target}, at, now, now)
                        moves.append((row.key, epoch_ms(at), report))
        stored = [
            SharedStamps(row.object_id, row.key, dict(row.meta.get(META_KEY) or {}), row.derived_status)
            for row in rows.values()
        ]
        await self._publish_stamps(space_id, stored)
        try:
            await self._write(space_id, moves)
        except _CACHE_ERRORS:
            log.warning("a shared timer's clock transition did not reach the cache store")
        if asks and self._refresh is not None:
            await self._refresh.want(space_id, asks)
        return len(fired)

    async def _moved(
        self, space_id: UUID, row: SharedRow, declared: ObjectType, names: Mapping[UUID, str], action: str
    ) -> tuple[str, datetime] | None:
        """For `transition(a->b)` on an object in `a`: `b`, and when the object entered `a`."""
        source, arrow, target = action.removeprefix("transition(").removesuffix(")").partition("->")
        if not action.startswith("transition(") or not arrow:
            return None
        payloads, versions = _stored(row)
        holding = await self._holding(space_id, row.object_id, payloads, versions)
        stage = lifecycle_stage(holding, declared, Attribution(declared, names))
        return (target, stage.at) if stage is not None and stage.state == source else None

    # Reads

    async def copies(self, space_id: UUID, keys: Sequence[SharedKey]) -> tuple[list[SharedCopy | None], bool]:
        """The objects as the hot copy holds them; the ones it lost, from the database in one statement and
        seeded back while they are in the working set. `degraded` when the cache store could not be
        reached."""
        digests = [await self._digest(space_id, key) for key in keys]
        degraded = False
        try:
            held = await self._hot.load(space_id, digests)
        except _CACHE_ERRORS:
            held, degraded = [None] * len(keys), True
        missing = [key for key, found in zip(keys, held, strict=True) if found is None]
        stored: dict[UUID, SharedRow] = {}
        if missing:
            async with self._uow(space_id) as uow:
                rows = await uow.typed.shared_by_id([shared_object_id(space_id, key) for key in missing])
            stored = {row.object_id: row for row in rows}
            if not degraded:
                await self._seed(space_id, self._config.space(space_id), rows, self._clock.now())
        space = self._config.space(space_id)
        copies: list[SharedCopy | None] = []
        for key, found in zip(keys, held, strict=True):
            if found is not None:
                object_id, fields, raw = found
                payloads = {slot_of(n): orjson.loads(p) for n, (_, p) in fields.items()}
                versions = {slot_of(n): v for n, (v, _) in fields.items()}
                typed = shared_typed(orjson.loads(raw)) if raw else UNTYPED
            else:
                row = stored.get(shared_object_id(space_id, key))
                if row is None:
                    copies.append(None)
                    continue
                object_id = row.object_id
                payloads, versions = _stored(row)
                typed = shared_typed(row.meta.get(META_KEY))
            holding = await self._holding(space_id, object_id, payloads, versions)
            declared = space.object_types.get(key.type)
            status = derived_status_of(declared, typed) if declared else None
            copies.append(SharedCopy(key, object_id, holding, typed, status))
        return copies, degraded

    async def _holding(
        self,
        space_id: UUID,
        object_id: UUID,
        payloads: Mapping[tuple[str, str], Mapping[str, Any]],
        versions: Mapping[tuple[str, str], int],
    ) -> Holding:
        holding: dict[str, dict[str, Observation]] = {}
        for (field, source), body in payloads.items():
            dek, text = body["c"]
            raw = await self._keys.decrypt(
                space_id, base64.b64decode(text), _aad(object_id, field, source), int(dek)
            )
            stamps = {"version": str(versions.get((field, source), 0)), **body["m"]}
            holding.setdefault(field, {})[source] = observation_of({"v": orjson.loads(raw)}, stamps)
        return holding

    async def _digest(self, space_id: UUID, key: SharedKey) -> str:
        raw = f"{key.type}\x1f{key.namespace}\x1f{key.external_id}\x1f{key.variant}".encode()
        return (await self._keys.hmac(space_id, "shared_object", raw)).hex()[:32]


def uuid5_of(object_id: UUID, handle_id: UUID) -> UUID:
    """One watch per subject and object: a new `watch` replaces its condition and expiry."""
    return uuid.uuid5(object_id, str(handle_id))


def _snapshot_prefix(space_id: UUID, import_id: UUID) -> str:
    """Under the imports' prefix: staged for two days at most, and deleted with the space."""
    return f"imports/{space_id}/snapshots/{import_id}/"


def _snapshot_part(space_id: UUID, import_id: UUID, part: int) -> str:
    return f"{_snapshot_prefix(space_id, import_id)}part-{part:05d}"


def _drift_entry(noticed: DriftNoticed) -> bytes:
    return orjson.dumps([noticed.type, noticed.field, noticed.source, noticed.ref, noticed.kind])


def _drift_of(entry: bytes) -> DriftNoticed:
    type_, field, source, ref, kind = orjson.loads(entry)
    return DriftNoticed(type_, field, source, ref, "field_held" if kind == "field_held" else "drift")


def _subject_report(item: ObjectPush, source: str, now: datetime) -> tuple[ObjectKey, Report]:
    fields = dict(item.fields)
    if item.inputs:
        fields["input_refs"] = dict(item.inputs)
    at = item.provenance.source_observed_at
    report = Report(source, fields, at, at, now, str(item.version), None, item.provenance.source)
    return ObjectKey(item.ref.type, item.ref.namespace, item.ref.id), report


def _aad(object_id: UUID, field: str, source: str) -> bytes:
    return f"shared_objects:{object_id}:{field}/{source}".encode()


def _stamps(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if k != "version"}


def _fields_of(row: SharedRow) -> dict[str, tuple[int, bytes]]:
    """A stored object's fields in the hot copy's form: per field and source, its version and payload."""
    return {
        slot_name(field, source): (
            int(meta["version"]),
            orjson.dumps({"c": row.state[field][source], "m": _stamps(meta)}),
        )
        for field, by in row.meta.items()
        if field != META_KEY
        for source, meta in by.items()
        if source in row.state.get(field, {})
    }


def _stored(
    row: SharedRow,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], int]]:
    """A stored object's observations as `_holding` opens them: by (field, source), payload and version."""
    payloads = {
        (f, s): {"c": row.state[f][s], "m": _stamps(m)}
        for f, by in row.meta.items()
        if f != META_KEY
        for s, m in by.items()
        if s in row.state.get(f, {})
    }
    versions = {
        (f, s): int(m["version"]) for f, by in row.meta.items() if f != META_KEY for s, m in by.items()
    }
    return payloads, versions


def _shared_json(key: SharedKey) -> dict[str, str]:
    body = {"type": key.type, "namespace": key.namespace, "id": key.external_id}
    return body | ({"variant": key.variant} if key.variant else {})


def input_fields(space: SpaceConfig) -> dict[str, list[str]]:
    """Each field a derived type names as an input of an object (`plan.price_table`), to the names that name
    it; parameters of the turn and versioned references (`calendar@2026`) are not fields."""
    found: dict[str, list[str]] = {}
    for declared in space.object_types.types:
        if declared.nature != "derived":
            continue
        for name in declared.inputs:
            prefix, _, field = name.partition(".")
            if field and "@" not in name and prefix != "turn":
                found.setdefault(field, []).append(name)
    return found


def _typed_types(space: SpaceConfig) -> frozenset[str]:
    """The shared types the typed pass plans: the ones with computed values, timers or a lifecycle, derived
    ones, composites (derived fields), and ones with a field some derived type takes as an input."""
    inputs = input_fields(space)
    return frozenset(
        declared.type
        for declared in space.object_types.types
        if declared.ownership == "shared"
        and declared.working_set is not None
        and (
            declared.values
            or declared.timers
            or declared.lifecycle
            or declared.nature == "derived"
            or declared.derived_fields
            or any(name in declared.fields or name in declared.values for name in inputs)
        )
    )


def derived_status_of(declared: ObjectType, typed: Typed) -> str | None:
    """A derived shared object's status, from its stamps: expired while an input change it was not computed
    after names it."""
    if declared.nature != "derived":
        return None
    return "expired_by_input" if typed.expired_by else "current"


def _bindings(
    space: SpaceConfig, declared: ObjectType, seen: Evaluated
) -> tuple[list[tuple[UUID, str]], set[str]]:
    """The (input object, input name) pairs a derived shared object's `input_refs` names, and the refs: only
    shared objects, by their deterministic ids, since a shared object is never a function of a customer's."""
    found = seen.resolved.get(INPUT_REFS)
    refs = found.value if found is not None and found.logic.known and isinstance(found.value, Mapping) else {}
    pairs: set[tuple[UUID, str]] = set()
    named: set[str] = set()
    for name in declared.inputs:
        prefix, _, field = name.partition(".")
        ref = refs.get(prefix)
        key = shared_key(ref) if isinstance(ref, str) and field and "@" not in name else None
        target = space.object_types.get(key.type) if key else None
        if key is None or target is None or target.ownership != "shared":
            continue
        pairs.add((shared_object_id(space.space_id, key), name))
        named.add(str(ref))
    return sorted(pairs), named


def _bound_digest(pairs: Sequence[tuple[UUID, str]]) -> str | None:
    if not pairs:
        return None
    return hashlib.sha256(orjson.dumps([[str(i), name] for i, name in pairs])).hexdigest()[:32]


def _refs_digest(refs: set[str]) -> str | None:
    return hashlib.sha256(orjson.dumps(sorted(refs))).hexdigest()[:32] if refs else None


def _pieces(
    space: SpaceConfig, declared: ObjectType, key: SharedKey, holding: Holding, now: datetime
) -> set[str]:
    """The shared objects a composite's derived fields read, as references its relations' paths name, of the
    types whose working set `related` enters."""
    names = {source.source_id: source.name for source in space.sources}
    obj = TypedObject(ObjectKey(key.type, key.namespace, key.external_id), holding)
    found: set[str] = set()
    for refs in related(declared, obj, names, now).values():
        for ref in refs[:PIECES_MAX]:
            target = space.object_types.get(ref.type)
            if target is not None and _enters(target, "related"):
                found.add(
                    f"{ref.type}:{ref.namespace}:{ref.external_id}"
                    + (f"#{ref.variant}" if ref.variant else "")
                )
    return found


def _cause(
    keys: Mapping[UUID, SharedKey], changed: Mapping[UUID, Sequence[str]], by: Sequence[str]
) -> dict[str, str] | None:
    """The shared object whose change expired a dependent, by the inputs it names."""
    cause = next((keys[i] for i, named in changed.items() if set(by) & set(named)), None)
    return _shared_json(cause) if cause else None


def _shared_report(
    item: ObjectPush, declared: ObjectType, source: str, now: datetime, *, provenance: str | None = None
) -> Report:
    """A shared item as a report: a derived object's inputs go with it, as a customer's quote's do."""
    fields = dict(item.fields)
    if item.inputs and declared.nature == "derived":
        fields[INPUT_REFS] = dict(item.inputs)
    at = item.provenance.source_observed_at
    return Report(source, fields, at, at, now, str(item.version), None, provenance or item.provenance.source)


def _answer(
    request_id: str, space_id: UUID, key: SharedKey, version: int, fields: Mapping[str, bytes]
) -> dict[str, Any]:
    """What a push brought for the request it names, as the consolidator settles it."""
    return {
        "request_id": request_id,
        "object_id": str(shared_object_id(space_id, key)),
        "fields": {slot: [version, payload.decode()] for slot, payload in fields.items()},
    }


def _at(state: Mapping[str, Any], via: str) -> list[str]:
    """The ids a relation's path finds in an object's state: one, or a list (`ids[*]`)."""
    try:
        found = jmespath.search(via, dict(state))
    except jmespath.exceptions.JMESPathError:
        return []
    items = found if isinstance(found, list) else [found]
    return [str(item) for item in items if isinstance(item, str | int) and not isinstance(item, bool)]


def _related_ref(type_: str, namespace: str, value: str) -> str:
    return value if value.count(":") >= 2 else f"{type_}:{namespace}:{value}"


def _enters(declared: ObjectType, reason: str) -> bool:
    return (
        declared.ownership == "shared"
        and declared.working_set is not None
        and reason in declared.working_set.enter_on
    )


def _leave_after(declared: ObjectType) -> timedelta:
    working_set = declared.working_set
    return timedelta(milliseconds=duration_ms(working_set.leave_after if working_set else "30d"))


def _shares(space: SpaceConfig) -> bool:
    return any(t.ownership == "shared" and t.working_set is not None for t in space.object_types.types)
