"""What tools showed of the objects, taken from the applied turns (24.3.5; 24.2.2, the observation rules), and
the shared objects the turns brought into the working set (24.3.12).

A tool's result is an observation of state only with its provenance, and only when it is new: a result the
tool served from its own cache, one without provenance (it can be shown, never stored as state) and one scoped
to the conversation (a quote, which lives with the reader) are left out, and so is what the result did not
say. Values of a declared field that are not of the field's kind are dropped. A subject's object is merged as
a system event's fields are; a shared object's goes through the hot copy and the consolidator, and a shared
object presented, engaged or watched in a turn enters the working set first.

The consumer runs inside the transaction that applies a group of turns, and spends one statement there: the
group's observations become one `apply_observations` task, sealed, since the outbox row carries values for the
milliseconds it lives. The objects worker opens it and applies it. A push of a subject's objects is kept the
same way, in the request's one statement.
"""

import base64
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import orjson

from niadra.app.objects import Observed
from niadra.config.models import SpaceConfig
from niadra.config.object_types import FieldSpec, ObjectType
from niadra.contracts.signals import Engaged, Presented, Unwatch, Watch
from niadra.contracts.turns import TurnCall, TurnObservation
from niadra.domain.common.errors import NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.rules import CUSTOMER_FACING_SOURCES
from niadra.domain.objects.state import TOOL_PREFIX
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.fences import erased_before
from niadra.domain.records import ObjectKey
from niadra.domain.state.observed import Report
from niadra.domain.state.shared import SharedKey, shared_key
from niadra.domain.tasks import Task, TaskName
from niadra.ports.erasure import ErasureMarks
from niadra.ports.infra import Clock, ConfigProvider, KeyVault
from niadra.ports.turns import AppliedTurn
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

_KINDS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "text": (str,),
    "enum": (str,),
    "ref": (str,),
    "date": (str,),
    "datetime": (str,),
    "number": (int, float),
    "money": (int, float),
    "percent": (int, float),
    "duration": (int,),
    "bool": (bool,),
    "list": (list,),
}


ENTERS = {"presented": "presented", "engaged": "engaged", "watch": "watched"}
"""The interactions of a turn that bring a shared object into the working set, and as what."""


@dataclass(frozen=True, slots=True)
class Watched:
    """A subject's explicit request to be told about a shared object (`watch`), or its withdrawal
    (`unwatch`, with no condition), for the conversation's customer."""

    ref: str
    conversation_id: str | None
    condition: str | None = None
    consent_event_key: str | None = None
    expires_at: datetime | None = None


WAIT_FOR_TYPE = timedelta(minutes=2)
"""How long an observation of a type this process's configuration does not know waits for it: a type turned
on reaches every process within seconds; past this, the type is not declared."""
RETRY_EVERY = timedelta(seconds=15)
"""Twice the configuration's refresh, and a margin."""
FENCE_TTL_S = 3600
"""How long an erased customer's object keys stay fenced: far past the life of a task that carries them."""
FENCE_PAGE = 500


@dataclass(frozen=True, slots=True)
class Unresolved:
    """What a tool showed of an object of a type the configuration of the process that took the turn did
    not know yet: kept until the objects worker's does, or the wait ends."""

    ref: str
    fields: Mapping[str, Any]
    source: str
    source_id: UUID
    observed_at: datetime
    received_at: datetime
    provenance: str
    scope: str


@dataclass(frozen=True, slots=True)
class Opened:
    """What one `apply_observations` task carries."""

    subject: list[Observed]
    shared: list[tuple[SharedKey, Report]]
    enter: list[tuple[str, str, datetime]]
    """(ref, reason, when): the shared objects to bring into the working set before the observations."""
    watches: list[Watched]
    unresolved: list[Unresolved]


