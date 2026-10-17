"""Requests to review an automated decision (24.5.3; LGPD, art. 20): filed with the explanation built from
receipts, sent to the controller's data protection officer, resolved once, kept by a legal obligation, in the
subject's package and past their erasure; and the routes of the reviewable profile, holds and reviews."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http import signals as signals_http
from niadra.adapters.inbound.http.app import create_app
from niadra.app.holds import LegalHoldsService
from niadra.app.inferences import InferencesService
from niadra.app.review_requests import ReviewRequestsService, exported_review_requests
from niadra.config.models import FeatureSettings, WebhookEndpoint
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.signals import ReviewRequestCreate, ReviewResolution
from niadra.domain.common.errors import Conflict, NotFound, Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal
from niadra.domain.privacy.reviews import KEPT_DAYS
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature, HandleType, ReceiptKind, Verification
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.flow.harness import Flow

PHONE = "+5511955556666"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
DPO = WebhookEndpoint(
    endpoint_id="dpo",
    url="https://dpo.example/hooks",
    source_id=BILLING_SOURCE,
    event_types=["review_request.created", "review_request.resolved"],
    secret_refs=[f"spaces/{SPACE_ID}/webhook/dpo"],
)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """A customer who wrote once and whose context the voice agent read, in a space that measures nothing
    but has the reviewable profile on."""
    features = frozenset({Feature.SIGNALS, Feature.LEGAL_HOLDS})
    cell.config.put(
        sample_config().model_copy(
            update={"features": FeatureSettings(enabled=features), "webhook_endpoints": [DPO]}
        )
    )
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook/dpo"] = "whsec_" + "c2VjcmV0LWtleS0xMjM0NTY3OA=="
    flow = Flow.build(cell)
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Quero um vestido"},
        "occurred_at": cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain()
    request = ContextRequest(subject=SUBJECT, conversation_id="call-1", verification=Verification.V0)
    await flow.serve.context(principal(VOICE_SOURCE, cell.config.space(SPACE_ID)), request)
    return flow


async def profile_id(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        record = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await uow.identity.profile_of(record.handle_id)
    assert profile is not None
    return profile.profile_id


def reviews(flow: Flow) -> ReviewRequestsService:
    return ReviewRequestsService(flow.cell.uow, flow.cell.keys, flow.cell.clock, flow.cell.config)


CALLER = principal(WHATSAPP_SOURCE)


async def test_a_request_goes_to_the_dpo_with_its_explanation_and_is_resolved_once(flow: Flow) -> None:
    profile = await profile_id(flow)
    [read] = [r for r in flow.receipts.sent if r.profile_id == profile and r.kind is ReceiptKind.READ][:1]
    body = ReviewRequestCreate(
        decision_receipt_id=read.receipt_id, reason="Por que o agente não me ofereceu?"
    )

    created = await reviews(flow).create(CALLER, profile, body, idempotency_key="r1")

    assert (created.status, created.reason) == ("open", "Por que o agente não me ofereceu?")
    [explained] = created.explanation["receipts"]
    assert explained["receipt_id"] == str(read.receipt_id)
    assert (explained["kind"], explained["surface"]) == ("read", "pack")
    [sent] = flow.deliveries("review_request.created")
    assert sent["data"]["receipt_ids"] == [str(read.receipt_id)]
    assert "ofereceu" not in str(sent), "the subject's words never leave in the event"
    again = await reviews(flow).create(CALLER, profile, body, idempotency_key="r1")
    assert again.request_id == created.request_id
    assert len(flow.deliveries("review_request.created")) == 1

    resolved = await reviews(flow).resolve(
        CALLER, created.request_id, ReviewResolution(outcome="upheld", note="Regra de contato"), by="user:dpo"
    )
    assert (resolved.status, resolved.outcome, resolved.resolution) == (
        "resolved",
        "upheld",
        "Regra de contato",
    )
    assert flow.deliveries("review_request.resolved")[0]["data"]["outcome"] == "upheld"
    with pytest.raises(Conflict):
        await reviews(flow).resolve(CALLER, created.request_id, ReviewResolution(outcome="reversed"), by="u")
    page = await reviews(flow).page(CALLER, cursor=None, limit=10)
    assert [r.request_id for r in page.items] == [created.request_id]


async def test_a_request_names_only_the_subjects_own_receipts(flow: Flow) -> None:
    profile = await profile_id(flow)
    with pytest.raises(NotFound):
        await reviews(flow).create(
            CALLER,
            profile,
            ReviewRequestCreate(decision_receipt_id=uuid7(), reason="?"),
            idempotency_key="r2",
        )
    with pytest.raises(NotFound):
        await reviews(flow).create(CALLER, uuid7(), ReviewRequestCreate(reason="?"), idempotency_key="r3")
    # Without a receipt, the subject's latest decisions: this one holds none, only a plain read.
    created = await reviews(flow).create(
        CALLER, profile, ReviewRequestCreate(reason="?"), idempotency_key="r4"
    )
    assert created.explanation == {"receipts": []}


async def test_a_request_is_kept_past_the_subjects_erasure_and_goes_after_its_days(flow: Flow) -> None:
    profile = await profile_id(flow)
    created = await reviews(flow).create(
        CALLER, profile, ReviewRequestCreate(reason="?"), idempotency_key="r5"
    )
    async with flow.cell.uow(SPACE_ID) as uow:
        [exported] = await exported_review_requests(uow, flow.cell.keys, SPACE_ID, profile)
    assert exported["reason"] == "?"

    accepted = await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="profile", profile_id=profile), idempotency_key="f1", requested_by="u"
    )
    await flow.drain()
    status = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert status.status == "completed"
    assert status.retained == {"review_requests": 1}
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await uow.review_requests.get(UUID(created.request_id)) is not None

    await reviews(flow).resolve(CALLER, created.request_id, ReviewResolution(outcome="corrected"), by="u")
    flow.cell.clock.advance(days=KEPT_DAYS - 1)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await uow.review_requests.get(UUID(created.request_id)) is not None
    flow.cell.clock.advance(days=2)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await uow.review_requests.get(UUID(created.request_id)) is None


# The routes.


class Edge:
    def __init__(self, space: Any) -> None:
        self._person = PersonPrincipal(space, uuid7(), frozenset({"security"}))

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        raise Unauthenticated("no keys here")

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "person":
            raise Unauthenticated("unknown token")
        return self._person

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Container:
    edge: Edge
    config: Any
    inferences: InferencesService
    review_requests: ReviewRequestsService
    legal_holds: LegalHoldsService
    admin_audit: RecordingAdminAudit


@pytest.fixture
async def client(flow: Flow) -> AsyncIterator[httpx.AsyncClient]:
    cell = flow.cell
    container = Container(
        Edge(CALLER.space),
        cell.config,
        InferencesService(cell.uow, flow.signals),
        reviews(flow),
        LegalHoldsService(cell.uow, cell.keys, cell.clock),
        RecordingAdminAudit(),
    )
    app = create_app("niadra-test", container, (signals_http.admin_router,))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        yield http


PERSON = {"authorization": "Bearer person"}


async def test_the_routes_answer_with_their_services_and_leave_admin_receipts(
    flow: Flow, client: httpx.AsyncClient
) -> None:
    profile = await profile_id(flow)
    listed = await client.get(f"/v1/profiles/{profile}/inferences", headers=PERSON)
    assert (listed.status_code, listed.json()) == (200, {"items": [], "next_cursor": None})
    unknown = await client.delete(f"/v1/profiles/{profile}/inferences/{'0' * 32}", headers=PERSON)
    assert unknown.status_code == 404
    filed = await client.post(
        f"/v1/profiles/{profile}/review-requests",
        json={"reason": "Por que?"},
        headers={**PERSON, "idempotency-key": "r1"},
    )
    assert filed.status_code == 201
    request_id = filed.json()["request_id"]
    resolved = await client.post(
        f"/v1/review-requests/{request_id}/resolve", json={"outcome": "upheld"}, headers=PERSON
    )
    assert resolved.json()["outcome"] == "upheld"
    held = await client.post(
        "/v1/legal-holds",
        json={"conversation_id": "wa-1", "reason": "processo"},
        headers={**PERSON, "idempotency-key": "h1"},
    )
    assert (held.status_code, held.json()["scope"]) == (201, "conversation")
    two = await client.post(
        "/v1/legal-holds",
        json={"conversation_id": "wa-1", "handle": {"type": "phone_e164", "value": PHONE}, "reason": "x"},
        headers={**PERSON, "idempotency-key": "h2"},
    )
    assert two.status_code == 422
    released = await client.post(
        f"/v1/legal-holds/{held.json()['hold_id']}/release", json={"reason": "fim"}, headers=PERSON
    )
    assert released.json()["status"] == "released"
    audit = client._transport.app.state.container.admin_audit  # type: ignore[attr-defined]
    assert audit.actions == [
        "profile.inferences.read",
        "review_request.created",
        "review_request.resolved",
        "legal_hold.created",
        "legal_hold.released",
    ]
