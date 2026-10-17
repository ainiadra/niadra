"""The governance routes of measure, review and the assistant over `create_app` (7.1)."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.measure import router
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.app.configure import AssistService
from niadra.app.measure import MeasureService
from niadra.app.review import ReviewService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.measure.model import DailyCounters, DailyKey
from niadra.domain.vocabulary import Audience, Environment, Scope
from niadra.ports.proposals import ConfigProposal
from niadra.testing.cell import SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit

SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
PEOPLE = {
    "analyst": PersonPrincipal(SPACE, uuid7(), frozenset({"analysis"})),
    "vendor-a": PersonPrincipal(
        SPACE, uuid7(), frozenset({"vendor"}), allowed_sources=frozenset({WHATSAPP_SOURCE})
    ),
    "reviewer": PersonPrincipal(SPACE, uuid7(), frozenset({"review"})),
    "integrator": PersonPrincipal(SPACE, uuid7(), frozenset({"integration"})),
}


KEYS = {
    # An agent's key with every scope, `admin` and `analytics` too: its own source only.
    "nia_sk_whatsapp": principal(WHATSAPP_SOURCE, scopes=frozenset(Scope)),
    # An analyst source with the `analytics` scope: the space's aggregates across its sources (19.4).
    "nia_sk_analyst": replace(
        principal(WHATSAPP_SOURCE, scopes=frozenset({Scope.ANALYTICS})), audience=Audience.ANALYST
    ),
    # `analytics` on an agent's key is not an analyst's.
    "nia_sk_agent": principal(WHATSAPP_SOURCE, scopes=frozenset({Scope.ANALYTICS})),
}


class FakeEdge:
    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token not in PEOPLE:
            raise Unauthenticated("unknown token")
        return PEOPLE[token]

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        return KEYS[token]

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


class NoProposals:
    async def submit(self, proposal: ConfigProposal) -> str:
        return "diff-1"


@dataclass
class Container:
    edge: FakeEdge
    measure: MeasureService
    review: ReviewService
    configure: AssistService
    admin_audit: RecordingAdminAudit


@pytest.fixture
def actions() -> RecordingAdminAudit:
    return RecordingAdminAudit()


@pytest.fixture
async def client(cell: Cell, actions: RecordingAdminAudit):
    repo = InMemoryMeasureRepository(cell.uow.store)
    today = cell.clock.now().date()
    for source, used in ((VOICE_SOURCE, 3), (WHATSAPP_SOURCE, 1)):
        await repo.add_daily(
            DailyKey(today, source, "voice", "voice", ""),
            DailyCounters(deliveries=4, deliveries_used=used, questions=5, repeated=2, sessions=4),
        )
    container = Container(
        edge=FakeEdge(),
        measure=MeasureService(cell.uow, cell.clock, cell.config),
        review=ReviewService(cell.uow, cell.clock, cell.config),
        configure=AssistService(cell.uow, ScriptedLlm(), cell.clock, cell.config, NoProposals()),
        admin_audit=actions,
    )
    app = create_app("niadra-admin", container, [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as c:
        yield c


def auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_context_use_aggregates_with_intervals(client: httpx.AsyncClient):
    response = await client.get("/v1/context-use", headers=auth("analyst"))
    assert response.status_code == 200
    buckets = {UUID(b["source_id"]): b for b in response.json()["buckets"]}
    assert buckets[VOICE_SOURCE]["usage_rate"]["value"] == 0.75
    assert buckets[VOICE_SOURCE]["usage_rate"]["low"] < 0.75 < buckets[VOICE_SOURCE]["usage_rate"]["high"]


async def test_a_vendor_sees_only_its_sources(client: httpx.AsyncClient):
    response = await client.get("/v1/context-use", headers=auth("vendor-a"))
    assert [b["source_id"] for b in response.json()["buckets"]] == [str(WHATSAPP_SOURCE)]
    key = await client.get("/v1/context-use", headers=auth("nia_sk_whatsapp"))
    assert [b["source_id"] for b in key.json()["buckets"]] == [str(WHATSAPP_SOURCE)]


async def test_an_analyst_key_reads_the_report_across_the_sources_and_no_conversation(
    client: httpx.AsyncClient, cell: Cell
):
    analyst = auth("nia_sk_analyst")
    report = await client.get("/v1/context-use", headers=analyst)
    assert report.status_code == 200, report.text
    assert {UUID(b["source_id"]) for b in report.json()["buckets"]} == {VOICE_SOURCE, WHATSAPP_SOURCE}
    other = await client.get(f"/v1/context-use?source_id={VOICE_SOURCE}", headers=analyst)
    assert [UUID(b["source_id"]) for b in other.json()["buckets"]] == [VOICE_SOURCE]
    # Aggregates only: the routes that list or open one conversation take governance, as before.
    today = cell.clock.now().date().isoformat()
    for response in (
        await client.get(f"/v1/context-use/conversations?since={today}&until={today}", headers=analyst),
        await client.get("/v1/context-use/call-1", headers=analyst),
        await client.post(
            "/v1/context-use/conversation", json={"conversation_id": "call-1"}, headers=analyst
        ),
    ):
        assert (response.status_code, response.json()["code"]) == (403, "scope_missing")
    agent = await client.get("/v1/context-use", headers=auth("nia_sk_agent"))
    assert (agent.status_code, agent.json()["code"]) == (403, "scope_missing")
    assert "analytics scope of an analyst source" in agent.json()["detail"]


async def test_context_use_by_vendor_sums_counts_and_rates_on_the_server(client: httpx.AsyncClient):
    response = await client.get("/v1/context-use?group_by=vendor", headers=auth("analyst"))
    buckets = {b["vendor"]: b for b in response.json()["buckets"]}
    assert set(buckets) == {"A", "B"}
    assert (buckets["B"]["deliveries"], buckets["B"]["deliveries_used"]) == (4, 3)
    assert buckets["B"]["usage_rate"]["value"] == 0.75
    assert buckets["B"]["usage_rate"]["low"] < 0.75
    mine = await client.get("/v1/context-use?group_by=vendor", headers=auth("vendor-a"))
    assert [b["vendor"] for b in mine.json()["buckets"]] == ["A"]


async def test_group_by_is_validated_and_roles_are_enforced(client: httpx.AsyncClient, cell: Cell):
    since = (cell.clock.now().date() - timedelta(days=1)).isoformat()
    ok = await client.get(
        f"/v1/context-use?group_by=day&group_by=channel&since={since}", headers=auth("analyst")
    )
    assert [(b["day"], b["channel"]) for b in ok.json()["buckets"]] == [
        (date(2026, 9, 22).isoformat(), "voice")
    ]
    bad = await client.get("/v1/context-use?group_by=profile_id", headers=auth("analyst"))
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_input")
    assert (await client.get("/v1/context-use", headers=auth("reviewer"))).status_code == 403
    assert (await client.get("/v1/context-use")).status_code == 401


async def test_unknown_conversation_is_not_found(client: httpx.AsyncClient):
    response = await client.get("/v1/context-use/call-404", headers=auth("analyst"))
    assert response.status_code == 404
    # The same read with the conversation id in the body.
    by_body = await client.post(
        "/v1/context-use/conversation", json={"conversation_id": "call-404"}, headers=auth("analyst")
    )
    assert by_body.status_code == 404


async def test_review_routes(client: httpx.AsyncClient):
    queue = await client.get("/v1/review/queue", headers=auth("reviewer"))
    assert queue.json() == {"items": []}
    missing = await client.post(
        f"/v1/review/{uuid7()}/verdicts",
        headers=auth("reviewer"),
        json={"verdicts": [{"target": "fact", "target_id": "f", "verdict": "correct"}]},
    )
    assert missing.status_code == 404
    as_key = await client.post(
        f"/v1/review/{uuid7()}/verdicts",
        headers=auth("nia_sk_whatsapp"),
        json={"verdicts": [{"target": "fact", "target_id": "f", "verdict": "correct"}]},
    )
    assert as_key.status_code == 403
    agreement = await client.get("/v1/review/agreement", headers=auth("reviewer"))
    assert agreement.json()["targets"] == []
    cases = await client.get("/v1/review/cases", params={"days": 30}, headers=auth("reviewer"))
    assert (cases.status_code, cases.json()["tallies"], cases.json()["failing"]) == (200, [], [])


async def test_assist_routes(client: httpx.AsyncClient, actions: RecordingAdminAudit):
    created = await client.post(
        "/v1/assist",
        headers=auth("integrator"),
        json={"task": "extraction_schema", "source_id": str(WHATSAPP_SOURCE)},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert actions.records == [
        {"action": "assist.request", "profile_id": None, "target": f"assist_run:{created.json()['run_id']}"}
    ]
    fetched = await client.get(f"/v1/assist/{created.json()['run_id']}", headers=auth("integrator"))
    assert fetched.json()["run_id"] == created.json()["run_id"]
    invalid = await client.post("/v1/assist", headers=auth("integrator"), json={"task": "webhook_mapping"})
    assert invalid.status_code == 422
    assert (
        await client.post("/v1/assist", headers=auth("analyst"), json={"task": "source_policy"})
    ).status_code == 403
