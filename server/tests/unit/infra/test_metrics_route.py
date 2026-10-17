"""Metrics are for the in-cluster scraper only: through the public edge the route does not exist."""

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app("niadra-test", container=object(), routers=[])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pod:8000")


async def test_the_scraper_reads_the_metrics_straight_from_the_pod(client: httpx.AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "niadra_http_requests_total" in response.text


@pytest.mark.parametrize(
    "header",
    [
        {"X-Forwarded-For": "203.0.113.7"},
        {"X-Forwarded-Host": "api.niadra.com"},
        {"X-Real-Ip": "203.0.113.7"},
        {"Forwarded": "for=203.0.113.7;proto=https"},
    ],
)
async def test_a_request_through_the_edge_finds_no_metrics(
    client: httpx.AsyncClient, header: dict[str, str]
) -> None:
    response = await client.get("/metrics", headers=header)
    assert response.status_code == 404
    assert "niadra_" not in response.text
    assert (await client.get("/healthz", headers=header)).status_code == 200
