"""Admission before a costly act (24.3.4): the units a paid operation (a search, a refresh, an expensive call)
spends against its budget, reserved before the act and settled after it. The coordinate module implements it
on the state store, with no statement: other modules take it here.

It fails closed (L23): with the state store or the confirmed queue down, nothing is admitted. A space without
the coordination feature, or an operation without a declared budget, is always admitted, with nothing
reserved.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from niadra.contracts.common import Handle, ObjectRef

Outcome = Literal["done", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class PaidReservation:
    """What a reservation got. Not `admitted`: `reason` says why (`budget_exhausted` with `retry_at`,
    `over_call_ceiling` or `unavailable`). `reservation_id` is None when nothing was reserved; `target` is
    where the units count, kept for the settlement."""

    admitted: bool
    operation: str
    units: int
    reason: str | None = None
    remaining: int | None = None
    retry_at: datetime | None = None
    reservation_id: UUID | None = None
    target: str | None = None


class PaidBudgets(Protocol):
    async def reserve(
        self,
        space_id: UUID,
        operation: str,
        *,
        object: ObjectRef | None = None,
        subject: Handle | None = None,
        units: int | None = None,
    ) -> PaidReservation:
        """`units` None is an unknown cost: it reserves the highest cost measured for the operation, or its
        ceiling per call (1 without one) before any was measured. The budget's scope says which of `object`
        and `subject` it counts on."""
        ...

    async def settle(
        self, space_id: UUID, reservation: PaidReservation, *, outcome: Outcome, units: int | None = None
    ) -> None:
        """`done` settles to the real cost `units` and raises the measured maximum; `failed` keeps what was
        reserved, since a paid call that fails still counts; `skipped` gives the units back, the act never
        happened."""
        ...


class Unbudgeted:
    """Admits everything and reserves nothing: where no coordination is wired."""

    async def reserve(self, space_id: UUID, operation: str, **asked: Any) -> PaidReservation:  # noqa: ARG002
        return PaidReservation(admitted=True, operation=operation, units=asked.get("units") or 0)

    async def settle(self, space_id: UUID, reservation: PaidReservation, **_: Any) -> None:  # noqa: ARG002
        return None
