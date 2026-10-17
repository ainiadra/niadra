"""The governance modules' own erasure and retention, on PostgreSQL: same scenarios as the unit tests."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from tests.unit.privacy.participants import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
async def test_scenario(pg_uow: PgUnitOfWorkFactory, scenario: Callable[[Any, Any], Awaitable[None]]) -> None:
    await scenario(pg_uow, uuid7())
