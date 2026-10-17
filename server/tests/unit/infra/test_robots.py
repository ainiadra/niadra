"""An API host is never indexed: it asks crawlers to stay out and marks every answer `noindex`."""

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app("niadra-test", container=object(), routers=[])
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://space.us-east-2.api.example"
    )


async def test_an_api_host_asks_crawlers_to_stay_out(client: httpx.AsyncClient) -> None:
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "User-agent: *\nDisallow: /\n"


@pytest.mark.parametrize("path", ["/healthz", "/v1/graph", "/"])
async def test_every_answer_says_noindex(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
