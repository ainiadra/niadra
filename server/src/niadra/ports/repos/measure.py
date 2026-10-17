"""Context-use rows and daily aggregates (19)."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

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
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class MeasureRepository(Protocol):
    """Owned by the `measure` module; no other module reads or writes these tables (4.3)."""

    async def insert_rows(self, rows: Sequence[ContextUseRow]) -> list[ContextUseRow]:
        """Inserts with UNIQUE idempotency and returns only the rows that were new (19.3)."""
        ...

    async def rows_for_session(self, session_id: UUID) -> list[ContextUseRow]: ...

    async def rows_for_conversation(self, conversation_key: str) -> list[ContextUseRow]: ...

    async def delete_rows(self, rows: Sequence[ContextUseRow]) -> None: ...

    async def mark_transfer(self, session_id: UUID, source_id: UUID, unread: bool) -> None:
        """Sets `transfer_unread` on the session's latest row of the source that handed off."""
        ...

    async def get_session(self, session_id: UUID) -> MeasuredSession | None: ...

    async def put_session(self, session: MeasuredSession) -> None:
        """Inserts or replaces the session, keeping its transfer state and recontact mark."""
        ...

    async def set_transfer_state(self, session_id: UUID, state: TransferState) -> bool:
        """True only the first time, so the daily transfer counters move once."""
        ...

    async def mark_recontact(self, session_id: UUID, scope: RecontactScope) -> bool:
        """Marks an earlier session; a `category` mark may replace a `general` one, never the reverse."""
        ...

    async def measured_sessions(
        self,
        *,
        source_ids: Sequence[UUID] | None,
        since: datetime,
        until: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[MeasuredSession]:
        """Measured sessions of the sources (all when None) that ended in [since, until), newest first,
        after the keyset `before` (ended_at, session_id)."""
        ...

    async def sessions_ended_between(
        self, profile_id: UUID, since: datetime, until: datetime
    ) -> list[MeasuredSession]: ...

    async def add_daily(self, key: DailyKey, counters: DailyCounters) -> None:
        """Adds (or, with negative counters, subtracts) to one `context_use_daily` row."""
        ...

    async def daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[DailyRow]: ...

    async def add_units(self, key: UnitKey, counters: UnitCounters) -> None:
        """Adds (or subtracts) to one `context_use_units` row: a unit's deliveries, uses and misses."""
        ...

    async def unit_totals(
        self, source_id: UUID, view: str, since: date, until: date
    ) -> list[tuple[str, UnitCounters]]:
        """Per unit, the counters of one source and view summed over the days."""
        ...

    async def put_trim(self, trim: ContextTrim) -> None:
        """Replaces the trim of the trim's source and view."""
        ...

    async def trims(self) -> list[ContextTrim]:
        """Every source and view's last trim; small, read by the read path and the compiler."""
        ...

    async def add_slot_daily(self, key: SlotDailyKey, counters: SlotCounters) -> None:
        """Adds (or subtracts) to one `context_use_slot_daily` row: a retrieval channel's slots and
        verdicts for one day, source, conversation channel, view and group (11, 3.4)."""
        ...

    async def slot_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[SlotDailyRow]: ...

    async def add_guard_daily(self, key: GuardDailyKey, counters: GuardCounters) -> None:
        """Adds (or subtracts) to one `context_use_guard_daily` row: per day, source, value type and arm,
        the values slotted or guarded and how many the agent contradicted (13, 6.1)."""
        ...

    async def guard_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[GuardDailyRow]: ...

    async def contradicted_value_types(self, since: date) -> frozenset[str]:
        """The value types with at least one contradiction since `since`, in any arm: the types the read
        path writes guard lines for. Read at most once a minute per space and process."""
        ...

    async def retrieval_weights(self) -> RetrievalWeights | None:
        """The last nightly run of the space's retrieval weights, which carries the weights in force."""
        ...

    async def weight_runs(self, limit: int) -> list[RetrievalWeights]:
        """The last runs, newest first: the weights' history, kept for audit."""
        ...

    async def put_retrieval_weights(self, run: RetrievalWeights) -> None:
        """Appends one run; runs are never updated."""
        ...

    async def add_view_use(self, key: ViewUseKey, counters: ViewUseCounters) -> None:
        """Adds (or subtracts) to one `context_use_view_daily` row: per day, reading source, target model,
        view, arm and learned section, what agents used and missed (13, 6.3)."""
        ...

    async def view_use_daily(
        self, since: date, until: date, source_ids: frozenset[UUID] | None = None
    ) -> list[ViewUseRow]: ...

    async def view_budgets(self) -> ViewBudgets | None:
        """The last nightly run of the learned views, which carries every reader's factors in force."""
        ...

    async def view_budget_runs(self, limit: int) -> list[ViewBudgets]:
        """The last runs, newest first, kept for audit."""
        ...

    async def put_view_budgets(self, run: ViewBudgets) -> None:
        """Appends one run; runs are never updated."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (19.6, 8.4): the rows and measured sessions of the profile, or of the
        conversation. Daily aggregates carry no personal data and stay."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` (22.3) for `context_use`: decrypted rows by `change_seq`."""
        ...
