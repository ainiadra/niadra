"""The Console calls the cell and the control plane from another origin: the headers it reads are exposed."""

import httpx
from fastapi import APIRouter

from niadra.adapters.inbound.http.app import create_app
from niadra.domain.common.errors import RateLimited, Unavailable

CONSOLE = "https://niadra.com"
router = APIRouter()


@router.get("/busy")
async def busy() -> None:
    raise Unavailable("the space is being prepared")


@router.get("/slow-down")
async def slow_down() -> None:
    raise RateLimited(7)


def _client() -> httpx.AsyncClient:
    app = create_app("niadra-test", container=object(), routers=[router], allowed_origins=[CONSOLE])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pod:8000")


async def test_a_503_and_a_429_tell_the_console_how_long_to_wait() -> None:
    async with _client() as client:
        busy = await client.get("/busy", headers={"Origin": CONSOLE})
        slow = await client.get("/slow-down", headers={"Origin": CONSOLE})
    assert (busy.status_code, busy.headers["retry-after"]) == (503, "2")
    assert (slow.status_code, slow.headers["retry-after"]) == (429, "7")
    for response in (busy, slow):
        assert response.headers["access-control-allow-origin"] == CONSOLE
        exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
        assert {"retry-after", "x-request-id", "etag"} <= exposed
