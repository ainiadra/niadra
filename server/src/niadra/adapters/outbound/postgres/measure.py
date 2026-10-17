"""PostgreSQL implementation of `MeasureRepository`.

Nothing in these tables is encrypted: they hold ids, counters and flags, never customer
content (19.6).
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Table,
    delete,
    func,
    literal,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import changed_rows, delete_where
from niadra.adapters.outbound.postgres.schema.operate import (
    ContextTrim as ContextTrimRow,
)
from niadra.adapters.outbound.postgres.schema.operate import (
    ContextUse,
    ContextUseDaily,
    ContextUseGuardDaily,
    ContextUseSlotDaily,
    ContextUseUnits,
    ContextUseViewDaily,
    RetrievalWeightRun,
    ViewBudgetRun,
)
from niadra.adapters.outbound.postgres.schema.operate import MeasuredSession as MeasuredSessionRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.exported import export_row
from niadra.domain.measure.model import (
    COUNTER_FIELDS,
    ContextUseRow,
    DailyCounters,
    DailyKey,
    DailyRow,
    MeasuredSession,
    RecontactScope,
    TransferState,
)
from niadra.domain.measure.slots import (
    SLOT_COUNTER_FIELDS,
    GuardCounters,
    GuardDailyKey,
    GuardDailyRow,
    SlotCounters,
    SlotDailyKey,
    SlotDailyRow,
)
from niadra.domain.measure.trim import ContextTrim, TrimUnit, UnitCounters, UnitKey
from niadra.domain.measure.views import (
    VIEW_USE_FIELDS,
    ViewBudgets,
    ViewUseCounters,
    ViewUseKey,
    ViewUseRow,
)
from niadra.domain.measure.weights import RetrievalWeights, WeightOutcome
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind

CONTEXT_USE = cast(Table, ContextUse.__table__)
DAILY = cast(Table, ContextUseDaily.__table__)
UNITS = cast(Table, ContextUseUnits.__table__)
TRIMS = cast(Table, ContextTrimRow.__table__)
SESSIONS = cast(Table, MeasuredSessionRow.__table__)
SLOT_DAILY = cast(Table, ContextUseSlotDaily.__table__)
WEIGHTS = cast(Table, RetrievalWeightRun.__table__)
GUARD_DAILY = cast(Table, ContextUseGuardDaily.__table__)
VIEW_DAILY = cast(Table, ContextUseViewDaily.__table__)
VIEW_RUNS = cast(Table, ViewBudgetRun.__table__)
_VIEW_DAILY_KEY = ("day", "source_id", "target", "view", "arm", "section")
_GUARD_DAILY_KEY = ("day", "source_id", "value_type", "arm")
_SLOT_DAILY_KEY = ("day", "source_id", "channel", "view", "experiment_group", "slot_channel")
_UNIT_KEY = ("day", "source_id", "view", "unit")
_UNIT_COUNTERS = ("deliveries", "used", "missed")
_ROW_KEY = ("conversation_key", "manifest_hash", "source_id", "measure_version")
_DAILY_KEY = ("day", "source_id", "channel", "view", "experiment_group")


class PgMeasureRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def insert_rows(self, rows: Sequence[ContextUseRow]) -> list[ContextUseRow]:
        if not rows:
            return []
        stmt = (
            insert(CONTEXT_USE)
            .values([{"space_id": CURRENT_SPACE, **_row_values(r)} for r in rows])
            .on_conflict_do_nothing(index_elements=["space_id", *_ROW_KEY])
            .returning(*(CONTEXT_USE.c[k] for k in _ROW_KEY))
        )
        inserted = {tuple(r) for r in (await self._db.execute(stmt)).all()}
        return [r for r in rows if _row_key(r) in inserted]

    async def rows_for_session(self, session_id: UUID) -> list[ContextUseRow]:
        return await self._rows(CONTEXT_USE.c.session_id == session_id)

    async def rows_for_conversation(self, conversation_key: str) -> list[ContextUseRow]:
        return await self._rows(CONTEXT_USE.c.conversation_key == conversation_key)

    async def delete_rows(self, rows: Sequence[ContextUseRow]) -> None:
        if rows:
            keys = tuple_(*(CONTEXT_USE.c[k] for k in _ROW_KEY))
            await self._db.execute(
                delete(CONTEXT_USE).where(
                    CONTEXT_USE.c.space_id == CURRENT_SPACE, keys.in_([_row_key(r) for r in rows])
                )
            )

    async def mark_transfer(self, session_id: UUID, source_id: UUID, unread: bool) -> None:
        keys = tuple_(*(CONTEXT_USE.c[k] for k in _ROW_KEY))
        await self._db.execute(
            update(CONTEXT_USE)
            .where(CONTEXT_USE.c.space_id == CURRENT_SPACE, keys.in_(_latest(session_id, source_id)))
            .values(transfer_unread=unread)
        )

    async def get_session(self, session_id: UUID) -> MeasuredSession | None:
        row = (
            (
                await self._db.execute(
                    select(SESSIONS).where(
                        SESSIONS.c.space_id == CURRENT_SPACE, SESSIONS.c.session_id == session_id
                    )
                )
            )
            .mappings()
            .first()
        )
        return _session(row) if row else None

    async def put_session(self, session: MeasuredSession) -> None:
        values = _session_values(session)
        kept = {"transfer_state", "recontact_scope", "session_id"}
        stmt = insert(SESSIONS).values(space_id=CURRENT_SPACE, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", "session_id"],
            set_={k: stmt.excluded[k] for k in values if k not in kept},
        )
        await self._db.execute(stmt)

    async def set_transfer_state(self, session_id: UUID, state: TransferState) -> bool:
        s = SESSIONS.c
        stmt = (
            update(SESSIONS)
            .where(s.space_id == CURRENT_SPACE, s.session_id == session_id, s.transfer_state.is_(None))
            .values(transfer_state=state.value)
            .returning(s.session_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def mark_recontact(self, session_id: UUID, scope: RecontactScope) -> bool:
        s = SESSIONS.c
        replaceable: list[ColumnElement[bool]] = [s.recontact_scope.is_(None)]
        if scope is RecontactScope.CATEGORY:
            replaceable.append(s.recontact_scope == RecontactScope.GENERAL.value)
        stmt = (
            update(SESSIONS)
            .where(s.space_id == CURRENT_SPACE, s.session_id == session_id, or_(*replaceable))
            .values(recontact_scope=scope.value)
            .returning(s.session_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def measured_sessions(
        self,
        *,
        source_ids: Sequence[UUID] | None,
        since: datetime,
        until: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[MeasuredSession]:
        s = SESSIONS.c
        stmt = select(SESSIONS).where(s.space_id == CURRENT_SPACE, s.ended_at >= since, s.ended_at < until)
        if source_ids is not None:
            stmt = stmt.where(s.source_id.in_(list(source_ids)))
        if before is not None:
            stmt = stmt.where(
                tuple_(s.ended_at, s.session_id) < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(s.ended_at.desc(), s.session_id.desc()).limit(limit)
        return [_session(r) for r in (await self._db.execute(stmt)).mappings()]

    async def sessions_ended_between(
        self, profile_id: UUID, since: datetime, until: datetime
    ) -> list[MeasuredSession]:
        s = SESSIONS.c
        stmt = (
            select(SESSIONS)
            .where(
                s.space_id == CURRENT_SPACE,
                s.profile_id == profile_id,
                s.ended_at >= since,
                s.ended_at <= until,
            )
            .order_by(s.ended_at)
        )
        return [_session(r) for r in (await self._db.execute(stmt)).mappings()]

    async def add_daily(self, key: DailyKey, counters: DailyCounters) -> None:
        values = counters.as_dict()
        stmt = insert(DAILY).values(
            space_id=CURRENT_SPACE,
            day=key.day,
            source_id=key.source_id,
            channel=key.channel,
            view=key.view,
            experiment_group=key.experiment_group,
            **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", *_DAILY_KEY],
            set_={name: DAILY.c[name] + stmt.excluded[name] for name in values},
        )
        await self._db.execute(stmt)

    async def daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[DailyRow]:
        d = DAILY.c
        stmt = select(DAILY).where(d.space_id == CURRENT_SPACE, d.day >= since, d.day <= until)
        if source_ids is not None:
            stmt = stmt.where(d.source_id.in_(list(source_ids)))
        rows = (await self._db.execute(stmt.order_by(*(d[k] for k in _DAILY_KEY)))).mappings()
        return [
            DailyRow(
                DailyKey(r["day"], r["source_id"], r["channel"], r["view"], r["experiment_group"]),
                DailyCounters(**{name: int(r[name]) for name in COUNTER_FIELDS}),
            )
            for r in rows
        ]

    async def add_units(self, key: UnitKey, counters: UnitCounters) -> None:
        values = {name: getattr(counters, name) for name in _UNIT_COUNTERS}
        stmt = insert(UNITS).values(
            space_id=CURRENT_SPACE,
            day=key.day,
            source_id=key.source_id,
            view=key.view,
            unit=key.unit,
            **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", *_UNIT_KEY],
            set_={name: UNITS.c[name] + stmt.excluded[name] for name in values},
        )
        await self._db.execute(stmt)

    async def unit_totals(
        self, source_id: UUID, view: str, since: date, until: date
    ) -> list[tuple[str, UnitCounters]]:
        u = UNITS.c
        stmt = (
            select(u.unit, *(func.sum(u[name]).label(name) for name in _UNIT_COUNTERS))
            .where(
                u.space_id == CURRENT_SPACE,
                u.source_id == source_id,
                u.view == view,
                u.day >= since,
                u.day <= until,
            )
            .group_by(u.unit)
            .order_by(u.unit)
        )
        return [
            (r.unit, UnitCounters(int(r.deliveries), int(r.used), int(r.missed)))
            for r in (await self._db.execute(stmt)).all()
        ]

    async def put_trim(self, trim: ContextTrim) -> None:
        values = {
            "units": [{"unit": u.unit, "deliveries": u.deliveries} for u in trim.units],
            "derived_at": trim.derived_at,
            "window_days": trim.window_days,
            "min_deliveries": trim.min_deliveries,
        }
        stmt = insert(TRIMS).values(
            space_id=CURRENT_SPACE, source_id=trim.source_id, view=trim.view, **values
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "source_id", "view"], set_={k: stmt.excluded[k] for k in values}
            )
        )

    async def trims(self) -> list[ContextTrim]:
        t = TRIMS.c
        rows = await self._db.execute(
            select(TRIMS).where(t.space_id == CURRENT_SPACE).order_by(t.source_id, t.view)
        )
        return [
            ContextTrim(
                source_id=r["source_id"],
                view=r["view"],
                units=tuple(TrimUnit(str(u["unit"]), int(u["deliveries"])) for u in r["units"]),
                derived_at=r["derived_at"],
                window_days=r["window_days"],
                min_deliveries=r["min_deliveries"],
            )
            for r in rows.mappings()
        ]

    async def add_slot_daily(self, key: SlotDailyKey, counters: SlotCounters) -> None:
        values = counters.as_dict()
        stmt = insert(SLOT_DAILY).values(
            space_id=CURRENT_SPACE, **{name: getattr(key, name) for name in _SLOT_DAILY_KEY}, **values
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", *_SLOT_DAILY_KEY],
            set_={name: SLOT_DAILY.c[name] + stmt.excluded[name] for name in values},
        )
        await self._db.execute(stmt)

    async def slot_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[SlotDailyRow]:
        d = SLOT_DAILY.c
        stmt = select(SLOT_DAILY).where(d.space_id == CURRENT_SPACE, d.day >= since, d.day <= until)
        if source_ids is not None:
            stmt = stmt.where(d.source_id.in_(list(source_ids)))
        rows = (await self._db.execute(stmt.order_by(*(d[k] for k in _SLOT_DAILY_KEY)))).mappings()
        return [
            SlotDailyRow(
                SlotDailyKey(**{name: r[name] for name in _SLOT_DAILY_KEY}),
                SlotCounters(**{name: int(r[name]) for name in SLOT_COUNTER_FIELDS}),
            )
            for r in rows
        ]

    async def add_guard_daily(self, key: GuardDailyKey, counters: GuardCounters) -> None:
        values = {"slots": counters.slots, "contradicted": counters.contradicted}
        stmt = insert(GUARD_DAILY).values(
            space_id=CURRENT_SPACE, **{name: getattr(key, name) for name in _GUARD_DAILY_KEY}, **values
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", *_GUARD_DAILY_KEY],
            set_={name: GUARD_DAILY.c[name] + stmt.excluded[name] for name in values},
        )
        await self._db.execute(stmt)

    async def guard_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[GuardDailyRow]:
        g = GUARD_DAILY.c
        stmt = select(GUARD_DAILY).where(g.space_id == CURRENT_SPACE, g.day >= since, g.day <= until)
        if source_ids is not None:
            stmt = stmt.where(g.source_id.in_(list(source_ids)))
        rows = (await self._db.execute(stmt.order_by(*(g[k] for k in _GUARD_DAILY_KEY)))).mappings()
        return [
            GuardDailyRow(
                GuardDailyKey(**{name: r[name] for name in _GUARD_DAILY_KEY}),
                GuardCounters(int(r["slots"]), int(r["contradicted"])),
            )
            for r in rows
        ]

    async def contradicted_value_types(self, since: date) -> frozenset[str]:
        g = GUARD_DAILY.c
        stmt = (
            select(g.value_type)
            .where(g.space_id == CURRENT_SPACE, g.day >= since)
            .group_by(g.value_type)
            .having(func.sum(g.contradicted) > 0)
        )
        return frozenset(str(v) for v in (await self._db.execute(stmt)).scalars())

    async def retrieval_weights(self) -> RetrievalWeights | None:
        runs = await self.weight_runs(1)
        return runs[0] if runs else None

    async def weight_runs(self, limit: int) -> list[RetrievalWeights]:
        w = WEIGHTS.c
        stmt = select(WEIGHTS).where(w.space_id == CURRENT_SPACE).order_by(w.run_id.desc()).limit(limit)
        return [
            RetrievalWeights(
                run_id=r["run_id"],
                day=r["day"],
                computed_at=r["computed_at"],
                version=r["version"],
                outcome=WeightOutcome(r["outcome"]),
                weights={
                    str(view): {str(c): float(v) for c, v in channels.items()}
                    for view, channels in (r["weights"] or {}).items()
                },
                evidence=dict(r["evidence"] or {}),
                rules=r["rules"],
            )
            for r in (await self._db.execute(stmt)).mappings()
        ]

    async def put_retrieval_weights(self, run: RetrievalWeights) -> None:
        await self._db.execute(
            insert(WEIGHTS).values(
                space_id=CURRENT_SPACE,
                run_id=run.run_id,
                day=run.day,
                computed_at=run.computed_at,
                version=run.version,
                outcome=run.outcome.value,
                weights={view: dict(channels) for view, channels in run.weights.items()},
                evidence=dict(run.evidence),
                rules=run.rules,
            )
        )

    async def add_view_use(self, key: ViewUseKey, counters: ViewUseCounters) -> None:
        values = counters.as_dict()
        stmt = insert(VIEW_DAILY).values(
            space_id=CURRENT_SPACE, **{name: getattr(key, name) for name in _VIEW_DAILY_KEY}, **values
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", *_VIEW_DAILY_KEY],
            set_={name: VIEW_DAILY.c[name] + stmt.excluded[name] for name in values},
        )
        await self._db.execute(stmt)

    async def view_use_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ViewUseRow]:
        v = VIEW_DAILY.c
        stmt = select(VIEW_DAILY).where(v.space_id == CURRENT_SPACE, v.day >= since, v.day <= until)
        if source_ids is not None:
            stmt = stmt.where(v.source_id.in_(list(source_ids)))
        rows = (await self._db.execute(stmt.order_by(*(v[k] for k in _VIEW_DAILY_KEY)))).mappings()
        return [
            ViewUseRow(
                ViewUseKey(**{name: r[name] for name in _VIEW_DAILY_KEY}),
                ViewUseCounters(**{name: int(r[name]) for name in VIEW_USE_FIELDS}),
            )
            for r in rows
        ]

    async def view_budgets(self) -> ViewBudgets | None:
        runs = await self.view_budget_runs(1)
        return runs[0] if runs else None

    async def view_budget_runs(self, limit: int) -> list[ViewBudgets]:
        v = VIEW_RUNS.c
        stmt = select(VIEW_RUNS).where(v.space_id == CURRENT_SPACE).order_by(v.run_id.desc()).limit(limit)
        return [
            ViewBudgets(
                run_id=r["run_id"],
                day=r["day"],
                computed_at=r["computed_at"],
                version=r["version"],
                outcome=WeightOutcome(r["outcome"]),
                factors={
                    str(reader): {str(s): float(f) for s, f in sections.items()}
                    for reader, sections in (r["factors"] or {}).items()
                },
                evidence=dict(r["evidence"] or {}),
                rules=r["rules"],
            )
            for r in (await self._db.execute(stmt)).mappings()
        ]

    async def put_view_budgets(self, run: ViewBudgets) -> None:
        await self._db.execute(
            insert(VIEW_RUNS).values(
                space_id=CURRENT_SPACE,
                run_id=run.run_id,
                day=run.day,
                computed_at=run.computed_at,
                version=run.version,
                outcome=run.outcome.value,
                factors={reader: dict(sections) for reader, sections in run.factors.items()},
                evidence=dict(run.evidence),
                rules=run.rules,
            )
        )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return []
        if scope.kind is TargetKind.CONVERSATION:
            rows = [CONTEXT_USE.c.conversation_key == scope.conversation_id]
            sessions = [SESSIONS.c.conversation_key == scope.conversation_id]
        elif scope.profile_id is not None:
            rows = [CONTEXT_USE.c.profile_id == scope.profile_id]
            sessions = [SESSIONS.c.profile_id == scope.profile_id]
        else:
            return []
        erased = await delete_where(self._db, CONTEXT_USE, _ROW_KEY, rows, limit)
        return erased + await delete_where(self._db, SESSIONS, ("session_id",), sessions, limit - len(erased))

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "context_use":
            raise ValueError(f"measure does not export {table}")
        rows = await changed_rows(self._db, CONTEXT_USE, after_seq=after_seq, upto_seq=upto_seq, limit=limit)
        return [export_row(_row(r._mapping), r.change_seq) for r in rows]

    async def _rows(self, condition: Any) -> list[ContextUseRow]:
        c = CONTEXT_USE.c
        stmt = (
            select(CONTEXT_USE)
            .where(c.space_id == CURRENT_SPACE, condition)
            .order_by(c.measured_at, c.manifest_hash, c.source_id, c.measure_version)
        )
        return [_row(r) for r in (await self._db.execute(stmt)).mappings()]


def _latest(session_id: UUID, source_id: UUID) -> Any:
    c = CONTEXT_USE.c
    return (
        select(*(c[k] for k in _ROW_KEY))
        .where(c.space_id == CURRENT_SPACE, c.session_id == session_id, c.source_id == source_id)
        .order_by(c.measured_at.desc(), c.manifest_hash.desc())
        .limit(1)
    )


def _row_key(row: ContextUseRow) -> tuple[str, str, UUID, str]:
    return (row.conversation_key, row.manifest_hash, row.source_id, row.measure_version)


def _row_values(row: ContextUseRow) -> dict[str, Any]:
    return {
        "conversation_key": row.conversation_key,
        "manifest_hash": row.manifest_hash,
        "source_id": row.source_id,
        "measure_version": row.measure_version,
        "session_id": row.session_id,
        "profile_id": row.profile_id,
        "channel": row.channel,
        "view": row.view,
        "experiment_group": row.experiment_group,
        "items": list(row.items),
        "questions": row.questions,
        "repeated": row.repeated,
        "used": row.used,
        "contradicted": row.contradicted,
        "late": row.late,
        "transfer_unread": row.transfer_unread,
        "not_measured_reason": row.not_measured_reason,
        "measured_at": row.measured_at,
        "tokens_saved": row.tokens_saved,
        "slots": list(row.slots) if row.slots else None,
        "object_rows": row.object_rows,
        "object_rows_used": row.object_rows_used,
        "instruction_violations": row.instruction_violations,
    }


def _row(r: Any) -> ContextUseRow:
    return ContextUseRow(
        conversation_key=r["conversation_key"],
        manifest_hash=r["manifest_hash"],
        source_id=r["source_id"],
        measure_version=r["measure_version"],
        session_id=r["session_id"],
        profile_id=r["profile_id"],
        channel=r["channel"],
        view=r["view"],
        experiment_group=r["experiment_group"] or "",
        items=tuple(r["items"]),
        questions=r["questions"],
        repeated=r["repeated"],
        used=r["used"],
        contradicted=r["contradicted"],
        late=r["late"],
        measured_at=r["measured_at"],
        transfer_unread=r["transfer_unread"],
        not_measured_reason=r["not_measured_reason"],
        tokens_saved=r["tokens_saved"],
        slots=tuple(r["slots"] or ()),
        object_rows=r["object_rows"],
        object_rows_used=r["object_rows_used"],
        instruction_violations=r["instruction_violations"],
    )


def _session_values(s: MeasuredSession) -> dict[str, Any]:
    return {
        "session_id": s.session_id,
        "measure_version": s.measure_version,
        "conversation_key": s.conversation_key,
        "source_id": s.source_id,
        "channel": s.channel,
        "profile_id": s.profile_id,
        "experiment_group": s.experiment_group,
        "day": s.day,
        "ended_at": s.ended_at,
        "measured_at": s.measured_at,
        "no_context": s.no_context,
        "normalized_category": s.normalized_category,
        "initiated_by": s.initiated_by,
        "transfer_state": s.transfer_state.value if s.transfer_state else None,
        "recontact_scope": s.recontact_scope.value if s.recontact_scope else None,
    }


def _session(r: Any) -> MeasuredSession:
    return MeasuredSession(
        session_id=r["session_id"],
        measure_version=r["measure_version"],
        conversation_key=r["conversation_key"],
        source_id=r["source_id"],
        channel=r["channel"],
        profile_id=r["profile_id"],
        experiment_group=r["experiment_group"],
        day=r["day"],
        ended_at=r["ended_at"],
        measured_at=r["measured_at"],
        no_context=r["no_context"],
        normalized_category=r["normalized_category"],
        initiated_by=r["initiated_by"],
        transfer_state=TransferState(r["transfer_state"]) if r["transfer_state"] else None,
        recontact_scope=RecontactScope(r["recontact_scope"]) if r["recontact_scope"] else None,
    )
