"""Every answer carries the request id and continues the caller's trace, the unexpected 500 included."""

import httpx
import pytest
from fastapi import APIRouter

from niadra.adapters.inbound.http.app import create_app

router = APIRouter()


@router.get("/boom")
async def boom() -> dict[str, str]:
    raise RuntimeError("database password is hunter2")


@router.get("/fine")
async def fine() -> dict[str, str]:
    return {"ok": "yes"}


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app("niadra-test", container=object(), routers=[router])
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://pod:8000"
    )


async def test_an_unexpected_error_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom", headers={"X-Request-Id": "req-123"})
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["request_id"] == "req-123"
    assert body["type"] == "https://docs.niadra.com/errors/internal_error"
    assert response.headers["x-request-id"] == "req-123"
    # The exception text never reaches the caller.
    assert "hunter2" not in response.text


async def test_the_request_id_is_minted_when_the_caller_sends_none(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom")
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert len(response.headers["x-request-id"]) == 32


@pytest.mark.parametrize("path", ["/fine", "/boom", "/does-not-exist"])
async def test_the_traceparent_comes_back_on_every_answer(client: httpx.AsyncClient, path: str) -> None:
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    response = await client.get(path, headers={"traceparent": traceparent})
    assert response.headers["traceparent"] == traceparent
    assert response.headers["x-request-id"]


async def test_an_unknown_path_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist", headers={"X-Request-Id": "req-404"})
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["code"], body["request_id"], body["type"]) == (
        "not_found",
        "req-404",
        "https://docs.niadra.com/errors/not_found",
    )
    assert "Not Found" not in response.text


async def test_a_wrong_method_is_a_problem_document_with_allow(client: httpx.AsyncClient) -> None:
    response = await client.post("/fine")
    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["allow"] == "GET"
    body = response.json()
    assert (body["code"], body["status"]) == ("method_not_allowed", 405)
    assert body["request_id"] == response.headers["x-request-id"]
