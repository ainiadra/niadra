from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest

from niadra.adapters.memory.fakes import InMemoryObjectStore
from niadra.app.privacy import PrivacyService
from niadra.config.models import ExportSchedule, SpaceConfig, WebhookEndpoint
from niadra.domain.common.errors import InvalidInput
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, PatchedUnitOfWorkFactory, sample_config

from .world import T0, World, owners


class FakeDestinations:
    def __init__(self) -> None:
        self.store = InMemoryObjectStore()
        self.opened: list[tuple[str, str]] = []
        self.closed = 0
        self.readable: set[str] = set()

    @asynccontextmanager
    async def open(self, schedule: ExportSchedule, credential: str) -> AsyncIterator[InMemoryObjectStore]:
        self.opened.append((schedule.schedule_id, credential))
        try:
            yield self.store
        finally:
            self.closed += 1

    async def check_write_only(self, schedule: ExportSchedule, credential: str) -> None:
        if credential in self.readable:
            raise InvalidInput("the credential can list the bucket; give it only permission to write")


LAKE_CREDENTIAL = f"spaces/{SPACE_ID}/export/lake"
DPO_ENDPOINT = WebhookEndpoint(
    endpoint_id="dpo",
    url="https://dpo.example/hooks",
    source_id=BILLING_SOURCE,
    event_types=["erasure.completed", "export.completed", "export.failed"],
    secret_refs=[f"spaces/{SPACE_ID}/webhook/dpo"],
)


def config_with_schedule(**overrides: Any) -> SpaceConfig:
    values: dict[str, Any] = {
        "schedule_id": "lake",
        "destination_kind": "s3",
        "bucket": "customer-lake",
        "prefix": "niadra",
        "region": "us-east-2",
        "tables": ["events", "receipts", "unknown_table"],
        "credential_ref": LAKE_CREDENTIAL,
    }
    values.update(overrides)
    return sample_config().model_copy(
        update={"export_schedules": [ExportSchedule(**values)], "webhook_endpoints": [DPO_ENDPOINT]}
    )


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def destinations() -> FakeDestinations:
    return FakeDestinations()


@pytest.fixture
def make_privacy(cell: Cell, world: World, destinations: FakeDestinations) -> Callable[..., PrivacyService]:
    def make(batch_size: int = 500, fences: Sequence[Any] = (), **repos: Any) -> PrivacyService:
        cell.clock.at = T0
        cell.config.put(config_with_schedule())
        uow = PatchedUnitOfWorkFactory(cell.uow, **{**owners(world), **repos})
        return PrivacyService(
            uow,
            cell.keys,
            cell.objects,
            cell.hot,
            cell.clock,
            cell.config,
            secrets=cell.secrets,
            destinations=destinations,
            batch_size=batch_size,
            archive_part_rows=2,
            fences=fences,
        )

    return make
