"""The HTTP adapter that delivers the assistant's diff to the control plane (7.4)."""

import json

import httpx
import pytest
import respx

from niadra.adapters.outbound.controlplane.proposals import CELL_TOKEN_HEADER, HttpConfigProposals
from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.ports.proposals import ConfigProposal
from niadra.testing.cell import SPACE_ID

PROPOSAL = ConfigProposal(SPACE_ID, uuid7(), "schemas", {"version": "s2"}, "configuration assistant run")


@respx.mock
async def test_posts_only_the_document_with_the_cell_token():
    route = respx.post("https://control.test/v1/config/proposals").mock(
        return_value=httpx.Response(201, json={"diff_id": "d-1"})
    )
    async with httpx.AsyncClient(base_url="https://control.test") as client:
        assert await HttpConfigProposals(client, "cell-1", "secret").submit(PROPOSAL) == "d-1"
    request = route.calls.last.request
    assert request.headers[CELL_TOKEN_HEADER] == "secret"
    assert json.loads(request.content) == {
        "cell_id": "cell-1",
        "space_id": str(SPACE_ID),
        "type": "schemas",
        "document": {"version": "s2"},
        "reason": "configuration assistant run",
    }


@respx.mock
@pytest.mark.parametrize(("status", "error"), [(503, Unavailable), (429, Unavailable), (422, InvalidInput)])
async def test_errors_retry_or_fail_the_run(status: int, error: type[Exception]):
    respx.post("https://control.test/v1/config/proposals").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient(base_url="https://control.test") as client:
        with pytest.raises(error):
            await HttpConfigProposals(client, "cell-1", "secret").submit(PROPOSAL)


@respx.mock
async def test_network_errors_are_unavailable():
    respx.post("https://control.test/v1/config/proposals").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient(base_url="https://control.test") as client:
        with pytest.raises(Unavailable):
            await HttpConfigProposals(client, "cell-1", "secret").submit(PROPOSAL)


@respx.mock
async def test_what_the_observed_layer_inferred_goes_to_its_own_route_with_the_cell_token():
    route = respx.post("https://control.test/v1/config/observed").mock(
        return_value=httpx.Response(201, json={"diff_id": "d-2"})
    )
    document = {"types": [{"type": "deadline"}]}
    async with httpx.AsyncClient(base_url="https://control.test") as client:
        recorded = await HttpConfigProposals(client, "cell-1", "secret").record(
            SPACE_ID, "object-types", document, "observed from the traffic"
        )
    assert recorded == "d-2"
    request = route.calls.last.request
    assert request.headers[CELL_TOKEN_HEADER] == "secret"
    assert json.loads(request.content)["type"] == "object-types"
    assert json.loads(request.content)["document"] == document