class ObservationsConsumer:
    """The objects' `TurnConsumer` (`ports.turns`), and the opener of the tasks it leaves."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        config: ConfigProvider,
        erasures: tuple[ErasureMarks, Clock] | None = None,
    ) -> None:
        """`erasures` keeps what erasures fenced and when, so an observation on its way never brings an erased
        customer's object back (5.5), with the clock the fence reads its time from."""
        self._uow = uow
        self._keys = keys
        self._config = config
        self._erasures = erasures
        self._fenced: dict[UUID, tuple[ObjectKey, ...]] = {}
        """The keys each erasure in this process fenced, for its purge: the owner's rows are gone by then."""

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        found = observed(space, turns)
        shared = shared_observed(space, turns)
        watches = watched(space, turns)
        pending = unresolved(space, turns)
        enter = entered(space, turns, waiting={u.ref for u in pending})
        if found or shared or enter or watches or pending:
            task = await self._task(space_id, found, shared, enter, watches, pending)
            await uow.pipeline.enqueue([task])

    async def record(
        self, space_id: UUID, source_id: UUID, reports: Sequence[tuple[ObjectKey, Report]]
    ) -> None:
        """A push of subjects' objects: kept for the objects worker in one statement, in a transaction of its
        own, and merged there by the same rule as a system event's fields."""
        found = [Observed(key, report, source_id) for key, report in reports]
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue([await self._task(space_id, found, [], [], [], [])])

    async def _task(
        self,
        space_id: UUID,
        found: Sequence[Observed],
        shared: Sequence[tuple[SharedKey, Report]],
        enter: Sequence[tuple[str, str, datetime]],
        watches: Sequence[Watched],
        pending: Sequence[Unresolved],
    ) -> Task:
        body = {
            "subject": [_encoded(seen) for seen in found],
            "shared": [_shared(key, report) for key, report in shared],
            "enter": [[ref, reason, at.isoformat()] for ref, reason, at in enter],
            "watches": [_watch_json(w) for w in watches],
            "unresolved": [_unresolved_json(u) for u in pending],
        }
        batch = uuid7()
        sealed, dek = await self._keys.encrypt(space_id, orjson.dumps(body), _aad(batch))
        payload = {"batch": str(batch), "sealed": base64.b64encode(sealed).decode(), "dek": dek}
        return Task(TaskName.APPLY_OBSERVATIONS, space_id, payload)

    async def open(self, task: Task) -> Opened:
        batch = UUID(task.payload["batch"])
        raw = await self._keys.decrypt(
            task.space_id, base64.b64decode(task.payload["sealed"]), _aad(batch), int(task.payload["dek"])
        )
        body = orjson.loads(raw)
        return Opened(
            [_decoded(item) for item in body["subject"]],
            [(SharedKey(*item["key"]), _report(item)) for item in body["shared"]],
            [(ref, reason, datetime.fromisoformat(at)) for ref, reason, at in body["enter"]],
            [_watch_of(w) for w in body["watches"]],
            [_unresolved_of(u) for u in body.get("unresolved", ())],
        )

    def resolve(
        self, space_id: UUID, pending: Sequence[Unresolved]
    ) -> tuple[list[Observed], list[tuple[SharedKey, Report]], list[Unresolved]]:
        """The observations this process's configuration now knows the type of, by the same rules as the
        consumer's, and the ones it still does not."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return [], [], list(pending)
        subject: list[Observed] = []
        shared: list[tuple[SharedKey, Report]] = []
        still: list[Unresolved] = []
        for u in pending:
            key = shared_key(u.ref)
            declared = space.object_types.get(key.type) if key else None
            if key is None or declared is None:
                still.append(u)
                continue
            fields = _sane(declared, u.fields)
            if not fields:
                continue
            report = Report(
                u.source, fields, u.observed_at, u.observed_at, u.received_at, provenance=u.provenance
            )
            if declared.ownership == "subject":
                subject.append(
                    Observed(ObjectKey(key.type, key.namespace, key.external_id), report, u.source_id)
                )
            elif declared.ownership == "shared" and u.scope == "global":
                shared.append((key, report))
        return subject, shared, still

    def kept_undeclared(
        self, space_id: UUID, ended: Sequence[Unresolved]
    ) -> tuple[list[Observed], list[Unresolved]]:
        """Past the wait, what a tool showed of a type the space never declared: an internal
        agent's or a system's is kept as a system event's fields are, so the inference names the type from
        it, and every field reaches an agent past an undeclared field's hold; a customer-facing agent's tool
        never makes a type (`observed_config.from_systems`), and its observation is returned, a data issue.
        """
        try:
            space = self._config.space(space_id)
        except NotFound:
            return [], list(ended)
        audiences = {source.source_id: source.audience for source in space.sources}
        kept: list[Observed] = []
        left: list[Unresolved] = []
        for u in ended:
            key = shared_key(u.ref)
            fields = {name: value for name, value in u.fields.items() if value is not None}
            if key is None or not fields or audiences.get(u.source_id) in CUSTOMER_FACING_SOURCES:
                left.append(u)
                continue
            report = Report(
                u.source, fields, u.observed_at, u.observed_at, u.received_at, provenance=u.provenance
            )
            kept.append(Observed(ObjectKey(key.type, key.namespace, key.external_id), report, u.source_id))
        return kept, left

    async def unfenced(self, space_id: UUID, observed: Sequence[Observed]) -> list[Observed]:
        """What the tools showed of objects no erasure reached: an observation still in its task when the
        owner was erased never brings the object back (5.5). One the cell received after the erasure is new
        activity."""
        if self._erasures is None or not observed:
            return list(observed)
        marks = {o.key: await self._mark(space_id, o.key) for o in observed}
        fenced = await self._erasures[0].fenced(space_id, list(dict.fromkeys(marks.values())))
        return [o for o in observed if not erased_before(o.report.received_at, marks[o.key], fenced)]

    async def fence(self, space_id: UUID, scope: ErasureScope) -> None:
        """`ErasureFence`: before the cascade, the keys of the objects the target owns are fenced, so an
        observation on its way skips them. A conversation's erasure keeps the person's objects."""
        if self._erasures is None or scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return
        marks, clock = self._erasures
        owners = set(scope.handle_ids)
        keys: list[ObjectKey] = []
        before = None
        async with self._uow(space_id) as uow:
            while True:
                page = await uow.objects.objects_for(list(owners), limit=FENCE_PAGE, before=before)
                keys += [o.key for o in page if o.origin_handle_id in owners]
                if len(page) < FENCE_PAGE:
                    break
                before = (page[-1].as_of, page[-1].object_id)
        self._fenced[scope.request_id] = tuple(keys)
        await marks.fence(space_id, [await self._mark(space_id, k) for k in keys], clock.now(), FENCE_TTL_S)

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None:
        """`ErasureFence`: after the cascade, an object of those keys that an observation created while it
        ran, with no owner, leaves with its typed rows."""
        keys = self._fenced.pop(scope.request_id, ())
        while keys:
            async with self._uow(space_id) as uow:
                gone = await uow.objects.erase_unowned(keys, limit=FENCE_PAGE)
                if gone:
                    await uow.typed.drop_orphans(limit=FENCE_PAGE)
            if gone < FENCE_PAGE:
                return

    async def _mark(self, space_id: UUID, key: ObjectKey) -> bytes:
        raw = f"{key.type}\x1f{key.namespace}\x1f{key.external_id}".encode()
        return await self._keys.hmac(space_id, "erased_object", raw)

    def known(
        self, space_id: UUID, enter: Sequence[tuple[str, str, datetime]]
    ) -> tuple[list[tuple[str, str, datetime]], list[tuple[str, str, datetime]]]:
        """The working set entries of the types this process's configuration declares, and the rest."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return [], list(enter)
        ready: list[tuple[str, str, datetime]] = []
        later: list[tuple[str, str, datetime]] = []
        for entry in enter:
            key = shared_key(entry[0])
            declared = space.object_types.get(key.type) if key else None
            (ready if declared is not None else later).append(entry)
        return ready, later

    async def defer(
        self,
        space_id: UUID,
        pending: Sequence[Unresolved],
        now: datetime,
        enter: Sequence[tuple[str, str, datetime]] = (),
    ) -> list[Unresolved]:
        """The observations still of an unknown type wait for the next configuration, in a task of their own
        a little later, one statement, with the working set entries of such types, which must come before
        them or the observations find their objects out of the set; the observations whose wait ended are
        returned: their type is not declared."""
        waiting = [u for u in pending if now - u.received_at < WAIT_FOR_TYPE]
        entries = [e for e in enter if now - e[2] < WAIT_FOR_TYPE]
        if waiting or entries:
            task = await self._task(space_id, [], [], entries, [], waiting)
            async with self._uow(space_id) as uow:
                await uow.pipeline.schedule(task, now + RETRY_EVERY, DelayReason.DELAY)
        return [u for u in pending if u not in waiting]


