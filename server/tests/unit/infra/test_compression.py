"""The cell's long answers go gzipped to clients that accept it (B22): one TCP window instead of two round
trips for a reader far from the region."""

import httpx
from fastapi import APIRouter

from niadra.adapters.inbound.http.app import COMPRESS_FROM, create_app

LONG = "x" * (COMPRESS_FROM * 4)


def _client(*, compress: bool) -> httpx.AsyncClient:
    router = APIRouter()

    @router.get("/v1/long")
    async def long() -> dict[str, str]:
        return {"pack": LONG}

    @router.get("/v1/short")
    async def short() -> dict[str, str]:
        return {"ok": "yes"}

    app = create_app("t", object(), [router], compress=compress)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_a_long_answer_goes_gzipped_with_its_request_id_and_server_timing() -> None:
    async with _client(compress=True) as client:
        response = await client.get("/v1/long", headers={"accept-encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert int(response.headers["content-length"]) < COMPRESS_FROM
    assert response.json() == {"pack": LONG}  # httpx decodes it, as the SDKs' clients do
    assert response.headers["x-request-id"]
    assert response.headers["server-timing"].startswith("app;dur=")


async def test_short_answers_and_clients_without_gzip_get_plain_bytes() -> None:
    async with _client(compress=True) as client:
        short = await client.get("/v1/short", headers={"accept-encoding": "gzip"})
        plain = await client.get("/v1/long", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in short.headers
    assert "content-encoding" not in plain.headers
    assert plain.json() == {"pack": LONG}


async def test_an_app_without_compression_never_gzips() -> None:
    async with _client(compress=False) as client:
        response = await client.get("/v1/long", headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in response.headers
    assert len(response.content) > COMPRESS_FROM  # the bytes went as they are
