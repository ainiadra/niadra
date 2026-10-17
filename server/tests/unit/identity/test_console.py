"""What the Console and the integrator see of identity: search, suggestions, receipts and webhooks."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.identity import router
from niadra.app.identity import IdentityService
from niadra.config.models import WebhookEndpoint
from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.model import SuggestionRecord
from niadra.domain.notify.webhooks import Delivery
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, PatchedUnitOfWorkFactory, sample_config
from tests.unit.identity.conftest import Env, FakePrivacy, cpf, handle
from tests.unit.identity.test_routes import Container, FakeEdge, RecordingAudit, key

T = HandleType
PHONE = handle(T.PHONE_E164, "+55 11 98765-4321")
EMAIL = handle(T.EMAIL, "ana.silva@acme.com.br")
CPF = handle(T.GOV_ID_HMAC, cpf("529982247"))
CRM = handle(T.SYSTEM_ID, "crm-77", "crm")


@pytest.fixture
async def http(env: Env, edge: FakeEdge, audit: RecordingAudit) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("admin", Container(edge, env.service, audit), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
        client.headers["authorization"] = "Bearer person-jwt"
        yield client


@pytest.fixture
def edge() -> FakeEdge:
    return FakeEdge()


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit()


async def test_search_by_identifier_in_any_format_and_by_pseudonym(env: Env, http: httpx.AsyncClient) -> None:
    person = await env.attach(PHONE, EMAIL, CPF)
    profile = await env.repo.get_profile(person.profile_id)
    assert profile is not None

    by_phone = await http.post("/v1/profiles/search", json={"query": "(11) 98765-4321"})
    by_cpf = await http.post("/v1/profiles/search", json={"query": CPF.value})
    by_pseudonym = await http.post("/v1/profiles/search", json={"query": profile.pseudonym})

    for response in (by_phone, by_cpf, by_pseudonym):
        assert response.status_code == 200
        [match] = response.json()["items"]
        assert match["profile_id"] == str(person.profile_id)
    [match] = by_phone.json()["items"]
    assert match["pseudonym"] == profile.pseudonym
    assert match["kind"] == "person"
    assert match["last_seen"] is not None
    assert {h["type"]: h["value"] for h in match["handles"]}["email"] == "a***@acme.com.br"


async def test_search_finds_nothing_for_unknown_values_and_needs_a_type_for_opaque_ids(
    env: Env, http: httpx.AsyncClient
) -> None:
    await env.attach(CRM, EMAIL)

    unknown = await http.post("/v1/profiles/search", json={"query": "nobody@acme.com.br"})
    opaque = await http.post("/v1/profiles/search", json={"query": "crm-77"})
    typed = await http.post(
        "/v1/profiles/search", json={"query": "crm-77", "type": "system_id", "scope": "crm"}
    )

    assert unknown.json() == {"items": []}
    assert opaque.status_code == 422
    assert "crm-77" not in opaque.text
    assert len(typed.json()["items"]) == 1


async def test_search_never_returns_a_forgotten_or_merged_away_profile(env: Env) -> None:
    await env.attach(PHONE)
    [found] = await env.service.search_profiles(SPACE_ID, "+5511987654321")
    await env.repo.retire_profile(found.profile.profile_id, datetime.now(UTC))

    assert await env.service.search_profiles(SPACE_ID, found.profile.pseudonym) == []
    with pytest.raises(InvalidInput):
        await env.service.search_profiles(SPACE_ID, "not an identifier")


async def test_suggestions_are_listed_and_dismissed_with_a_receipt(
    env: Env, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    ids = []
    for _ in range(3):
        suggestion = SuggestionRecord(uuid4(), uuid4(), uuid4(), 0.8, datetime.now(UTC), {"agree": ["name"]})
        await env.repo.add_suggestion(suggestion)
        ids.append(suggestion.suggestion_id)

    page = (await http.get("/v1/identity/suggestions", params={"limit": 2})).json()
    rest = (await http.get("/v1/identity/suggestions", params={"cursor": page["next_cursor"]})).json()
    dismissed = await http.post(f"/v1/identity/suggestions/{ids[0]}/dismiss", headers=key())
    missing = await http.post(f"/v1/identity/suggestions/{uuid4()}/dismiss", headers=key())
    after = (await http.get("/v1/identity/suggestions")).json()

    assert [s["suggestion_id"] for s in page["items"] + rest["items"]] == [str(i) for i in sorted(ids)]
    assert page["items"][0]["identifier_a"] is None  # the handles of these pairs do not exist
    assert page["items"][0]["features"] == {"agree": ["name"]}
    assert (dismissed.status_code, dismissed.json()["status"]) == (200, "rejected")
    assert missing.status_code == 404
    assert str(ids[0]) not in {s["suggestion_id"] for s in after["items"]}
    assert audit.receipts[-1].action == "identity.suggestion.dismissed"
    assert audit.receipts[-1].target == f"suggestion:{ids[0]}"


async def test_every_identity_change_leaves_an_admin_receipt_with_ids_only(
    env: Env, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    first = (await env.attach(handle(T.PHONE_E164, "+5511988887777"))).profile_id
    second = (await env.attach(EMAIL)).profile_id
    await http.post(
        "/v1/identity/assertions", json={"handles": [PHONE.model_dump(), CRM.model_dump()]}, headers=key()
    )
    await http.post("/v1/identity/merge", json={"profile_ids": [str(first), str(second)]}, headers=key())
    await env.drain()
    survivor = (await env.profile(EMAIL)).profile_id
    email_id = (await env.find(EMAIL)).handle_id
    await http.post(
        "/v1/identity/unmerge",
        json={"profile_id": str(survivor), "handle_ids": [str(email_id)]},
        headers=key(),
    )
    assertion_id = (await http.get("/v1/identity/assertions")).json()["items"][0]["assertion_id"]
    await http.post(f"/v1/identity/assertions/{assertion_id}/retract", json={}, headers=key())
    await http.post("/v1/identity/handles/block", json={"handle": PHONE.model_dump()}, headers=key())
    org = {"type": "system_id", "value": "carrier-1", "scope": "tms", "subject_kind": "partner"}
    link = await http.post(
        "/v1/identity/links",
        json={"person": EMAIL.model_dump(), "organization": org, "role": "driver"},
        headers=key(),
    )
    await http.post(f"/v1/identity/links/{link.json()['link_id']}/end", json={}, headers=key())

    assert audit.actions == [
        "identity.assertion.created",
        "identity.merge.requested",
        "identity.unmerge.requested",
        "identity.assertion.retracted",
        "identity.handle.blocked",
        "identity.link.created",
        "identity.link.ended",
    ]
    assert audit.receipts[2].profile_id == survivor
    rendered = repr(audit.receipts)
    for value in ("98765", "ana.silva", "carrier-1"):
        assert value not in rendered


async def test_a_replayed_request_writes_no_second_receipt(
    env: Env, edge: FakeEdge, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    edge.remember = True
    await env.attach(PHONE)
    await env.attach(EMAIL)
    headers = key()
    body = {"handles": [PHONE.model_dump(), EMAIL.model_dump()]}

    await http.post("/v1/identity/assertions", json=body, headers=headers)
    await http.post("/v1/identity/assertions", json=body, headers=headers)

    assert audit.actions == ["identity.assertion.created"]


@dataclass
class RecordingNotify:
    deliveries: list[Delivery] = field(default_factory=list)

    async def add_delivery(self, delivery: Delivery) -> None:
        self.deliveries.append(delivery)


def webhook_env(cell: Cell, events: list[str] | None = None) -> tuple[Env, RecordingNotify]:
    endpoint = WebhookEndpoint(
        endpoint_id="crm",
        url="https://crm.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=events or ["identity.merged", "identity.unmerged"],
        secret_refs=["spaces/crm/webhook/crm"],
    )
    cell.config.put(sample_config().model_copy(update={"webhook_endpoints": [endpoint]}))
    privacy, notify = FakePrivacy(), RecordingNotify()
    uow = PatchedUnitOfWorkFactory(cell.uow, privacy=privacy, notify=notify)
    return Env(cell, IdentityService(uow, cell.clock, cell.config), privacy, uow), notify


async def test_merge_and_unmerge_announce_webhooks_with_ids_only(cell: Cell) -> None:
    env, notify = webhook_env(cell)
    first = await env.attach(PHONE)
    second = await env.attach(EMAIL)
    await env.service.merge(
        SPACE_ID, [first.profile_id, second.profile_id], force=False, actor="u", reason=None
    )
    await env.drain()
    survivor = await env.profile(EMAIL)
    email = await env.find(EMAIL)
    await env.service.unmerge(
        SPACE_ID, survivor.profile_id, handle_ids=[email.handle_id], actor="u", reason=None
    )
    await env.drain()

    merged, unmerged = notify.deliveries
    assert (merged.event_type, unmerged.event_type) == ("identity.merged", "identity.unmerged")
    assert merged.body["subject"] == {"pseudonym": survivor.pseudonym, "kind": "person"}
    assert merged.body["data"]["profile_id"] == str(survivor.profile_id)
    assert set(merged.body["data"]["merged_profile_ids"]) <= {str(first.profile_id), str(second.profile_id)}
    assert unmerged.body["data"]["handles_moved"] == [str(email.handle_id)]
    assert "98765" not in repr(merged.body) + repr(unmerged.body)
    assert len(env.outbox.take(TaskName.DELIVER_WEBHOOK)) == 2


async def test_accepting_a_suggestion_queues_an_assertion_and_never_merges_directly(
    env: Env, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    first, second = await env.attach(PHONE), await env.attach(EMAIL)
    suggestion = SuggestionRecord(
        uuid4(), first.handle_ids[0], second.handle_ids[0], 0.9, datetime.now(UTC), {"agree": ["name"]}
    )
    await env.repo.add_suggestion(suggestion)

    accepted = await http.post(
        f"/v1/identity/suggestions/{suggestion.suggestion_id}/accept", json={"reason": "same"}, headers=key()
    )
    again = await http.post(
        f"/v1/identity/suggestions/{suggestion.suggestion_id}/accept", json={}, headers=key()
    )

    assert (accepted.status_code, accepted.json()["status"]) == (202, "queued")
    assert again.status_code == 409
    assert not await env.same(PHONE, EMAIL)
    [assertion] = await env.repo.get_assertions([UUID(accepted.json()["assertion_id"])])
    assert assertion.method is AssertionMethod.ACCEPTED_SUGGESTION
    stored = await env.repo.get_suggestion(suggestion.suggestion_id)
    assert stored is not None
    assert stored.status == "accepted"
    assert audit.actions == ["identity.suggestion.accepted"]
    await env.drain()
    assert await env.same(PHONE, EMAIL)


async def test_new_suggestions_are_announced_with_ids_and_score_and_dismissed_pairs_stay_closed(
    cell: Cell,
) -> None:
    env, notify = webhook_env(cell, ["identity.suggestion"])
    await env.attach(handle(T.EMAIL, "Ana.Silva+x@gmail.com"))
    await env.attach(handle(T.EMAIL, "anasilva@gmail.com"))
    job = Task(TaskName.IDENTITY_SUGGEST, SPACE_ID)

    await env.service.identity_suggest(job)
    [delivery] = notify.deliveries
    [suggestion] = await env.repo.open_suggestions(after=None, limit=10)
    await env.service.dismiss_suggestion(SPACE_ID, suggestion.suggestion_id)
    await env.service.identity_suggest(job)

    assert delivery.event_type == "identity.suggestion"
    assert delivery.body["subject"] is None
    assert set(delivery.body["data"]) == {"suggestion_id", "handle_ids", "score", "as_of"}
    assert "gmail" not in repr(delivery.body)
    assert len(notify.deliveries) == 1
    assert await env.repo.open_suggestions(after=None, limit=10) == []


async def test_a_reviewer_sees_both_profiles_and_masked_identifiers(
    env: Env, edge: FakeEdge, http: httpx.AsyncClient
) -> None:
    first, second = await env.attach(PHONE), await env.attach(EMAIL)
    suggestion = SuggestionRecord(uuid4(), first.handle_ids[0], second.handle_ids[0], 0.9, datetime.now(UTC))
    await env.repo.add_suggestion(suggestion)

    [item] = (await http.get("/v1/identity/suggestions")).json()["items"]
    edge.roles = frozenset({"security"})
    [revealed] = (await http.get("/v1/identity/suggestions")).json()["items"]

    assert (item["profile_a"], item["profile_b"]) == (str(first.profile_id), str(second.profile_id))
    assert item["identifier_a"]["value"] == "+55*******4321"
    assert item["identifier_b"]["value"] == "a***@acme.com.br"
    assert revealed["identifier_a"]["value"] == "+5511987654321"


async def test_a_declared_identify_never_merges_two_profiles_it_asks_a_person(cell: Cell) -> None:
    # `declared` counts little until confirmed. It still joins a new handle to a profile, but two
    # profiles that exist stay apart until a person accepts the suggestion it leaves in the queue.
    env, notify = webhook_env(cell, ["identity.suggestion", "identity.merged"])
    await env.attach(PHONE)
    await env.attach(EMAIL)
    crm = handle(T.SYSTEM_ID, "48213", scope="crm")

    await env.attach(PHONE, EMAIL, crm, method=AssertionMethod.DECLARED)
    assert await env.drain() == 0
    assert not await env.same(PHONE, EMAIL)
    assert await env.same(PHONE, crm) or await env.same(EMAIL, crm)  # a new handle still joins

    [suggestion] = await env.repo.open_suggestions(after=None, limit=10)
    assert suggestion.features == {"agree": ["declared"]}
    assert {suggestion.handle_a, suggestion.handle_b} == {
        (await env.find(PHONE)).handle_id,
        (await env.find(EMAIL)).handle_id,
    }
    [announced] = notify.deliveries
    assert announced.event_type == "identity.suggestion"
    # Declared again: the pair is already waiting for review, nothing new.
    await env.attach(PHONE, EMAIL, method=AssertionMethod.DECLARED)
    assert len(await env.repo.open_suggestions(after=None, limit=10)) == 1

    await env.service.accept_suggestion(SPACE_ID, suggestion.suggestion_id, actor="u", reason="same person")
    await env.drain()
    assert await env.same(PHONE, EMAIL)


async def test_an_explicit_identify_merges_two_profiles_only_on_a_record_id(cell: Cell) -> None:
    # Two profiles that exist: `explicit_identify` without proof leaves the pair to a person, like
    # `declared`; with a record id of one of them (the tenant's systems vouch) it still merges.
    env, _ = webhook_env(cell)
    crm = handle(T.SYSTEM_ID, "48213", scope="crm")
    await env.attach(PHONE)
    await env.attach(EMAIL, crm)
    await env.attach(PHONE, EMAIL, method=AssertionMethod.DECLARED)
    await env.attach(PHONE, EMAIL, method=AssertionMethod.EXPLICIT_IDENTIFY)
    assert await env.drain() == 0
    assert not await env.same(PHONE, EMAIL)
    assert len(await env.repo.open_suggestions(after=None, limit=10)) == 1

    await env.attach(PHONE, crm, method=AssertionMethod.EXPLICIT_IDENTIFY)
    assert await env.drain() >= 1
    assert await env.same(PHONE, EMAIL, crm)
