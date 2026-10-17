"""The measurement routes of outcomes over `create_app` (7.7): a person with the analysis role, in a space
that turned `measurement` on, reads the outcomes and the attribution report and sends the BI file."""

import hashlib
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.signals import admin_router
from niadra.adapters.memory.fakes import StaticConfig
from niadra.app.outcomes import OutcomesService
from niadra.config.models import FeatureSettings
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.signals.storage import bi_key
from niadra.domain.vocabulary import Audience, Environment, Feature, Scope
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.measure.test_outcomes_flow import EXPOSURE, LINES, configured, order, shown

SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
PEOPLE = {
    "analyst": PersonPrincipal(SPACE, uuid7(), frozenset({"analysis"})),
    "integrator": PersonPrincipal(SPACE, uuid7(), frozenset({"integration"})),
}

KEYS = {
    # An analyst source with the `analytics` scope reads the reports that sum the space (19.4).
    "nia_sk_analyst": replace(
        principal(WHATSAPP_SOURCE, configured(), frozenset({Scope.ANALYTICS})), audience=Audience.ANALYST
    ),
    "nia_sk_agent": principal(WHATSAPP_SOURCE, configured(), frozenset({Scope.ANALYTICS})),
}


class FakeEdge:
    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token not in PEOPLE:
            raise Unauthenticated("unknown token")
        return PEOPLE[token]

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token not in KEYS:
            raise Unauthenticated("unknown key")
        return KEYS[token]

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Container:
    edge: FakeEdge
    config: StaticConfig
    outcomes: OutcomesService


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    cell.config.put(configured())
    flow = Flow.build(cell)
    hello = {
        "type": "event",
        "idempotency_key": "wa-1-hello",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": "+5511955554444"}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Oi"},
        "occurred_at": cell.clock.now().isoformat(),
    }
    await flow.send(WHATSAPP_SOURCE, hello)
    await flow.drain()
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    return flow


def client(flow: Flow) -> httpx.AsyncClient:
    app = create_app("niadra-test", Container(FakeEdge(), flow.cell.config, flow.outcomes), (admin_router,))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell")


async def reconcile(http: httpx.AsyncClient, flow: Flow, day: str, content: bytes) -> dict[str, Any]:
    """The BI file uploaded first, then reconciled by its upload id."""
    analyst = {"authorization": "Bearer analyst"}
    digest = hashlib.sha256(content).hexdigest()
    upload = await http.post(
        "/v1/measure/reconcile/uploads", json={"size_bytes": len(content), "sha256": digest}, headers=analyst
    )
    assert upload.status_code == 201, upload.text
    upload_id = upload.json()["upload_id"]
    flow.cell.objects.objects[bi_key(SPACE_ID, UUID(upload_id))] = content
    body = {"since": day, "until": day, "file": {"upload_id": upload_id}}
    found: dict[str, Any] = (await http.post("/v1/measure/reconcile", json=body, headers=analyst)).json()
    return found


async def test_the_analyst_reads_outcomes_and_the_report_and_reconciles(flow: Flow) -> None:
    analyst = {"authorization": "Bearer analyst"}
    async with client(flow) as http:
        page = (await http.get("/v1/measure/outcomes", headers=analyst)).json()
        line = next(i for i in page["items"] if i["method"] == "line")
        assert (line["object"], line["line"], line["finality"], line["value_minor"]) == (
            "order:shop:8812", "1", "provisional", 19990
        )  # fmt: skip
        assert line["exposure_id"] == str(EXPOSURE)
        assert "handle" not in str(line)
        day = line["valid_at"][:10]
        report = await http.get(
            "/v1/measure/attribution", params={"since": day, "until": day}, headers=analyst
        )
        assert {(t["method"], t["band"]) for t in report.json()["totals"]} >= {("line", "deterministic")}
        reconciled = await reconcile(http, flow, day, b"order,line,value,currency\n8812,1,199.90,BRL\n")
        assert (reconciled["matched"], reconciled["deviation"], reconciled["within_tolerance"]) == (
            1,
            0.0,
            True,
        )
        refused = await reconcile(http, flow, day, b"order,value\n8812,199.90\n")
        assert refused["code"] == "invalid_input"
        too_big = await http.post(
            "/v1/measure/reconcile/uploads",
            json={"size_bytes": 60_000_000, "sha256": "0" * 64},
            headers=analyst,
        )
        assert too_big.status_code == 422
        interleaving = await http.get(
            "/v1/measure/interleaving", params={"since": day, "until": day}, headers=analyst
        )
        assert interleaving.json() == {"since": day, "until": day, "rows": [], "totals": []}
        # Another role is refused; a space that did not turn measurement on answers 404.
        denied = await http.get("/v1/measure/outcomes", headers={"authorization": "Bearer integrator"})
        assert denied.status_code == 403
    flow.cell.config.put(
        configured().model_copy(update={"features": FeatureSettings(enabled=frozenset({Feature.SIGNALS}))})
    )
    async with client(flow) as http:
        assert (await http.get("/v1/measure/outcomes", headers=analyst)).status_code == 404


async def test_an_analyst_key_reads_the_reports_and_not_the_links(flow: Flow) -> None:
    async with client(flow) as http:
        analyst = {"authorization": "Bearer analyst"}
        day = (await http.get("/v1/measure/outcomes", headers=analyst)).json()["items"][0]["valid_at"][:10]
        key = {"authorization": "Bearer nia_sk_analyst"}
        period = {"since": day, "until": day}
        report = await http.get("/v1/measure/attribution", params=period, headers=key)
        assert report.status_code == 200, report.text
        assert {(t["method"], t["band"]) for t in report.json()["totals"]} >= {("line", "deterministic")}
        assert (await http.get("/v1/measure/interleaving", params=period, headers=key)).status_code == 200
        # The links one by one, and the BI file, stay with governance.
        links = await http.get("/v1/measure/outcomes", headers=key)
        assert (links.status_code, links.json()["code"]) == (403, "scope_missing")
        upload = await http.post(
            "/v1/measure/reconcile/uploads", json={"size_bytes": 10, "sha256": "0" * 64}, headers=key
        )
        assert upload.status_code == 403
        agent = {"authorization": "Bearer nia_sk_agent"}
        refused = await http.get("/v1/measure/attribution", params=period, headers=agent)
        assert (refused.status_code, refused.json()["code"]) == (403, "scope_missing")