def observed(space: SpaceConfig, turns: Iterable[AppliedTurn]) -> list[Observed]:
    """The observations of the turns' tool calls that are state of a subject's object of a declared type."""
    found: list[Observed] = []
    for applied in turns:
        for call in applied.record.calls:
            if not _new(call):
                continue
            for observation in call.observations:
                seen = _observed(space, applied, call, observation)
                if seen is not None:
                    found.append(seen)
    return found


def shared_observed(space: SpaceConfig, turns: Iterable[AppliedTurn]) -> list[tuple[SharedKey, Report]]:
    """The observations of the turns' tool calls that are state of a shared object of a declared type: only
    of global scope, since a value for one customer or one context never feeds what everyone reads."""
    found: list[tuple[SharedKey, Report]] = []
    for applied in turns:
        for call in applied.record.calls:
            if not _new(call):
                continue
            for observation in call.observations:
                provenance = observation.provenance
                if provenance is None or provenance.source == "cache" or provenance.scope != "global":
                    continue
                key = shared_key(observation.ref)
                declared = space.object_types.get(key.type) if key else None
                if key is None or declared is None or declared.ownership != "shared":
                    continue
                fields = _sane(declared, observation.fields)
                if fields:
                    at = provenance.source_observed_at
                    source = f"{TOOL_PREFIX}{call.name or 'tool'}"
                    report = Report(source, fields, at, at, applied.received_at, provenance=provenance.source)
                    found.append((key, report))
    return found


