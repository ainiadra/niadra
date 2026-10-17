"""What the objects module hands the measurement of outcomes (24.7).

`OutcomeConsumer.consume` runs inside the transaction that wrote the objects, wherever an object's state is
committed (a system event, a tool's observation, and later a push, the consolidator and the lifecycle's
timers), so an object that reaches an outcome is never missed. It must be idempotent (a retried write calls
it again) and cheap: one outbox row at most, and none for an object no outcome definition names.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from niadra.domain.records import BusinessObject
from niadra.ports.uow import UnitOfWork


class OutcomeConsumer(Protocol):
    async def consume(self, uow: UnitOfWork, space_id: UUID, objects: Sequence[BusinessObject]) -> None: ...


class NoOutcomes:
    """The place of the measurement of outcomes when nothing measures them."""

    async def consume(self, uow: UnitOfWork, space_id: UUID, objects: Sequence[BusinessObject]) -> None:  # noqa: ARG002
        return None
