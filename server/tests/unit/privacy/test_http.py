"""The governance routes over the real services, with a fake edge for authentication."""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.governance import router
from niadra.adapters.memory.fakes import InMemoryObjectStore
from niadra.app.audit import AuditService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.vocabulary import Environment, Scope
from niadra.testing.cell import SPACE_ID, TENANT_ID, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit

from .world import World


class FakeEdge:
    async def authenticate_key(self, token: str) -> Any:
        scopes = frozenset(Scope) if token.endswith("admin") else frozenset({Scope.CONTEXT})
        return principal(scopes=scopes)

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token == "expired":
            raise Unauthenticated("expired token")
        roles = frozenset({token})
        return PersonPrincipal(
            SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX), uuid4(), roles
        )


class NoReceipts:
    async def batches(self, max_items: int):  # type: ignore[no-untyped-def]
        return
        yield


@dataclass
class Container:
    edge: FakeEdge
    audit: AuditService
    privacy: Any
    admin_audit: RecordingAdminAudit
    purposes: Any = None


@pytest.fixture
def actions() -> RecordingAdminAudit:
    return RecordingAdminAudit()


@pytest.fixture
async def client(
    cell: Cell, world: World, make_privacy: Any, actions: RecordingAdminAudit
) -> httpx.AsyncClient:
    world.person("+5511999990001")
    marina = next(iter(world.handles))
    world.event(marina, "c-1", "hello")
    privacy = make_privacy()
    audit = AuditService(cell.uow, NoReceipts(), InMemoryObjectStore(), cell.clock)
    app = create_app("admin", Container(FakeEdge(), audit, privacy, actions), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        yield http


SECURITY = {"Authorization": "Bearer security"}


async def test_forget_needs_an_idempotency_key_and_answers_202(
    client: httpx.AsyncClient, actions: RecordingAdminAudit
) -> None:
    body = {"target": "handle", "handle": {"type": "phone_e164", "value": "+5511999990001"}}
    missing = await client.post("/v1/forget", json=body, headers=SECURITY)
    assert missing.status_code == 422
    assert actions.records == [], "nothing happened, nothing is recorded"
    accepted = await client.post("/v1/forget", json=body, headers={**SECURITY, "Idempotency-Key": "k1"})
    assert accepted.status_code == 202
    assert actions.records == [
        {"action": "privacy.forget", "profile_id": None, "target": f"erasure:{accepted.json()['request_id']}"}
    ]
    assert "+5511" not in str(actions.records)
    assert accepted.json()["status"] == "pending"
    assert "+5511" not in accepted.text
    status = await client.get(f"/v1/forget/{accepted.json()['request_id']}", headers=SECURITY)
    assert status.status_code == 200
    assert status.json()["request_id"] == accepted.json()["request_id"]


async def test_a_reused_key_with_another_person_answers_409(client: httpx.AsyncClient) -> None:
    first = {"target": "handle", "handle": {"type": "phone_e164", "value": "+5511999990001"}}
    second = {"target": "handle", "handle": {"type": "phone_e164", "value": "+5511999990009"}}
    headers = {**SECURITY, "Idempotency-Key": "same-key"}
    assert (await client.post("/v1/forget", json=first, headers=headers)).status_code == 202
    refused = await client.post("/v1/forget", json=second, headers=headers)
    assert refused.status_code == 409
    assert refused.headers["content-type"].startswith("application/problem+json")
    assert "+5511" not in refused.text


async def test_erasure_requests_are_listed_newest_first(client: httpx.AsyncClient) -> None:
    body = {"target": "handle", "handle": {"type": "phone_e164", "value": "+5511999990001"}}
    created = [
        (await client.post("/v1/forget", json=body, headers={**SECURITY, "Idempotency-Key": key})).json()
        for key in ("k1", "k2")
    ]
    first = await client.get("/v1/forget", params={"limit": 1}, headers=SECURITY)
    assert first.status_code == 200
    rest = await client.get(
        "/v1/forget", params={"limit": 5, "cursor": first.json()["next_cursor"]}, headers=SECURITY
    )
    listed = [e["request_id"] for e in first.json()["items"] + rest.json()["items"]]
    assert sorted(listed) == sorted(e["request_id"] for e in created)
    assert rest.json()["next_cursor"] is None
    assert "+5511" not in first.text + rest.text
    running = await client.get("/v1/forget", params={"status": "completed"}, headers=SECURITY)
    assert running.json() == {"items": [], "next_cursor": None}
    assert (await client.get("/v1/forget", params={"cursor": "%%%"}, headers=SECURITY)).status_code == 422
    assert (await client.get("/v1/forget", headers={"Authorization": "Bearer analysis"})).status_code == 403


async def test_forget_rejects_a_body_naming_two_targets(client: httpx.AsyncClient) -> None:
    body = {"target": "profile", "profile_id": str(uuid4()), "conversation_id": "c-1"}
    response = await client.post("/v1/forget", json=body, headers={**SECURITY, "Idempotency-Key": "k"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"


async def test_governance_needs_the_security_role_or_an_admin_key(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/receipts", headers={"Authorization": "Bearer viewer"})).status_code == 403
    assert (
        await client.get("/v1/receipts", headers={"Authorization": "Bearer nia_sk_agent"})
    ).status_code == 403
    assert (
        await client.get("/v1/receipts", headers={"Authorization": "Bearer nia_sk_admin"})
    ).status_code == 200
    assert (await client.get("/v1/receipts")).status_code == 401


async def test_receipts_and_chain_verification(client: httpx.AsyncClient) -> None:
    page = await client.get("/v1/receipts", params={"kind": "read", "limit": 10}, headers=SECURITY)
    assert page.json() == {"items": [], "next_cursor": None}
    # The same filters in a body, so a conversation id never goes in a URL.
    body = {"kind": "read", "limit": 10, "conversation_id": "+5511999990001"}
    searched = await client.post("/v1/receipts/search", json=body, headers=SECURITY)
    assert searched.json() == {"items": [], "next_cursor": None}
    assert (await client.post("/v1/receipts/search", json=body)).status_code == 401
    verified = await client.get(
        "/v1/receipts/verify", params={"day": date(2026, 9, 22).isoformat()}, headers=SECURITY
    )
    assert verified.json()["valid"] is True
    assert verified.json()["root_matches"] is None
    missing = await client.get(f"/v1/lineage/receipt/{uuid4()}", headers=SECURITY)
    assert missing.status_code == 404
    viewers = await client.get(f"/v1/lineage/fact/{uuid4()}", headers=SECURITY)
    assert viewers.json()["receipts"] == []


async def test_export_package_downloads_as_a_zip(
    client: httpx.AsyncClient, actions: RecordingAdminAudit
) -> None:
    body = {"handle": {"type": "phone_e164", "value": "+5511999990001"}}
    created = await client.post("/v1/export", json=body, headers=SECURITY)
    assert created.status_code == 201
    run_id, profile_id = created.json()["run_id"], created.json()["profile_id"]
    assert created.json()["download_url"].startswith("https://objects.test/exports/")
    download = await client.get(f"/v1/exports/{run_id}/download", headers=SECURITY)
    assert download.status_code == 200
    assert actions.records == [
        {"action": "privacy.export", "profile_id": UUID(profile_id), "target": f"export_run:{run_id}"},
        {"action": "privacy.export_download", "profile_id": None, "target": f"export_run:{run_id}"},
    ]
    assert download.headers["content-type"] == "application/zip"
    assert download.content[:2] == b"PK"
    runs = await client.get("/v1/exports/runs", params={"schedule_id": "on_demand"}, headers=SECURITY)
    assert [r["run_id"] for r in runs.json()["items"]] == [created.json()["run_id"]]


async def test_secrets_are_write_only(
    client: httpx.AsyncClient, cell: Cell, actions: RecordingAdminAudit
) -> None:
    written = await client.put("/v1/secrets/webhook/wh-1", json={"value": "s3cr3t"}, headers=SECURITY)
    assert written.status_code == 200
    assert written.json() == {"version": 1}
    assert "s3cr3t" not in written.text
    assert "s3cr3t" in cell.secrets.values.values()
    wrong_kind = await client.put("/v1/secrets/database/x", json={"value": "v"}, headers=SECURITY)
    assert wrong_kind.status_code == 422
    credential = await client.put(
        "/v1/exports/destinations/lake/credential", json={"value": "k"}, headers=SECURITY
    )
    assert credential.status_code == 200
    unknown = await client.put(
        "/v1/exports/destinations/nope/credential", json={"value": "k"}, headers=SECURITY
    )
    assert unknown.status_code == 404
    assert [(r["action"], r["target"]) for r in actions.records] == [
        ("privacy.secret", "webhook_secret:wh-1"),
        ("privacy.destination_credential", "export_schedule:lake"),
    ]
    assert "s3cr3t" not in str(actions.records)
