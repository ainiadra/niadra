"""Business objects, system events and agent actions (18, 5.8, 8.2b).

`apply_object_event` is deterministic and never calls an LLM: it keeps the object's derived state,
its timeline and participants, reconciles an agent's action with the system event that confirms
it, and hands the rest of the pipeline its work (closing open items, recompiling, traits, triggers).
"""

import hashlib
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import orjson

from niadra.app.archive import ArchiveReader
from niadra.app.decide import DecideService, answered
from niadra.app.derived import NOT_DERIVED, Derivation, matched, named_by
from niadra.app.notify import enqueue_event
from niadra.app.profile_view import read_objects
from niadra.app.refresh import Ask, RefreshService
from niadra.app.typed_changes import ARMED, FIRED, Planned, plan
from niadra.app.typed_state import (
    DEFAULT_PERIOD_S,
    Attribution,
    ReadRequest,
    TypedObject,
    Wanted,
    lifecycle_stage,
    read,
    resolved_state,
)
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectType
from niadra.contracts.common import ObjectRef
from niadra.contracts.context import HistoryItem
from niadra.contracts.objects import ObjectTimeline, ProfileObjects
from niadra.contracts.state import ObjectRead
from niadra.domain.common.errors import Forbidden, NotFound, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.decide import DecisionTask
from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.ingest.scan import ScanState
from niadra.domain.memory.items import amount_of
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.objects.state import (
    CLOCK_SOURCE,
    CONFIRMATION_WINDOW,
    choose_owner,
    closes_items,
    flagged_for_injection,
    object_of_action,
    operation_of,
    reconciled_status,
    record_ref_of,
    report_of,
    within_window,
)
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.policy.fields import reader_of
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.raw_index import RAW_INDEX_PURPOSE, TOKEN_BYTES, body_tokens, redact_value
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    EventRecord,
    HandleRecord,
    ObjectKey,
    ProfileRecord,
)
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.state.expr import duration_ms
from niadra.domain.state.lifecycle import EXPIRED_WITHOUT_OUTCOME
from niadra.domain.state.observed import Report, latest, merge
from niadra.domain.state.shared import SharedKey, shared_key, shared_object_id
from niadra.domain.state.typed import ScanMark, Typed
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import ActionStatus, EventKind, Feature
from niadra.ports.admission import PaidBudgets, Unbudgeted
from niadra.ports.infra import Clock, ConfigProvider, KeyVault, NoTelemetry, Telemetry
from niadra.ports.outcomes import NoOutcomes, OutcomeConsumer
from niadra.ports.repos.typed import FiredTimer, ScanRow, content_ref
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.state import ObservedDrift
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

_FOLLOW_UPS = (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.TRIGGER)
TRIAGE_LOOKBACK = timedelta(hours=24)
FIRE_PAGE = 500
SCAN_PAGE = 50
RETENTION_PAGE = 500
VALUE_VERSIONS_KEPT = timedelta(days=90)
"""How long a computed value's superseded versions are kept (24.5.1); the latest stays with its object."""
"""Content per batch of the full scan."""
"""Timers fired per statement of the sweep (24.3.8)."""
SCHEDULE_AHEAD = timedelta(hours=1)
"""A timer due within this gets a task at its due time, besides the five-minute sweep (24.4.1)."""
_REFRESH = re.compile(r"^refresh\(([a-z][a-z0-9_]*)\)$")
INPUT_REFS = "input_refs"
"""The member of a derived object's report that names the objects its inputs are fields of (24.3.7)."""
"""How far back an action's conversation is read for the injection guard: a session's own span."""


@dataclass(frozen=True, slots=True)
class ObjectAnswer:
    """What a read returns plus what its receipt needs (5.13)."""

    state: ObjectRead
    object_id: UUID
    profile_id: UUID | None


@dataclass(frozen=True, slots=True)
class TimelineRead:
    timeline: ObjectTimeline
    object_id: UUID
    profile_id: UUID | None
    item_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Observed:
    """What a tool's result showed of one object, with the key the turn came under."""

    key: ObjectKey
    report: Report
    source_id: UUID


def parents_of(space: SpaceConfig, obj: BusinessObject) -> list[ObjectKey]:
    """The objects `obj` belongs to: what each to-one relation its declared type follows from its own state
    names (a filing's case by its `case_id`, an order line's order)."""
    declared = space.object_types.get(obj.key.type)
    if declared is None:
        return []
    found = [
        ObjectKey(r.type, r.namespace, r.external_id)
        for relation in declared.relations.values()
        if not relation.many
        for r in named_by(relation, obj.key.namespace, obj.state)
    ]
    return list(dict.fromkeys(found))


def closing_payload(
    space: SpaceConfig,
    event: EventRecord,
    key: ObjectKey | None,
    operation: str,
    handle_ids: Iterable[UUID],
    *,
    action: AgentAction | None = None,
    obj: BusinessObject | None = None,
) -> dict[str, Any]:
    """What `close_items` reads of one closing: the action or the system event, its object and the objects
    that object belongs to, its operation and what it records."""
    payload: dict[str, Any] = {
        "event_id": str(event.event_id),
        "action_id": str(action.action_id) if action else None,
        "object": _key_json(key) if key else None,
        "parents": [_key_json(p) for p in parents_of(space, obj)] if obj else [],
        "operation": operation,
        "closes": (event.action or {}).get("closes"),
        "handle_ids": [str(h) for h in handle_ids],
        "occurred_at": event.occurred_at.isoformat(),
        # What a system event records ("custas.paid"): an item that waits for no operation is judged
        # against it (`items.to_judge`).
        "label": event.canonical_type if action is None else None,
        # An agent's action in a conversation closes only what its reply there gives (`close_items`).
        "conversation_id": event.conversation_id if action else None,
    }
    # How much, when the event says: a promise naming an amount but no object closes by it (17, WP-10).
    amount = amount_of(event.fields) or amount_of((event.action or {}).get("fields"))
    if amount is not None:
        payload["amount"] = str(amount)
    return payload


def _ref(key: ObjectKey) -> ObjectRef:
    return ObjectRef(type=key.type, namespace=key.namespace, id=key.external_id)


def _key_json(key: ObjectKey) -> dict[str, str]:
    return {"type": key.type, "namespace": key.namespace, "id": key.external_id}


