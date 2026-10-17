"""PostgreSQL implementation of `ObjectRepository`.

Events and participants hold ids only. An object's state is sealed as one document in `state_ct` (24.9,
item 1): the values each source observed of each field, and what they resolve to. `field_meta` keeps their
stamps and never a value, so a type's freshness rule reads them without opening the state. An action's
free-text result is sealed in `agent_actions.result_ct` (5.14.1).
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import orjson
from sqlalchemy import (
    Row,
    Select,
    Table,
    and_,
    case,
    delete,
    exists,
    func,
    literal_column,
    or_,
    select,
    text,
    tuple_,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.keyset import pair
from niadra.adapters.outbound.postgres.mentions import with_mentions
from niadra.adapters.outbound.postgres.privacy_rows import changed_rows, delete_where
from niadra.adapters.outbound.postgres.schema.memory import (
    AgentActionRow,
    BusinessObjectRow,
    ObjectEvent,
    ObjectParticipant,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.vectors import (
    NO_VECTOR,
    needs_vector,
    opened_vector,
    sealed_vector,
    store,
    vector_columns,
)
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.state.observed import Holding, observation_of, stamps_part, value_part
from niadra.domain.state.typed import (
    META_KEY,
    UNTYPED,
    ScanMark,
    TimerMark,
    Typed,
    meta_part,
    scan_json,
    sealed_part,
    timer_json,
    typed_of,
)
from niadra.domain.vocabulary import ActionStatus

_O = cast(Table, BusinessObjectRow.__table__)
_E = cast(Table, ObjectEvent.__table__)
_P = cast(Table, ObjectParticipant.__table__)
_A = cast(Table, AgentActionRow.__table__)
_NO_KEY = 0  # dek_version of an action without a result: nothing was encrypted
_SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"
_EXPORTED = {"business_objects": _O, "object_events": _E, "agent_actions": _A}


# Each search's newest objects, through the GIN index on `match_keys`, in one statement.
_MATCHING = text(
    f"""
    SELECT s.i, o.type, o.namespace, o.external_id
    FROM unnest(CAST(:types AS text[]), CAST(:keys AS text[])) WITH ORDINALITY AS s(type, key, i)
    CROSS JOIN LATERAL (
        SELECT b.type, b.namespace, b.external_id, b.change_seq FROM business_objects b
        WHERE b.space_id = {_SPACE} AND b.type = s.type AND b.match_keys @> ARRAY[s.key]
        ORDER BY b.change_seq DESC
        LIMIT :limit
    ) AS o
    ORDER BY s.i, o.change_seq DESC
    """
)


# Each handle's latest objects, owned or taken part in, by the owner's and the participants' indexes: a
# customer with thousands of objects costs `per_handle` rows of each, never all of them.
_INVOLVING = text(
    f"""
    WITH picked AS (
        SELECT h.handle_id, x.object_id, x.role
        FROM unnest(CAST(:handles AS uuid[])) AS h(handle_id)
        CROSS JOIN LATERAL (
            -- An object the handle owns is often one it takes part in too: each object once, owner first.
            SELECT both_sides.object_id,
                CASE WHEN bool_or(both_sides.role = 'owner') THEN 'owner' ELSE min(both_sides.role) END
                    AS role
            FROM (
                (SELECT o.object_id, o.as_of, 'owner' AS role FROM business_objects o
                 WHERE o.space_id = {_SPACE} AND o.origin_handle_id = h.handle_id
                 ORDER BY o.as_of DESC, o.object_id DESC LIMIT :per_handle)
                UNION ALL
                (SELECT o.object_id, o.as_of, p.role FROM object_participants p
                 JOIN business_objects o ON o.space_id = p.space_id AND o.object_id = p.object_id
                 WHERE p.space_id = {_SPACE} AND p.handle_id = h.handle_id
                 ORDER BY o.as_of DESC, o.object_id DESC LIMIT :per_handle)
            ) AS both_sides
            GROUP BY both_sides.object_id, both_sides.as_of
            ORDER BY both_sides.as_of DESC, both_sides.object_id DESC
            LIMIT :per_handle
        ) AS x
    )
    SELECT o.*, array_agg(CAST(picked.handle_id AS text) || ':' || picked.role) AS taking_part
    FROM business_objects o
    JOIN picked ON o.space_id = {_SPACE} AND o.object_id = picked.object_id
    GROUP BY o.space_id, o.object_id
    ORDER BY o.as_of DESC, o.object_id DESC
    """
)


class PgObjectRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    @property
    def _seal(self) -> Sealer:
        assert self._sealer is not None, "object repositories run in a space-scoped transaction"
        return self._sealer

    async def get_object(self, key: ObjectKey) -> BusinessObject | None:
        row = (
            await self._db.execute(
                select(_O).where(
                    _O.c.space_id == CURRENT_SPACE,
                    _O.c.type == key.type,
                    _O.c.namespace == key.namespace,
                    _O.c.external_id == key.external_id,
                )
            )
        ).first()
        return await self._object(row) if row else None

    async def get_objects(self, keys: Sequence[ObjectKey]) -> list[BusinessObject]:
        if not keys:
            return []
        wanted = [(k.type, k.namespace, k.external_id) for k in keys]
        rows = await self._db.execute(
            select(_O).where(
                _O.c.space_id == CURRENT_SPACE,
                tuple_(_O.c.type, _O.c.namespace, _O.c.external_id).in_(wanted),
            )
        )
        return [await self._object(r) for r in rows]

    async def get_object_by_id(self, object_id: UUID) -> BusinessObject | None:
        row = (
            await self._db.execute(
                select(_O).where(_O.c.space_id == CURRENT_SPACE, _O.c.object_id == object_id)
            )
        ).first()
        return await self._object(row) if row else None

    async def get_objects_by_id(self, object_ids: Sequence[UUID]) -> list[BusinessObject]:
        if not object_ids:
            return []
        rows = await self._db.execute(
            select(_O).where(
                _O.c.space_id == CURRENT_SPACE, _O.c.object_id.in_(list(dict.fromkeys(object_ids)))
            )
        )
        return [await self._object(r) for r in rows]

    async def changed_since(self, revision: int, *, limit: int) -> list[BusinessObject]:
        rows = await self._db.execute(
            select(_O)
            .where(_O.c.space_id == CURRENT_SPACE, _O.c.change_seq > revision)
            .order_by(_O.c.change_seq)
            .limit(limit)
        )
        return [await self._object(r) for r in rows]

    async def last_revision(self) -> int:
        stmt = text("SELECT CASE WHEN is_called THEN last_value ELSE 0 END FROM change_seq")
        return int((await self._db.execute(stmt)).scalar_one())

    async def types_keyed_by(
        self, namespaces: Sequence[str], types: Sequence[str], ids: Sequence[str]
    ) -> dict[str, list[str]]:
        if not (namespaces and types and ids):
            return {}
        rows = await self._db.execute(
            select(_O.c.external_id, _O.c.type)
            .where(
                _O.c.space_id == CURRENT_SPACE,
                _O.c.namespace.in_(list(dict.fromkeys(namespaces))),
                _O.c.type.in_(list(dict.fromkeys(types))),
                _O.c.external_id.in_(list(dict.fromkeys(ids))),
            )
            .distinct()
        )
        found: dict[str, list[str]] = {}
        for r in rows:
            found.setdefault(r.external_id, []).append(r.type)
        return found

    async def matching(
        self, searches: Sequence[tuple[str, str, str]], *, limit: int
    ) -> dict[tuple[str, str, str], list[ObjectKey]]:
        wanted = list(dict.fromkeys(searches))
        if not wanted:
            return {}
        keys = [await self._match_key(field, value) for _, field, value in wanted]
        rows = await self._db.execute(
            _MATCHING, {"types": [t for t, _, _ in wanted], "keys": keys, "limit": limit}
        )
        found: dict[tuple[str, str, str], list[ObjectKey]] = {search: [] for search in wanted}
        for r in rows:
            found[wanted[r.i - 1]].append(ObjectKey(r.type, r.namespace, r.external_id))
        return found

    async def objects_for(
        self, handle_ids: Sequence[UUID], *, limit: int = 50, before: tuple[datetime, UUID] | None = None
    ) -> list[BusinessObject]:
        # By the time of the state, not by `change_seq`: storing a vector bumps the sequence and must not
        # make an old object look recent.
        query = select(_O).where(_O.c.space_id == CURRENT_SPACE, _involving(handle_ids))
        if before is not None:
            query = query.where(tuple_(_O.c.as_of, _O.c.object_id) < pair(before))
        rows = await self._db.execute(query.order_by(_O.c.as_of.desc(), _O.c.object_id.desc()).limit(limit))
        return [await self._object(r) for r in rows]

    async def subject_records(
        self, handle_ids: Sequence[UUID], *, types: Collection[str], keys: Collection[tuple[str, str]]
    ) -> list[BusinessObject]:
        handles = sorted(set(handle_ids))
        if not handles:
            return []
        own = [func.lower(_O.c.type).in_(sorted(types))]
        if keys:
            own.append(tuple_(_O.c.namespace, _O.c.external_id).in_(sorted(set(keys))))
        # Served by `ix_business_objects_origin_handle`, then kept by type or key.
        query = select(_O).where(
            _O.c.space_id == CURRENT_SPACE, _O.c.origin_handle_id.in_(handles), or_(*own)
        )
        rows = await self._db.execute(query.order_by(_O.c.as_of.desc(), _O.c.object_id))
        return [await self._object(r) for r in rows]

    async def objects_involving(
        self, handle_ids: Sequence[UUID], *, per_handle: int
    ) -> list[tuple[BusinessObject, tuple[tuple[UUID, str], ...]]]:
        handles = sorted(set(handle_ids))
        if not handles:
            return []
        rows = await self._db.execute(_INVOLVING, {"handles": handles, "per_handle": per_handle})
        found = []
        for r in rows.all():
            parts = (p.partition(":") for p in r.taking_part)
            found.append((await self._object(r), tuple(sorted((UUID(h), role) for h, _, role in parts))))
        return found

    async def actions_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 100,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[AgentAction]:
        owned = select(_O.c.object_id).where(_O.c.space_id == CURRENT_SPACE, _involving(handle_ids))
        query = select(_A).where(
            _A.c.space_id == CURRENT_SPACE,
            or_(_A.c.object_id.in_(owned), _A.c.closes_handle_id.in_(list(handle_ids))),
        )
        if since is not None:
            query = query.where(_A.c.occurred_at >= since)
        if before is not None:
            query = query.where(tuple_(_A.c.occurred_at, _A.c.action_id) < pair(before))
        return await self._actions(
            query.order_by(_A.c.occurred_at.desc(), _A.c.action_id.desc()).limit(limit)
        )

    async def operation_counts(self, *, since: datetime, limit: int) -> list[tuple[str, int]]:
        uses = func.count().label("uses")
        rows = await self._db.execute(
            select(_A.c.operation, uses)
            .where(_A.c.space_id == CURRENT_SPACE, _A.c.occurred_at >= since)
            .group_by(_A.c.operation)
            .order_by(uses.desc(), _A.c.operation)
            .limit(limit)
        )
        return [(str(r.operation), int(r.uses)) for r in rows.all()]

    async def operation_results(self, operations: Sequence[str], *, since: datetime, limit: int) -> list[str]:
        if not operations:
            return []
        actions = await self._actions(
            select(_A)
            .where(
                _A.c.space_id == CURRENT_SPACE,
                _A.c.operation.in_(list(operations)),
                _A.c.occurred_at >= since,
                _A.c.result_ct.is_not(None),
            )
            .order_by(_A.c.occurred_at.desc(), _A.c.action_id.desc())
            .limit(limit)
        )
        return [a.result for a in actions if a.result]

    async def object_event_ids(self, object_id: UUID, *, limit: int = 100) -> list[UUID]:
        rows = await self._db.execute(
            select(_E.c.event_id)
            .where(_E.c.space_id == CURRENT_SPACE, _E.c.object_id == object_id)
            .order_by(_E.c.occurred_at.desc(), _E.c.event_id.desc())
            .limit(limit)
        )
        return list(rows.scalars())

    async def add_object(self, obj: BusinessObject) -> BusinessObject:
        await self._execute_named(
            insert(_O)
            .values(
                space_id=CURRENT_SPACE,
                object_id=obj.object_id,
                type=obj.key.type,
                namespace=obj.key.namespace,
                external_id=obj.key.external_id,
                origin_handle_id=obj.origin_handle_id,
                **await self._sealed_state(obj.object_id, obj.state, obj.fields, obj.typed),
                derived_status=obj.derived_status,
                state_version=obj.state_version,
                as_of=obj.as_of,
                source_id=obj.source_id,
                record_ref=obj.record_ref,
            )
            .on_conflict_do_nothing(index_elements=["space_id", "namespace", "type", "external_id"])
        )
        stored = await self.get_object(obj.key)
        assert stored is not None
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
        inputs_hash: bytes | None = None,
    ) -> int:
        written = await self._execute_named(
            update(_O)
            .where(_O.c.space_id == CURRENT_SPACE, _O.c.object_id == object_id)
            .values(
                **await self._sealed_state(object_id, state, fields, typed),
                derived_status=derived_status,
                inputs_hash=inputs_hash,
                as_of=as_of,
                source_id=source_id,
                state_version=state_version,
                record_ref=record_ref,
                **NO_VECTOR,
            )
            .returning(_O.c.change_seq)
        )
        return int(written.scalar_one())

    async def set_owner(self, object_id: UUID, handle_id: UUID) -> int | None:
        written = await self._db.execute(
            update(_O)
            .where(
                _O.c.space_id == CURRENT_SPACE,
                _O.c.object_id == object_id,
                _O.c.origin_handle_id.is_(None),
            )
            .values(origin_handle_id=handle_id)
            .returning(_O.c.change_seq)
        )
        seq = written.scalar_one_or_none()
        return None if seq is None else int(seq)

    async def add_object_event(self, object_id: UUID, event_id: UUID, occurred_at: datetime) -> bool:
        inserted = await self._db.execute(
            insert(_E)
            .values(space_id=CURRENT_SPACE, object_id=object_id, event_id=event_id, occurred_at=occurred_at)
            .on_conflict_do_nothing()
            .returning(_E.c.event_id)
        )
        return inserted.first() is not None

    async def add_participants(self, object_id: UUID, participants: Sequence[tuple[UUID, str]]) -> None:
        if not participants:
            return
        rows = [
            {"space_id": CURRENT_SPACE, "object_id": object_id, "handle_id": h, "role": r}
            for h, r in participants
        ]
        await self._db.execute(insert(_P).values(rows).on_conflict_do_nothing())

    async def participants_of(
        self, object_ids: Sequence[UUID], *, per_object: int
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        if not object_ids:
            return {}
        # The owner first, a handle with a role of its own next, a bare participant last: the cap keeps the
        # people whose words the object is about, not the first handles by id.
        priority = case((_P.c.role == "owner", 0), (_P.c.role == "participant", 2), else_=1)
        wanted = (_P.c.space_id == CURRENT_SPACE, _P.c.object_id.in_(list(dict.fromkeys(object_ids))))
        per_handle = (
            select(_P.c.object_id, _P.c.handle_id, func.min(priority).label("p"))
            .where(*wanted)
            .group_by(_P.c.object_id, _P.c.handle_id)
            .subquery()
        )
        ranked = select(
            per_handle.c.object_id,
            per_handle.c.handle_id,
            func.row_number()
            .over(partition_by=per_handle.c.object_id, order_by=(per_handle.c.p, per_handle.c.handle_id))
            .label("n"),
        ).subquery()
        rows = await self._db.execute(
            select(_P.c.object_id, _P.c.handle_id, _P.c.role)
            .join(ranked, and_(ranked.c.object_id == _P.c.object_id, ranked.c.handle_id == _P.c.handle_id))
            .where(*wanted, ranked.c.n <= per_object)
            .order_by(_P.c.object_id, ranked.c.n, _P.c.role)
        )
        found: dict[UUID, list[tuple[UUID, str]]] = {}
        for object_id, handle_id, role in rows:
            found.setdefault(object_id, []).append((handle_id, role))
        return found

    async def participant_count(self, object_id: UUID) -> int:
        found = await self._db.scalar(
            select(func.count(func.distinct(_P.c.handle_id))).where(
                _P.c.space_id == CURRENT_SPACE, _P.c.object_id == object_id
            )
        )
        return int(found or 0)

    async def add_action(self, action: AgentAction) -> None:
        result_ct, dek = None, _NO_KEY
        if action.result is not None:
            result_ct, dek = await self._seal.seal(
                action.result, table="agent_actions", row=action.action_id, column="result_ct"
            )
        await self._execute_named(
            insert(_A)
            .values(
                space_id=CURRENT_SPACE,
                action_id=action.action_id,
                event_id=action.event_id,
                source_id=action.source_id,
                purpose=action.purpose,
                object_id=action.object_id,
                operation=action.operation,
                result_ct=result_ct,
                closes_handle_id=action.closes_handle_id,
                closes_item_id=action.closes_item_id,
                status=action.status.value,
                confirmed_by_event_id=action.confirmed_by_event_id,
                corrects_action_id=action.corrects_action_id,
                occurred_at=action.occurred_at,
                dek_version=dek,
            )
            .on_conflict_do_nothing()
        )

    async def action_for_event(self, event_id: UUID) -> AgentAction | None:
        found = await self._actions(
            select(_A).where(_A.c.space_id == CURRENT_SPACE, _A.c.event_id == event_id).limit(1)
        )
        return found[0] if found else None

    async def actions_on(
        self,
        object_id: UUID,
        operation: str,
        *,
        since: datetime,
        until: datetime,
        statuses: Sequence[ActionStatus] | None = None,
    ) -> list[AgentAction]:
        query = select(_A).where(
            _A.c.space_id == CURRENT_SPACE,
            _A.c.object_id == object_id,
            _A.c.operation == operation,
            _A.c.occurred_at.between(since, until),
        )
        if statuses is not None:
            query = query.where(_A.c.status.in_([s.value for s in statuses]))
        return await self._actions(query.order_by(_A.c.occurred_at, _A.c.action_id))

    async def actions_of_object(self, object_id: UUID, *, limit: int = 100) -> list[AgentAction]:
        return await self._actions(
            select(_A)
            .where(_A.c.space_id == CURRENT_SPACE, _A.c.object_id == object_id)
            .order_by(_A.c.occurred_at.desc(), _A.c.action_id.desc())
            .limit(limit)
        )

    async def is_confirming(self, event_id: UUID) -> bool:
        query = select(exists().where(_A.c.space_id == CURRENT_SPACE, _A.c.confirmed_by_event_id == event_id))
        return bool((await self._db.execute(query)).scalar())

    async def set_action_status(
        self, action_id: UUID, status: ActionStatus, *, confirmed_by_event_id: UUID | None = None
    ) -> None:
        values: dict[str, Any] = {"status": status.value}
        if confirmed_by_event_id is not None:
            values["confirmed_by_event_id"] = confirmed_by_event_id
        await self._db.execute(
            update(_A).where(_A.c.space_id == CURRENT_SPACE, _A.c.action_id == action_id).values(**values)
        )

    async def declared_before(self, cutoff: datetime, *, limit: int = 500) -> list[AgentAction]:
        return await self._actions(
            select(_A)
            .join(_O, (_O.c.space_id == _A.c.space_id) & (_O.c.object_id == _A.c.object_id))
            .where(
                _A.c.space_id == CURRENT_SPACE,
                _A.c.status == ActionStatus.DECLARED.value,
                _A.c.occurred_at < cutoff,
                _O.c.namespace != COORDINATION_NAMESPACE,
            )
            .order_by(_A.c.occurred_at, _A.c.action_id)
            .limit(limit)
        )

    async def divergent_actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime
    ) -> list[tuple[AgentAction, UUID]]:
        query = (
            select(_A, _O.c.origin_handle_id.label("object_origin"))
            .join(_O, (_O.c.space_id == _A.c.space_id) & (_O.c.object_id == _A.c.object_id))
            .where(
                _A.c.space_id == CURRENT_SPACE,
                _A.c.status == ActionStatus.DIVERGENT.value,
                _A.c.occurred_at >= since,
                _O.c.origin_handle_id.in_(list(handle_ids)),
            )
            .order_by(_A.c.occurred_at.desc(), _A.c.action_id.desc())
        )
        rows = (await self._db.execute(query)).all()
        return [(await self._action(r), r.object_origin) for r in rows]

    async def vectors_of(self, object_ids: Sequence[UUID], action_ids: Sequence[UUID]) -> list[ItemVector]:
        vectors = []
        if object_ids:
            rows = await self._db.execute(
                select(_O.c.object_id, *vector_columns(_O)).where(
                    _O.c.space_id == CURRENT_SPACE,
                    _O.c.object_id.in_(list(object_ids)),
                    _O.c.embedding_ct.is_not(None),
                )
            )
            for r in rows:
                vectors.append(
                    await opened_vector(self._seal, ItemKind.OBJECT, "business_objects", r, r.object_id)
                )
        if action_ids:
            rows = await self._db.execute(
                select(_A.c.action_id, *vector_columns(_A)).where(
                    _A.c.space_id == CURRENT_SPACE,
                    _A.c.action_id.in_(list(action_ids)),
                    _A.c.embedding_ct.is_not(None),
                )
            )
            for r in rows:
                vectors.append(
                    await opened_vector(self._seal, ItemKind.ACTION, "agent_actions", r, r.action_id)
                )
        return vectors

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        objects = select(_O).where(_O.c.space_id == CURRENT_SPACE, needs_vector(_O, model))
        actions = select(_A).where(_A.c.space_id == CURRENT_SPACE, needs_vector(_A, model))
        if handle_ids is not None:
            objects = objects.where(_involving(handle_ids))
            owned = select(_O.c.object_id).where(_O.c.space_id == CURRENT_SPACE, _involving(handle_ids))
            actions = actions.where(
                or_(_A.c.object_id.in_(owned), _A.c.closes_handle_id.in_(list(handle_ids)))
            )
        rows = (await self._db.execute(objects.order_by(_O.c.object_id).limit(limit))).all()
        # An object's text is its state, which a system event rewrites in place.
        found = [PendingVector(await self._object(r), fence=r.change_seq) for r in rows]
        if len(found) < limit:
            query = actions.order_by(_A.c.action_id).limit(limit - len(found))
            found += [PendingVector(a) for a in await self._actions(query)]
        return found

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        record = pending.record
        if isinstance(record, BusinessObject):
            values = await sealed_vector(self._seal, "business_objects", record.object_id, vector, model)
            where = [_O.c.space_id == CURRENT_SPACE, _O.c.object_id == record.object_id]
            return await store(self._db, _O, where, values, pending.fence)
        if isinstance(record, AgentAction):
            values = await sealed_vector(self._seal, "agent_actions", record.action_id, vector, model)
            where = [_A.c.space_id == CURRENT_SPACE, _A.c.action_id == record.action_id]
            return await store(self._db, _A, where, values, pending.fence)
        raise ValueError(f"objects hold no {pending.kind.value} rows")

    async def expire_retained(
        self,
        type_: str,
        before: datetime,
        *,
        limit: int,
        keep_objects: Collection[UUID],
        keep_handles: Collection[UUID],
    ) -> list[UUID]:
        where = [_O.c.space_id == CURRENT_SPACE, _O.c.type == type_, _O.c.as_of < before]
        if keep_objects:
            where.append(_O.c.object_id.not_in(list(keep_objects)))
        if keep_handles:
            where.append(
                or_(_O.c.origin_handle_id.is_(None), _O.c.origin_handle_id.not_in(list(keep_handles)))
            )
        ids = list((await self._db.execute(select(_O.c.object_id).where(*where).limit(limit))).scalars())
        if not ids:
            return []
        # The object row goes last, as an erasure's does.
        for table in (_P, _A, _E, _O):
            await self._db.execute(
                delete(table).where(table.c.space_id == CURRENT_SPACE, table.c.object_id.in_(ids))
            )
        return ids

    async def objects_page(self, after: UUID | None, limit: int) -> list[BusinessObject]:
        query = select(_O).where(_O.c.space_id == CURRENT_SPACE)
        if after is not None:
            query = query.where(_O.c.object_id > after)
        rows = await self._db.execute(query.order_by(_O.c.object_id).limit(limit))
        return [await self._object(r) for r in rows.all()]

    async def rewrite_state(self, obj: BusinessObject, unmatched: Sequence[tuple[str, str]]) -> int:
        """The object's state and observations sealed again as they are now, its stamps and dates kept; the
        reverse relations stop finding it by the values in `unmatched`. Returns the new change sequence."""
        sealed = await self._sealed_state(obj.object_id, obj.state, obj.fields, obj.typed)
        del sealed["match_keys"]
        kept: Any = _O.c.match_keys
        for key in sorted({await self._match_key(field, value) for field, value in unmatched}):
            kept = func.array_remove(kept, key)
        written = await self._db.execute(
            update(_O)
            .where(_O.c.space_id == CURRENT_SPACE, _O.c.object_id == obj.object_id)
            .values(**sealed, match_keys=func.nullif(kept, literal_column("'{}'::text[]")), **NO_VECTOR)
            .returning(_O.c.change_seq)
        )
        return int(written.scalar_one())

    async def actions_by_ids(self, action_ids: Sequence[UUID]) -> list[AgentAction]:
        if not action_ids:
            return []
        return await self._actions(
            select(_A).where(_A.c.space_id == CURRENT_SPACE, _A.c.action_id.in_(list(action_ids)))
        )

    async def actions_page(self, after: UUID | None, limit: int) -> list[AgentAction]:
        query = select(_A).where(_A.c.space_id == CURRENT_SPACE)
        if after is not None:
            query = query.where(_A.c.action_id > after)
        return await self._actions(query.order_by(_A.c.action_id).limit(limit))

    async def rewrite_result(self, action: AgentAction) -> None:
        """The action's result sealed again as it is now; its vector goes with the old text."""
        result_ct, dek = None, _NO_KEY
        if action.result is not None:
            result_ct, dek = await self._seal.seal(
                action.result, table="agent_actions", row=action.action_id, column="result_ct"
            )
        await self._db.execute(
            update(_A)
            .where(_A.c.space_id == CURRENT_SPACE, _A.c.action_id == action.action_id)
            .values(result_ct=result_ct, dek_version=dek, **NO_VECTOR)
        )

    async def erase_unowned(self, keys: Sequence[ObjectKey], *, limit: int) -> int:
        if not keys:
            return 0
        wanted = tuple_(_O.c.type, _O.c.namespace, _O.c.external_id).in_(
            [(k.type, k.namespace, k.external_id) for k in keys]
        )
        found = select(_O.c.object_id).where(
            _O.c.space_id == CURRENT_SPACE, wanted, _O.c.origin_handle_id.is_(None)
        )
        ids = list((await self._db.execute(found.limit(limit))).scalars())
        for table in (_P, _A, _E, _O):
            if ids:
                await self._db.execute(
                    delete(table).where(table.c.space_id == CURRENT_SPACE, table.c.object_id.in_(ids))
                )
        return len(ids)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            events = list(scope.event_ids)
            actions = await delete_where(self._db, _A, ("action_id",), [_A.c.event_id.in_(events)], limit)
            return actions + await delete_where(
                self._db, _E, ("object_id", "event_id"), [_E.c.event_id.in_(events)], limit - len(actions)
            )
        if scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return []
        handles = list(scope.handle_ids)
        owned = select(_O.c.object_id).where(
            _O.c.space_id == CURRENT_SPACE, _O.c.origin_handle_id.in_(handles)
        )
        # The object row goes last: while it exists, a later batch still finds its actions and events.
        steps: list[tuple[Table, tuple[str, ...], Any]] = [
            (
                _P,
                ("object_id", "handle_id", "role"),
                or_(_P.c.handle_id.in_(handles), _P.c.object_id.in_(owned)),
            ),
            (_A, ("action_id",), or_(_A.c.closes_handle_id.in_(handles), _A.c.object_id.in_(owned))),
            (_E, ("object_id", "event_id"), _E.c.object_id.in_(owned)),
            (_O, ("object_id",), _O.c.origin_handle_id.in_(handles)),
        ]
        erased: list[ErasedRow] = []
        for table, key, where in steps:
            erased += await delete_where(self._db, table, key, [where], limit - len(erased))
        return erased

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        source = _EXPORTED.get(table)
        if source is None:
            raise ValueError(f"objects does not export {table}")
        rows = await changed_rows(self._db, source, after_seq=after_seq, upto_seq=upto_seq, limit=limit)
        if table == "business_objects":
            return [export_row(await self._object(r), r.change_seq) for r in rows]
        if table == "agent_actions":
            return [export_row(await self._action(r), r.change_seq) for r in rows]
        return [
            {
                "object_id": r.object_id,
                "event_id": r.event_id,
                "occurred_at": r.occurred_at,
                "change_seq": r.change_seq,
            }
            for r in rows
        ]

    async def expire_dependents(
        self, changed: Mapping[UUID, Sequence[str]], at: datetime
    ) -> list[BusinessObject]:
        """One statement (24.3.7): every object that names an input of `changed` expires, with the names of
        the inputs that changed; the expired objects come back as stored."""
        pairs = [
            [str(object_id), name] for object_id, names in changed.items() for name in sorted(set(names))
        ]
        if not pairs:
            return []
        rows = await self._db.execute(
            text(
                f"""
                WITH hit AS (
                    SELECT d.dependent_object_id,
                           array_agg(DISTINCT d.input_field ORDER BY d.input_field) AS inputs
                    FROM object_dependencies d
                    JOIN jsonb_array_elements(CAST(:pairs AS jsonb)) AS p
                      ON d.input_object_id = CAST(p ->> 0 AS uuid) AND d.input_field = p ->> 1
                    WHERE d.space_id = {_SPACE}
                    GROUP BY d.dependent_object_id
                )
                UPDATE business_objects o SET
                    derived_status = 'expired_by_input',
                    field_meta = jsonb_set(
                        coalesce(o.field_meta, '{{}}'), '{{{META_KEY}}}',
                        coalesce(o.field_meta -> '{META_KEY}', '{{}}')
                            || jsonb_build_object('by', to_jsonb(hit.inputs), 'at', CAST(:at AS text))
                    )
                FROM hit
                WHERE o.space_id = {_SPACE} AND o.object_id = hit.dependent_object_id
                RETURNING o.*
                """
            ),
            {"pairs": orjson.dumps(pairs).decode(), "at": at.isoformat()},
        )
        return [await self._object(r) for r in rows.all()]

    async def mark_timers(self, marks: Mapping[UUID, Mapping[str, TimerMark]]) -> list[BusinessObject]:
        """The timers' new marks on their objects, in one statement, without opening any state; the objects
        come back as stored."""
        return await self._marked(
            "t", {o: {n: timer_json(m) for n, m in by.items()} for o, by in marks.items()}
        )

    async def mark_scans(self, marks: Mapping[UUID, Mapping[str, ScanMark]]) -> list[BusinessObject]:
        return await self._marked(
            "s", {o: {n: scan_json(m) for n, m in by.items()} for o, by in marks.items()}
        )

    async def _marked(self, entry: str, marks: Mapping[UUID, Mapping[str, Any]]) -> list[BusinessObject]:
        if not marks:
            return []
        body = [{"object_id": str(object_id), "m": by} for object_id, by in marks.items()]
        rows = await self._db.execute(
            text(
                f"""
                WITH marks AS (
                    SELECT CAST(m ->> 'object_id' AS uuid) AS object_id, m -> 'm' AS m
                    FROM jsonb_array_elements(CAST(:marks AS jsonb)) AS m
                )
                UPDATE business_objects o SET
                    field_meta = jsonb_set(
                        coalesce(o.field_meta, '{{}}'), ARRAY['{META_KEY}', CAST(:entry AS text)],
                        coalesce(o.field_meta -> '{META_KEY}' -> CAST(:entry AS text), '{{}}') || marks.m
                    )
                FROM marks
                WHERE o.space_id = {_SPACE} AND o.object_id = marks.object_id
                RETURNING o.*
                """
            ),
            {"marks": orjson.dumps(body).decode(), "entry": entry},
        )
        return [await self._object(r) for r in rows.all()]

    async def _sealed_state(
        self, object_id: UUID, state: dict[str, Any], fields: Holding, typed: Typed
    ) -> dict[str, Any]:
        values = {name: {src: value_part(o) for src, o in by.items()} for name, by in fields.items()}
        body: dict[str, Any] = {"s": state, "f": values}
        if extra := sealed_part(typed):
            body["x"] = extra
        document = orjson.dumps(body, option=orjson.OPT_SORT_KEYS)
        ciphertext, dek = await self._seal.seal(
            document, table="business_objects", row=object_id, column="state_ct"
        )
        meta: dict[str, Any] = {
            name: {src: stamps_part(o) for src, o in by.items()} for name, by in fields.items()
        }
        if stamps := meta_part(typed):
            meta[META_KEY] = stamps
        keys = sorted({await self._match_key(field, value) for field, value in typed.matched})
        return {"state_ct": ciphertext, "dek_version": dek, "field_meta": meta, "match_keys": keys or None}

    async def _execute_named(self, stmt: Any) -> Any:
        """One statement that also indexes what the rows it sealed name a person by
        (`domain.privacy.mentions`)."""
        return await self._db.execute(await with_mentions(stmt, self._seal))

    async def _match_key(self, field: str, value: str) -> str:
        return (await self._seal.hmac("relation_match", f"{field}\x1f{value}")).hex()

    async def _object(self, r: Row[Any]) -> BusinessObject:
        state, fields, typed = await self._state(r)
        return BusinessObject(
            object_id=r.object_id,
            key=ObjectKey(r.type, r.namespace, r.external_id),
            state=state,
            as_of=r.as_of,
            source_id=r.source_id,
            origin_handle_id=r.origin_handle_id,
            record_ref=r.record_ref,
            state_version=r.state_version,
            fields=fields,
            revision=r.change_seq,
            derived_status=r.derived_status,
            typed=typed,
        )

    async def _state(self, r: Row[Any]) -> tuple[dict[str, Any], Holding, Typed]:
        if r.state_ct is None:
            return {}, {}, Typed()
        document = orjson.loads(
            await self._seal.open_bytes(
                r.state_ct, r.dek_version, table="business_objects", row=r.object_id, column="state_ct"
            )
        )
        state = dict(document["s"])
        meta = r.field_meta or {}
        fields = {
            name: {src: observation_of(value, meta[name][src]) for src, value in by.items()}
            for name, by in document["f"].items()
        }
        return state, fields, typed_of(meta.get(META_KEY), document.get("x"))

    async def _actions(self, query: Select[Any]) -> list[AgentAction]:
        return [await self._action(r) for r in (await self._db.execute(query)).all()]

    async def _action(self, r: Row[Any]) -> AgentAction:
        result = None
        if r.result_ct is not None:
            result = await self._seal.open_text(
                r.result_ct, r.dek_version, table="agent_actions", row=r.action_id, column="result_ct"
            )
        return AgentAction(
            action_id=r.action_id,
            event_id=r.event_id,
            source_id=r.source_id,
            operation=r.operation,
            status=ActionStatus(r.status),
            occurred_at=r.occurred_at,
            object_id=r.object_id,
            result=result,
            purpose=r.purpose,
            closes_item_id=r.closes_item_id,
            closes_handle_id=r.closes_handle_id,
            confirmed_by_event_id=r.confirmed_by_event_id,
            corrects_action_id=r.corrects_action_id,
        )


def _involving(handle_ids: Sequence[UUID]) -> Any:
    handles = list(handle_ids)
    # A union rather than an OR: each side reads its own index (the owner's, the participants'), where an OR
    # over a subquery scans every object of the space.
    owned = select(_O.c.object_id).where(_O.c.space_id == CURRENT_SPACE, _O.c.origin_handle_id.in_(handles))
    taking_part = select(_P.c.object_id).where(_P.c.space_id == CURRENT_SPACE, _P.c.handle_id.in_(handles))
    return _O.c.object_id.in_(union(owned, taking_part))