def unresolved(space: SpaceConfig, turns: Iterable[AppliedTurn]) -> list[Unresolved]:
    """The observations, with their provenance, of objects of a type this configuration does not declare:
    the type may have been turned on moments ago, and the process that applies the turn not know it yet."""
    found: list[Unresolved] = []
    for applied in turns:
        for call in applied.record.calls:
            if not _new(call):
                continue
            for observation in call.observations:
                provenance = observation.provenance
                if provenance is None or provenance.source == "cache" or provenance.scope == "context":
                    continue
                key = shared_key(observation.ref)
                if key is None or space.object_types.get(key.type) is not None:
                    continue
                found.append(
                    Unresolved(
                        observation.ref,
                        dict(observation.fields),
                        f"{TOOL_PREFIX}{call.name or 'tool'}",
                        applied.source_id,
                        provenance.source_observed_at,
                        applied.received_at,
                        provenance.source,
                        provenance.scope,
                    )
                )
    return found


def entered(
    space: SpaceConfig, turns: Iterable[AppliedTurn], *, waiting: Collection[str] = ()
) -> list[tuple[str, str, datetime]]:
    """The shared objects the turns presented, engaged or watched, and, among the objects of a type not
    declared yet, the ones whose observation waits for the type (`waiting`): (ref, reason, when)."""
    found: list[tuple[str, str, datetime]] = []
    for applied in turns:
        at = applied.record.started_at
        for interaction in applied.record.interactions:
            reason = ENTERS.get(interaction.kind)
            if reason is None:
                continue
            if isinstance(interaction, Presented):
                refs = [item.ref for item in interaction.items]
            elif isinstance(interaction, Engaged | Watch):
                refs = [interaction.ref]
            else:
                continue
            for ref in refs:
                key = shared_key(ref)
                declared = space.object_types.get(key.type) if key else None
                # A type this configuration does not know yet may have been turned on moments ago: the
                # objects worker enters the object, before its observation, once its own configuration does.
                if (declared is not None and declared.ownership == "shared") or (
                    declared is None and ref in waiting
                ):
                    found.append((ref, reason, at))
    return found


def watched(space: SpaceConfig, turns: Iterable[AppliedTurn]) -> list[Watched]:
    """The turns' `watch` and `unwatch` of shared objects of a declared type."""
    found: list[Watched] = []
    for applied in turns:
        conversation = applied.record.conversation_id
        for interaction in applied.record.interactions:
            if not isinstance(interaction, Watch | Unwatch):
                continue
            key = shared_key(interaction.ref)
            declared = space.object_types.get(key.type) if key else None
            if declared is None or declared.ownership != "shared":
                continue
            if isinstance(interaction, Watch):
                found.append(
                    Watched(
                        interaction.ref,
                        conversation,
                        interaction.condition,
                        interaction.consent_event_key,
                        interaction.expires_at,
                    )
                )
            else:
                found.append(Watched(interaction.ref, conversation))
    return found


