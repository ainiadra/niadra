from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.turns import TurnService
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.config.recording import RecordingSettings
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.vocabulary import Feature
from niadra.ports.turns import AppliedTurn, TurnConsumers
from niadra.ports.uow import UnitOfWork
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.audit.fakes import RecordingAdminAudit


@dataclass
class Taken:
    """A consumer double: what a module was handed, and in which transaction."""

    turns: list[AppliedTurn] = field(default_factory=list)

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:
        assert uow.space_id == space_id
        self.turns += list(turns)


@dataclass
class World:
    cell: Cell
    audit: RecordingAdminAudit
    taken: Taken
    service: TurnService

    @property
    def caller(self) -> SourcePrincipal:
        return principal(WHATSAPP_SOURCE, self.cell.config.space(sample_config().space_id))

    def configure(self, **recording: Any) -> None:
        config = self.cell.config.space(sample_config().space_id)
        self.cell.config.put(config.model_copy(update={"recording": RecordingSettings(**recording)}))

    def turn_objects(self) -> list[str]:
        return sorted(k for k in self.cell.objects.objects if k.startswith("turns/"))


def turned_on() -> SpaceConfig:
    """A space that set retentions for its recordings, so these tests see the tiers expire; with none set,
    every recording is kept (`test_turn_erasure`)."""
    return sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.TURNS})),
            "recording": RecordingSettings(sample_rate=0, kept_days=30, scenario_days=180),
        }
    )


@pytest.fixture
def world() -> World:
    cell = Cell()
    cell.config.put(turned_on())
    audit, taken = RecordingAdminAudit(), Taken()
    service = TurnService(
        cell.uow,
        TransactionTurnIntakeLog(cell.uow),
        cell.keys,
        cell.objects,
        cell.clock,
        cell.config,
        audit,  # type: ignore[arg-type]
        consumers=TurnConsumers(observations=taken),
    )
    return World(cell, audit, taken, service)
