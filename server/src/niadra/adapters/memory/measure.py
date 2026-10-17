"""In-memory `MeasureRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.change_seq import ChangeSeq
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.exported import export_row
from niadra.domain.measure.model import (
    ContextUseRow,
    DailyCounters,
    DailyKey,
    DailyRow,
    MeasuredSession,
    RecontactScope,
    TransferState,
)
from niadra.domain.measure.slots import (
    GuardCounters,
    GuardDailyKey,
    GuardDailyRow,
    SlotCounters,
    SlotDailyKey,
    SlotDailyRow,
)
from niadra.domain.measure.trim import ContextTrim, UnitCounters, UnitKey
from niadra.domain.measure.views import ViewBudgets, ViewUseCounters, ViewUseKey, ViewUseRow
from niadra.domain.measure.weights import RetrievalWeights
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind

type RowKey = tuple[str, str, UUID, str]


def _key(row: ContextUseRow) -> RowKey:
    return (row.conversation_key, row.manifest_hash, row.source_id, row.measure_version)


class InMemoryMeasureRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("measure")
        self._rows: dict[RowKey, ContextUseRow] = self._s.setdefault("rows", {})
        self._sessions: dict[UUID, MeasuredSession] = self._s.setdefault("sessions", {})
        self._daily: dict[DailyKey, DailyCounters] = self._s.setdefault("daily", {})
        self._units: dict[UnitKey, UnitCounters] = self._s.setdefault("units", {})
        self._trims: dict[tuple[UUID, str], ContextTrim] = self._s.setdefault("trims", {})
        self._slot_daily: dict[SlotDailyKey, SlotCounters] = self._s.setdefault("slot_daily", {})
        self._weights: list[RetrievalWeights] = self._s.setdefault("weights", [])
        self._guard_daily: dict[GuardDailyKey, GuardCounters] = self._s.setdefault("guard_daily", {})
        self._view_daily: dict[ViewUseKey, ViewUseCounters] = self._s.setdefault("view_daily", {})
        self._views: list[ViewBudgets] = self._s.setdefault("views", [])
        self._seq = ChangeSeq(store, "measure")

    async def insert_rows(self, rows: Sequence[ContextUseRow]) -> list[ContextUseRow]:
        inserted = []
        for row in rows:
            if _key(row) not in self._rows:
                self._rows[_key(row)] = row
                self._seq.touch("context_use", _key(row))
                inserted.append(row)
        return inserted

    async def rows_for_session(self, session_id: UUID) -> list[ContextUseRow]:
        return sorted((r for r in self._rows.values() if r.session_id == session_id), key=_key)

    async def rows_for_conversation(self, conversation_key: str) -> list[ContextUseRow]:
        rows = (r for r in self._rows.values() if r.conversation_key == conversation_key)
        return sorted(rows, key=lambda r: (r.measured_at, r.manifest_hash, str(r.source_id)))

    async def delete_rows(self, rows: Sequence[ContextUseRow]) -> None:
        for row in rows:
            self._rows.pop(_key(row), None)
            self._seq.forget("context_use", _key(row))

    async def mark_transfer(self, session_id: UUID, source_id: UUID, unread: bool) -> None:
        mine = [r for r in self._rows.values() if r.session_id == session_id and r.source_id == source_id]
        if mine:
            latest = max(mine, key=lambda r: (r.measured_at, r.manifest_hash))
            self._rows[_key(latest)] = replace(latest, transfer_unread=unread)
            self._seq.touch("context_use", _key(latest))

    async def get_session(self, session_id: UUID) -> MeasuredSession | None:
        return self._sessions.get(session_id)

    async def put_session(self, session: MeasuredSession) -> None:
        current = self._sessions.get(session.session_id)
        if current is not None:
            session = replace(
                session, transfer_state=current.transfer_state, recontact_scope=current.recontact_scope
            )
        self._sessions[session.session_id] = session

    async def set_transfer_state(self, session_id: UUID, state: TransferState) -> bool:
        current = self._sessions.get(session_id)
        if current is None or current.transfer_state is not None:
            return False
        self._sessions[session_id] = replace(current, transfer_state=state)
        return True

    async def mark_recontact(self, session_id: UUID, scope: RecontactScope) -> bool:
        current = self._sessions.get(session_id)
        if current is None or current.recontact_scope in (scope, RecontactScope.CATEGORY):
            return False
        self._sessions[session_id] = replace(current, recontact_scope=scope)
        return True

    async def measured_sessions(
        self,
        *,
        source_ids: Sequence[UUID] | None,
        since: datetime,
        until: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[MeasuredSession]:
        found = [
            s
            for s in self._sessions.values()
            if since <= s.ended_at < until
            and (source_ids is None or s.source_id in source_ids)
            and (before is None or (s.ended_at, s.session_id) < before)
        ]
        return sorted(found, key=lambda s: (s.ended_at, s.session_id), reverse=True)[:limit]

    async def sessions_ended_between(
        self, profile_id: UUID, since: datetime, until: datetime
    ) -> list[MeasuredSession]:
        found = (
            s for s in self._sessions.values() if s.profile_id == profile_id and since <= s.ended_at <= until
        )
        return sorted(found, key=lambda s: s.ended_at)

    async def add_daily(self, key: DailyKey, counters: DailyCounters) -> None:
        self._daily.setdefault(key, DailyCounters()).add(counters)

    async def daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[DailyRow]:
        return [
            DailyRow(key, replace(counters))
            for key, counters in sorted(self._daily.items(), key=lambda kv: (kv[0].day, str(kv[0].source_id)))
            if since <= key.day <= until and (source_ids is None or key.source_id in source_ids)
        ]

    async def add_units(self, key: UnitKey, counters: UnitCounters) -> None:
        self._units.setdefault(key, UnitCounters()).add(counters)

    async def unit_totals(
        self, source_id: UUID, view: str, since: date, until: date
    ) -> list[tuple[str, UnitCounters]]:
        return [
            (key.unit, replace(counters))
            for key, counters in sorted(self._units.items(), key=lambda kv: (kv[0].day, kv[0].unit))
            if key.source_id == source_id and key.view == view and since <= key.day <= until
        ]

    async def put_trim(self, trim: ContextTrim) -> None:
        self._trims[(trim.source_id, trim.view)] = trim

    async def trims(self) -> list[ContextTrim]:
        return [self._trims[k] for k in sorted(self._trims, key=lambda k: (str(k[0]), k[1]))]

    async def add_slot_daily(self, key: SlotDailyKey, counters: SlotCounters) -> None:
        self._slot_daily.setdefault(key, SlotCounters()).add(counters)

    async def slot_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[SlotDailyRow]:
        return [
            SlotDailyRow(key, replace(counters))
            for key, counters in sorted(
                self._slot_daily.items(), key=lambda kv: (kv[0].day, str(kv[0].source_id), kv[0].slot_channel)
            )
            if since <= key.day <= until and (source_ids is None or key.source_id in source_ids)
        ]

    async def add_guard_daily(self, key: GuardDailyKey, counters: GuardCounters) -> None:
        self._guard_daily.setdefault(key, GuardCounters()).add(counters)

    async def guard_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[GuardDailyRow]:
        return [
            GuardDailyRow(key, replace(counters))
            for key, counters in sorted(
                self._guard_daily.items(),
                key=lambda kv: (kv[0].day, str(kv[0].source_id), kv[0].value_type, kv[0].arm),
            )
            if since <= key.day <= until and (source_ids is None or key.source_id in source_ids)
        ]

    async def contradicted_value_types(self, since: date) -> frozenset[str]:
        totals: dict[str, int] = {}
        for key, counters in self._guard_daily.items():
            if key.day >= since:
                totals[key.value_type] = totals.get(key.value_type, 0) + counters.contradicted
        return frozenset(t for t, n in totals.items() if n > 0)

    async def retrieval_weights(self) -> RetrievalWeights | None:
        return max(self._weights, key=lambda r: r.run_id, default=None)

    async def weight_runs(self, limit: int) -> list[RetrievalWeights]:
        return sorted(self._weights, key=lambda r: r.run_id, reverse=True)[:limit]

    async def put_retrieval_weights(self, run: RetrievalWeights) -> None:
        self._weights.append(run)

    async def add_view_use(self, key: ViewUseKey, counters: ViewUseCounters) -> None:
        self._view_daily.setdefault(key, ViewUseCounters()).add(counters)

    async def view_use_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ViewUseRow]:
        return [
            ViewUseRow(key, replace(counters))
            for key, counters in sorted(
                self._view_daily.items(),
                key=lambda kv: (
                    kv[0].day,
                    str(kv[0].source_id),
                    kv[0].target,
                    kv[0].view,
                    kv[0].arm,
                    kv[0].section,
                ),
            )
            if since <= key.day <= until and (source_ids is None or key.source_id in source_ids)
        ]

    async def view_budgets(self) -> ViewBudgets | None:
        return max(self._views, key=lambda r: r.run_id, default=None)

    async def view_budget_runs(self, limit: int) -> list[ViewBudgets]:
        return sorted(self._views, key=lambda r: r.run_id, reverse=True)[:limit]

    async def put_view_budgets(self, run: ViewBudgets) -> None:
        self._views.append(run)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or (scope.kind is not TargetKind.CONVERSATION and scope.profile_id is None):
            return []
        if scope.kind is TargetKind.CONVERSATION:
            rows = [k for k, r in self._rows.items() if r.conversation_key == scope.conversation_id]
            sessions = [s for s, m in self._sessions.items() if m.conversation_key == scope.conversation_id]
        else:
            rows = [k for k, r in self._rows.items() if r.profile_id == scope.profile_id]
            sessions = [s for s, m in self._sessions.items() if m.profile_id == scope.profile_id]
        erased = []
        for key in sorted(rows, key=str)[:limit]:
            del self._rows[key]
            names = ("conversation_key", "manifest_hash", "source_id", "measure_version")
            keys = {n: str(v) for n, v in zip(names, key, strict=True)}
            erased.append(ErasedRow("context_use", keys, change_seq=self._seq.forget("context_use", key)))
        for session_id in sorted(sessions)[: max(limit - len(erased), 0)]:
            del self._sessions[session_id]
            erased.append(ErasedRow("measured_sessions", {"session_id": str(session_id)}))
        return erased

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table != "context_use":
            raise ValueError(f"measure does not export {table}")
        return [
            export_row(self._rows[k], seq) for seq, k in self._seq.between(table, after_seq, upto_seq, limit)
        ]
