"""Deprecation and sunset headers: the registry, the headers the SDKs read, and the one-year rule."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter

from niadra.adapters.inbound.http import deprecations
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.deprecations import DEPRECATIONS, Deprecation

OPENAPI = Path(__file__).parents[3] / "openapi"
SINCE = datetime(2026, 10, 1, tzinfo=UTC)
CONSOLE = "https://niadra.com"


def _app() -> httpx.AsyncClient:
    router = APIRouter()

    @router.get("/v1/old/{item_id}")
    async def old(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @router.get("/v1/new")
    async def new() -> dict[str, str]:
        return {}

    app = create_app("t", object(), [router], allowed_origins=[CONSOLE])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_every_real_deprecation_names_a_served_route_and_leaves_a_year() -> None:
    served = {
        (method.upper(), path)
        for name in ("cell.json", "control.json")
        for path, operations in json.loads((OPENAPI / name).read_text())["paths"].items()
        for method in operations
    }
    # The SDKs warn once per migration note: two routes never share one.
    notes = {(d.method, d.route): d.link for d in DEPRECATIONS}
    assert len(set(notes.values())) == len(notes)
    for d in DEPRECATIONS:
        where = f"{d.method} {d.route} {d.field or ''}"
        assert (d.method, d.route) in served, where
        assert d.sunset - d.since >= timedelta(days=365), where
        assert d.link.startswith("https://docs.niadra.com/"), where
        assert d.field is None or d.field.strip(), where


async def test_a_deprecated_route_announces_itself_on_every_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = Deprecation(
        "GET",
        "/v1/old/{item_id}",
        SINCE,
        SINCE + timedelta(days=366),
        "https://docs.niadra.com/changelog#old",
    )
    monkeypatch.setattr(deprecations, "DEPRECATIONS", (entry,))
    async with _app() as client:
        old = await client.get("/v1/old/x", headers={"Origin": CONSOLE})
        missing = await client.get("/v1/old/x/y")
        new = await client.get("/v1/new")
    assert old.headers["deprecation"] == f"@{int(SINCE.timestamp())}"
    assert old.headers["sunset"] == "Sat, 02 Oct 2027 00:00:00 GMT"
    assert old.headers["link"] == '<https://docs.niadra.com/changelog#old>; rel="deprecation"'
    exposed = {h.strip() for h in old.headers["access-control-expose-headers"].split(",")}
    assert {"deprecation", "sunset", "link"} <= exposed
    for other in (missing, new):
        assert not {"deprecation", "sunset", "link"} & set(other.headers)


async def test_a_deprecated_field_marks_its_route_and_the_earliest_dates_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    later = SINCE + timedelta(days=30)
    entries = (
        Deprecation(
            "GET", "/v1/old/{item_id}", later, later + timedelta(days=400), "https://docs.niadra.com/a"
        ),
        Deprecation(
            "GET", "/v1/old/{item_id}", SINCE, SINCE + timedelta(days=370), "https://docs.niadra.com/b", "id"
        ),
        Deprecation("POST", "/v1/old/{item_id}", SINCE, SINCE, "https://docs.niadra.com/c"),
    )
    monkeypatch.setattr(deprecations, "DEPRECATIONS", entries)
    async with _app() as client:
        old = await client.get("/v1/old/x")
    assert old.headers["deprecation"] == f"@{int(SINCE.timestamp())}"
    assert old.headers["sunset"] == "Wed, 06 Oct 2027 00:00:00 GMT"
    assert old.headers["link"] == (
        '<https://docs.niadra.com/a>; rel="deprecation", <https://docs.niadra.com/b>; rel="deprecation"'
    )
