"""`GET /v1/usage` over the real FastAPI app with a small container."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.usage import router
from niadra.adapters.memory.metering import InMemoryUsageRepository
from niadra.app.extract import LlmBudget
from niadra.app.metering import MeteringService
from niadra.config.models import SpaceLimits
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.metering import Metric
from niadra.domain.metering.prompt_cache import ModelUsageCounters, ModelUsageKey, ModelUsageRow
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import (
    SPACE_ID,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)

H = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)


class FakeEdge:
    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "person-jwt":
            raise Unauthenticated("bad token")
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space=space, user_id=uuid7(), roles=self.roles)

    async def authenticate_key(self, token: str) -> Any:
        return principal()

    async def check_rate(self, *args: Any) -> None: ...


@dataclass
class Container:
    edge: FakeEdge
    metering: MeteringService


@pytest.fixture
async def seeded(cell: Cell) -> MeteringService:
    service = MeteringService(cell.uow, cell.clock)
    repo = InMemoryUsageRepository(cell.uow.store)
    repo.seed_source(SPACE_ID, H, Metric.SESSION_BILLABLE.value, 5, source_id=WHATSAPP_SOURCE)
    repo.seed_source(SPACE_ID, H, Metric.SESSION_BILLABLE.value, 2, source_id=VOICE_SOURCE)
    repo.seed_source(SPACE_ID, H, Metric.EXPORT_RUN.value, 1)
    await service.rollup_hour(SPACE_ID, H)
    return service


def client(service: MeteringService, roles: frozenset[str]) -> TestClient:
    return TestClient(create_app("admin", Container(FakeEdge(roles), service), [router]))


def params(**extra: str) -> dict[str, str]:
    return {
        "since": (H - timedelta(hours=1)).isoformat(),
        "until": (H + timedelta(hours=1)).isoformat(),
        **extra,
    }


def test_analysts_read_hourly_usage(seeded: MeteringService) -> None:
    response = client(seeded, frozenset({"analysis"})).get(
        "/v1/usage", params=params(), headers={"authorization": "Bearer person-jwt"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["granularity"] == "hour"
    assert body["items"] == [
        {"start": "2026-09-22T16:00:00Z", "metric": m, "quantity": q, "cost_micros": 0, "source_id": None}
        for m, q in (("export_run", 1), ("session_billable", 7))
    ]


def test_usage_per_source_and_for_one_source(seeded: MeteringService) -> None:
    http = client(seeded, frozenset({"analysis"}))
    headers = {"authorization": "Bearer person-jwt"}
    grouped = http.get("/v1/usage", params=params(group_by="source", granularity="day"), headers=headers)
    assert grouped.json()["group_by"] == "source"
    rows = {(i["metric"], i["source_id"]): i["quantity"] for i in grouped.json()["items"]}
    assert rows == {
        ("export_run", None): 1,
        ("session_billable", str(VOICE_SOURCE)): 2,
        ("session_billable", str(WHATSAPP_SOURCE)): 5,
    }
    one = http.get("/v1/usage", params=params(source_id=str(VOICE_SOURCE)), headers=headers).json()
    assert [(i["metric"], i["quantity"], i["source_id"]) for i in one["items"]] == [
        ("session_billable", 2, None)
    ]
    assert one["source_id"] == str(VOICE_SOURCE)
    bad = http.get("/v1/usage", params=params(group_by="vendor"), headers=headers)
    assert bad.status_code == 422


def test_daily_granularity_and_metric_filter(seeded: MeteringService) -> None:
    http = client(seeded, frozenset({"admin"}))
    headers = {"authorization": "Bearer person-jwt"}
    daily = http.get("/v1/usage", params=params(granularity="day"), headers=headers).json()
    assert daily["items"][0]["start"] == "2026-09-22T00:00:00Z"
    filtered = http.get("/v1/usage", params=params(metric="context_read"), headers=headers).json()
    assert filtered["items"] == []


def test_other_roles_and_anonymous_callers_are_refused(seeded: MeteringService) -> None:
    assert (
        client(seeded, frozenset({"reviewer"}))
        .get("/v1/usage", params=params(), headers={"authorization": "Bearer person-jwt"})
        .status_code
        == 403
    )
    assert client(seeded, frozenset({"analysis"})).get("/v1/usage", params=params()).status_code == 401


def test_an_inverted_range_is_a_problem_response(seeded: MeteringService) -> None:
    response = client(seeded, frozenset({"analysis"})).get(
        "/v1/usage",
        params={"since": H.isoformat(), "until": (H - timedelta(hours=1)).isoformat()},
        headers={"authorization": "Bearer person-jwt"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def test_the_prompt_cache_report_for_analysts_and_vendors(cell: Cell) -> None:
    # `GET /v1/usage/prompt-cache`, the estimate labeled as one; a vendor sees its own agents.
    service = MeteringService(cell.uow, cell.clock, cell.config)
    key = ModelUsageKey(H.date(), WHATSAPP_SOURCE, "openai", "gpt-4.1-2025-04-14")
    other = ModelUsageKey(H.date(), VOICE_SOURCE, "anthropic", "claude-haiku-4-5")
    repo = InMemoryUsageRepository(cell.uow.store)
    asyncio.run(
        repo.add_model_usage(
            [
                ModelUsageRow(key, ModelUsageCounters(2, 6000, 4096)),
                ModelUsageRow(other, ModelUsageCounters(1, 3000, 2000, 100)),
            ]
        )
    )
    headers = {"authorization": "Bearer person-jwt"}
    day = H.date().isoformat()
    body = (
        client(service, frozenset({"analysis"}))
        .get("/v1/usage/prompt-cache", params={"since": day, "until": day}, headers=headers)
        .json()
    )
    assert body["estimate"] is True
    whatsapp = next(b for b in body["buckets"] if b["source_id"] == str(WHATSAPP_SOURCE))
    assert (whatsapp["cached_tokens"], whatsapp["estimated_savings_micros"]) == (4096, 6144)
    vendor = client(service, frozenset({"vendor"}))
    seen = vendor.get("/v1/usage/prompt-cache", params={"since": day, "until": day}, headers=headers)
    assert seen.status_code == 200
    assert (
        client(service, frozenset({"reviewer"})).get("/v1/usage/prompt-cache", headers=headers).status_code
        == 403
    )
    bad = client(service, frozenset({"analysis"})).get(
        "/v1/usage/prompt-cache", params={"group_by": "profile_id"}, headers=headers
    )
    assert bad.status_code == 422


def test_the_budget_shows_todays_spend_against_the_contracts_ceiling(cell: Cell) -> None:
    # What the Console shows beside the usage, and why memory arrives late when it does.
    limits = SpaceLimits(llm_daily_micros={"extraction": 1000})
    cell.config.put(sample_config().model_copy(update={"limits": limits}))
    service = MeteringService(cell.uow, cell.clock, cell.config)
    budget = LlmBudget(cell.uow, cell.clock, config=cell.config)

    async def spend() -> None:
        await budget.settle(await budget.reserve(SPACE_ID, "extraction"), 1200)

    asyncio.run(spend())
    headers = {"authorization": "Bearer person-jwt"}
    body = client(service, frozenset({"analysis"})).get("/v1/usage/budget", headers=headers).json()
    lines = {line["purpose"]: line for line in body["items"]}
    assert lines["extraction"] == {
        "purpose": "extraction",
        "day": cell.clock.now().date().isoformat(),
        "cap_micros": 1000,
        "from_contract": True,
        "spent_micros": 1200,
        "reached": True,
        "resumes_at": "2026-09-23T00:00:00Z",
    }
    assert (lines["assistant"]["cap_micros"], lines["assistant"]["from_contract"]) == (200_000, False)
    assert lines["assistant"]["reached"] is False
    refused = client(service, frozenset({"reviewer"})).get("/v1/usage/budget", headers=headers)
    assert refused.status_code == 403
