"""`GET /v1/objects/{type}/{namespace}/{id}` and `/timeline` over the FastAPI app, with receipts."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.objects import router
from niadra.app.objects import ObjectsService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.records import ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, HandleType, ReceiptKind, ReceiptSurface, Scope
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, principal
from tests.unit.objects.fakes import World, at, config_with, event, handle

INVOICE = ObjectKey("invoice", "erp", "0823")
SLASHED = ObjectKey("order", "shop", "2026/09/0042")


class FakeEdge:
    def __init__(self, keys: dict[str, SourcePrincipal]) -> None:
        self.keys = keys

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token not in self.keys:
            raise Unauthenticated("unknown key")
        return self.keys[token]

    async def check_rate(self, principal: SourcePrincipal, path: str) -> None:
        return None


class FakeServe:
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []

    async def emit_receipt(self, caller: SourcePrincipal, **receipt: Any) -> None:
        self.receipts.append({"source_id": caller.source_id, **receipt})

    async def guarded(self, space_id: UUID) -> None:
        return None


@pytest.fixture
async def client(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, FakeServe, World]]:
    cell.config.put(config_with())
    world = World(cell)
    marina = world.identity.add_profile(handle(HandleType.SYSTEM_ID))
    objects = ObjectsService(world.uow, cell.clock, cell.config)
    e = event(
        EventKind.SYSTEM_EVENT,
        at(6),
        [world.identity.handles[marina.handle_ids[0]]],
        objects=[INVOICE],
        canonical_type="invoice.credited",
        fields={"amount": "40"},
    )
    slashed = event(
        EventKind.SYSTEM_EVENT,
        at(7),
        [world.identity.handles[marina.handle_ids[0]]],
        objects=[SLASHED],
        canonical_type="order.shipped",
    )
    world.ingest.add(e, slashed)
    for applied in (e, slashed):
        await objects.apply_object_event(
            Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(applied.event_id)})
        )
    serve = FakeServe()
    keys = {
        "nia_sk_billing": principal(BILLING_SOURCE),
        "nia_sk_no_context": principal(BILLING_SOURCE, scopes=frozenset({Scope.TRACK})),
    }
    container = SimpleNamespace(edge=FakeEdge(keys), objects=objects, serve=serve)
    app = create_app("read", container, [router])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://cell.test"
    ) as http:
        yield http, serve, world


def auth(key: str = "nia_sk_billing") -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


async def test_reads_the_typed_state_and_leaves_a_receipt(client: Any) -> None:
    http, serve, _ = client
    response = await http.get("/v1/objects/invoice/erp/0823", headers=auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ref"] == {"type": "invoice", "namespace": "erp", "id": "0823", "variant": None}
    assert "amount" in body["fields"]
    assert "last_event_type" not in body["fields"]  # what a system event says of itself, undeclared
    [receipt] = serve.receipts
    assert receipt["kind"] is ReceiptKind.READ
    assert receipt["surface"] is ReceiptSurface.OBJECT
    assert receipt["object_id"] is not None
    assert receipt["profile_id"] is not None


async def test_timeline_is_paginated(client: Any) -> None:
    http, serve, _ = client
    response = await http.get("/v1/objects/invoice/erp/0823/timeline", params={"limit": 1}, headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert [i["text"] for i in body["items"]] == ["invoice.credited"]
    assert body["next_cursor"] is None
    assert len(serve.receipts) == 1


async def test_ids_with_slashes_keep_both_routes(client: Any) -> None:
    http, serve, _ = client
    state = await http.get("/v1/objects/order/shop/2026/09/0042", headers=auth())
    assert state.json()["ref"]["id"] == "2026/09/0042"
    encoded = await http.get("/v1/objects/order/shop/2026%2F09%2F0042", headers=auth())
    assert encoded.json()["ref"]["id"] == "2026/09/0042"
    timeline = await http.get("/v1/objects/order/shop/2026/09/0042/timeline", headers=auth())
    assert [i["text"] for i in timeline.json()["items"]] == ["order.shipped"]
    assert len(serve.receipts) == 3


async def test_errors_are_problems(client: Any) -> None:
    http, serve, _ = client
    assert (await http.get("/v1/objects/invoice/erp/9999", headers=auth())).status_code == 404
    assert (
        await http.get("/v1/objects/invoice/erp/0823", headers=auth("nia_sk_no_context"))
    ).status_code == 403
    assert (await http.get("/v1/objects/invoice/erp/0823")).status_code == 401
    assert serve.receipts == []
