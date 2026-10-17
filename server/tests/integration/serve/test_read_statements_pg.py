"""The read process counts the statements a request runs before it answers, through
SQLAlchemy's asyncio bridge as production runs them: what a request awaits counts for its route, what it
left running after the answer does not."""

import asyncio

import httpx
from fastapi import APIRouter, Request
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.engine import make_url

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.observability import metrics
from niadra.adapters.observability.metrics import ReadSummary
from niadra.adapters.outbound.postgres.engines import make_engine
from tests.integration.conftest import DSN

ROUTE = "/v1/context"


def counted() -> float:
    labels = {"route": ROUTE, "read": "warm"}
    return REGISTRY.get_sample_value("niadra_read_db_statements_total", labels) or 0.0


async def test_a_requests_statements_count_until_its_answer_starts() -> None:
    assert DSN
    engine = make_engine(make_url(DSN), pool_size=1, max_overflow=0, pre_ping=False)
    later: list[asyncio.Task[None]] = []
    router = APIRouter()

    async def warm() -> None:
        await asyncio.sleep(0.05)
        async with engine.connect() as db:
            await db.execute(text("SELECT 2"))

    @router.post(ROUTE)
    async def context(request: Request) -> dict[str, str]:
        async with engine.connect() as db:
            await db.execute(text("SELECT 1"))
            await db.execute(text("SELECT 1"))
        later.append(asyncio.create_task(warm()))  # a warm-up the read starts, off its path
        request.state.read = ReadSummary({}, "t0", False, True)
        return {}

    async with engine.connect() as db:  # the dialect's own first-connect queries, outside any request
        await db.execute(text("SELECT 0"))
    metrics.count_statements()
    before = counted()
    try:
        app = create_app("read", object(), [router])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://read"
        ) as client:
            await client.post(ROUTE)
        await asyncio.gather(*later)
    finally:
        await engine.dispose()
    assert counted() - before == 2
