"""`POST /v1/state/read` and `GET /v1/sdk/profile` over the FastAPI app: the receipt of a state read, the
error of an undeclared type, and the profile's ETag."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.state import router
from niadra.app.state import StateService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.vocabulary import ReceiptKind, ReceiptSurface
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import World
from tests.unit.state.test_state_service import QUOTE_API, apply, health_config

KEY = {"authorization": "Bearer nia_sk_agent"}
QUOTE = {"type": "cotacao_plano_saude", "namespace": "operadora", "id": "q-77"}


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
        self.followed: list[UUID] = []

    async def guarded(self, space_id: UUID) -> PrivacyGuard:
        self.followed.append(space_id)
        return PrivacyGuard()

    async def emit_receipt(self, caller: SourcePrincipal, **receipt: Any) -> None:
        self.receipts.append({"source_id": caller.source_id, **receipt})


@pytest.fixture
async def client(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, FakeServe, World, StateService]]:
    cell.config.put(health_config())
    world = World(cell)
    state = StateService(world.uow, cell.cache, cell.keys, cell.clock, cell.config)
    serve = FakeServe()
    keys = {"nia_sk_agent": principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))}
    container = SimpleNamespace(edge=FakeEdge(keys), config=cell.config, state=state, serve=serve)
    app = create_app("read", container, [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cell.test") as c:
        yield c, serve, world, state


async def test_a_state_read_answers_the_objects_and_leaves_a_receipt_naming_them(
    client: tuple[httpx.AsyncClient, FakeServe, World, StateService],
) -> None:
    http, serve, world, state = client
    await apply(world, state, QUOTE_API, 1, {"price_full": 511.06})
    response = await http.post("/v1/state/read", headers=KEY, json={"refs": [QUOTE], "purpose": "claim"})
    assert response.status_code == 200
    [quote] = response.json()["objects"]
    assert quote["fields"]["price_full"]["v"] == 511.06
    assert serve.followed == [SPACE_ID]
    [receipt] = serve.receipts
    assert (receipt["kind"], receipt["surface"]) == (ReceiptKind.READ, ReceiptSurface.STATE)
    assert len(receipt["item_ids"]) == 1


async def test_a_type_nobody_declared_yet_is_answered_not_refused(
    client: tuple[httpx.AsyncClient, FakeServe, World, StateService],
) -> None:
    http, _, _, _ = client
    ref = {"type": "invoice", "namespace": "erp", "id": "1"}
    response = await http.post("/v1/state/read", headers=KEY, json={"refs": [ref]})
    assert response.status_code == 200
    [invoice] = response.json()["objects"]
    assert invoice["ref"]["type"] == "invoice"


async def test_the_profile_carries_an_etag_and_a_repeated_ask_costs_nothing(
    client: tuple[httpx.AsyncClient, FakeServe, World, StateService],
) -> None:
    http, serve, _, _ = client
    first = await http.get("/v1/sdk/profile", headers=KEY)
    assert first.status_code == 200
    assert first.json()["types"][0]["type"] == "cotacao_plano_saude"
    etag = first.headers["etag"]
    again = await http.get("/v1/sdk/profile", headers={**KEY, "if-none-match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert serve.receipts == []  # configuration, not a customer's data
