"""PostgreSQL implementation of `TypedRepository`: computed values' versions, timers and the inputs of derived
objects (24.3.6 to 24.3.8).

A value is sealed on its own (AAD `object_values:<object id>/<name>/<version>:value_ct`); the rule, the
inputs' hash and names, and the versions stay in the clear, as stamps. Each call is one statement.
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import orjson
from sqlalchemy import (
    ARRAY,
    DateTime,
    String,
    Table,
    Uuid,
    case,
    delete,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
    tuple_,
    union,
    union_all,
    update,
)
from sqlalchemy import column as sql_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.mentions import with_mentions
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.memory import BusinessObjectRow
from niadra.adapters.outbound.postgres.schema.state import (
    ContentScan,
    ObjectDependency,
    ObjectTimer,
    ObjectValue,
    ObjectWatch,
    RefreshRequest,
    SharedObject,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.state.shared import SharedKey
from niadra.domain.state.typed import META_KEY, STAMP_SEQ, NewValue, ScanMark, TimerWrite
from niadra.ports.repos.typed import (
    FiredTimer,
    RefreshRow,
    ScanRow,
    SharedRow,
    SharedStamps,
    SharedWrite,
    WatchRow,
    content_ref,
)

_V = cast(Table, ObjectValue.__table__)
_T = cast(Table, ObjectTimer.__table__)
_D = cast(Table, ObjectDependency.__table__)
_S = cast(Table, SharedObject.__table__)
_W = cast(Table, ObjectWatch.__table__)
_R = cast(Table, RefreshRequest.__table__)
_C = cast(Table, ContentScan.__table__)
_O = cast(Table, BusinessObjectRow.__table__)
_PRIORITY = {"high": 0, "normal": 1, "low": 2}
_SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"

# One statement (24.3.12): per object, the (field, source) pairs whose version is greater than the stored one
# replace it, value and stamps together; an object no longer in the working set has no row and keeps nothing.
_CONSOLIDATE = f"""
WITH incoming AS (
    SELECT CAST(i ->> 'object_id' AS uuid) AS object_id, i -> 'state' AS state, i -> 'meta' AS meta
    FROM jsonb_array_elements(CAST(:objects AS jsonb)) AS i
),
newer AS (
    SELECT o.object_id, f.key AS field, s.key AS source, s.value AS meta,
           incoming.state -> f.key -> s.key AS sealed
    FROM incoming
    JOIN shared_objects o ON o.space_id = {_SPACE} AND o.object_id = incoming.object_id
    CROSS JOIN LATERAL jsonb_each(incoming.meta) AS f
    CROSS JOIN LATERAL jsonb_each(f.value) AS s
    WHERE CAST(s.value ->> 'version' AS bigint)
        > coalesce(CAST(o.field_meta -> f.key -> s.key ->> 'version' AS bigint), -1)
),
fields AS (
    SELECT object_id, field,
           jsonb_object_agg(source, meta) AS meta, jsonb_object_agg(source, sealed) AS sealed
    FROM newer GROUP BY object_id, field
),
moved AS (
    SELECT object_id, jsonb_object_agg(field, meta) AS meta, jsonb_object_agg(field, sealed) AS sealed
    FROM fields GROUP BY object_id
)
UPDATE shared_objects o SET
    field_meta = (
        SELECT jsonb_object_agg(k, coalesce(o.field_meta -> k, '{{}}') || coalesce(moved.meta -> k, '{{}}'))
        FROM jsonb_object_keys(o.field_meta || moved.meta) AS k
    ),
    state_ct = (
        SELECT jsonb_object_agg(k, coalesce(o.state_ct -> k, '{{}}') || coalesce(moved.sealed -> k, '{{}}'))
        FROM jsonb_object_keys(o.state_ct || moved.sealed) AS k
    ),
    version = o.version + 1
