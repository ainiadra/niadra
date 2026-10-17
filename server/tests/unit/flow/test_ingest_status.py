"""`POST /v1/ingest/status`: whether what was sent for a conversation became memory yet, states only."""

from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.ingest import router
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.ingest import IngestStatusRequest
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import remembered, said


async def test_a_conversation_goes_from_open_to_ready(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    agent = principal()
    ask = IngestStatusRequest(conversation_id="wa-7")
    assert (await flow.extract.status(agent, ask)).state == "unknown"

    now = cell.clock.now().isoformat()
    turn = said("wa-7", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, turn)).errors == []
    opened = await flow.extract.status(agent, ask)
    assert opened.state == "open"
    assert opened.last_event_at is not None

    ended = {"type": "conversation.ended", "idempotency_key": "wa-7-end", "conversation_id": "wa-7"}
    await flow.send(WHATSAPP_SOURCE, {**ended, "occurred_at": now})
    # The end is a request: the session closes, then extracts, as the workers drain the queue.
    await flow.drain()
    ready = await flow.extract.status(agent, ask)
    assert ready.state == "ready"
    assert ready.extraction == "ok"
    assert ready.closed_at is not None
    # States and times only: nothing the customer said.
    assert "Rafa" not in ready.model_dump_json()


def test_the_request_names_exactly_one_thread() -> None:
    with pytest.raises(ValidationError):
        IngestStatusRequest()
    with pytest.raises(ValidationError):
        IngestStatusRequest(conversation_id="c", task_id="t")


async def test_the_route_takes_the_thread_in_the_body(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    caller = principal(scopes=frozenset({Scope.TRACK}))

    class Edge:
        async def authenticate_key(self, token: str) -> SourcePrincipal:
            return caller

        async def check_rate(self, principal: SourcePrincipal, path: str) -> None:
            return None

    app = create_app("ingest", SimpleNamespace(edge=Edge(), extract=flow.extract), [router])
    headers = {"Authorization": "Bearer nia_sk_x"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        found = await http.post("/v1/ingest/status", json={"conversation_id": "wa-404"}, headers=headers)
        both = await http.post(
            "/v1/ingest/status", json={"conversation_id": "c", "task_id": "t"}, headers=headers
        )
    assert found.status_code == 200
    assert found.json() == {"state": "unknown"}
    assert both.status_code == 422
