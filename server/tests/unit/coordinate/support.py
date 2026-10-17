"""The coordination service's test world: the state store's scripts on fakeredis, an in-memory confirmed
queue and the in-memory durable record, around the in-memory cell (its vault, hot layer and caches)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import fakeredis
from pydantic import TypeAdapter

from niadra.adapters.outbound.redis.coordinate import RedisCoordination
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.app.coordinate import CoordinateService
from niadra.config.coordination import ContactBudget, CoordinationSettings, PurposeRule
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.coordinate import CheckRequest, CheckResult, DeclareRequest
from niadra.domain.coordinate.model import FailDirection
from niadra.domain.coordinate.records import CoordinationRecord
from niadra.domain.vocabulary import Feature, HandleType
from niadra.ports.state import WorkingSet
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal, sample_config

PHONE = Handle(type=HandleType.PHONE_E164, value="+55 11 98765-4321")
SAME_PHONE = Handle(type=HandleType.WA_ID, value="5511987654321")
OTHER_PHONE = Handle(type=HandleType.PHONE_E164, value="+5511912345678")
FAREWELL = "farewell:conv-1"
DECLARATION: TypeAdapter[DeclareRequest] = TypeAdapter(DeclareRequest)


class MemoryLog:
    """The confirmed queue: `down` makes the broker stop confirming."""

    def __init__(self) -> None:
        self.records: list[CoordinationRecord] = []
        self.down = False

    async def publish(self, records: Sequence[CoordinationRecord], timeout_s: float) -> bool:
        if self.down:
            return False
        self.records.extend(records)
        return True


@dataclass
class World:
    cell: Cell
    server: fakeredis.FakeServer
    log: MemoryLog
    service: CoordinateService

    async def check(self, source: UUID = WHATSAPP_SOURCE, **fields: Any) -> CheckResult:
        body = {"agent": "agent:closing", "intent": "farewell", "direction": "outbound", "purpose": "service"}
        return await self.service.check(principal(source), CheckRequest.model_validate({**body, **fields}))

    async def declare(self, source: UUID = WHATSAPP_SOURCE, key: str = "", **fields: Any) -> Any:
        request = DECLARATION.validate_python({"agent": "agent:closing", **fields})
        return await self.service.declare(principal(source), request, key or str(fields))

    async def drain(self) -> None:
        """The batch writer takes what the queue holds."""
        records, self.log.records = self.log.records, []
        await self.service.write(records)

    def advance(self, **delta: float) -> None:
        self.cell.clock.advance(**delta)


def settings_with_budget() -> CoordinationSettings:
    marketing = PurposeRule(
        name="marketing", fail=FailDirection.CLOSED, token=True, budget=ContactBudget(limit=2, per_hours=168)
    )
    purposes = [p if p.name != "marketing" else marketing for p in CoordinationSettings().purposes]
    return CoordinationSettings(purposes=purposes)


async def make_world(
    settings: CoordinationSettings | None = None, *features: Feature, working_set: WorkingSet | None = None
) -> World:
    cell = Cell()
    cell.clock.at = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    config = sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset(features or {Feature.COORDINATION})),
            "coordination": settings or settings_with_budget(),
        }
    )
    cell.config.put(config)
    server = fakeredis.FakeServer()
    log = MemoryLog()
    service = CoordinateService(
        cell.uow,
        RedisCoordination(fakeredis.FakeAsyncRedis(server=server)),
        log,
        cell.keys,
        cell.clock,
        cell.config,
        marks=RedisErasureMarks(fakeredis.FakeAsyncRedis(server=server)),
        secrets=cell.secrets,
        hot=cell.hot,
        handle_cache=cell.cache,
        working_set=working_set,
    )
    # The loops process seals a store that came up empty once it reloaded every space: here, none.
    await service.watch_state_store()
    return World(cell, server, log, service)
