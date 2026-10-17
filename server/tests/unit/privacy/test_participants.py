"""The governance modules' own erasure and retention, on the in-memory repositories."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from niadra.domain.common.ids import uuid7
from niadra.testing.cell import Cell

from .participants import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
async def test_scenario(cell: Cell, scenario: Callable[[Any, Any], Awaitable[None]]) -> None:
    await scenario(cell.uow, uuid7())
