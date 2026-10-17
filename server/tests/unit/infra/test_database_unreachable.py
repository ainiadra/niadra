"""A database out of reach answers 503 with a retry delay, never 500: the connection is refused before any
statement runs, so the client may send the request again."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import Request

from niadra.adapters.inbound.http.errors import handle_niadra_error
from niadra.adapters.outbound.postgres.engines import UNREACHABLE_RETRY_S, make_engine
from niadra.adapters.outbound.postgres.intake import PgIntakeLog
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.errors import Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.intake import NewIntake

NOWHERE = "postgresql://niadra_app@127.0.0.1:1/niadra_cell"
"""Port 1 refuses every connection."""


async def test_a_database_out_of_reach_answers_503_with_a_retry_delay() -> None:
    engine = make_engine(NOWHERE, pool_size=1, max_overflow=0, pool_timeout=2)
    factory = PgUnitOfWorkFactory(cast(Any, None), cast(Any, None), engine)
    try:
        with pytest.raises(Unavailable) as refused:
            async with factory.cell():
                pass
    finally:
        await engine.dispose()
    assert refused.value.retry_after_s == UNREACHABLE_RETRY_S
    app = SimpleNamespace(state=SimpleNamespace(container=None))
    request = Request(
        {"type": "http", "method": "POST", "path": "/v1/state/read", "headers": [], "state": {}, "app": app}
    )
    response = await handle_niadra_error(request, refused.value)
    assert (response.status_code, response.headers["Retry-After"]) == (503, str(UNREACHABLE_RETRY_S))
    assert json.loads(bytes(response.body))["code"] == "unavailable"


class _Nowhere:
    """A directory whose every space lives on a server that refuses connections."""

    def __init__(self) -> None:
        self.engine = make_engine(NOWHERE, pool_size=1, max_overflow=0, pool_timeout=2, pre_ping=False)

    @asynccontextmanager
    async def lease(self, space_id: UUID, role: str) -> AsyncIterator[AsyncEngine]:
        yield self.engine


async def test_an_event_batch_to_a_database_out_of_reach_answers_503_too() -> None:
    nowhere = _Nowhere()
    intake = NewIntake(uuid7(), uuid4(), datetime.now(UTC), uuid4(), (("k1", uuid7()),), b"\x00sealed", 1)
    try:
        with pytest.raises(Unavailable) as refused:
            await PgIntakeLog(cast(Any, nowhere)).accept(intake)
    finally:
        await nowhere.engine.dispose()
    assert refused.value.retry_after_s == UNREACHABLE_RETRY_S