def _new(call: TurnCall) -> bool:
    """A tool call that answered, for real: a synthetic call or a hit of the tool's own cache is no new
    observation."""
    return call.kind == "tool" and call.status == "ok" and not call.cache_hit and not call.synthetic


def _observed(
    space: SpaceConfig, applied: AppliedTurn, call: TurnCall, observation: TurnObservation
) -> Observed | None:
    provenance = observation.provenance
    if provenance is None or provenance.source == "cache" or provenance.scope == "context":
        return None
    kind, namespace, external_id = observation.ref.split(":", 2)
    declared = space.object_types.get(kind)
    if declared is None or declared.ownership != "subject":
        return None
    fields = _sane(declared, observation.fields)
    if not fields:
        return None
    at = provenance.source_observed_at
    report = Report(
        f"{TOOL_PREFIX}{call.name or 'tool'}",
        fields,
        valid_at=at,
        observed_at=at,
        received_at=applied.received_at,
        provenance=provenance.source,
    )
    return Observed(ObjectKey(kind, namespace, external_id), report, applied.source_id)


def _sane(declared: ObjectType, fields: Mapping[str, Any]) -> dict[str, Any]:
    """The fields whose value is of the declared kind (a price that is not a number never becomes state)."""
    return {name: value for name, value in fields.items() if _fits(declared.fields.get(name), value)}


def _fits(spec: FieldSpec | None, value: Any) -> bool:
    if spec is None or value is None:
        return True
    kinds = _KINDS[spec.type]
    return isinstance(value, kinds) and (spec.type == "bool" or not isinstance(value, bool))


def _aad(batch: UUID) -> bytes:
    return f"task:apply_observations:{batch}".encode()


def _encoded(seen: Observed) -> dict[str, Any]:
    return {
        "key": [seen.key.type, seen.key.namespace, seen.key.external_id],
        "source_id": str(seen.source_id),
        **_report_json(seen.report),
    }


def _watch_json(w: Watched) -> dict[str, Any]:
    return {
        "ref": w.ref,
        "conversation": w.conversation_id,
        "condition": w.condition,
        "consent": w.consent_event_key,
        "expires_at": w.expires_at.isoformat() if w.expires_at else None,
    }


def _watch_of(item: Mapping[str, Any]) -> Watched:
    expires = item.get("expires_at")
    return Watched(
        item["ref"],
        item.get("conversation"),
        item.get("condition"),
        item.get("consent"),
        datetime.fromisoformat(expires) if expires else None,
    )


def _unresolved_json(u: Unresolved) -> dict[str, Any]:
    return {
        "ref": u.ref,
        "fields": dict(u.fields),
        "source": u.source,
        "source_id": str(u.source_id),
        "observed_at": u.observed_at.isoformat(),
        "received_at": u.received_at.isoformat(),
        "provenance": u.provenance,
        "scope": u.scope,
    }


def _unresolved_of(item: Mapping[str, Any]) -> Unresolved:
    return Unresolved(
        item["ref"],
        item["fields"],
        item["source"],
        UUID(item["source_id"]),
        datetime.fromisoformat(item["observed_at"]),
        datetime.fromisoformat(item["received_at"]),
        item["provenance"],
        item["scope"],
    )


def _decoded(item: Mapping[str, Any]) -> Observed:
    return Observed(ObjectKey(*item["key"]), _report(item), UUID(item["source_id"]))


def _shared(key: SharedKey, report: Report) -> dict[str, Any]:
    return {"key": [key.type, key.namespace, key.external_id, key.variant], **_report_json(report)}


def _report_json(report: Report) -> dict[str, Any]:
    return {
        "source": report.source,
        "fields": dict(report.fields),
        "valid_at": report.valid_at.isoformat(),
        "received_at": report.received_at.isoformat(),
        "provenance": report.provenance,
        "version": report.version,
    }


def _report(item: Mapping[str, Any]) -> Report:
    at = datetime.fromisoformat(item["valid_at"])
    return Report(
        item["source"],
        item["fields"],
        valid_at=at,
        observed_at=at,
        received_at=datetime.fromisoformat(item["received_at"]),
        version=item.get("version"),
        provenance=item["provenance"],
    )
