"""The read path measured in production: a context read's stages, path and server time by
space reach Prometheus and `Server-Timing`, a slow or failed request leaves one log line with ids and
times only, and the read process counts the statements a request ran before answering."""

import asyncio
import logging
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, HTTPException, Request
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, text

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.observability import metrics
from niadra.adapters.observability.metrics import ContextReads, ReadSummary
from niadra.domain.common.space import SpaceRef
from niadra.domain.vocabulary import Environment

SPACE = uuid4()
STAGES = {"privacy": 0.1, "resolve": 0.4, "pack": 2.0, "live": 0.3, "receipt": 0.6}


class Caller:
    space = SpaceRef(SPACE, uuid4(), "us-east-2", Environment.SANDBOX)


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def app(*, wait_s: float = 0.0, degraded: bool = False, warm: bool = False) -> httpx.AsyncClient:
    router = APIRouter()
    engine = create_engine("sqlite://")

    @router.post("/v1/context")
    async def context(request: Request) -> dict[str, str]:
        request.state.principal = Caller()
        with engine.connect() as db:
            db.execute(text("SELECT 1"))
        await asyncio.sleep(wait_s)
        request.state.read = ReadSummary(STAGES, "t0", degraded, warm)
        return {"text": "a customer's pack"}

    @router.post("/v1/other/{item_id}")
    async def other(item_id: str, request: Request) -> dict[str, str]:
        request.state.principal = Caller()
        if item_id == "boom":
            raise RuntimeError("a value the error would quote")
        if item_id == "busy":
            raise HTTPException(429, "slow down")
        return {"item": item_id}

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app("t", object(), [router])), base_url="http://t"
    )


async def test_a_context_read_reaches_its_stage_path_and_space_series_and_server_timing() -> None:
    space = str(SPACE)
    before = (
        sample("niadra_context_stage_seconds_count", stage="pack"),
        sample("niadra_context_path_total", path="t0", degraded="false"),
        sample("niadra_context_duration_seconds_count", space=space, degraded="false"),
        sample("niadra_context_duration_seconds_count", space=space, degraded="true"),
    )
    async with app() as client:
        response = await client.post("/v1/context")
    async with app(degraded=True) as client:
        await client.post("/v1/context")
    after = (
        sample("niadra_context_stage_seconds_count", stage="pack"),
        sample("niadra_context_path_total", path="t0", degraded="false"),
        sample("niadra_context_duration_seconds_count", space=space, degraded="false"),
        sample("niadra_context_duration_seconds_count", space=space, degraded="true"),
    )
    assert [a - b for a, b in zip(after, before, strict=True)] == [2, 1, 1, 1]
    timing = response.headers["server-timing"]
    # The total stays first: the in-region probe reads it as the server's time.
    assert timing.startswith("app;dur=")
    assert "pack;dur=2.0" in timing
    assert "receipt;dur=0.6" in timing
    # The 35 ms bucket: the VPC target.
    assert (
        REGISTRY.get_sample_value(
            "niadra_http_request_duration_seconds_bucket",
            {"route": "/v1/context", "method": "POST", "le": "0.035"},
        )
        is not None
    )


async def test_the_read_process_counts_the_statements_a_request_ran_by_the_kind_of_read() -> None:
    """A warm read (resolved from the read state, served from the cache) is where a statement is a
    regression; the first read of a conversation resolves its customer on the database by design."""
    metrics.count_statements()
    warm, cold = ({"route": "/v1/context", "read": kind} for kind in ("warm", "cold"))
    before = (
        sample("niadra_read_db_statements_total", **warm),
        sample("niadra_read_db_statements_total", **cold),
    )
    async with app(warm=True) as client:
        await client.post("/v1/context")
    async with app() as client:
        await client.post("/v1/context")
        await client.post("/v1/context")
        await client.post("/v1/other/x")  # no statement, no increment
    after = (
        sample("niadra_read_db_statements_total", **warm),
        sample("niadra_read_db_statements_total", **cold),
    )
    assert (after[0] - before[0], after[1] - before[1]) == (1, 2)
    assert sample("niadra_read_db_statements_total", route="/v1/other/{item_id}", read="none") == 0


async def test_a_slow_read_leaves_one_line_with_ids_and_times_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="niadra.adapters.inbound.http.app")
    async with app(wait_s=0.06) as client:
        response = await client.post("/v1/context")
        await client.post("/v1/other/fast")
    [line] = [r for r in caplog.records if r.getMessage() == "slow request"]
    fields: dict[str, Any] = line.__dict__
    assert (fields["route"], fields["method"], fields["status"]) == ("/v1/context", "POST", 200)
    assert fields["duration_ms"] >= 60
    assert fields["space_id"] == str(SPACE)
    assert fields["stages"] == STAGES
    assert fields["read_path"] == "t0"
    assert fields["db_statements"] == 1
    assert response.headers["x-request-id"]
    assert "pack" not in str(fields.get("msg"))


async def test_every_failed_or_refused_request_leaves_a_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="niadra.adapters.inbound.http.app")
    async with app() as client:
        assert (await client.post("/v1/other/boom")).status_code == 500
        assert (await client.post("/v1/other/busy")).status_code == 429
    failed = [r for r in caplog.records if r.getMessage() == "failed request"]
    assert [(r.__dict__["route"], r.__dict__["status"]) for r in failed] == [
        ("/v1/other/{item_id}", 500),
        ("/v1/other/{item_id}", 429),
    ]
    # The route's template, never the raw path, and never what the error says.
    assert all("boom" not in r.getMessage() and "item_id" in r.__dict__["route"] for r in failed)


def test_a_space_that_stopped_reading_leaves_its_series() -> None:
    idle, active = str(uuid4()), str(uuid4())
    read = ReadSummary({}, "t0", False)
    reads = ContextReads()
    with patch("niadra.adapters.observability.metrics.time.monotonic", return_value=1_000.0):
        reads.observe(idle, read, 0.01)
    with patch("niadra.adapters.observability.metrics.time.monotonic", return_value=1_000.0 + 3_000):
        reads.observe(active, read, 0.01)
    assert sample("niadra_context_duration_seconds_count", space=idle, degraded="false") == 1
    with patch("niadra.adapters.observability.metrics.time.monotonic", return_value=1_000.0 + 3_700):
        reads.observe(active, read, 0.01)
    assert (
        REGISTRY.get_sample_value(
            "niadra_context_duration_seconds_count", {"space": idle, "degraded": "false"}
        )
        is None
    )
    assert sample("niadra_context_duration_seconds_count", space=active, degraded="false") == 2