FROM moved
WHERE o.space_id = {_SPACE} AND o.object_id = moved.object_id
RETURNING o.object_id, o.watch_count, o.type
"""

# One statement: the watches kept (a newer one of the same subject replaces its condition and expiry), and the
# ones that are new counted on their objects.
_WATCH = f"""
WITH kept AS (
    INSERT INTO object_watches (space_id, watch_id, object_id, origin_handle_id, condition, consent_event_key,
                                expires_at, created_at)
    SELECT {_SPACE}, CAST(w ->> 'watch_id' AS uuid), CAST(w ->> 'object_id' AS uuid),
           CAST(w ->> 'handle_id' AS uuid), w ->> 'condition', w ->> 'consent',
           CAST(w ->> 'expires_at' AS timestamptz),
           CAST(:now AS timestamptz)
    FROM jsonb_array_elements(CAST(:watches AS jsonb)) AS w
    ON CONFLICT (space_id, watch_id) DO UPDATE
        SET condition = EXCLUDED.condition, consent_event_key = EXCLUDED.consent_event_key,
            expires_at = EXCLUDED.expires_at
    RETURNING object_id, (xmax = 0) AS created
),
counted AS (SELECT object_id, count(*) AS n FROM kept WHERE created GROUP BY object_id)
UPDATE shared_objects o SET watch_count = o.watch_count + counted.n
FROM counted WHERE o.space_id = {_SPACE} AND o.object_id = counted.object_id
"""

_UNWATCH = f"""
WITH gone AS (
    DELETE FROM object_watches w
    USING jsonb_array_elements(CAST(:pairs AS jsonb)) AS p
    WHERE w.space_id = {_SPACE} AND w.object_id = CAST(p ->> 0 AS uuid)
      AND w.origin_handle_id = CAST(p ->> 1 AS uuid)
    RETURNING w.object_id
),
counted AS (SELECT object_id, count(*) AS n FROM gone GROUP BY object_id)
UPDATE shared_objects o SET watch_count = greatest(0, o.watch_count - counted.n)
FROM counted WHERE o.space_id = {_SPACE} AND o.object_id = counted.object_id
"""


# The stored stamps of the row the statement updates, as it stands when the statement reaches it: a sweep that
# fired a timer meanwhile committed first, and its firing is what this write merges with.
_CUR = f"coalesce(shared_objects.field_meta -> '{META_KEY}', CAST('{{}}' AS jsonb))"
# The typed pass owns its values' versions, timers, input digests, latches, bindings and pieces; a timer the
# stored stamps say fired, under the same firing, stays fired (the sweep fired its row too).
_PLANNED = f"""
CASE WHEN target.m IS NULL THEN {_CUR} ELSE
    ({_CUR} - 'v' - 't' - 'i' - 'l' - 'r' - 'p')
    || (target.m - 'by' - 'at' - '{STAMP_SEQ}' - 't')
    || jsonb_build_object('t', coalesce((
        SELECT jsonb_object_agg(e.k, CASE
            WHEN {_CUR} -> 't' -> e.k ->> 'id' = e.v ->> 'id' AND {_CUR} -> 't' -> e.k ->> 'st' = 'fired'
            THEN {_CUR} -> 't' -> e.k ELSE e.v END)
        FROM jsonb_each(coalesce(target.m -> 't', CAST('{{}}' AS jsonb))) AS e(k, v)
    ), CAST('{{}}' AS jsonb)))
END"""
# A derived object computed after its expiry is current again; one expired after that stays expired.
_RECOMPUTED = f"""(target.since IS NOT NULL
    AND ({_CUR} ->> 'at' IS NULL OR CAST({_CUR} ->> 'at' AS timestamptz) <= target.since))"""
_SHARED_STAMPS = f"""jsonb_set(coalesce(shared_objects.field_meta, CAST('{{}}' AS jsonb)), '{{{META_KEY}}}',
    (CASE WHEN {_RECOMPUTED} THEN ({_PLANNED}) - 'by' - 'at' ELSE ({_PLANNED}) END)
    || CASE WHEN target.inputs IS NULL THEN CAST('{{}}' AS jsonb)
            ELSE jsonb_build_object('by', to_jsonb(target.inputs), 'at', target.at) END
    || jsonb_build_object('{STAMP_SEQ}', coalesce(CAST({_CUR} ->> '{STAMP_SEQ}' AS bigint), 0) + 1))"""
_SHARED_STATUS = f"""CASE WHEN target.inputs IS NOT NULL THEN 'expired_by_input'
    WHEN {_RECOMPUTED} THEN 'current' ELSE shared_objects.derived_status END"""
# The objects a typed pass writes, and the derived shared objects that name an input that changed (by the
# stored bindings, or by the ones this write makes).
_SHARED_TARGET = f"""
WITH marks AS (
    SELECT CAST(w ->> 'object_id' AS uuid) AS object_id, w -> 'm' AS m,
           CAST(w ->> 'since' AS timestamptz) AS since,
           CASE WHEN jsonb_typeof(w -> 'expire') = 'array'
                THEN ARRAY(SELECT jsonb_array_elements_text(w -> 'expire')) END AS expire
    FROM jsonb_array_elements(CAST(:writes AS jsonb)) AS w
),
hit AS (
    SELECT d.dependent_object_id AS object_id,
           array_agg(DISTINCT d.input_field ORDER BY d.input_field) AS inputs
    FROM object_dependencies d
    JOIN jsonb_array_elements(CAST(:changed AS jsonb)) AS p
      ON d.input_object_id = CAST(p ->> 0 AS uuid) AND d.input_field = p ->> 1
    WHERE d.space_id = {_SPACE}
    GROUP BY d.dependent_object_id
)
SELECT coalesce(marks.object_id, hit.object_id) AS object_id, marks.m, marks.since,
       coalesce(hit.inputs, marks.expire) AS inputs,
       CAST(:at AS text) AS at