class ObjectDerivations(Protocol):
    """The derived fields of an object read here, from its related objects as a state read finds them: the
    typed state module answers it."""

    async def derived_of(
        self,
        caller: SourcePrincipal,
        obj: TypedObject,
        declared: ObjectType,
        request: ReadRequest,
        guard: PrivacyGuard | None,
    ) -> Derivation: ...


class ObjectsService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: ConfigProvider,
        telemetry: Telemetry | None = None,
        decide: DecideService | None = None,
        archive: ArchiveReader | None = None,
        outcomes: OutcomeConsumer | None = None,
        refresh: RefreshService | None = None,
        drift: ObservedDrift | None = None,
        derivations: ObjectDerivations | None = None,
        instructions: ObjectInstructions | None = None,
        keys: KeyVault | None = None,
    ) -> None:
        """`outcomes` is told, inside each transaction that writes objects, which ones it wrote (24.7);
        `drift` sees each report merged into an object of a declared type (object-type spec 3.2.2);
        `derivations` computes the derived fields of an object read (`read_object`); `instructions` holds
        what people said must or must not be done about an object, for the Console;
        `keys` reads what an erasure redacted out of an object, so a report that writes it again does not
        bring the erased person back (`Typed.erased`)."""
        self._uow = uow
        self._keys = keys
        self._clock = clock
        self._instructions = instructions or NoObjectInstructions()
        self._config = config
        self._telemetry = telemetry or NoTelemetry()
        self._decide = decide
        self._archive = archive
        self._outcomes = outcomes or NoOutcomes()
        self._refresh = refresh
        self._drift = drift
        self._derivations = derivations

    @property
    def admission(self) -> PaidBudgets:
        """The paid budgets a refresh is admitted against (24.3.4): the refresh service's, which the cell sets
        to the coordination's through here."""
        return self._refresh.admission if self._refresh else Unbudgeted()

    @admission.setter
    def admission(self, budgets: PaidBudgets) -> None:
        if self._refresh is not None:
            self._refresh.admission = budgets

    async def profile_objects(
        self,
        principal: Principal,
        profile_id: UUID,
        *,
        objects_cursor: str | None = None,
        actions_cursor: str | None = None,
    ) -> ProfileObjects:
        """The Console's view of a customer's business objects and agent actions (5.22)."""
        space_id = principal.space.space_id
        async with self._uow(space_id) as uow:
            return await read_objects(
                uow,
                principal,
                self._config.space(space_id),
                profile_id,
                self._instructions,
                self._clock.now(),
                objects_cursor=objects_cursor,
                actions_cursor=actions_cursor,
            )

    async def apply_object_event(self, task: Task) -> list[BusinessObject]:
        """Returns the objects whose state or owner the event wrote, as committed: the read path's copies of
        them follow (`StateService.publish`)."""
        space = self._config.space(task.space_id)
        event_id = UUID(task.payload["event_id"])
        session_flagged = await self._triage_session(task.space_id, event_id)
        written: dict[UUID, BusinessObject] = {}
        async with self._uow(task.space_id) as uow:
            events = await uow.ingest.get_events([event_id])
            if not events:
                return []  # erased before the task ran
            event = events[0]
            handles = await uow.identity.get_handles(event.handle_ids)
            if not handles:
                # An event that names only objects (24.9, item 2): they keep the owner they already have.
                handles = await self._owners_of(uow, event.object_refs)
            owner = choose_owner(handles)
            if event.kind is EventKind.ACTION:
                changed = await self._apply_action(
                    uow, space, event, owner, handles, written, session_flagged=session_flagged
                )
            else:
                changed = await self._apply_event(uow, space, event, owner, handles, written)
            if changed:
                await self._request_follow_ups(uow, task.space_id, handles, task.profile_id)
            await self._outcomes.consume(uow, task.space_id, list(written.values()))
        if changed:
            self._telemetry.object_event_applied((self._clock.now() - event.received_at).total_seconds())
        return list(written.values())

    async def _triage_session(self, space_id: UUID, event_id: UUID) -> bool:
        """Before an action counts, every turn of its conversation or task so far goes through the
        injection guard, whoever said it: a session the guard flags has its actions
        quarantined from the start, not only once the conversation ends and is extracted. The verdicts
        are cached per event, so the extraction reuses them. Outside the transaction: the guard is a call
        to the decision model."""
        if self._decide is None:
            return False
        async with self._uow(space_id) as uow:
            found = await uow.ingest.get_events([event_id])
            if not found or found[0].kind is not EventKind.ACTION:
                return False
            action = found[0]
            if flagged_for_injection(action.content_meta):
                return True
            if not (action.conversation_id or action.task_id):
                return False
            earlier = await uow.ingest.events_between(
                conversation_id=action.conversation_id,
                task_id=None if action.conversation_id else action.task_id,
                since=action.occurred_at - TRIAGE_LOOKBACK,
                until=action.occurred_at,
            )
        if any(flagged_for_injection(e.content_meta) for e in earlier):
            return True
        said = [e for e in earlier if e.kind is EventKind.MESSAGE and e.text]
        if not said:
            return False
        try:
            verdicts = await self._decide.decide_many(
                space_id,
                DecisionTask.INJECTION,
                [e.text or "" for e in said],
                [f"event:{e.event_id}" for e in said],
            )
        except BudgetExceeded:
            return True  # nothing could be asked today: the action waits in quarantine, as for a flag
        flagged = [e.event_id for e, v in zip(said, verdicts, strict=True) if v.label == "injection"]
        # Only the model's verdicts mark the log; a default for a model that did not answer quarantines this
        # action, and the turn is asked again by the next action or the extraction.
        marked = sorted(
            e.event_id for e, v in zip(said, verdicts, strict=True) if v.label == "injection" and answered(v)
        )
        if marked:
            # Marks on a stored event go as the system role: the application role cannot change events.
            async with self._uow.system(space_id) as uow:
                await uow.ingest.mark_content(marked, {"injection": True})
        return bool(flagged)

    async def _apply_event(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        event: EventRecord,
        owner: HandleRecord | None,
        handles: Sequence[HandleRecord],
        written: dict[UUID, BusinessObject],
    ) -> bool:
        changed = False
        for key in event.object_refs:
            obj = await self._ensure_object(uow, key, event, owner, written)
            if not await self._link(uow, obj, event, owner, handles):
                continue
            changed = True
            if event.kind is not EventKind.SYSTEM_EVENT:
                continue
            obj = await self._merge_state(
                uow,
                space,
                obj,
                report_of(event),
                event.source_id,
                written,
                record_ref=record_ref_of(event),
                event_id=event.event_id,
            )
            operation = operation_of(event)
            if operation:
                confirmed = await self._confirm_declared(uow, obj, operation, event)
                await self._close_items(
                    uow, space, event, key, operation, owner, handles, action=confirmed, obj=obj
                )
        return changed

    async def _merge_state(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        obj: BusinessObject,
        report: Report,
        source_id: UUID,
        written: dict[UUID, BusinessObject],
        *,
        record_ref: str | None = None,
        event_id: UUID | None = None,
    ) -> BusinessObject:
        """Field by field (24.3.1): a report moves only the fields it is newer for, so a late one still
        brings what no newer report said and never moves a field back. For a declared type of a space with
        typed state on, the write also versions its computed values, keeps its latches, arms its timers, binds
        a derived object to its inputs and expires the objects derived from the fields it moved (24.3.6 to
        24.3.8)."""
        if obj.typed.erased:
            report = await self._without_erased(space.space_id, obj.typed.erased, report)
        declared = space.object_types.get(obj.key.type)
        fields, moved = merge(
            obj.fields,
            report,
            revisions=bool(declared and declared.revisions and declared.revisions.allowed),
            tracked=declared.tracked_fields() if declared else (),
        )
        if not moved:
            return obj
        newest = not obj.fields or report.valid_at >= obj.as_of
        # What readers of the plain state see: a declared type's sources resolve it as a read would.
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        on = Feature.STATE in space.features.enabled
        if self._drift is not None and declared is not None:
            await self._drift.observed(uow, space, obj.key, report, names)
        planned: Planned | None = None
        derived = obj.derived_status
        inputs_hash = None
        if on and declared is not None:
            before = TypedObject(obj.key, obj.fields, obj.as_of, obj.derived_status, obj.typed)
            if declared.nature == "derived":
                derived, inputs_hash, retyped = await self._rederived(uow, space, obj, report, now)
                before = replace(before, typed=retyped)
            after = replace(before, fields=fields, derived_status=derived)
            planned = plan(before, after, declared, names, now, utc_offset_min=offset_min(space, now))
            if planned.erase:
                # Sealed: what the transition names leaves the object with it (24.3.8).
                fields = {name: by for name, by in fields.items() if name not in planned.erase}
        state = resolved_state(fields, declared, names, now) if declared else latest(fields)
        typed = planned.typed if planned else obj.typed
        if declared is not None:
            typed = replace(typed, matched=matched(state, space.object_types.matched_fields(declared.type)))
        merged = replace(
            obj,
            state=state,
            fields=fields,
            as_of=report.valid_at if newest else obj.as_of,
            source_id=source_id if newest else obj.source_id,
            state_version=report.version if newest else obj.state_version,
            record_ref=record_ref if newest else obj.record_ref,
            typed=typed,
            derived_status=derived,
        )
        revision = await uow.objects.set_state(
            obj.object_id,
            state=merged.state,
            fields=fields,
            as_of=merged.as_of,
            source_id=merged.source_id,
            state_version=merged.state_version,
            record_ref=merged.record_ref,
            typed=merged.typed,
            derived_status=merged.derived_status,
            inputs_hash=inputs_hash,
        )
        written[obj.object_id] = replace(merged, revision=revision)
        if planned is not None:
            await self._typed_follow_ups(uow, space, written[obj.object_id], planned, now, event_id)
        if on:
            # An input needs no declaration of its own: any object a derived one names.
            await self._expire_dependents(uow, space, written[obj.object_id], moved, written, now)
        return written[obj.object_id]

    async def _without_erased(self, space_id: UUID, erased: frozenset[str], report: Report) -> Report:
        """A report from the system of record that writes again an identifier an erasure redacted out of the
        object: the system keeps its own copy until the company erases it there, so the identifier is
        redacted on the way in, and the rest of the report moves the object as usual (5.14.0)."""
        if self._keys is None:
            return report
        named = set()
        for token in body_tokens(orjson.dumps(report.fields, default=str)):
            digest = await self._keys.hmac(space_id, RAW_INDEX_PURPOSE, token.encode())
            if digest[:TOKEN_BYTES].hex() in erased:
                named.add(token)
        if not named:
            return report
        fields, _ = redact_value(dict(report.fields), frozenset(named))
        return replace(report, fields=fields)

    async def _typed_follow_ups(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        obj: BusinessObject,
        planned: Planned,
        now: datetime,
        event_id: UUID | None,
    ) -> None:
        """The versions of the values the write revised, one statement; the timers it moved, one statement;
        and a task at the due time of a timer due within the hour (24.4.1)."""
        await uow.typed.add_values({obj.object_id: planned.values}, event_id)
        await uow.typed.write_timers({obj.object_id: planned.timers}, now)
        await uow.typed.record_scans(obj.object_id, obj.origin_handle_id, planned.scans, now)
        for field, mark in planned.scans.items():
            if mark.state == ScanState.FLAGGED:
                await self._announce_content(uow, space, obj, field, mark)
        for value in planned.revised:
            data = {
                "object": _key_json(obj.key),
                "name": value.name,
                "version": value.version,
                "supersedes_version": value.supersedes,
                "rule": value.rule,
                "computed_at": value.computed_at.isoformat(),
            }
            await enqueue_event(uow, space, "object.value_revised", None, data, links=_links(obj.key))
        soon = [
            t.due_at
            for t in planned.timers
            if t.state == ARMED and t.due_at and t.due_at <= now + SCHEDULE_AHEAD
        ]
        if soon:
            task = Task(TaskName.FIRE_TIMERS, space.space_id, {"reason": "due"})
            await uow.pipeline.schedule(task, max(min(soon), now), DelayReason.VALID_UNTIL)

    async def _announce_content(
        self, uow: UnitOfWork, space: SpaceConfig, obj: BusinessObject, field: str, mark: ScanMark
    ) -> None:
        data = {
            "object": _key_json(obj.key),
            "field": field,
            "content_ref": content_ref(obj.object_id, field, mark.sha256),
            "reason": mark.reason,
        }
        await enqueue_event(uow, space, "content.flagged", None, data, links=_links(obj.key))

    async def scan_pending(self, space_id: UUID) -> list[BusinessObject]:
        """A per-space loop step (24.3.10): content its type asks to scan in full goes to the decision model
        in a batch, as a conversation's turns do; what it flags stays out of every model's read, what it
        clears is released. A default for a model that did not answer leaves the content pending."""
        if self._decide is None:
            return []
        try:
            space = self._config.space(space_id)
        except NotFound:
            return []
        if Feature.STATE not in space.features.enabled or not any(
            t.content and t.content.scan == "required" for t in space.object_types.types
        ):
            return []
        async with self._uow(space_id) as uow:
            pending = await uow.typed.pending_scans(limit=SCAN_PAGE)
            objects = {
                o.object_id: o for o in await uow.objects.get_objects_by_id([p.object_id for p in pending])
            }
        texts: list[tuple[ScanRow, str]] = []
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        for row in pending:
            obj = objects.get(row.object_id)
            declared = space.object_types.get(obj.key.type) if obj else None
            if obj is None or declared is None:
                continue
            value = resolved_state(obj.fields, declared, names, now).get(row.field)
            if isinstance(value, str) and hashlib.sha256(value.encode()).hexdigest()[:32] == row.sha256:
                texts.append((row, value))
        if not texts:
            return []
        try:
            verdicts = await self._decide.decide_many(
                space_id, DecisionTask.INJECTION, [t for _, t in texts], [r.content_ref for r, _ in texts]
            )
        except BudgetExceeded:
            return []  # nothing could be asked today: the content stays pending, and blocked
        decided = [
            replace(
                row, state=ScanState.FLAGGED if v.label == "injection" else ScanState.CLEAN, reason="model"
            )
            for (row, _), v in zip(texts, verdicts, strict=True)
            if answered(v)
        ]
        if not decided:
            return []
        async with self._uow(space_id) as uow:
            await uow.typed.decide_scans(decided, "model", now)
            marks: dict[UUID, dict[str, ScanMark]] = {}
            for row in decided:
                held = objects[row.object_id].typed.scans.get(row.field)
                if held is not None:
                    marks.setdefault(row.object_id, {})[row.field] = replace(
                        held, state=row.state, reason="model"
                    )
            written = await uow.objects.mark_scans(marks)
            for obj in written:
                for field, mark in marks.get(obj.object_id, {}).items():
                    if mark.state == ScanState.FLAGGED:
                        await self._announce_content(uow, space, obj, field, mark)
        return written

    async def release_content(
        self, principal: PersonPrincipal, ref: str, reason: str
    ) -> BusinessObject | None:
        """`POST /v1/content/{ref}/release` (24.3.10): a person clears content the scan flagged or holds, with
        a reason; the object's read serves it from then on. None when there is no such content."""
        space_id = principal.space.space_id
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            row = await uow.typed.release_scan(ref, f"person:{principal.user_id}", reason, now)
            if row is None:
                return None
            obj = await uow.objects.get_object_by_id(row.object_id)
            held = obj.typed.scans.get(row.field) if obj else None
            if obj is None or held is None:
                return None
            [written] = await uow.objects.mark_scans(
                {obj.object_id: {row.field: replace(held, state=ScanState.CLEAN, reason="released")}}
            ) or [obj]
        return written

    async def _rederived(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        obj: BusinessObject,
        report: Report,
        now: datetime,
    ) -> tuple[str | None, bytes | None, Typed]:
        """A derived object that a report computed again (24.3.7): current once more, bound to the objects its
        `input_refs` names. A report older than the input change that expired it leaves it expired."""
        expired_at = obj.typed.expired_at
        if obj.derived_status == "expired_by_input" and expired_at and report.valid_at < expired_at:
            return obj.derived_status, None, obj.typed
        declared = space.object_types.get(obj.key.type)
        refs = report.fields.get(INPUT_REFS)
        if declared is None or not isinstance(refs, Mapping):
            return "current", None, replace(obj.typed, expired_by=(), expired_at=None)
        bound: dict[str, ObjectKey] = {}
        shared: dict[str, UUID] = {}
        for name, ref in refs.items():
            key = _object_key(ref)
            target = space.object_types.get(key.type) if key else None
            if key is not None and target is not None and target.ownership == "shared":
                # A shared input is named by its deterministic id: no statement, and no customer's object is
                # made up for it (24.3.12).
                found_key = shared_key(ref) if isinstance(ref, str) else None
                shared[str(name)] = shared_object_id(
                    space.space_id, found_key or SharedKey(key.type, key.namespace, key.external_id)
                )
            elif key is not None:
                bound[str(name)] = key
        found = {o.key: o for o in await uow.objects.get_objects(list(dict.fromkeys(bound.values())))}
        pairs: list[tuple[UUID, str]] = []
        for name in declared.inputs:
            prefix, _, field = name.partition(".")
            if prefix in shared and field and "@" not in name:
                pairs.append((shared[prefix], name))
                continue
            key = bound.get(prefix)
            if key is None or not field or "@" in name:
                continue
            source = found.get(key)
            if source is None:
                source = await uow.objects.add_object(
                    BusinessObject(uuid7(), key, {}, report.valid_at, obj.source_id, obj.origin_handle_id)
                )
                found[key] = source
            pairs.append((source.object_id, name))
        await uow.typed.bind_inputs(obj.object_id, pairs, now)
        digest = hashlib.sha256(orjson.dumps(refs, option=orjson.OPT_SORT_KEYS, default=str)).digest()
        return "current", digest, replace(obj.typed, expired_by=(), expired_at=None)

    async def _expire_dependents(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        obj: BusinessObject,
        moved: Iterable[str],
        written: dict[UUID, BusinessObject],
        now: datetime,
    ) -> None:
        """The objects derived from a field the write moved expire at once, with the input's name, in one
        statement (24.3.7); nothing runs when no declared input is a field of that name."""
        changed = set(moved)
        inputs = sorted(
            name
            for declared in space.object_types.types
            if declared.nature == "derived"
            for name in declared.inputs
            if "@" not in name and name.partition(".")[2] in changed and not name.startswith("turn.")
        )
        if not inputs:
            return
        for expired in await uow.objects.expire_dependents({obj.object_id: inputs}, now):
            written[expired.object_id] = expired
            data = {
                "object": _key_json(expired.key),
                "inputs": list(expired.typed.expired_by),
                "input": _key_json(obj.key),
            }
            await enqueue_event(uow, space, "object.expired_by_input", None, data, links=_links(expired.key))

    async def apply_observations(self, space_id: UUID, observed: Sequence[Observed]) -> list[BusinessObject]:
        """The `apply_observations` task (24.3.5): what the tools of a group of applied turns showed of the
        subjects' objects, merged by the same rule as a system event's fields. An object nobody reported yet
        is born without an owner, as a system event without a handle leaves it. Returns what it wrote."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return []
        written: dict[UUID, BusinessObject] = {}
        ordered = sorted(observed, key=lambda o: (o.report.received_at, o.report.valid_at))
        async with self._uow(space_id) as uow:
            found = {
                o.key: o for o in await uow.objects.get_objects(list(dict.fromkeys(o.key for o in ordered)))
            }
            for seen in ordered:
                obj = found.get(seen.key)
                if obj is None:
                    obj = await uow.objects.add_object(
                        BusinessObject(uuid7(), seen.key, {}, seen.report.valid_at, seen.source_id)
                    )
                found[seen.key] = await self._merge_state(
                    uow, space, obj, seen.report, seen.source_id, written
                )
            owners = sorted({o.origin_handle_id for o in written.values() if o.origin_handle_id})
            if owners:
                await self._request_follow_ups(uow, space_id, await uow.identity.get_handles(owners), None)
            await self._outcomes.consume(uow, space_id, list(written.values()))
        return list(written.values())

    async def _confirm_declared(
        self, uow: UnitOfWork, obj: BusinessObject, operation: str, event: EventRecord
    ) -> AgentAction | None:
        """An action and the system event with the same key become one record (5.8, 18.4)."""
        if await uow.objects.is_confirming(event.event_id):
            return None
        declared = await uow.objects.actions_on(
            obj.object_id,
            operation,
            since=event.occurred_at - CONFIRMATION_WINDOW,
            until=event.occurred_at + CONFIRMATION_WINDOW,
            statuses=[ActionStatus.DECLARED],
        )
        if not declared:
            return None
        action = min(declared, key=lambda a: abs(a.occurred_at - event.occurred_at))
        status = reconciled_status(action.result, event)
        await uow.objects.set_action_status(action.action_id, status, confirmed_by_event_id=event.event_id)
        return action

    async def _apply_action(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        event: EventRecord,
        owner: HandleRecord | None,
        handles: Sequence[HandleRecord],
        written: dict[UUID, BusinessObject],
        *,
        session_flagged: bool = False,
    ) -> bool:
        if await uow.objects.action_for_event(event.event_id):
            return False
        key = object_of_action(event)
        obj = await self._ensure_object(uow, key, event, owner, written) if key else None
        if obj:
            await self._link(uow, obj, event, owner, handles)
        operation = operation_of(event) or "unknown"
        if session_flagged:
            status, confirmed_by = ActionStatus.QUARANTINED, None
        else:
            status, confirmed_by = await self._initial_status(uow, obj, operation, event)
        block: dict[str, Any] = dict(event.action or {})
        closes = block.get("closes") or {}
        action = AgentAction(
            action_id=uuid7(),
            event_id=event.event_id,
            source_id=event.source_id,
            operation=operation,
            status=status,
            occurred_at=event.occurred_at,
            object_id=obj.object_id if obj else None,
            result=block.get("result"),
            purpose=block.get("purpose"),
            closes_item_id=_uuid(closes.get("item_id")),
            confirmed_by_event_id=confirmed_by,
            corrects_action_id=_uuid(block.get("corrects_action_id")),
        )
        await uow.objects.add_action(action)
        source = space.source(event.source_id)
        trusted = source.trusted_action_ops if source else frozenset()
        if closes_items(status, operation, trusted):
            await self._close_items(uow, space, event, key, operation, owner, handles, action=action, obj=obj)
        await self._announce_action(uow, space, action, key, owner)
        return True

    async def _initial_status(
        self, uow: UnitOfWork, obj: BusinessObject | None, operation: str, event: EventRecord
    ) -> tuple[ActionStatus, UUID | None]:
        # A session the gate flagged for injection keeps its actions out of memory's "done" list (18.4).
        if flagged_for_injection(event.content_meta):
            return ActionStatus.QUARANTINED, None
        if obj is None:
            return ActionStatus.DECLARED, None
        confirming = await self._confirming_event(uow, obj, operation, event.occurred_at)
        if confirming is None:
            return ActionStatus.DECLARED, None
        return reconciled_status((event.action or {}).get("result"), confirming), confirming.event_id

    async def _confirming_event(
        self, uow: UnitOfWork, obj: BusinessObject, operation: str, at: datetime
    ) -> EventRecord | None:
        """The system event that already arrived for this action, when the ERP was faster than the agent."""
        events = await uow.ingest.get_events(await uow.objects.object_event_ids(obj.object_id, limit=200))
        candidates = [
            e
            for e in events
            if e.kind is EventKind.SYSTEM_EVENT
            and operation_of(e) == operation
            and within_window(e.occurred_at, at)
        ]
        for candidate in sorted(candidates, key=lambda e: abs(e.occurred_at - at)):
            if not await uow.objects.is_confirming(candidate.event_id):
                return candidate
        return None

    async def _owners_of(self, uow: UnitOfWork, keys: Sequence[ObjectKey]) -> list[HandleRecord]:
        owners: list[UUID] = []
        for key in keys:
            known = await uow.objects.get_object(key)
            if known and known.origin_handle_id and known.origin_handle_id not in owners:
                owners.append(known.origin_handle_id)
        return await uow.identity.get_handles(owners) if owners else []

    async def _ensure_object(
        self,
        uow: UnitOfWork,
        key: ObjectKey,
        event: EventRecord,
        owner: HandleRecord | None,
        written: dict[UUID, BusinessObject],
    ) -> BusinessObject:
        existing = await uow.objects.get_object(key)
        if existing and existing.origin_handle_id is None and owner is not None:
            # Created by an event that named no one, it waited without an owner until this one (24.9, item 2).
            revision = await uow.objects.set_owner(existing.object_id, owner.handle_id)
            owned = replace(
                existing, origin_handle_id=owner.handle_id, revision=revision or existing.revision
            )
            written[existing.object_id] = owned
            return owned
        if existing:
            return existing
        # Created empty: only a system event gives it state, so an action never invents one (5.8). Without a
        # handle it is born without an owner, pending until an event links it to one.
        return await uow.objects.add_object(
            BusinessObject(
                object_id=uuid7(),
                key=key,
                state={},
                as_of=event.occurred_at,
                source_id=event.source_id,
                origin_handle_id=owner.handle_id if owner else None,
            )
        )

    async def _link(
        self,
        uow: UnitOfWork,
        obj: BusinessObject,
        event: EventRecord,
        owner: HandleRecord | None,
        handles: Sequence[HandleRecord],
    ) -> bool:
        if not await uow.objects.add_object_event(obj.object_id, event.event_id, event.occurred_at):
            return False
        roles = _subject_roles(event)
        participants = [
            (
                h.handle_id,
                "owner"
                if owner and h.handle_id == owner.handle_id
                else roles.get(h.handle_id, "participant"),
            )
            for h in handles
        ]
        await uow.objects.add_participants(obj.object_id, participants)
        return True

    async def _close_items(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        event: EventRecord,
        key: ObjectKey | None,
        operation: str,
        owner: HandleRecord | None,
        handles: Sequence[HandleRecord],
        *,
        action: AgentAction | None,
        obj: BusinessObject | None = None,
    ) -> None:
        # The event's subject, and the object's owner when that is someone else: an attendant acting for
        # one of the firm's clients (the action's subject) on the firm's case fulfils what the firm and its
        # lawyers (`close_items`, 21.2) promised about the case, items the client's profile never held.
        profiles: list[ProfileRecord] = []
        for handle_id in (owner.handle_id if owner else None, obj.origin_handle_id if obj else None):
            profile = await uow.identity.profile_of(handle_id) if handle_id else None
            if profile is not None and all(p.profile_id != profile.profile_id for p in profiles):
                profiles.append(profile)
        if not profiles:
            return
        payload = closing_payload(
            space, event, key, operation, [h.handle_id for h in handles], action=action, obj=obj
        )
        await uow.pipeline.enqueue(
            [Task(TaskName.CLOSE_ITEMS, space.space_id, payload, profile_id=p.profile_id) for p in profiles]
        )

    async def _announce_action(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        action: AgentAction,
        key: ObjectKey | None,
        owner: HandleRecord | None,
    ) -> None:
        profile = await uow.identity.profile_of(owner.handle_id) if owner else None
        subject = WebhookSubject(profile.pseudonym, profile.kind.value) if profile else None
        data = {
            "action_id": str(action.action_id),
            "event_id": str(action.event_id),
            "operation": action.operation,
            "status": action.status.value,
            "source_id": str(action.source_id),
            "object": _key_json(key) if key else None,
            "as_of": action.occurred_at.isoformat(),
        }
        links = (
            {"open": f"/v1/objects/{key.type}/{key.namespace}/{key.external_id}/timeline"} if key else None
        )
        await enqueue_event(uow, space, "action.recorded", subject, data, links=links)

    async def _request_follow_ups(
        self, uow: UnitOfWork, space_id: UUID, handles: Sequence[HandleRecord], task_profile: UUID | None
    ) -> None:
        profiles = {task_profile} if task_profile else set()
        for handle in handles:
            profile = await uow.identity.profile_of(handle.handle_id)
            if profile:
                profiles.add(profile.profile_id)
        await _request(uow, space_id, profiles, _FOLLOW_UPS)
        # The new object, action or state gets its vector for history search now, not at search time (16.2).
        await uow.pipeline.enqueue([Task(TaskName.EMBED, space_id, profile_id=p) for p in sorted(profiles)])
        # The linked account's packs show what agents did on its objects too (21.4).
        linked: set[UUID] = set()
        for link in await uow.identity.active_links([h.handle_id for h in handles]):
            org = await uow.identity.profile_of(link.org_handle_id)
            if org and org.profile_id not in profiles:
                linked.add(org.profile_id)
        await _request(uow, space_id, linked, (TaskName.COMPILE,))

    async def fire_all_timers(self, now: datetime | None = None) -> dict[UUID, list[BusinessObject]]:
        """The five-minute sweep's part (24.3.8): the due timers of every space with typed state on and a
        type that declares a timer. Returns what it wrote, by space, for the read path's copies."""
        written: dict[UUID, list[BusinessObject]] = {}
        for space in self._config.spaces():
            if not _times(space):
                continue
            try:
                if found := await self.fire_timers(space.space_id, now):
                    written[space.space_id] = found
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return written

    async def fire_timers(self, space_id: UUID, now: datetime | None = None) -> list[BusinessObject]:
        """Fires the space's due timers (24.3.8): each fires once per firing, runs what its type says on fire
        (a notice, a transition the clock makes), and an object past its outcome's deadline expires without
        one. One statement fires a page; one statement per transition; one marks the rest."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return []
        if not _times(space):
            return []
        now = now or self._clock.now()
        written: dict[UUID, BusinessObject] = {}
        async with self._uow(space_id) as uow:
            fired = await uow.typed.fire_due(now, limit=FIRE_PAGE)
            if not fired:
                return []
            found = {
                o.object_id: o for o in await uow.objects.get_objects_by_id([t.object_id for t in fired])
            }
            marks: dict[UUID, dict[str, Any]] = {}
            moved: list[BusinessObject] = []
            refreshes: list[Ask] = []
            for timer in sorted(fired, key=lambda t: (t.due_at or now, t.timer_id)):
                obj = written.get(timer.object_id) or found.get(timer.object_id)
                declared = space.object_types.get(obj.key.type) if obj else None
                if obj is None or declared is None:
                    continue
                mark = obj.typed.timers.get(timer.name)
                if mark is None or mark.timer_id != timer.timer_id:
                    continue  # a firing its object no longer names: re-armed since it was written
                fired_mark = replace(mark, state=FIRED, fired_at=now)
                obj = replace(
                    obj, typed=replace(obj.typed, timers={**obj.typed.timers, timer.name: fired_mark})
                )
                move = self._on_fire(space, obj, timer)
                if move is not None:
                    # The clock moves the state from the one it is in: never before that one began.
                    target, since = move
                    at = max(timer.due_at or now, since) if since else timer.due_at or now
                    report = Report(
                        CLOCK_SOURCE, {"state": target}, valid_at=at, observed_at=now, received_at=now
                    )
                    written[obj.object_id] = await self._merge_state(
                        uow, space, obj, report, obj.source_id, written
                    )
                    moved.append(written[obj.object_id])
                    marks.pop(obj.object_id, None)
                else:
                    written[obj.object_id] = obj
                    marks.setdefault(obj.object_id, {})[timer.name] = fired_mark
                if any(
                    _REFRESH.match(a)
                    for a in (declared.timers[timer.name].on_fire if timer.name in declared.timers else ())
                ):
                    # A timer that asks for a field fetched again: a high-priority reason of its own name.
                    wanted = Wanted(timer.name, "high", None, False, DEFAULT_PERIOD_S, False)
                    key = SharedKey(obj.key.type, obj.key.namespace, obj.key.external_id)
                    refreshes.append(Ask(obj.object_id, key, declared, wanted))
                if timer.name in declared.timers and "notify" in declared.timers[timer.name].on_fire:
                    await enqueue_event(
                        uow,
                        space,
                        "object.timer_due",
                        None,
                        timer_data(_key_json(obj.key), timer, now),
                        links=_links(obj.key),
                    )
            for marked in await uow.objects.mark_timers(marks):
                written[marked.object_id] = marked
            if moved:
                await self._outcomes.consume(uow, space_id, moved)
            if any(r.enabled and r.condition == "object.timer_due" for r in space.trigger_rules):
                # The owners' triggers look at the firings now, not at their next change.
                owners = sorted({o.origin_handle_id for o in written.values() if o.origin_handle_id})
                profiles = [await uow.identity.profile_of(handle) for handle in owners]
                await _request(uow, space_id, {p.profile_id for p in profiles if p}, (TaskName.TRIGGER,))
        if refreshes and self._refresh is not None:
            await self._refresh.want(space_id, refreshes)
        return list(written.values())

    def _on_fire(
        self, space: SpaceConfig, obj: BusinessObject, timer: FiredTimer
    ) -> tuple[str, datetime | None] | None:
        """The state the clock moves the object to, if the firing moves it: the transition its type names
        from the state it is in, or, for its outcome's deadline, expired without outcome unless it reached a
        final state."""
        declared = space.object_types.get(obj.key.type)
        if declared is None:
            return None
        names = {source.source_id: source.name for source in space.sources}
        stage = lifecycle_stage(obj.fields, declared, Attribution(declared, names))
        current, since = (stage.state, stage.at) if stage else (None, None)
        if timer.name == EXPIRED_WITHOUT_OUTCOME and timer.name not in declared.timers:
            outcome = declared.lifecycle.outcome if declared.lifecycle else None
            if current in (outcome.final if outcome else []) or current == EXPIRED_WITHOUT_OUTCOME:
                return None
            return EXPIRED_WITHOUT_OUTCOME, since
        spec = declared.timers.get(timer.name)
        for action in spec.on_fire if spec else ():
            source, arrow, target = action.removeprefix("transition(").removesuffix(")").partition("->")
            if action.startswith("transition(") and arrow and source == current:
                return target, since
        return None

    async def expire_declared(self, now: datetime | None = None) -> int:
        """Declared actions whose window ran out without a system event turn divergent (18.4). A commitment or
        a handoff's outcome, which no system confirms, stays declared."""
        now = now or self._clock.now()
        expired = 0
        for space in self._config.spaces():
            try:
                async with self._uow(space.space_id) as uow:
                    profiles: set[UUID] = set()
                    for action in await uow.objects.declared_before(now - CONFIRMATION_WINDOW):
                        await uow.objects.set_action_status(action.action_id, ActionStatus.DIVERGENT)
                        expired += 1
                        obj = (
                            await uow.objects.get_object_by_id(action.object_id) if action.object_id else None
                        )
                        profile = (
                            await uow.identity.profile_of(obj.origin_handle_id)
                            if obj and obj.origin_handle_id
                            else None
                        )
                        if profile:
                            profiles.add(profile.profile_id)
                    await _request(uow, space.space_id, profiles, (TaskName.COMPILE, TaskName.TRIGGER))
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return expired

    async def expire_retained(self, space_id: UUID, held: Held) -> int:
        """A per-space loop step (24.5.1): customers' objects whose type's `projection` retention passed since
        they last changed leave with what hangs on them, and computed values' superseded versions older than
        90 days; what a legal hold holds, by the object or by its owner, stays. A page a step."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        if Feature.STATE not in space.features.enabled:
            return 0
        now = self._clock.now()
        keep_objects, keep_handles = frozenset(held.objects), frozenset(held.handles)
        gone = 0
        async with self._uow(space_id) as uow:
            for declared in space.object_types.types:
                kept = declared.retention.get("projection")
                if declared.ownership != "subject" or kept is None or kept == "while_in_working_set":
                    continue
                before = now - timedelta(milliseconds=duration_ms(kept))
                gone += len(
                    await uow.objects.expire_retained(
                        declared.type,
                        before,
                        limit=RETENTION_PAGE,
                        keep_objects=keep_objects,
                        keep_handles=keep_handles,
                    )
                )
            if gone:
                gone += await uow.typed.drop_orphans(limit=RETENTION_PAGE)
            gone += await uow.typed.expire_versions(
                now - VALUE_VERSIONS_KEPT,
                limit=RETENTION_PAGE,
                keep_objects=keep_objects,
                keep_handles=keep_handles,
            )
        return gone

    async def read_object(
        self, principal: SourcePrincipal, key: ObjectKey, guard: PrivacyGuard | None = None
    ) -> ObjectAnswer:
        """`GET /v1/objects/...` (7.1): the object as a `display` read serves it (object-state v1), its
        derived fields included, as `POST /v1/state/read` serves them. A type the space does not declare
        reads as one with no rules: every reported field, as observed."""
        space = self._config.space(principal.space.space_id)
        check_purpose(space, principal, key.type)
        async with self._uow(principal.space.space_id) as uow:
            obj = await uow.objects.get_object(key)
            if obj is None:
                raise NotFound("object not found")
            profile = await uow.identity.profile_of(obj.origin_handle_id) if obj.origin_handle_id else None
        declared = space.object_types.get(key.type) or ObjectType.model_construct(
            type=key.type, ownership="subject"
        )
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        request = ReadRequest(
            "display",
            reader=reader_of(names.get(principal.source_id, ""), principal.purposes),
            commercial=space.object_types.commercial_purposes,
        )
        typed = TypedObject(obj.key, obj.fields, obj.as_of, obj.derived_status, obj.typed)
        derivation = (
            await self._derivations.derived_of(principal, typed, declared, request, guard)
            if self._derivations is not None and declared.derived_fields
            else NOT_DERIVED
        )
        state = read(
            typed,
            declared,
            request,
            names=names,
            now=now,
            utc_offset_min=offset_min(space, now),
            derived=derivation.fields,
            derived_withheld=derivation.withheld,
        )
        return ObjectAnswer(state, obj.object_id, profile.profile_id if profile else None)

    async def timeline(
        self, principal: SourcePrincipal, key: ObjectKey, *, cursor: str | None = None, limit: int = 20
    ) -> TimelineRead:
        space = self._config.space(principal.space.space_id)
        check_purpose(space, principal, key.type)
        async with self._uow(principal.space.space_id) as uow:
            obj = await uow.objects.get_object(key)
            if obj is None:
                raise NotFound("object not found")
            profile = await uow.identity.profile_of(obj.origin_handle_id) if obj.origin_handle_id else None
            linked = await uow.objects.object_event_ids(obj.object_id, limit=500)
            events = await uow.ingest.get_events(linked)
            actions = await uow.objects.actions_of_object(obj.object_id, limit=500)
        # Past the hot window an event lives only in the cold archive; the link to the object stays, so the
        # timeline reads what left the database from there, outside the transaction.
        gone = set(linked) - {e.event_id for e in events}
        if gone and self._archive is not None:
            events = [*events, *await self._archive.events(principal.space.space_id, gone)]
        rows = sorted(
            [*_event_items(events), *map(_action_item, actions)], key=lambda i: (i.at, i.id), reverse=True
        )
        before = decode_cursor(cursor)
        if before:
            rows = [r for r in rows if (r.at, r.id) < (before[0], str(before[1]))]
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].at, UUID(page[-1].id)) if len(rows) > limit else None
        timeline = ObjectTimeline(ref=_ref(key), items=page, next_cursor=next_cursor, as_of=obj.as_of)
        return TimelineRead(
            timeline, obj.object_id, profile.profile_id if profile else None, tuple(i.id for i in page)
        )


async def _request(
    uow: UnitOfWork, space_id: UUID, profiles: Iterable[UUID], names: Iterable[TaskName]
) -> None:
    for profile_id in sorted(profiles):
        for name in names:
            await uow.pipeline.request_work(
                Task(name, space_id, {"reason": "object_event"}, profile_id=profile_id)
            )


def _times(space: SpaceConfig) -> bool:
    """Whether the space runs timers: typed state on and a type that declares a timer or an outcome's
    deadline."""
    if Feature.STATE not in space.features.enabled:
        return False
    return any(
        declared.timers
        or (
            declared.lifecycle
            and declared.lifecycle.outcome
            and declared.lifecycle.outcome.expired_without_outcome
        )
        for declared in space.object_types.types
    )


def offset_min(space: SpaceConfig, now: datetime) -> int:
    """The space's UTC offset now, where a date meets an instant (a deadline due at the start of its day)."""
    try:
        offset = now.astimezone(ZoneInfo(space.settings.timezone)).utcoffset()
    except (KeyError, ValueError):
        return 0
    return int(offset.total_seconds() // 60) if offset else 0


def _object_key(ref: Any) -> ObjectKey | None:
    """An object named as `type:namespace:id` or as `{type, namespace, id}`."""
    if isinstance(ref, str):
        parts = ref.split(":", 2)
        return ObjectKey(*parts) if len(parts) == 3 and all(parts) else None
    if isinstance(ref, Mapping) and all(
        isinstance(ref.get(k), str) and ref.get(k) for k in ("type", "namespace", "id")
    ):
        return ObjectKey(ref["type"], ref["namespace"], ref["id"])
    return None


def _links(key: ObjectKey) -> dict[str, str]:
    return {"object": f"/v1/objects/{key.type}/{key.namespace}/{key.external_id}"}


def timer_data(key: Mapping[str, str], timer: FiredTimer, now: datetime) -> dict[str, Any]:
    """The body of `object.timer_due`: the object, the firing and the one it supersedes, never a value."""
    late = (now - timer.due_at).total_seconds() if timer.due_at else 0.0
    return {
        "object": dict(key),
        "timer": timer.name,
        "firing_id": str(timer.timer_id),
        "supersedes_firing_id": str(timer.supersedes_firing_id) if timer.supersedes_firing_id else None,
        "due_at": timer.due_at.isoformat() if timer.due_at else None,
        "fired_at": now.isoformat(),
        "late_s": max(0, int(late)),
    }


def check_purpose(space: SpaceConfig, principal: SourcePrincipal, object_type: str) -> None:
    """Objects follow the purpose of the task views that cover their type (18.3, 18.5)."""
    views = [v for v in space.views if object_type in v.object_types and v.purposes]
    if views and not any(v.purposes & principal.purposes for v in views):
        raise Forbidden("the source's purpose does not cover this object type")


def _event_items(events: Sequence[EventRecord]) -> list[HistoryItem]:
    # Actions are listed from `agent_actions`, which carry their reconciled status.
    return [
        HistoryItem(
            id=str(e.event_id),
            kind=e.kind.value,
            text=e.canonical_type or e.kind.value,
            at=e.occurred_at,
            channel=e.channel,
            source_id=str(e.source_id),
            origin_event_id=str(e.event_id),
        )
        for e in events
        if e.kind is not EventKind.ACTION
    ]


def _action_item(a: AgentAction) -> HistoryItem:
    return HistoryItem(
        id=str(a.action_id),
        kind="action",
        text=f"{a.operation} ({a.status.value})",
        at=a.occurred_at,
        source_id=str(a.source_id),
        origin_event_id=str(a.event_id),
    )


def _subject_roles(event: EventRecord) -> dict[UUID, str]:
    roles: dict[UUID, str] = {}
    for subject in event.subjects:
        role = subject.get("role")
        for handle_id in subject.get("handle_ids", ()):
            if role:
                roles[UUID(str(handle_id))] = str(role)
    return roles


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
