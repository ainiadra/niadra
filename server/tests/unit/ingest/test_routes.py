import base64
import hashlib
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.bodies import LargeBodies
from niadra.adapters.inbound.http.ingest import router
from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.edge import EdgeService
from niadra.app.ingest import IngestService
from niadra.app.turns import TurnService
from niadra.config.models import HandleExpr, Mapping, MappingRule, WebhookAuth
from niadra.domain.edge.keys import SourceKeyRow, fast_hash, slow_hash
from niadra.domain.vocabulary import Environment, HandleType, Scope
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    mint_source_key,
    sample_config,
)
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.ingest.fakes import build, message

PEPPER = b"p" * 32
CLIENT_CERT = b"the DER bytes of the source's client certificate"


@dataclass
class Container:
    edge: EdgeService
    ingest: IngestService
    turns: TurnService
    large_bodies: LargeBodies = field(default_factory=lambda: LargeBodies(2, 1024 * 1024))


@pytest.fixture
async def client(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    config = sample_config().model_copy(
        update={
            "mappings": [
                Mapping(
                    source_id=BILLING_SOURCE,
                    version="m1",
                    auth=WebhookAuth(
                        mode="hmac", header="x-signature", secret_ref=f"spaces/{SPACE_ID}/webhook-in/billing"
                    ),
                    verification_challenge="hub.challenge",
                    rules=[
                        MappingRule(
                            match="type == 'invoice.paid'",
                            canonical_type="invoice.paid",
                            idempotency_key="id",
                            occurred_at="at",
                            channel="erp",
                            handles=[HandleExpr(type=HandleType.SYSTEM_ID, value="customer", scope="erp")],
                        )
                    ],
                ),
                # A client certificate, pinned by its fingerprint.
                Mapping(
                    source_id=VOICE_SOURCE,
                    version="m1",
                    auth=WebhookAuth(mode="mtls", secret_ref=f"spaces/{SPACE_ID}/webhook-in/voice"),
                    rules=[MappingRule(match="type", idempotency_key="id", occurred_at="at")],
                ),
            ]
        }
    )
    cell.config.put(config)
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/billing"] = "whsec"
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/voice"] = hashlib.sha256(CLIENT_CERT).hexdigest()
    edge = EdgeService(
        cell.uow, cell.cache, cell.config, cell.clock, {}, PEPPER, databases=cell.databases, rate_per_s=5
    )
    ingest, _, _ = build(cell)
    token, parsed = mint_source_key(Environment.SANDBOX, "us-east-2", "acme")
    row = SourceKeyRow(
        space_id=SPACE_ID,
        key_id=parsed.key_id,
        source_id=WHATSAPP_SOURCE,
        fast_hash=fast_hash(PEPPER, parsed.key_id, parsed.secret),
        slow_hash=slow_hash(parsed.key_id, parsed.secret),
        scopes=frozenset({Scope.TRACK}),
    )
    await edge.mirror_keys(SPACE_ID, [row])
    # A space without turn records on: the spans become messages and no turn.
    turns = TurnService(
        cell.uow,
        TransactionTurnIntakeLog(cell.uow),
        cell.keys,
        cell.objects,
        cell.clock,
        cell.config,
        RecordingAdminAudit(),  # type: ignore[arg-type]
    )
    app = create_app("niadra-ingest", Container(edge, ingest, turns), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        yield http, {"authorization": f"Bearer {token}"}


async def test_batch_is_200_when_every_item_is_accepted_and_207_otherwise(client: Any) -> None:
    http, auth = client

    ok = await http.post("/v1/batch", json={"items": [message("m1")]}, headers=auth)
    partial = await http.post("/v1/batch", json={"items": [message("m2"), {"type": "event"}]}, headers=auth)

    assert ok.status_code == 200
    assert ok.json() == {"accepted": 1, "duplicates": 0, "errors": [], "masked": {}}
    assert partial.status_code == 207
    assert partial.json()["errors"][0]["index"] == 1
    assert partial.json()["errors"][0]["code"] == "invalid_input"


async def test_batch_envelope_errors_and_missing_credentials(client: Any) -> None:
    http, auth = client

    empty = await http.post("/v1/batch", json={"items": []}, headers=auth)
    anonymous = await http.post("/v1/batch", json={"items": [message("m1")]})

    assert empty.status_code == 422
    assert empty.json()["code"] == "invalid_input"
    assert anonymous.status_code == 401


async def test_validation_errors_never_echo_values(client: Any) -> None:
    http, auth = client
    item = message("m1", handles=[{"type": "phone_e164", "value": "+5511999999999", "extra": "x"}])

    response = await http.post("/v1/batch", json={"items": [item]}, headers=auth)

    assert "+5511999999999" not in response.text


async def test_rate_limit_answers_429_with_retry_after(client: Any) -> None:
    http, auth = client
    statuses = [
        (
            await http.post(
                "/v1/media/uploads",
                json={
                    "content_type": "audio/ogg",
                    "size_bytes": 10,
                    "sha256": "a" * 64,
                },
                headers=auth,
            )
        ).status_code
        for _ in range(6)
    ]

    assert statuses[:5] == [201] * 5
    assert statuses[5] == 429


async def test_webhook_with_hmac_signature(client: Any) -> None:
    http, _ = client
    body = b'{"type": "invoice.paid", "id": "evt-1", "at": "2026-09-22T16:00:00Z", "customer": "C-1"}'
    signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()

    accepted = await http.post(
        f"/v1/ingest/webhook/{BILLING_SOURCE}", content=body, headers={"x-signature": signature}
    )
    forged = await http.post(
        f"/v1/ingest/webhook/{BILLING_SOURCE}", content=body, headers={"x-signature": "00"}
    )

    assert accepted.status_code == 200
    assert accepted.json()["accepted"] == 1
    assert forged.status_code == 401


async def test_a_webhook_on_a_client_certificate_takes_only_the_registered_one(client: Any) -> None:
    # The edge's mTLS address forwards the certificate of the handshake in this header, and every
    # other route of the edge drops it; the cell compares it with the pinned fingerprint.
    http, _ = client
    body = b'{"type": "call", "id": "c-1", "at": "2026-09-22T16:00:00Z"}'
    forwarded = quote(base64.b64encode(CLIENT_CERT).decode())

    accepted = await http.post(
        f"/v1/ingest/webhook/{VOICE_SOURCE}", content=body, headers={"x-forwarded-tls-client-cert": forwarded}
    )
    other = quote(base64.b64encode(b"another certificate").decode())
    wrong = await http.post(
        f"/v1/ingest/webhook/{VOICE_SOURCE}", content=body, headers={"x-forwarded-tls-client-cert": other}
    )
    claimed = await http.post(
        f"/v1/ingest/webhook/{VOICE_SOURCE}", content=body, headers={"x-client-cert-sha256": "ab12"}
    )

    assert accepted.status_code in (200, 207)  # authenticated; the mapping then reads the payload
    assert (wrong.status_code, claimed.status_code) == (401, 401)
    assert claimed.headers["content-type"].startswith("application/problem+json")


async def test_webhook_verification_challenge_is_not_echoed_without_a_url_token(client: Any) -> None:
    # The billing mapping signs its POSTs with HMAC; a GET carries no signature, so it is not answered.
    http, _ = client

    response = await http.get(f"/v1/ingest/webhook/{BILLING_SOURCE}", params={"hub.challenge": "abc123"})

    assert response.status_code == 404
    assert "abc123" not in response.text


async def test_otlp_traces_answer_with_partial_success(client: Any) -> None:
    http, auth = client
    span = {
        "traceId": "t",
        "startTimeUnixNano": "1790000000000000000",
        "endTimeUnixNano": "1790000001000000000",
        "attributes": [
            {"key": "gen_ai.conversation.id", "value": {"stringValue": "c-9"}},
            {"key": "niadra.handle.email", "value": {"stringValue": "ana@example.com"}},
            {"key": "gen_ai.prompt.0.role", "value": {"stringValue": "user"}},
            {"key": "gen_ai.prompt.0.content", "value": {"stringValue": "oi"}},
        ],
    }
    body = {"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}

    response = await http.post("/v1/otel/v1/traces", json=body, headers=auth)

    assert response.status_code == 200
    assert response.json() == {"partialSuccess": {"rejectedSpans": 0, "errorMessage": ""}}


async def test_feedback_route(client: Any) -> None:
    http, auth = client
    body = {
        "idempotency_key": "f1",
        "subject": {"type": "email", "value": "ana@example.com"},
        "action": "conversation_outcome",
        "conversation_id": "c-1",
        "value": "resolved",
    }

    response = await http.post("/v1/feedback", json=body, headers=auth)
    incomplete = await http.post("/v1/feedback", json={**body, "conversation_id": None}, headers=auth)

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert incomplete.status_code == 422
    assert "ana@example.com" not in incomplete.text


async def test_webhooks_are_rate_limited_per_source_before_authentication(client: Any) -> None:
    http, _ = client
    url = f"/v1/ingest/webhook/{BILLING_SOURCE}"

    statuses = [
        (await http.post(url, content=b"{}", headers={"x-signature": "00"})).status_code for _ in range(6)
    ]

    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    assert (await http.get(url, params={"hub.challenge": "abc"})).status_code == 429


async def test_file_upload_is_accepted_then_followed_by_id(client: Any, cell: Cell) -> None:
    http, auth = client
    body = b"email,phone_e164\nana@example.com,+5511988887777\n"

    created = await http.post("/v1/ingest/files", content=body, headers={**auth, "content-type": "text/csv"})
    wrong_type = await http.post(
        "/v1/ingest/files", content=body, headers={**auth, "content-type": "text/plain"}
    )
    anonymous = await http.post("/v1/ingest/files", content=body, headers={"content-type": "text/csv"})

    assert created.status_code == 202
    out = created.json()
    assert (out["status"], out["format"], out["records"], out["size_bytes"]) == (
        "queued",
        "csv",
        1,
        len(body),
    )
    assert wrong_type.status_code == 422
    assert anonymous.status_code == 401
    followed = await http.get(f"/v1/ingest/files/{out['import_id']}", headers=auth)
    assert followed.json()["import_id"] == out["import_id"]
    assert [k for k in cell.objects.objects if k.startswith(f"imports/{SPACE_ID}/{out['import_id']}/")]


async def test_media_upload_route_takes_the_subject_in_the_body(client: Any) -> None:
    http, auth = client
    body = {
        "content_type": "image/png",
        "size_bytes": 10,
        "sha256": "b" * 64,
        "subject": {"type": "phone_e164", "value": "+5511988887777"},
    }

    response = await http.post("/v1/media/uploads", json=body, headers=auth)

    assert response.status_code == 201
    assert "5511988887777" not in response.json()["upload_url"]
    assert response.json()["media_ref"].count("_") == 2


async def test_feedback_batch_route(client: Any) -> None:
    http, auth = client
    one = {
        "subject": {"type": "email", "value": "ana@example.com"},
        "action": "conversation_outcome",
        "conversation_id": "c-1",
        "value": "resolved",
    }
    body = {"items": [{**one, "idempotency_key": "fb-1"}, {**one, "idempotency_key": "fb-2"}]}

    response = await http.post("/v1/feedback/batch", json=body, headers=auth)
    replay = await http.post("/v1/feedback/batch", json=body, headers=auth)
    incomplete = await http.post(
        "/v1/feedback/batch",
        json={"items": [{**one, "idempotency_key": "fb-3", "value": None}]},
        headers=auth,
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == 2
    assert replay.json()["duplicates"] == 2
    assert incomplete.status_code == 422
    assert "ana@example.com" not in incomplete.text
