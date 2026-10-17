"""In-memory `ObjectRepository` for unit tests."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.change_seq import ChangeSeq
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.state.observed import Holding
from niadra.domain.state.typed import UNTYPED, ScanMark, TimerMark, Typed
from niadra.domain.vocabulary import ActionStatus


class InMemoryObjectRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("objects")
        self._objects: dict[UUID, BusinessObject] = self._s.setdefault("objects", {})
        self._by_key: dict[ObjectKey, UUID] = self._s.setdefault("by_key", {})
        self._events: dict[tuple[UUID, UUID], datetime] = self._s.setdefault("events", {})
        self._participants: set[tuple[UUID, UUID, str]] = self._s.setdefault("participants", set())
        self._actions: dict[UUID, AgentAction] = self._s.setdefault("actions", {})
        # (table, id) to (vector, model), as the rows' vector columns.
        self._vectors: dict[tuple[str, UUID], tuple[tuple[float, ...], str]] = self._s.setdefault(
            "vectors", {}
        )
        # (input object, input name, dependent), as `object_dependencies`, which the typed repository writes.
        self._dependencies: set[tuple[UUID, str, UUID]] = self._s.setdefault("dependencies", set())
        self._seq = ChangeSeq(store, "objects")

    def _touch(self, object_id: UUID) -> int:
        return self._seq.touch("business_objects", object_id)

    async def get_object(self, key: ObjectKey) -> BusinessObject | None:
        object_id = self._by_key.get(key)
        return self._objects.get(object_id) if object_id else None

    async def get_objects(self, keys: Sequence[ObjectKey]) -> list[BusinessObject]:
        return [self._objects[self._by_key[key]] for key in dict.fromkeys(keys) if key in self._by_key]

    async def get_object_by_id(self, object_id: UUID) -> BusinessObject | None:
        return self._objects.get(object_id)

    async def get_objects_by_id(self, object_ids: Sequence[UUID]) -> list[BusinessObject]:
        return [self._objects[o] for o in dict.fromkeys(object_ids) if o in self._objects]

    async def changed_since(self, revision: int, *, limit: int) -> list[BusinessObject]:
        changed = sorted(
            (o for o in self._objects.values() if o.revision > revision), key=lambda o: o.revision
        )
        return changed[:limit]

    async def last_revision(self) -> int:
        return self._seq.last()

    async def types_keyed_by(
        self, namespaces: Sequence[str], types: Sequence[str], ids: Sequence[str]
    ) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for o in self._objects.values():
            k = o.key
            named = k.namespace in namespaces and k.type in types and k.external_id in ids
            if named and k.type not in found.setdefault(k.external_id, []):
                found[k.external_id].append(k.type)
        return found

    async def matching(
        self, searches: Sequence[tuple[str, str, str]], *, limit: int
    ) -> dict[tuple[str, str, str], list[ObjectKey]]:
        newest = sorted(self._objects.values(), key=lambda o: o.revision, reverse=True)
        return {
            (kind, field, value): [
                o.key for o in newest if o.key.type == kind and (field, value) in o.typed.matched
            ][:limit]
            for kind, field, value in dict.fromkeys(searches)
        }

    async def objects_for(
        self, handle_ids: Sequence[UUID], *, limit: int = 50, before: tuple[datetime, UUID] | None = None
    ) -> list[BusinessObject]:
        handles = set(handle_ids)
        involved = {o for o, h, _ in self._participants if h in handles}
        found = [
            o
            for o in self._objects.values()
            if (o.origin_handle_id in handles or o.object_id in involved)
            and (before is None or (o.as_of, o.object_id) < before)
        ]
        return sorted(found, key=lambda o: (o.as_of, o.object_id), reverse=True)[:limit]

    async def subject_records(
        self, handle_ids: Sequence[UUID], *, types: Collection[str], keys: Collection[tuple[str, str]]
    ) -> list[BusinessObject]:
        handles, kinds, named = set(handle_ids), set(types), set(keys)
        found = [
            o
            for o in self._objects.values()
            if o.origin_handle_id in handles
            and (o.key.type.lower() in kinds or (o.key.namespace, o.key.external_id) in named)
        ]
        return sorted(found, key=lambda o: (-o.as_of.timestamp(), str(o.object_id)))

    async def objects_involving(
        self, handle_ids: Sequence[UUID], *, per_handle: int
    ) -> list[tuple[BusinessObject, tuple[tuple[UUID, str], ...]]]:
        picked: dict[UUID, set[tuple[UUID, str]]] = {}
        newest = sorted(self._objects.values(), key=lambda o: (o.as_of, o.object_id), reverse=True)
        for handle in dict.fromkeys(handle_ids):
            roles = {o: role for o, h, role in self._participants if h == handle}
            mine = [
                (o, "owner" if o.origin_handle_id == handle else roles[o.object_id])
                for o in newest
                if o.origin_handle_id == handle or o.object_id in roles
            ]
            for obj, role in mine[:per_handle]:
                picked.setdefault(obj.object_id, set()).add((handle, role))
        return [(o, tuple(sorted(picked[o.object_id]))) for o in newest if o.object_id in picked]

    async def actions_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 100,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[AgentAction]:
        objects = {o.object_id for o in await self.objects_for(handle_ids, limit=10_000)}
        handles = set(handle_ids)
        found = [
            a
            for a in self._actions.values()
            if (a.object_id in objects or a.closes_handle_id in handles)
            and (since is None or a.occurred_at >= since)
            and (before is None or (a.occurred_at, a.action_id) < before)
        ]
        return sorted(found, key=lambda a: (a.occurred_at, a.action_id), reverse=True)[:limit]

    async def operation_counts(self, *, since: datetime, limit: int) -> list[tuple[str, int]]:
        uses: dict[str, int] = {}
        for a in self._actions.values():
            if a.occurred_at >= since:
                uses[a.operation] = uses.get(a.operation, 0) + 1
        return sorted(uses.items(), key=lambda u: (-u[1], u[0]))[:limit]

    async def operation_results(self, operations: Sequence[str], *, since: datetime, limit: int) -> list[str]:
        wanted = set(operations)
        found = [
            a for a in self._actions.values() if a.operation in wanted and a.occurred_at >= since and a.result
        ]
        newest = sorted(found, key=lambda a: (a.occurred_at, a.action_id), reverse=True)[:limit]
        return [a.result for a in newest if a.result]

    async def object_event_ids(self, object_id: UUID, *, limit: int = 100) -> list[UUID]:
        rows = [(at, e) for (o, e), at in self._events.items() if o == object_id]
        return [e for _, e in sorted(rows, reverse=True)[:limit]]

    async def add_object(self, obj: BusinessObject) -> BusinessObject:
        existing = await self.get_object(obj.key)
        if existing:
            return existing
        stored = replace(obj, revision=self._touch(obj.object_id))
        self._objects[obj.object_id] = stored
        self._by_key[obj.key] = obj.object_id
        return stored

    async def set_state(
        self,
        object_id: UUID,
        *,
        state: dict[str, Any],
        fields: Holding,
        as_of: datetime,
        source_id: UUID,
        state_version: str | None,
        record_ref: str | None,
        typed: Typed = UNTYPED,
        derived_status: str | None = None,
        inputs_hash: bytes | None = None,  # noqa: ARG002 (a column only the database keeps)
    ) -> int:
        self._vectors.pop(("business_objects", object_id), None)
        self._objects[object_id] = replace(
            self._objects[object_id],
            state=dict(state),
            fields={name: dict(by) for name, by in fields.items()},
            as_of=as_of,
            source_id=source_id,
            state_version=state_version,
            record_ref=record_ref,
            revision=self._touch(object_id),
            typed=typed,
            derived_status=derived_status,
        )
        return self._objects[object_id].revision

    async def objects_page(self, after: UUID | None, limit: int) -> list[BusinessObject]:
        return [self._objects[o] for o in sorted(self._objects) if after is None or o > after][:limit]

    async def rewrite_state(self, obj: BusinessObject, unmatched: Sequence[tuple[str, str]]) -> int:
        if obj.object_id not in self._objects:
            return 0
        self._vectors.pop(("business_objects", obj.object_id), None)
        current = self._objects[obj.object_id]
        matched = tuple(m for m in current.typed.matched if m not in set(unmatched))
        self._objects[obj.object_id] = replace(
            current,
            state=dict(obj.state),
            fields={name: dict(by) for name, by in obj.fields.items()},
            typed=replace(obj.typed, matched=matched),
            revision=self._touch(obj.object_id),
        )
        return self._objects[obj.object_id].revision

    async def expire_dependents(
        self, changed: Mapping[UUID, Sequence[str]], at: datetime
    ) -> list[BusinessObject]:
        hit: dict[UUID, set[str]] = {}
        for source, name, dependent in self._dependencies:
            if name in changed.get(source, ()):
                hit.setdefault(dependent, set()).add(name)
        expired = []
        for dependent, names in sorted(hit.items()):
            current = self._objects.get(dependent)
            if current is None:
                continue
            typed = replace(current.typed, expired_by=tuple(sorted(names)), expired_at=at)
            self._objects[dependent] = replace(
                current, derived_status="expired_by_input", typed=typed, revision=self._touch(dependent)
            )
            expired.append(self._objects[dependent])
        return expired

    async def mark_scans(self, marks: Mapping[UUID, Mapping[str, ScanMark]]) -> list[BusinessObject]:
        marked = []
        for object_id, by in marks.items():
            current = self._objects.get(object_id)
            if current is None:
                continue
            typed = replace(current.typed, scans={**current.typed.scans, **by})
            self._objects[object_id] = replace(current, typed=typed, revision=self._touch(object_id))
            marked.append(self._objects[object_id])
        return marked

    async def mark_timers(self, marks: Mapping[UUID, Mapping[str, TimerMark]]) -> list[BusinessObject]:
        marked = []
        for object_id, by in marks.items():
            current = self._objects.get(object_id)
            if current is None:
                continue
            typed = replace(current.typed, timers={**current.typed.timers, **by})
            self._objects[object_id] = replace(current, typed=typed, revision=self._touch(object_id))
            marked.append(self._objects[object_id])
        return marked

    async def set_owner(self, object_id: UUID, handle_id: UUID) -> int | None:
        current = self._objects[object_id]
        if current.origin_handle_id is not None:
            return None
        revision = self._touch(object_id)
        self._objects[object_id] = replace(current, origin_handle_id=handle_id, revision=revision)
        return revision

    async def add_object_event(self, object_id: UUID, event_id: UUID, occurred_at: datetime) -> bool:
        if (object_id, event_id) in self._events:
            return False
        self._events[(object_id, event_id)] = occurred_at
        self._seq.touch("object_events", (object_id, event_id))
        return True

    async def add_participants(self, object_id: UUID, participants: Sequence[tuple[UUID, str]]) -> None:
        self._participants.update((object_id, handle_id, role) for handle_id, role in participants)

    async def participants_of(
        self, object_ids: Sequence[UUID], *, per_object: int
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        wanted = set(object_ids)
        first: dict[tuple[UUID, UUID], int] = {}
        for object_id, handle_id, role in self._participants:
            if object_id in wanted:
                key = (object_id, handle_id)
                first[key] = min(first.get(key, 2), _priority(role))
        found: dict[UUID, list[tuple[UUID, str]]] = {}
        ranked = sorted(first, key=lambda k: (str(k[0]), first[k], str(k[1])))
        taken: dict[UUID, list[UUID]] = {}
        for object_id, handle_id in ranked:
            mine = taken.setdefault(object_id, [])
            if len(mine) < per_object:
                mine.append(handle_id)
        for object_id, handles in taken.items():
            roles = sorted((h, r) for o, h, r in self._participants if o == object_id and h in handles)
            found[object_id] = sorted(roles, key=lambda p: (handles.index(p[0]), p[1]))
        return found

    async def participant_count(self, object_id: UUID) -> int:
        return len({h for o, h, _ in self._participants if o == object_id})

    async def add_action(self, action: AgentAction) -> None:
        if action.action_id not in self._actions:
            self._actions[action.action_id] = action
            self._seq.touch("agent_actions", action.action_id)

    async def actions_by_ids(self, action_ids: Sequence[UUID]) -> list[AgentAction]:
        return [self._actions[a] for a in dict.fromkeys(action_ids) if a in self._actions]

    async def actions_page(self, after: UUID | None, limit: int) -> list[AgentAction]:
        return [self._actions[a] for a in sorted(self._actions) if after is None or a > after][:limit]

    async def rewrite_result(self, action: AgentAction) -> None:
        if action.action_id in self._actions:
            self._vectors.pop(("agent_actions", action.action_id), None)
            self._actions[action.action_id] = replace(self._actions[action.action_id], result=action.result)

    async def action_for_event(self, event_id: UUID) -> AgentAction | None:
        return next((a for a in self._actions.values() if a.event_id == event_id), None)

    async def actions_on(
        self,
        object_id: UUID,
        operation: str,
        *,
        since: datetime,
        until: datetime,
        statuses: Sequence[ActionStatus] | None = None,
    ) -> list[AgentAction]:
        found = [
            a
            for a in self._actions.values()
            if a.object_id == object_id
            and a.operation == operation
            and since <= a.occurred_at <= until
            and (statuses is None or a.status in statuses)
        ]
        return sorted(found, key=lambda a: (a.occurred_at, a.action_id))

    async def actions_of_object(self, object_id: UUID, *, limit: int = 100) -> list[AgentAction]:
        found = [a for a in self._actions.values() if a.object_id == object_id]
        return sorted(found, key=lambda a: (a.occurred_at, a.action_id), reverse=True)[:limit]

    async def is_confirming(self, event_id: UUID) -> bool:
        return any(a.confirmed_by_event_id == event_id for a in self._actions.values())

    async def set_action_status(
        self, action_id: UUID, status: ActionStatus, *, confirmed_by_event_id: UUID | None = None
    ) -> None:
        current = self._actions[action_id]
        self._actions[action_id] = replace(
            current,
            status=status,
            confirmed_by_event_id=confirmed_by_event_id or current.confirmed_by_event_id,
        )
        self._seq.touch("agent_actions", action_id)

    async def declared_before(self, cutoff: datetime, *, limit: int = 500) -> list[AgentAction]:
        found = [
            a
            for a in self._actions.values()
            if a.status is ActionStatus.DECLARED
            and a.occurred_at < cutoff
            and (obj := self._objects.get(a.object_id) if a.object_id else None) is not None
            and obj.key.namespace != COORDINATION_NAMESPACE
        ]
        return sorted(found, key=lambda a: (a.occurred_at, a.action_id))[:limit]

    async def divergent_actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime
    ) -> list[tuple[AgentAction, UUID]]:
        handles = set(handle_ids)
        found = []
        for a in self._actions.values():
            obj = self._objects.get(a.object_id) if a.object_id else None
            if (
                a.status is ActionStatus.DIVERGENT
                and a.occurred_at >= since
                and obj
                and obj.origin_handle_id in handles
            ):
                found.append((a, obj.origin_handle_id))
        return sorted(found, key=lambda pair: (pair[0].occurred_at, pair[0].action_id), reverse=True)

    async def vectors_of(self, object_ids: Sequence[UUID], action_ids: Sequence[UUID]) -> list[ItemVector]:
        wanted = [("business_objects", ItemKind.OBJECT, i) for i in object_ids]
        wanted += [("agent_actions", ItemKind.ACTION, i) for i in action_ids]
        return [
            ItemVector(kind, item_id, list(stored[0]), stored[1])
            for table, kind, item_id in wanted
            if (stored := self._vectors.get((table, item_id)))
        ]

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        def wanted(table: str, item_id: UUID) -> bool:
            stored = self._vectors.get((table, item_id))
            return stored is None or (model is not None and stored[1] != model)

        if handle_ids is None:
            objects = sorted(self._objects.values(), key=lambda o: o.object_id)
            actions = sorted(self._actions.values(), key=lambda a: a.action_id)
        else:
            objects = await self.objects_for(handle_ids, limit=10_000)
            actions = await self.actions_for(handle_ids, limit=10_000)
        found = [
            PendingVector(o, fence=self._seq.of("business_objects", o.object_id))
            for o in objects
            if wanted("business_objects", o.object_id)
        ]
        found += [PendingVector(a) for a in actions if wanted("agent_actions", a.action_id)]
        return found[:limit]

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        record = pending.record
        if isinstance(record, BusinessObject):
            table, exists = "business_objects", record.object_id in self._objects
        elif isinstance(record, AgentAction):
            table, exists = "agent_actions", record.action_id in self._actions
        else:
            raise ValueError(f"objects hold no {pending.kind.value} rows")
        if not exists or (
            pending.fence is not None and pending.fence != self._seq.of(table, pending.item_id)
        ):
            return False
        self._vectors[(table, pending.item_id)] = (tuple(vector), model)
        self._seq.touch(table, pending.item_id)
        return True

    async def expire_retained(
        self,
        type_: str,
        before: datetime,
        *,
        limit: int,
        keep_objects: Collection[UUID],
        keep_handles: Collection[UUID],
    ) -> list[UUID]:
        gone = [
            object_id
            for object_id, obj in sorted(self._objects.items())
            if obj.key.type == type_
            and obj.as_of < before
            and object_id not in keep_objects
            and (obj.origin_handle_id is None or obj.origin_handle_id not in keep_handles)
        ][:limit]
        doomed = set(gone)
        for p in [p for p in self._participants if p[0] in doomed]:
            self._participants.discard(p)
        for a in [a for a, action in self._actions.items() if action.object_id in doomed]:
            del self._actions[a]
        for k in [k for k in self._events if k[0] in doomed]:
            del self._events[k]
        for object_id in gone:
            self._by_key.pop(self._objects.pop(object_id).key, None)
        return gone

    async def erase_unowned(self, keys: Sequence[ObjectKey], *, limit: int) -> int:
        wanted = set(keys)
        gone = [
            o
            for o, obj in sorted(self._objects.items())
            if obj.key in wanted and obj.origin_handle_id is None
        ][:limit]
        for object_id in gone:
            self._by_key.pop(self._objects.pop(object_id).key, None)
        return len(gone)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            events = set(scope.event_ids)
            doomed: list[tuple[str, Any]] = [
                ("agent_actions", a)
                for a, action in sorted(self._actions.items())
                if action.event_id in events
            ]
            doomed += [("object_events", k) for k in sorted(self._events) if k[1] in events]
            return [self._drop(table, key) for table, key in doomed[:limit]]
        if scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return []
        handles = set(scope.handle_ids)
        owned = {o for o, obj in self._objects.items() if obj.origin_handle_id in handles}
        doomed = [
            ("object_participants", p)
            for p in sorted(self._participants, key=str)
            if p[1] in handles or p[0] in owned
        ]
        doomed += [
            ("agent_actions", a)
            for a, action in sorted(self._actions.items())
            if action.closes_handle_id in handles or action.object_id in owned
        ]
        doomed += [("object_events", k) for k in sorted(self._events) if k[0] in owned]
        doomed += [("business_objects", o) for o in sorted(owned)]
        return [self._drop(table, key) for table, key in doomed[:limit]]

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in ("business_objects", "object_events", "agent_actions"):
            raise ValueError(f"objects does not export {table}")
        rows: list[dict[str, Any]] = []
        for seq, key in self._seq.between(table, after_seq, upto_seq, limit):
            if table == "business_objects":
                rows.append(export_row(self._objects[key], seq))
            elif table == "agent_actions":
                rows.append(export_row(self._actions[key], seq))
            else:
                rows.append(
                    {
                        "object_id": key[0],
                        "event_id": key[1],
                        "occurred_at": self._events[key],
                        "change_seq": seq,
                    }
                )
        return rows

    def _drop(self, table: str, key: Any) -> ErasedRow:
        if table == "object_participants":
            self._participants.discard(key)
            return ErasedRow(table, {"object_id": str(key[0]), "handle_id": str(key[1]), "role": key[2]})
        if table in ("agent_actions", "business_objects"):
            self._vectors.pop((table, key), None)
        if table == "agent_actions":
            del self._actions[key]
            names: dict[str, str] = {"action_id": str(key)}
        elif table == "object_events":
            del self._events[key]
            names = {"object_id": str(key[0]), "event_id": str(key[1])}
        else:
            obj = self._objects.pop(key)
            self._by_key.pop(obj.key, None)
            names = {"object_id": str(key)}
        return ErasedRow(table, names, change_seq=self._seq.forget(table, key))


def _priority(role: str) -> int:
    """The owner first, a handle with a role of its own next, a bare participant last."""
    return 0 if role == "owner" else 2 if role == "participant" else 1
