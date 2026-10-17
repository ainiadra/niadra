"""The identity routes over the real FastAPI app, with the service on the in-memory cell."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.identity import router
from niadra.app.identity import IdentityService
from niadra.domain.common.errors import Conflict, Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Environment, HandleType, Scope, SubjectKind
from niadra.testing.cell import SPACE_ID, TENANT_ID, principal
from tests.unit.identity.conftest import Env, cpf, handle

T = HandleType
PHONE = {"type": "phone_e164", "value": "+55 11 98765-4321"}
EMAIL = {"type": "email", "value": "ana.silva@acme.com.br"}
CPF = {"type": "gov_id_hmac", "value": cpf("529982247")}


@dataclass
class FakeEdge:
    roles: frozenset[str] = frozenset({"integration"})
    key_scopes: frozenset[Scope] | None = None
    remember: bool = False
    answers: dict[str, tuple[bytes, tuple[int, dict[str, Any]]]] = field(default_factory=dict)

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "person-jwt":
            raise Unauthenticated("bad token")
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space=space, user_id=UUID(int=7), roles=self.roles)

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        return principal(scopes=self.key_scopes)

    async def check_rate(self, *args: Any) -> None: ...

    async def idempotent(
        self,
        space_id: UUID,
        key: str,
        body_hash: bytes,
        compute: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any]]:
        if not self.remember:
            return await compute()
        if key in self.answers:
            stored_hash, answer = self.answers[key]
            if stored_hash != body_hash:
                raise Conflict("the key was used with another body")
            return answer
        answer = await compute()
        self.answers[key] = (body_hash, answer)
        return answer


@dataclass
class Receipt:
    principal: Any
    action: str
    profile_id: UUID | None
    target: str | None


@dataclass
class RecordingAudit:
    receipts: list[Receipt] = field(default_factory=list)

    async def record(
        self, principal: Any, *, action: str, profile_id: UUID | None = None, target: str | None = None
    ) -> None:
        self.receipts.append(Receipt(principal, action, profile_id, target))

    @property
    def actions(self) -> list[str]:
        return [r.action for r in self.receipts]


@dataclass
class Container:
    edge: FakeEdge
    identity: IdentityService
    admin_audit: RecordingAudit


@pytest.fixture
def edge() -> FakeEdge:
    return FakeEdge()


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit()


@pytest.fixture
async def http(env: Env, edge: FakeEdge, audit: RecordingAudit) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("admin", Container(edge, env.service, audit), [router])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
        client.headers["authorization"] = "Bearer person-jwt"
        yield client


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid7())}


async def test_governance_posts_require_an_idempotency_key(http: httpx.AsyncClient) -> None:
    response = await http.post("/v1/identity/assertions", json={"handles": [PHONE, EMAIL]})
    assert response.status_code == 422
    assert "idempotency-key" in response.json()["detail"].lower()


async def test_assertion_is_queued_then_applied_by_the_worker(http: httpx.AsyncClient, env: Env) -> None:
    await env.attach(handle(T.PHONE_E164, PHONE["value"]))
    await env.attach(handle(T.EMAIL, EMAIL["value"]))
    response = await http.post("/v1/identity/assertions", json={"handles": [PHONE, EMAIL]}, headers=key())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    await env.drain()
    listed = await http.get("/v1/identity/assertions", params={"state": "applied"})
    assert [a["assertion_id"] for a in listed.json()["items"]] == [body["assertion_id"]]
    assert listed.json()["items"][0]["actor"] == f"user:{UUID(int=7)}"


async def test_idempotency_key_replays_the_first_answer(
    env: Env, edge: FakeEdge, http: httpx.AsyncClient
) -> None:
    edge.remember = True
    await env.attach(handle(T.PHONE_E164, PHONE["value"]))
    await env.attach(handle(T.EMAIL, EMAIL["value"]))
    headers = key()
    first = await http.post("/v1/identity/assertions", json={"handles": [PHONE, EMAIL]}, headers=headers)
    again = await http.post("/v1/identity/assertions", json={"handles": [PHONE, EMAIL]}, headers=headers)
    assert first.json() == again.json()
    assert len(env.outbox.take(TaskName.IDENTITY_APPLY)) == 1
    other = await http.post("/v1/identity/assertions", json={"handles": [EMAIL, PHONE]}, headers=headers)
    assert other.status_code == 409


async def test_merge_unmerge_retract_and_block(http: httpx.AsyncClient, env: Env) -> None:
    first = (await env.attach(handle(T.PHONE_E164, PHONE["value"]))).profile_id
    second = (await env.attach(handle(T.EMAIL, EMAIL["value"]))).profile_id
    forced = await http.post(
        "/v1/identity/merge", json={"profile_ids": [str(first), str(second)], "force": True}, headers=key()
    )
    assert forced.status_code == 422
    merged = await http.post(
        "/v1/identity/merge", json={"profile_ids": [str(first), str(second)]}, headers=key()
    )
    assert merged.status_code == 202
    await env.drain()
    email_id = (await env.find(handle(T.EMAIL, EMAIL["value"]))).handle_id
    survivor = (await env.profile(handle(T.EMAIL, EMAIL["value"]))).profile_id
    split = await http.post(
        "/v1/identity/unmerge",
        json={"profile_id": str(survivor), "handle_ids": [str(email_id)]},
        headers=key(),
    )
    assert split.status_code == 202
    await env.drain()
    assert not await env.same(handle(T.PHONE_E164, PHONE["value"]), handle(T.EMAIL, EMAIL["value"]))
    assertion_id = json_first(await http.get("/v1/identity/assertions"))["assertion_id"]
    retracted = await http.post(f"/v1/identity/assertions/{assertion_id}/retract", json={}, headers=key())
    assert retracted.status_code == 202
    blocked = await http.post(
        "/v1/identity/handles/block", json={"handle": PHONE, "reason": "qa"}, headers=key()
    )
    assert (blocked.status_code, blocked.json()["status"]) == (202, "queued")
    missing = await http.post(f"/v1/identity/assertions/{uuid4()}/retract", json={}, headers=key())
    assert missing.status_code == 404


def json_first(response: httpx.Response) -> dict[str, Any]:
    items: list[dict[str, Any]] = response.json()["items"]
    return items[0]


async def test_person_and_organization_assertion_answers_linked(http: httpx.AsyncClient) -> None:
    org = {"type": "org_registry_hmac", "value": "11.222.333/0001-81"}
    response = await http.post("/v1/identity/assertions", json={"handles": [CPF, org]}, headers=key())
    assert (response.status_code, response.json()["status"]) == (200, "linked")


async def test_links_and_profile_with_masked_handles(
    http: httpx.AsyncClient, env: Env, edge: FakeEdge
) -> None:
    result = await env.attach(*(handle(h["type"], h["value"]) for h in (PHONE, EMAIL, CPF)))
    org = {"type": "system_id", "value": "carrier-1", "scope": "tms", "subject_kind": "partner"}
    created = await http.post(
        "/v1/identity/links", json={"person": PHONE, "organization": org, "role": "driver"}, headers=key()
    )
    assert created.status_code == 201
    link_id = created.json()["link_id"]

    profile = (await http.get(f"/v1/profiles/{result.profile_id}")).json()
    values = {h["type"]: h["value"] for h in profile["handles"]}
    assert values == {
        "phone_e164": "+55*******4321",
        "email": "a***@acme.com.br",
        "gov_id_hmac": "*********25",
    }
    assert [link["role"] for link in profile["links"]] == ["driver"]
    assert profile["partial"] is False
    assert profile["pseudonym"].startswith("p_")

    edge.roles = frozenset({"security"})
    revealed = (await http.get(f"/v1/profiles/{result.profile_id}")).json()
    assert {h["type"]: h["value"] for h in revealed["handles"]}["phone_e164"] == "+5511987654321"
    assert {h["type"]: h["value"] for h in revealed["handles"]}["gov_id_hmac"] == "*********25"

    ended = await http.post(f"/v1/identity/links/{link_id}/end", json={}, headers=key())
    assert ended.status_code == 200
    assert ended.json()["valid_to"] is not None
    assert (await http.get(f"/v1/profiles/{result.profile_id}")).json()["links"] == []
    assert (await http.get(f"/v1/profiles/{uuid4()}")).status_code == 404


async def test_roles_and_source_keys(http: httpx.AsyncClient, edge: FakeEdge) -> None:
    edge.roles = frozenset({"review"})
    assert (await http.get("/v1/identity/assertions")).status_code == 403
    http.headers["authorization"] = "Bearer nia_sk_live_x"
    assert (await http.get("/v1/identity/assertions")).status_code == 403


async def test_a_person_reading_a_profile_leaves_a_receipt(
    http: httpx.AsyncClient, env: Env, audit: RecordingAudit
) -> None:
    # People's reads are on the chain like agents' reads.
    result = await env.attach(handle(PHONE["type"], PHONE["value"]), handle(EMAIL["type"], EMAIL["value"]))
    assert (await http.get(f"/v1/profiles/{result.profile_id}")).status_code == 200
    assert [(r.action, r.profile_id) for r in audit.receipts] == [("profile.read", result.profile_id)]


async def test_an_organization_stands_alone(http: httpx.AsyncClient, env: Env) -> None:
    """An organization is never placed under another: no route records a parent, and its profile carries its
    own identifiers and the people linked to it, nothing above or below it."""
    north = {"type": "system_id", "value": "ACC-2201", "scope": "crm", "subject_kind": "account"}
    group = {"type": "org_registry_hmac", "value": "11.222.333/0001-81"}
    body = {"organization": north, "parent": group, "method": "system_import"}

    assert (await http.post("/v1/identity/parents", json=body, headers=key())).status_code == 404
    assert (await http.post(f"/v1/identity/parents/{uuid4()}/end", json={}, headers=key())).status_code == 404
    link = {"person": PHONE, "organization": north, "role": "buyer"}
    created = await http.post("/v1/identity/links", json=link, headers=key())
    assert created.status_code == 201, created.text
    company = await env.profile(handle(T.SYSTEM_ID, "ACC-2201", "crm", SubjectKind.ACCOUNT))
    profile = (await http.get(f"/v1/profiles/{company.profile_id}")).json()
    assert [li["role"] for li in profile["links"]] == ["buyer"]
    assert "parent" not in profile
    assert "subsidiaries" not in profile


async def test_a_system_key_with_identity_link_links_and_ends_but_never_opens_contacts(
    http: httpx.AsyncClient, env: Env, edge: FakeEdge
) -> None:
    # A CRM that knows who works for whom links its people to their company without the admin scope.
    await env.attach(handle(PHONE["type"], PHONE["value"]))
    org = {"type": "system_id", "value": "acme-9", "scope": "crm", "subject_kind": "account"}
    edge.key_scopes = frozenset({Scope.IDENTITY_LINK})
    http.headers["authorization"] = "Bearer nia_sk_live_x"
    body = {"person": PHONE, "organization": org, "role": "buyer"}
    created = await http.post("/v1/identity/links", json=body, headers=key())
    assert created.status_code == 201, created.text
    # Seeing the company's other contacts is a privacy decision: a person or an admin key.
    wider = await http.post("/v1/identity/links", json={**body, "can_see_contacts": True}, headers=key())
    assert (wider.status_code, wider.json()["code"]) == (403, "forbidden")
    ended = await http.post(f"/v1/identity/links/{created.json()['link_id']}/end", json={}, headers=key())
    assert ended.status_code == 200
    # The scope reaches only the links: the rest of identity governance stays closed.
    assert (await http.get("/v1/identity/assertions")).status_code == 403
    edge.key_scopes = frozenset({Scope.TRACK})
    refused = await http.post("/v1/identity/links", json=body, headers=key())
    assert (refused.status_code, refused.json()["code"]) == (403, "scope_missing")
