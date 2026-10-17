"""The Console's latency card: the server time of a space's context reads, from the cell's
Prometheus by the space's label, cached a minute; never another space's, never from the database."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.testclient import TestClient

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.usage import router
from niadra.adapters.outbound.prometheus import PrometheusReader
from niadra.app.latency import ContextLatencyService, queries
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.metering.test_http import FakeEdge


class Reader:
    def __init__(self, values: Mapping[str, float | None] | None = None, *, down: bool = False) -> None:
        self.values = dict(values or {})
        self.down = down
        self.asked: list[str] = []

    async def scalars(self, wanted: Mapping[str, str]) -> dict[str, float | None]:
        if self.down:
            raise httpx.ConnectError("no route")
        self.asked += wanted.values()
        return {name: self.values.get(name) for name in wanted}


DAY = {"24h:reads": 1200, "24h:p50": 0.0081, "24h:p95": 0.0423, "24h:under": 0.996, "24h:degraded": 0.0025}


async def test_the_card_reads_the_spaces_series_and_says_what_the_numbers_mean(cell: Cell) -> None:
    reader = Reader(DAY)
    report = await ContextLatencyService(reader, cell.clock).report(SPACE_ID)
    assert report.available
    assert (report.target_ms, report.slo) == (100, 0.99)
    day, week = report.windows
    assert (day.window, day.reads, day.p50_ms, day.p95_ms) == ("24h", 1200, 8.1, 42.3)
    assert (day.under_target, day.within_slo, day.error_budget_used, day.degraded) == (
        0.996,
        True,
        0.4,
        0.0025,
    )
    # No reads in the week's series: the Console says "sem leituras no período".
    assert (week.window, week.reads, week.p95_ms, week.under_target) == ("7d", 0, None, None)
    assert all(f'space="{SPACE_ID}"' in q for q in reader.asked)


def test_the_queries_count_the_target_bucket_and_the_degraded_reads() -> None:
    found = queries(SPACE_ID, "7d")
    assert 'le="0.1"' in found["under"]
    assert 'degraded="true"' in found["degraded"]
    assert found["p95"].startswith("histogram_quantile(0.95, sum by (le) (increase(")
    assert "[7d]" in found["reads"]


async def test_the_card_is_kept_a_minute_and_says_when_the_metrics_are_out(cell: Cell) -> None:
    reader = Reader(DAY)
    service = ContextLatencyService(reader, cell.clock)
    await service.report(SPACE_ID)
    asked = len(reader.asked)
    await service.report(SPACE_ID)
    assert len(reader.asked) == asked
    cell.clock.advance(seconds=61)
    await service.report(SPACE_ID)
    assert len(reader.asked) == 2 * asked
    down = await ContextLatencyService(Reader(down=True), cell.clock).report(SPACE_ID)
    unset = await ContextLatencyService(None, cell.clock).report(SPACE_ID)
    assert (down.available, down.windows, unset.available) == (False, [], False)


async def test_the_reader_asks_prometheus_for_instant_values() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        query = request.url.params["query"]
        result = (
            [] if "empty" in query else [{"metric": {}, "value": [1.0, "NaN" if "nan" in query else "0.25"]}]
        )
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )

    reader = PrometheusReader("http://prometheus:9090/", transport=httpx.MockTransport(answer))
    found = await reader.scalars({"a": "up", "b": "empty", "c": "nan"})
    assert found == {"a": 0.25, "b": None, "c": None}


@dataclass
class Container:
    edge: FakeEdge
    latency: Any


def test_the_route_is_a_space_report_of_the_analysis_role(cell: Cell) -> None:
    def client(roles: frozenset[str]) -> TestClient:
        service = ContextLatencyService(Reader(DAY), cell.clock)
        return TestClient(create_app("admin", Container(FakeEdge(roles), service), [router]))

    headers = {"authorization": "Bearer person-jwt"}
    response = client(frozenset({"analysis"})).get("/v1/usage/context-latency", headers=headers)
    assert response.status_code == 200
    assert response.json()["windows"][0]["p95_ms"] == 42.3
    assert client(frozenset({"admin"})).get("/v1/usage/context-latency", headers=headers).status_code == 200
    assert client(frozenset({"review"})).get("/v1/usage/context-latency", headers=headers).status_code == 403