FROM marks FULL JOIN hit ON hit.object_id = marks.object_id
"""
# One statement: the due timers of the shared objects fire, and each object's stamps say so where they still
# name that firing. It locks the timers and their objects it takes and skips the ones a typed pass holds, so
# the sweep never waits on a pass, and a pass that waits on the sweep merges with what it committed.
_FIRE_SHARED = f"""
WITH due AS (
    SELECT t.timer_id
    FROM object_timers t
    JOIN shared_objects s ON s.space_id = t.space_id AND s.object_id = t.object_id
    WHERE t.space_id = {_SPACE} AND t.state = 'armed' AND t.due_at <= CAST(:now AS timestamptz)
    ORDER BY t.due_at
    LIMIT :limit
    FOR UPDATE OF t, s SKIP LOCKED
),
fired AS (
    UPDATE object_timers t SET state = 'fired', fired_at = CAST(:now AS timestamptz)
    FROM due
    WHERE t.space_id = {_SPACE} AND t.timer_id = due.timer_id
    RETURNING t.timer_id, t.object_id, t.name, t.due_at, t.fired_at, t.value_version, t.supersedes_firing_id
),
ids AS (
    SELECT object_id, jsonb_object_agg(name, CAST(timer_id AS text)) AS ids FROM fired GROUP BY object_id
),
stamped AS (
    UPDATE shared_objects s SET field_meta = jsonb_set(s.field_meta, '{{{META_KEY}}}',
        coalesce(s.field_meta -> '{META_KEY}', CAST('{{}}' AS jsonb)) || jsonb_build_object(
            '{STAMP_SEQ}', coalesce(CAST(s.field_meta -> '{META_KEY}' ->> '{STAMP_SEQ}' AS bigint), 0) + 1,
            't', coalesce(s.field_meta -> '{META_KEY}' -> 't', CAST('{{}}' AS jsonb)) || coalesce((
                SELECT jsonb_object_agg(e.k, (s.field_meta -> '{META_KEY}' -> 't' -> e.k)
                    || jsonb_build_object('st', 'fired', 'fired', CAST(:at AS text)))
                FROM jsonb_each_text(ids.ids) AS e(k, id)
                WHERE s.field_meta -> '{META_KEY}' -> 't' -> e.k ->> 'id' = e.id
            ), CAST('{{}}' AS jsonb))))
    FROM ids
    WHERE s.space_id = {_SPACE} AND s.object_id = ids.object_id
    RETURNING s.object_id
)
SELECT timer_id, object_id, name, due_at, fired_at, value_version, supersedes_firing_id FROM fired
"""


class PgTypedRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    @property
    def _seal(self) -> Sealer:
        assert self._sealer is not None, "typed state repositories run in a space-scoped transaction"
        return self._sealer

    async def add_values(self, values: Mapping[UUID, Sequence[NewValue]], event_id: UUID | None) -> None:
        if (stmt := await self._values(values, event_id)) is not None:
            await self._db.execute(await with_mentions(stmt, self._seal))

    async def write_timers(self, writes: Mapping[UUID, Sequence[TimerWrite]], now: datetime) -> None:
        if (stmt := _timers(writes, now)) is not None:
            await self._db.execute(stmt)

    async def write_shared(
        self,
        writes: Mapping[UUID, SharedWrite],
        values: Mapping[UUID, Sequence[NewValue]],
        timers: Mapping[UUID, Sequence[TimerWrite]],
        changed: Mapping[UUID, Sequence[str]],
        now: datetime,
    ) -> list[SharedStamps]:
        ctes: list[Any] = []
        if (versions := await self._values(values, None)) is not None:
            ctes.append(versions.returning(_V.c.object_id).cte("versions"))
        if (armed := _timers(timers, now)) is not None:
            ctes.append(armed.returning(_T.c.timer_id).cte("armed"))
        rebound = {object_id: w.bound for object_id, w in writes.items() if w.bound is not None}
        if rebound:
            ctes += _rebind(rebound, now)
        pairs = [[str(o), name] for o, names in changed.items() for name in sorted(set(names))]
        if not writes and not pairs:
            if ctes:
                await self._db.execute(await with_mentions(select(literal(1)).add_cte(*ctes), self._seal))
            return []
        body = [
            {
                "object_id": str(object_id),
                "m": dict(w.stamps),
                "since": w.since.isoformat() if w.since else None,
                "expire": list(w.expired_by) or None,
            }
            for object_id, w in writes.items()
        ]
        target = (
            text(_SHARED_TARGET)
            .bindparams(
                writes=orjson.dumps(body).decode(), changed=orjson.dumps(pairs).decode(), at=now.isoformat()
            )
            .columns(
                sql_column("object_id", Uuid),
                sql_column("m", JSONB),
                sql_column("since", DateTime(timezone=True)),
                sql_column("inputs", ARRAY(String)),
                sql_column("at", String),
            )
            .cte("target")
        )
        stmt = (
            update(_S)
            .where(_S.c.space_id == CURRENT_SPACE, _S.c.object_id == target.c.object_id)
            .values(field_meta=literal_column(_SHARED_STAMPS), derived_status=literal_column(_SHARED_STATUS))
            .returning(
                _S.c.object_id,
                _S.c.type,
                _S.c.namespace,
                _S.c.external_id,
                _S.c.variant,
                _S.c.field_meta[META_KEY].label("stamps"),
                _S.c.derived_status,
                target.c.inputs.is_not(None).label("expired"),
            )
        )
        rows = await self._db.execute(await with_mentions(stmt.add_cte(target, *ctes), self._seal))
        return [
            SharedStamps(
                r.object_id,
                SharedKey(r.type, r.namespace, r.external_id, r.variant),
                dict(r.stamps or {}),
                r.derived_status,
                bool(r.expired),
            )
            for r in rows.all()
        ]

    async def _values(self, values: Mapping[UUID, Sequence[NewValue]], event_id: UUID | None) -> Any:
        rows = []
        for object_id, found in values.items():
            for value in found:
                sealed, dek = None, None
                if value.value is not None:
                    row = f"{object_id}/{value.name}/{value.version}"
                    sealed, dek = await self._seal.seal(
                        orjson.dumps(value.value), table="object_values", row=row, column="value_ct"
                    )
                rule, _, rule_version = value.rule.partition("@")
                rows.append(
                    {
                        "space_id": CURRENT_SPACE,
                        "object_id": object_id,
                        "name": value.name,
                        "version": value.version,
                        "value_ct": sealed,
                        "dek_version": dek,
                        "absent_as": value.absent_as,
                        "rule": rule,
                        "rule_version": rule_version,
                        "inputs_hash": value.inputs_hash,
                        "input_refs": {"inputs": list(value.inputs)},
                        "computed_at": value.computed_at,
                        "valid_until": value.valid_until,
                        "declared_gaps": list(value.declared_gaps),
                        "gap_effect": value.gap_effect,
                        "supersedes_version": value.supersedes,
                        "event_id": event_id,
                        "recorded_at": value.recorded_at,
                    }
                )
        return upsert(_V).values(rows).on_conflict_do_nothing() if rows else None

    async def fire_due(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        of_shared = exists().where(_S.c.space_id == _T.c.space_id, _S.c.object_id == _T.c.object_id)
        due = (
            select(_T.c.timer_id)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.state == "armed", _T.c.due_at <= now, ~of_shared)
            .order_by(_T.c.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        rows = await self._db.execute(
            update(_T)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.timer_id.in_(due))
            .values(state="fired", fired_at=now)
            .returning(
                _T.c.timer_id,
                _T.c.object_id,
                _T.c.name,
                _T.c.due_at,
                _T.c.fired_at,
                _T.c.value_version,
                _T.c.supersedes_firing_id,
            )
        )
        return [FiredTimer(*row) for row in rows.all()]

    async def fire_shared(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        rows = await self._db.execute(text(_FIRE_SHARED), {"now": now, "at": now.isoformat(), "limit": limit})
        return [FiredTimer(*row) for row in rows.all()]

    async def bind_inputs(
        self, dependent_id: UUID, inputs: Sequence[tuple[UUID, str]], now: datetime
    ) -> None:
        pairs = list(dict.fromkeys(inputs))
        stale = delete(_D).where(_D.c.space_id == CURRENT_SPACE, _D.c.dependent_object_id == dependent_id)
        if pairs:
            stale = stale.where(tuple_(_D.c.input_object_id, _D.c.input_field).not_in(pairs))
        if not pairs:
            await self._db.execute(stale)
            return
        # One statement: the pairs no longer named go, the new ones come; no row is both deleted and inserted.
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "input_object_id": input_id,
                "input_field": name,
                "dependent_object_id": dependent_id,
                "created_at": now,
            }
            for input_id, name in pairs
        ]
        cleared = stale.returning(_D.c.dependent_object_id).cte("cleared")
        await self._db.execute(upsert(_D).values(rows).add_cte(cleared).on_conflict_do_nothing())

    async def enter_working_set(
        self, entries: Sequence[tuple[UUID, SharedKey, datetime, datetime]]
    ) -> list[SharedRow]:
        if not entries:
            return []
        latest: dict[UUID, tuple[UUID, SharedKey, datetime, datetime]] = {}
        for entry in entries:
            kept = latest.get(entry[0])
            if kept is None or entry[3] > kept[3]:
                latest[entry[0]] = entry
        stmt = upsert(_S).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "object_id": object_id,
                    "type": key.type,
                    "namespace": key.namespace,
                    "external_id": key.external_id,
                    "variant": key.variant,
                    "last_referenced_at": at,
                    "hot_until": until,
                }
                for object_id, key, at, until in latest.values()
            ]
        )
        rows = await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "type", "namespace", "external_id", "variant"],
                set_={
                    "last_referenced_at": func.greatest(
                        _S.c.last_referenced_at, stmt.excluded.last_referenced_at
                    ),
                    "hot_until": func.greatest(_S.c.hot_until, stmt.excluded.hot_until),
                },
            ).returning(*_SHARED)
        )
        return [_shared(r) for r in rows.all()]

    async def consolidate(
        self, objects: Sequence[tuple[UUID, dict[str, Any], dict[str, Any]]]
    ) -> list[tuple[UUID, int, str]]:
        if not objects:
            return []
        body = [{"object_id": str(i), "state": state, "meta": meta} for i, state, meta in objects]
        rows = await self._db.execute(text(_CONSOLIDATE), {"objects": orjson.dumps(body).decode()})
        return [(r.object_id, r.watch_count, r.type) for r in rows.all()]

    async def shared_by_id(self, object_ids: Sequence[UUID]) -> list[SharedRow]:
        if not object_ids:
            return []
        rows = await self._db.execute(
            select(*_SHARED).where(_S.c.space_id == CURRENT_SPACE, _S.c.object_id.in_(list(object_ids)))
        )
        return [_shared(r) for r in rows.all()]

    async def working_set(self, now: datetime, *, after: UUID | None, limit: int) -> list[SharedRow]:
        query = select(*_SHARED).where(_S.c.space_id == CURRENT_SPACE, _S.c.hot_until > now)
        if after is not None:
            query = query.where(_S.c.object_id > after)
        rows = await self._db.execute(query.order_by(_S.c.object_id).limit(limit))
        return [_shared(r) for r in rows.all()]

    async def leave_working_set(
        self, type_: str, unreferenced_since: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[UUID]:
        page = (
            select(_S.c.object_id)
            .where(
                _S.c.space_id == CURRENT_SPACE,
                _S.c.type == type_,
                _S.c.last_referenced_at < unreferenced_since,
                _S.c.watch_count == 0,
                *([_S.c.object_id.not_in(list(keep))] if keep else []),
            )
            .limit(limit)
            .scalar_subquery()
        )
        gone = (
            delete(_S)
            .where(_S.c.space_id == CURRENT_SPACE, _S.c.object_id.in_(page))
            .returning(_S.c.object_id)
            .cte("gone")
        )
        # The armed timers leave with their objects, in the same statement.
        timers = (
            delete(_T)
            .where(
                _T.c.space_id == CURRENT_SPACE,
                _T.c.state == "armed",
                _T.c.object_id.in_(select(gone.c.object_id)),
            )
            .returning(_T.c.timer_id)
            .cte("timers")
        )
        rows = await self._db.execute(select(gone.c.object_id).add_cte(timers))
        return list(rows.scalars())

    async def watch(self, watches: Sequence[WatchRow], now: datetime) -> None:
        if not watches:
            return
        body = [
            {
                "watch_id": str(w.watch_id),
                "object_id": str(w.object_id),
                "handle_id": str(w.handle_id),
                "condition": w.condition,
                "consent": w.consent_event_key,
                "expires_at": w.expires_at.isoformat(),
            }
            for w in {w.watch_id: w for w in watches}.values()
        ]
        await self._db.execute(text(_WATCH), {"watches": orjson.dumps(body).decode(), "now": now})

    async def unwatch(self, pairs: Sequence[tuple[UUID, UUID]]) -> None:
        if pairs:
            body = [[str(o), str(h)] for o, h in dict.fromkeys(pairs)]
            await self._db.execute(text(_UNWATCH), {"pairs": orjson.dumps(body).decode()})

    async def watches_of(self, object_ids: Sequence[UUID], now: datetime) -> list[WatchRow]:
        if not object_ids:
            return []
        rows = await self._db.execute(
            select(_W).where(
                _W.c.space_id == CURRENT_SPACE, _W.c.object_id.in_(list(object_ids)), _W.c.expires_at > now
            )
        )
        return [_watch(r) for r in rows.all()]

    async def watches_for(self, handle_ids: Sequence[UUID]) -> list[WatchRow]:
        if not handle_ids:
            return []
        rows = await self._db.execute(
            select(_W)
            .where(_W.c.space_id == CURRENT_SPACE, _W.c.origin_handle_id.in_(list(handle_ids)))
            .order_by(_W.c.expires_at)
        )
        return [_watch(r) for r in rows.all()]

    async def watches_fired(self, watch_ids: Sequence[UUID], at: datetime) -> None:
        if watch_ids:
            await self._db.execute(
                update(_W)
                .where(_W.c.space_id == CURRENT_SPACE, _W.c.watch_id.in_(list(watch_ids)))
                .values(last_fired_at=at)
            )

    async def request_refreshes(self, rows: Sequence[RefreshRow]) -> None:
        if not rows:
            return
        latest = {(r.object_id, r.reason): r for r in rows}
        stmt = upsert(_R).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "request_id": r.request_id,
                    "object_id": r.object_id,
                    "type": r.key.type,
                    "namespace": r.key.namespace,
                    "external_id": r.key.external_id,
                    "variant": r.key.variant,
                    "reason": r.reason,
                    "priority": r.priority,
                    "budget_units": r.budget_units,
                    "requested_at": r.requested_at,
                    "reservation": r.reservation,
                }
                for r in latest.values()
            ]
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "object_id", "reason"],
                set_={
                    "priority": stmt.excluded.priority,
                    "budget_units": stmt.excluded.budget_units,
                    "requested_at": stmt.excluded.requested_at,
                    "reservation": stmt.excluded.reservation,
                    "lease_until": None,
                    "attempts": 0,
                },
            )
        )

    async def lease_refreshes(
        self, owner: str, now: datetime, lease_until: datetime, *, limit: int
    ) -> list[RefreshRow]:
        rank = case(_PRIORITY, value=_R.c.priority, else_=3)
        free = (
            select(_R.c.request_id)
            .where(_R.c.space_id == CURRENT_SPACE, or_(_R.c.lease_until.is_(None), _R.c.lease_until < now))
            .order_by(rank, _R.c.requested_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        rows = await self._db.execute(
            update(_R)
            .where(_R.c.space_id == CURRENT_SPACE, _R.c.request_id.in_(free))
            .values(lease_until=lease_until, lease_owner=owner, attempts=_R.c.attempts + 1)
            .returning(_R)
        )
        found = [_refresh(r) for r in rows.all()]
        return sorted(found, key=lambda r: (_PRIORITY.get(r.priority, 3), r.requested_at))

    async def refreshed(self, object_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        if not object_ids:
            return []
        rows = await self._db.execute(
            delete(_R)
            .where(_R.c.space_id == CURRENT_SPACE, _R.c.object_id.in_(list(object_ids)))
            .returning(_R.c.reservation)
        )
        return [r for r in rows.scalars() if r]

    async def give_up_refreshes(self, now: datetime, attempts: int, *, limit: int) -> list[RefreshRow]:
        page = (
            select(_R.c.request_id)
            .where(_R.c.space_id == CURRENT_SPACE, _R.c.attempts >= attempts, _R.c.lease_until < now)
            .limit(limit)
            .scalar_subquery()
        )
        rows = await self._db.execute(
            delete(_R).where(_R.c.space_id == CURRENT_SPACE, _R.c.request_id.in_(page)).returning(_R)
        )
        return [_refresh(r) for r in rows.all()]

    async def settle_refreshes(self, request_ids: Sequence[UUID]) -> list[RefreshRow]:
        if not request_ids:
            return []
        rows = await self._db.execute(
            delete(_R)
            .where(_R.c.space_id == CURRENT_SPACE, _R.c.request_id.in_(list(request_ids)))
            .returning(_R)
        )
        return [_refresh(r) for r in rows.all()]

    async def record_scans(
        self, object_id: UUID, owner: UUID | None, scans: Mapping[str, ScanMark], now: datetime
    ) -> None:
        if not scans:
            return
        stmt = upsert(_C).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "content_ref": content_ref(object_id, name, mark.sha256),
                    "origin_handle_id": owner,
                    "state": mark.state,
                    "reason": mark.reason,
                    "decided_by": "rules" if mark.state != "pending" else None,
                    "decided_at": now if mark.state != "pending" else None,
                    "created_at": now,
                }
                for name, mark in scans.items()
            ]
        )
        await self._db.execute(stmt.on_conflict_do_nothing())

    async def pending_scans(self, *, limit: int) -> list[ScanRow]:
        rows = await self._db.execute(
            select(_C.c.content_ref, _C.c.state, _C.c.reason)
            .where(_C.c.space_id == CURRENT_SPACE, _C.c.state == "pending")
            .order_by(_C.c.created_at)
            .limit(limit)
        )
        return [_scan(r.content_ref, r.state, r.reason) for r in rows.all()]

    async def decide_scans(self, decisions: Sequence[ScanRow], by: str, at: datetime) -> None:
        if not decisions:
            return
        body = [{"ref": d.content_ref, "state": d.state, "reason": d.reason} for d in decisions]
        await self._db.execute(
            text(
                f"""
                UPDATE content_scans c SET state = d ->> 'state', reason = d ->> 'reason',
                    decided_by = CAST(:by AS text), decided_at = CAST(:at AS timestamptz)
                FROM jsonb_array_elements(CAST(:decisions AS jsonb)) AS d
                WHERE c.space_id = {_SPACE} AND c.content_ref = d ->> 'ref' AND c.state = 'pending'
                """
            ),
            {"decisions": orjson.dumps(body).decode(), "by": by, "at": at},
        )

    async def release_scan(self, ref: str, by: str, reason: str, at: datetime) -> ScanRow | None:
        rows = await self._db.execute(
            update(_C)
            .where(_C.c.space_id == CURRENT_SPACE, _C.c.content_ref == ref, _C.c.state != "clean")
            .values(state="clean", reason=reason, decided_by=by, decided_at=at)
            .returning(_C.c.content_ref, _C.c.state, _C.c.reason)
        )
        found = rows.first()
        return _scan(found.content_ref, found.state, found.reason) if found else None

    async def field_stamps(self, types: Sequence[str], *, limit: int) -> list[tuple[str, dict[str, Any]]]:
        owned = (
            select(_O.c.type, _O.c.field_meta)
            .where(_O.c.space_id == CURRENT_SPACE, _O.c.type.in_(list(types)))
            .order_by(_O.c.change_seq.desc())
            .limit(limit)
        )
        shared = (
            select(_S.c.type, _S.c.field_meta)
            .where(_S.c.space_id == CURRENT_SPACE, _S.c.type.in_(list(types)))
            .order_by(_S.c.change_seq.desc())
            .limit(limit)
        )
        rows = (
            await self._db.execute(union_all(owned.subquery().select(), shared.subquery().select()))
        ).all()
        return [(r.type, dict(r.field_meta or {})) for r in rows]

    async def values_named(self, rows: Sequence[tuple[UUID, str, int]]) -> dict[tuple[UUID, str, int], Any]:
        """The computed values of these versions that hold one, opened."""
        if not rows:
            return {}
        found = await self._db.execute(
            select(_V.c.object_id, _V.c.name, _V.c.version, _V.c.value_ct, _V.c.dek_version).where(
                _V.c.space_id == CURRENT_SPACE,
                tuple_(_V.c.object_id, _V.c.name, _V.c.version).in_(list(rows)),
                _V.c.value_ct.is_not(None),
            )
        )
        return {(r.object_id, r.name, r.version): await self._open_value(r) for r in found.all()}

    async def values_page(
        self, after: tuple[UUID, str, int] | None, limit: int
    ) -> list[tuple[tuple[UUID, str, int], Any]]:
        """The computed values that hold one, in key order after `after`, opened."""
        query = select(_V.c.object_id, _V.c.name, _V.c.version, _V.c.value_ct, _V.c.dek_version).where(
            _V.c.space_id == CURRENT_SPACE, _V.c.value_ct.is_not(None)
        )
        if after is not None:
            key = tuple_(_V.c.object_id, _V.c.name, _V.c.version)
            query = query.where(key > tuple_(*(literal(v) for v in after)))
        found = await self._db.execute(query.order_by(_V.c.object_id, _V.c.name, _V.c.version).limit(limit))
        return [((r.object_id, r.name, r.version), await self._open_value(r)) for r in found.all()]

    async def rewrite_value(self, object_id: UUID, name: str, version: int, value: Any) -> None:
        """One version of a computed value sealed again as it is now; its stamps keep."""
        row = f"{object_id}/{name}/{version}"
        sealed, dek = await self._seal.seal(
            orjson.dumps(value), table="object_values", row=row, column="value_ct"
        )
        await self._db.execute(
            update(_V)
            .where(
                _V.c.space_id == CURRENT_SPACE,
                _V.c.object_id == object_id,
                _V.c.name == name,
                _V.c.version == version,
            )
            .values(value_ct=sealed, dek_version=dek)
        )

    async def _open_value(self, r: Any) -> Any:
        row = f"{r.object_id}/{r.name}/{r.version}"
        return orjson.loads(
            await self._seal.open_bytes(
                r.value_ct, r.dek_version, table="object_values", row=row, column="value_ct"
            )
        )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.kind is TargetKind.CONVERSATION:
            return []
        handles = list(scope.handle_ids)
        erased: list[ErasedRow] = []
        if handles:
            erased += await delete_where(
                self._db, _C, ("content_ref",), [_C.c.origin_handle_id.in_(handles)], limit
            )
            erased += await delete_where(
                self._db, _W, ("watch_id",), [_W.c.origin_handle_id.in_(handles)], limit - len(erased)
            )
        return erased + await self._orphans(limit - len(erased))

    async def expire_versions(
        self, before: datetime, *, limit: int, keep_objects: Collection[UUID], keep_handles: Collection[UUID]
    ) -> int:
        newer = aliased(_V)
        superseded = exists().where(
            newer.c.space_id == _V.c.space_id,
            newer.c.object_id == _V.c.object_id,
            newer.c.name == _V.c.name,
            newer.c.version > _V.c.version,
        )
        where: list[Any] = [_V.c.recorded_at < before, superseded]
        if keep_objects:
            where.append(_V.c.object_id.not_in(list(keep_objects)))
        if keep_handles:
            owned = select(_O.c.object_id).where(
                _O.c.space_id == CURRENT_SPACE, _O.c.origin_handle_id.in_(list(keep_handles))
            )
            where.append(_V.c.object_id.not_in(owned))
        return len(await delete_where(self._db, _V, ("object_id", "name", "version"), where, limit))

    async def drop_orphans(self, *, limit: int) -> int:
        return len(await self._orphans(limit))

    async def _orphans(self, limit: int) -> list[ErasedRow]:
        erased: list[ErasedRow] = []
        known = union(
            select(_O.c.object_id).where(_O.c.space_id == CURRENT_SPACE),
            select(_S.c.object_id).where(_S.c.space_id == CURRENT_SPACE),
        )
        for table, key, column in (
            (_V, ("object_id", "name", "version"), _V.c.object_id),
            (_T, ("timer_id",), _T.c.object_id),
            (_D, ("input_object_id", "input_field", "dependent_object_id"), _D.c.dependent_object_id),
            (_D, ("input_object_id", "input_field", "dependent_object_id"), _D.c.input_object_id),
            (_R, ("request_id",), _R.c.object_id),
        ):
            erased += await delete_where(self._db, table, key, [column.not_in(known)], limit - len(erased))
        # A content's reference starts with its object's id.
        named = union(
            select(_O.c.object_id.cast(String)).where(_O.c.space_id == CURRENT_SPACE),
            select(_S.c.object_id.cast(String)).where(_S.c.space_id == CURRENT_SPACE),
        )
        owner = func.split_part(_C.c.content_ref, "/", 1)
        erased += await delete_where(
            self._db, _C, ("content_ref",), [owner.not_in(named)], limit - len(erased)
        )
        return erased


def _rebind(bound: Mapping[UUID, Sequence[tuple[UUID, str]]], now: datetime) -> list[Any]:
    """Two parts of a statement: what each derived object no longer names goes, what it names now comes; no
    row is both deleted and inserted."""
    pairs = [[str(d), str(i), name] for d, found in bound.items() for i, name in dict.fromkeys(found)]
    dependents = [str(d) for d in bound]
    unbound = (
        text(
            f"""
            DELETE FROM object_dependencies d
            USING jsonb_array_elements_text(CAST(:dependents AS jsonb)) AS x(id)
            WHERE d.space_id = {_SPACE} AND d.dependent_object_id = CAST(x.id AS uuid)
              AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(CAST(:kept AS jsonb)) AS p
                WHERE CAST(p ->> 0 AS uuid) = d.dependent_object_id
                  AND CAST(p ->> 1 AS uuid) = d.input_object_id AND p ->> 2 = d.input_field)
            RETURNING d.dependent_object_id
            """
        )
        .bindparams(dependents=orjson.dumps(dependents).decode(), kept=orjson.dumps(pairs).decode())
        .columns(sql_column("dependent_object_id", Uuid))
        .cte("unbound")
    )
    inserted = (
        text(
            f"""
            INSERT INTO object_dependencies (space_id, input_object_id, input_field, dependent_object_id,
                                             created_at)
            SELECT {_SPACE}, CAST(p ->> 1 AS uuid), p ->> 2, CAST(p ->> 0 AS uuid),
                   CAST(:bound_at AS timestamptz)
            FROM jsonb_array_elements(CAST(:bound AS jsonb)) AS p
            ON CONFLICT DO NOTHING
            RETURNING dependent_object_id
            """
        )
        .bindparams(bound=orjson.dumps(pairs).decode(), bound_at=now)
        .columns(sql_column("dependent_object_id", Uuid))
        .cte("bound")
    )
    return [unbound, inserted] if pairs else [unbound]


def _timers(writes: Mapping[UUID, Sequence[TimerWrite]], now: datetime) -> Any:
    """Arms, moves or cancels the timers; a timer the sweep already fired keeps its firing."""
    rows = [
        {
            "space_id": CURRENT_SPACE,
            "timer_id": w.timer_id,
            "object_id": object_id,
            "name": w.name,
            "once_per_key": w.once_per_key,
            "due_at": w.due_at,
            "state": w.state,
            "value_version": w.value_version,
            "fired_at": w.fired_at,
            "supersedes_firing_id": w.supersedes_firing_id,
            "created_at": now,
        }
        for object_id, found in writes.items()
        for w in found
    ]
    if not rows:
        return None
    stmt = upsert(_T).values(rows)
    return stmt.on_conflict_do_update(
        index_elements=["space_id", "timer_id"],
        set_={
            "due_at": stmt.excluded.due_at,
            "state": stmt.excluded.state,
            "value_version": stmt.excluded.value_version,
            "once_per_key": stmt.excluded.once_per_key,
        },
        where=_T.c.state == "armed",
    )


_SHARED = (
    _S.c.object_id,
    _S.c.type,
    _S.c.namespace,
    _S.c.external_id,
    _S.c.variant,
    _S.c.state_ct,
    _S.c.field_meta,
    _S.c.hot_until,
    _S.c.last_referenced_at,
    _S.c.watch_count,
    _S.c.derived_status,
)


def _watch(r: Any) -> WatchRow:
    return WatchRow(
        r.watch_id,
        r.object_id,
        r.origin_handle_id,
        r.condition,
        r.consent_event_key,
        r.expires_at,
        r.last_fired_at,
    )


def _shared(r: Any) -> SharedRow:
    return SharedRow(
        r.object_id,
        SharedKey(r.type, r.namespace, r.external_id, r.variant),
        dict(r.state_ct),
        dict(r.field_meta),
        r.hot_until,
        r.last_referenced_at,
        r.watch_count,
        r.derived_status,
    )


def _refresh(r: Any) -> RefreshRow:
    return RefreshRow(
        r.request_id,
        r.object_id,
        SharedKey(r.type, r.namespace, r.external_id, r.variant),
        r.reason,
        r.priority,
        r.budget_units,
        r.requested_at,
        r.reservation,
        r.lease_until,
    )


def _scan(ref: str, state: str, reason: str | None) -> ScanRow:
    object_id, field, digest = ref.split("/", 2)
    return ScanRow(ref, UUID(object_id), field, digest, state, reason)
