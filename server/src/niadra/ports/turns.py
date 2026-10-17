"""What other modules take from the turns a worker applies (24.3.5): the observations a tool result showed,
for the objects; the interactions, for the subject signals; the claim verdicts, for their measurement; the
external effects, for coordination.

A consumer runs inside the transaction that applies a group of up to 50 intakes and removes them, so what
it takes is never lost: a group that fails runs again, and a consumer must take the same turns twice as
once. The group's budget is 7 statements (24.6); the turns module spends 3 (the space pin, the take and the
write), so the consumers share the other 4. Consumers get the frames: a recorded value never reaches them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.contracts.turns import TurnRecord
from niadra.ports.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class AppliedTurn:
    source_id: UUID
    received_at: datetime
    record: TurnRecord


class TurnConsumer(Protocol):
    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None: ...


class Unconsumed:
    """A consumer that takes nothing from the turns."""

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:  # noqa: ARG002
        return None


@dataclass(frozen=True, slots=True)
class TurnConsumers:
    observations: TurnConsumer = field(default_factory=Unconsumed)
    interactions: TurnConsumer = field(default_factory=Unconsumed)
    claims: TurnConsumer = field(default_factory=Unconsumed)
    effects: TurnConsumer = field(default_factory=Unconsumed)

    def all(self) -> tuple[TurnConsumer, ...]:
        return (self.observations, self.interactions, self.claims, self.effects)


class ConfigHistory(Protocol):
    """Where the configuration module leaves each space's applied snapshot, for the change log's `config`
    kind: a space whose document changed, with the snapshot's version and time."""

    async def record_config(self, config: SpaceConfig, version: int, at: datetime) -> None: ...


class TurnIndex(Protocol):
    """What the day's audit anchor takes from the turns module (24.5.4): how many rows the kept tier took that
    day and their Merkle root, anchored next to the receipts'; None where no turn is recorded."""

    async def day_index(self, space_id: UUID, day: date) -> tuple[int, bytes] | None: ...
