"""`GET /v1/sources/me`: what a key authenticates as, for any key and without customer data."""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.sources import router
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal


class Edge:
    def __init__(self, caller: SourcePrincipal) -> None:
        self.caller = caller

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        return self.caller

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        return PersonPrincipal(self.caller.space, uuid4(), frozenset({"admin"}))

    async def check_rate(self, principal: SourcePrincipal, path: str) -> None:
        return None


@dataclass
class Container:
    edge: Edge
    config: Any


async def test_a_key_with_one_scope_learns_its_space_source_and_scopes(cell: Cell) -> None:
    caller = principal(scopes=frozenset({Scope.TRACK}))
    app = create_app("admin", Container(Edge(caller), cell.config), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        me = await http.get("/v1/sources/me", headers={"Authorization": "Bearer nia_sk_test"})
        person = await http.get("/v1/sources/me", headers={"Authorization": "Bearer a-person"})

    assert me.status_code == 200
    body = me.json()
    assert body["space_id"] == str(SPACE_ID)
    assert body["source_id"] == str(WHATSAPP_SOURCE)
    assert body["source_name"] == "whatsapp-bot"
    assert body["vendor"] == "A"
    assert body["scopes"] == ["track"]
    assert body["agent_memory"] is False
    # People read who they are from the control plane; this route is for keys.
    assert person.status_code == 403
