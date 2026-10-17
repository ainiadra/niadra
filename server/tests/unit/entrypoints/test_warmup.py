import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from niadra.adapters.inbound.http.app import create_app
from niadra.entrypoints.warmup import warm_up


def test_readiness_waits_for_the_warm_up_and_liveness_never_does() -> None:
    ready = asyncio.Event()
    client = TestClient(create_app("t", object(), [], ready=ready))
    assert client.get("/healthz").status_code == 200
    warming = client.get("/readyz")
    assert warming.status_code == 503
    assert warming.json() == {"status": "warming"}
    ready.set()
    assert client.get("/readyz").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_an_app_without_a_warm_up_is_ready_at_once() -> None:
    client = TestClient(create_app("t", object(), []))
    assert client.get("/readyz").status_code == 200


async def test_the_warm_up_runs_every_step_and_a_failing_one_holds_nothing() -> None:
    ran: list[str] = []
    ready = asyncio.Event()

    async def ok(name: str) -> None:
        ran.append(name)

    async def broken() -> None:
        ran.append("broken")
        raise RuntimeError("valkey away")

    await warm_up(
        [("config", lambda: ok("config")), ("valkey", broken), ("encoder", lambda: ok("encoder"))],
        ready,
        timeout_s=1.0,
    )
    assert ready.is_set()
    assert ran == ["config", "broken", "encoder"]


async def test_a_warm_up_past_its_bound_gets_ready_anyway_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready = asyncio.Event()

    async def stuck() -> None:
        await asyncio.sleep(30)

    caplog.set_level(logging.WARNING, logger="niadra.warmup")
    await asyncio.wait_for(warm_up([("models", stuck)], ready, timeout_s=0.05), timeout=2)
    assert ready.is_set()
    said = [r.getMessage() for r in caplog.records]
    assert "warm-up past its bound in step models; the pod is ready anyway" in said


async def test_the_space_pools_open_once_each_and_a_failing_space_is_skipped() -> None:
    from niadra.entrypoints.warmup import open_space_pools

    opened: list[str] = []

    async def open_one(space: str) -> None:
        if space == "s2":
            raise RuntimeError("database not ready")
        opened.append(space)

    count = await open_space_pools(["s1", "s2", "s3"], open_one, limit=10, parallel=2)
    assert sorted(opened) == ["s1", "s3"]
    assert count == 2


async def test_the_space_pools_stop_at_their_limit() -> None:
    from niadra.entrypoints.warmup import open_space_pools

    opened: list[str] = []

    async def open_one(space: str) -> None:
        opened.append(space)

    await open_space_pools([f"s{i}" for i in range(10)], open_one, limit=3, parallel=2)
    assert len(opened) == 3
