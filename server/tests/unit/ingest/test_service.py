from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import orjson
import pytest

from niadra.app.collaborators import PendingMerge
from niadra.app.ingest import IngestService
from niadra.config.models import Mapping, MappingRule, WebhookAuth
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.events import BatchResponse, FeedbackRequest
from niadra.contracts.ingest import MediaUploadIn, validate_items
from niadra.domain.common.errors import Forbidden, NotFound, Unauthenticated
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.hot import hot_aad, unpack_envelope
from niadra.domain.ingest.storage import media_key
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.sessions.rules import HOT_LAYER
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, EventKind, HandleType, Scope, SubjectKind, Verification
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
    sample_config,
)
from tests.unit.ingest.fakes import BLOCKED, build, handle_id, held, message

PHONE = Handle(type=HandleType.PHONE_E164, value="+5511988887777")
T0 = datetime(2026, 10, 5, 12, tzinfo=UTC)


async def send(ingest: IngestService, caller: SourcePrincipal, *raw: dict[str, Any]) -> BatchResponse:
    items, errors = validate_items(list(raw))
    return await ingest.ingest_items(caller, items, errors)


def outbox(cell: Cell, name: TaskName) -> list[Task]:
    return [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name == name]


def stored(cell: Cell) -> list[Any]:
    return list(cell.uow.store.data["ingest"]["events"].values())


def sessions(cell: Cell) -> list[Any]:
    return [s for _, s in cell.uow.store.data["sessions"]["sessions"].values()]


async def test_a_message_is_stored_encrypted_in_the_hot_layer_and_opens_a_session(cell: Cell) -> None:
    ingest, _, identity = build(cell)

    result = await send(ingest, principal(), message("m1"))

    assert result == BatchResponse(accepted=1, duplicates=0, errors=[])
    [event] = stored(cell)
    assert event.record.text == "a fatura veio errada"
    assert event.record.handle_ids == (handle_id(PHONE),)
    assert event.record.event_id.version == 7
    [attach] = identity.calls
    assert attach.method is AssertionMethod.SAME_EVENT
    assert attach.origin_event_id == event.record.event_id

    conversation = cell.uow.store.data["ingest"]["conversations"][(SPACE_ID, "conv-1")]
    assert conversation["dirty"] is True
    [session] = sessions(cell)
    assert (session.conversation_id, session.channel, session.profile_id) == (
        "conv-1",
        "whatsapp",
        identity.profiles[handle_id(PHONE)],
    )

    [turn] = (await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).turns
    version, ciphertext = unpack_envelope(turn.envelope_ct)
    document = orjson.loads(
        await cell.keys.decrypt(SPACE_ID, ciphertext, hot_aad(SPACE_ID, turn.event_id), version)
    )
    assert document["text"] == "a fatura veio errada"
    assert document["speaker"] == "customer"

    [(net, due, reason)] = cell.uow.store.data["pipeline"]["scheduled"]
    assert (net.name, reason, due) == (
        TaskName.HOT_REPAIR,
        "safety_net",
        cell.clock.now() + timedelta(seconds=2),
    )
    assert net.payload == {"event_ids": [str(event.record.event_id)]}
    beat = cell.uow.store.data["ingest"]["heartbeats"][
        (SPACE_ID, WHATSAPP_SOURCE, cell.clock.now().replace(second=0))
    ]
    assert (beat["received"], beat["rejected"]) == (1, 0)


async def test_duplicates_inside_a_batch_and_across_batches(cell: Cell) -> None:
    ingest, _, _ = build(cell)

    first = await send(ingest, principal(), message("m1"), message("m1"), message("m2"))
    again = await send(ingest, principal(), message("m2"), message("m3"))

    assert (first.accepted, first.duplicates) == (2, 1)
    assert (again.accepted, again.duplicates) == (1, 1)
    assert len(stored(cell)) == 3


async def test_one_bad_item_never_fails_the_batch(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    whatsapp = principal(scopes=frozenset({Scope.TRACK}))
    action = message(
        "a1", kind="action", content=None, speaker={"role": "ai_agent"}, action={"operation": "credit"}
    )
    malformed = {"type": "event", "idempotency_key": "x"}
    verify = {
        "type": "verify",
        "idempotency_key": "v1",
        "method": "otp_sms",
        "level": "V4",
        "conversation_id": "conv-1",
        "handle": {"type": "phone_e164", "value": "+5511988887777"},
        "occurred_at": "2026-09-22T16:59:00Z",
    }

    result = await send(ingest, whatsapp, message("m1"), action, malformed, verify)

    assert result.accepted == 1
    assert [(e.index, e.code) for e in result.errors] == [
        (1, "scope_missing"),
        (2, "invalid_input"),
        (3, "verification_not_allowed"),
    ]


async def test_item_rejected_after_dedupe_releases_its_key(cell: Cell) -> None:
    # An identify goes through the full transaction, where an attach may refuse it; a turn never gets there
    # with anything left to refuse.
    ingest, _, _ = build(cell)
    blocked = {
        "type": "identify",
        "idempotency_key": "m1",
        "handles": [{"type": "phone_e164", "value": BLOCKED}, {"type": "email", "value": "m@x.co"}],
    }

    rejected = await send(ingest, principal(), blocked)
    retried = await send(ingest, principal(), message("m1"))

    assert [e.code for e in rejected.errors] == ["invalid_input"]
    assert (retried.accepted, retried.duplicates) == (1, 0)


async def test_system_events_and_actions_go_to_object_work_without_dirtying(cell: Cell) -> None:
    ingest, _, identity = build(cell)
    billing = principal(BILLING_SOURCE)
    system = message(
        "s1",
        kind="system_event",
        canonical_type="invoice.disputed",
        content=None,
        speaker={"role": "system"},
        channel="erp",
        object_refs=[{"type": "invoice", "namespace": "erp", "id": "0823"}],
        fields={"amount": 12},
    )
    action = message(
        "a1",
        kind="action",
        content=None,
        speaker={"role": "ai_agent"},
        channel="erp",
        action={"operation": "credit", "result": "credited 12"},
    )
    refused = message(
        "a2", kind="action", content=None, speaker={"role": "ai_agent"}, action={"operation": "refund"}
    )

    result = await send(ingest, billing, system, action, refused)

    assert result.accepted == 2
    assert [e.code for e in result.errors] == ["operation_not_allowed"]
    tasks = outbox(cell, TaskName.APPLY_OBJECT_EVENT)
    assert len(tasks) == 2
    assert {t.profile_id for t in tasks} == {identity.profiles[handle_id(PHONE)]}
    conversation = cell.uow.store.data["ingest"]["conversations"][(SPACE_ID, "conv-1")]
    assert conversation["dirty"] is False
    by_kind = {e.record.kind: e.record for e in stored(cell)}
    assert by_kind[EventKind.SYSTEM_EVENT].fields == {"amount": 12}
    assert by_kind[EventKind.ACTION].action == {"operation": "credit", "result": "credited 12"}


async def test_only_coordination_names_objects_in_its_namespace(cell: Cell) -> None:
    # An action there never turns divergent (18.4): a source that could name it would hide a forged action.
    ingest, _, _ = build(cell)
    forged = message(
        "a1",
        kind="action",
        content=None,
        speaker={"role": "ai_agent"},
        object_refs=[{"type": "commitment", "namespace": "coordination", "id": "c1"}],
        action={"operation": "credit", "result": "R$ 900"},
    )
    result = await send(ingest, principal(BILLING_SOURCE), forged)
    assert (result.accepted, [e.code for e in result.errors]) == (0, ["invalid_input"])
    assert outbox(cell, TaskName.APPLY_OBJECT_EVENT) == []


async def test_each_subject_is_attached_separately_person_first(cell: Cell) -> None:
    ingest, _, identity = build(cell)
    item = message(
        "m1",
        handles=[],
        subjects=[
            {
                "kind": "account",
                "role": "carrier",
                "handles": [{"type": "system_id", "value": "T-1", "scope": "erp"}],
            },
            {
                "kind": "person",
                "role": "driver",
                "handles": [
                    {"type": "phone_e164", "value": "+5511988887777"},
                    {"type": "email", "value": "ana@example.com"},
                ],
            },
        ],
    )

    await send(ingest, principal(), item)

    assert [(c.subject_kind, len(c.handles)) for c in identity.calls] == [
        (SubjectKind.PERSON, 2),
        (SubjectKind.ACCOUNT, 1),
    ]
    [event] = stored(cell)
    assert [s["kind"] for s in event.record.subjects] == ["person", "account"]
    assert len(event.record.handle_ids) == 3
    # The person and the organization the event names are linked, each attached on its own.
    person, account = (UUID(s["handle_ids"][0]) for s in event.record.subjects)
    assert identity.links == [(person, account)]


async def test_verify_raises_the_proven_level_of_the_session(cell: Cell) -> None:
    ingest, _, identity = build(cell)
    voice = principal(VOICE_SOURCE)
    verify = {
        "type": "verify",
        "idempotency_key": "v1",
        "method": "otp_sms",
        "level": "V3",
        "conversation_id": "call-1",
        "handle": {"type": "phone_e164", "value": "+5511988887777"},
        "occurred_at": "2026-09-22T16:59:00Z",
    }

    result = await send(ingest, voice, message("m1", conversation_id="call-1", channel="voice"), verify)

    assert result.accepted == 2
    [session] = sessions(cell)
    assert session.proven_level is Verification.V3
    assert identity.calls[-1].method is AssertionMethod.OTP


async def test_network_attestation_proves_v2_for_the_call(cell: Cell) -> None:
    ingest, _, identity = build(cell)
    item = message(
        "m1",
        conversation_id="call-1",
        channel="voice",
        voice={"network_attestation": "A", "ani": "+5511988887777"},
    )

    await send(ingest, principal(VOICE_SOURCE), item)

    [session] = sessions(cell)
    assert session.proven_level is Verification.V2
    assert identity.calls[0].level is Verification.V2
    voice = stored(cell)[0].voice
    assert "ani" not in voice
    assert len(voice["ani_hmac"]) == 64


async def test_conversation_end_and_handoff_request_closing(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    ended = {
        "type": "conversation.ended",
        "idempotency_key": "e1",
        "conversation_id": "conv-1",
        "occurred_at": "2026-09-22T16:59:30Z",
    }
    handoff = {
        "type": "handoff",
        "idempotency_key": "h1",
        "conversation_id": "conv-2",
        "target": "human",
        "occurred_at": "2026-09-22T16:59:30Z",
    }

    result = await send(
        ingest, principal(), message("m1"), message("m2", conversation_id="conv-2"), ended, handoff
    )

    assert result.accepted == 4
    assert sorted(t.payload["reason"] for t in outbox(cell, TaskName.CLOSE_SESSION)) == ["ended", "handoff"]
    [event] = [e.record for e in stored(cell) if e.record.canonical_type == "handoff"]
    assert event.fields == {"target": "human", "mode": "warm"}
    assert not outbox(cell, TaskName.APPLY_OBJECT_EVENT)


async def test_aliases_find_the_stored_conversation(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    await send(ingest, principal(), message("m1", conversation_id="trunk-9"))

    await send(
        ingest, principal(), message("m2", conversation_id="platform-1", conversation_aliases=["trunk-9"])
    )

    assert {e.record.conversation_id for e in stored(cell)} == {"trunk-9"}
    assert cell.uow.store.data["ingest"]["conversations"][(SPACE_ID, "trunk-9")]["aliases"] == {"platform-1"}


async def test_future_timestamp_is_corrected(cell: Cell) -> None:
    ingest, _, _ = build(cell)

    await send(ingest, principal(), message("m1", occurred_at="2026-09-22T18:00:00Z"))

    [event] = stored(cell)
    assert event.skew_corrected
    assert event.record.occurred_at == cell.clock.now()


async def test_hot_layer_failure_marks_it_degraded_and_the_repair_restores_it(cell: Cell) -> None:
    ingest, sessions_, _ = build(cell)
    cell.hot.fail = True

    result = await send(ingest, principal(), message("m1"))

    assert result.accepted == 1
    assert await sessions_.is_degraded(HOT_LAYER)
    cell.hot.fail = False
    [(net, _, _)] = cell.uow.store.data["pipeline"]["scheduled"]
    cell.clock.advance(seconds=11)
    await ingest.hot_repair(net)
    assert len((await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).turns) == 1
    assert not await sessions_.is_degraded(HOT_LAYER)


async def test_hot_rebuild_re_adds_the_whole_hot_window(cell: Cell, monkeypatch: pytest.MonkeyPatch) -> None:
    # After a restore purged the space's turns: everything within the cap comes back, nothing older.
    ingest, sessions_, _ = build(cell)
    await send(ingest, principal(), message("m1"))
    cell.clock.advance(hours=48)
    await send(ingest, principal(), message("m2", occurred_at="2026-09-24T17:00:00Z"))
    cell.clock.advance(hours=25)
    await send(ingest, principal(), message("m3", occurred_at="2026-09-25T18:00:00Z"))
    cell.hot.turns.clear()

    monkeypatch.setattr("niadra.app.ingest.REBUILD_PAGE", 1)  # one event per page: the pages chain by id
    await ingest.hot_rebuild(Task(TaskName.HOT_REBUILD, SPACE_ID))

    turns = (await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).turns
    assert len(turns) == 2  # m1 is 73 hours old, past the 72-hour cap
    assert not await sessions_.is_degraded(HOT_LAYER)


async def test_dedupe_rotation_keeps_the_previous_generation(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    await send(ingest, principal(), message("old"))

    assert await ingest.dedupe_rotate(Task(TaskName.DEDUPE_ROTATE, SPACE_ID)) == 1
    assert await ingest.dedupe_rotate(Task(TaskName.DEDUPE_ROTATE, SPACE_ID)) is None  # younger than 29 days
    assert (await send(ingest, principal(), message("old"))).duplicates == 1

    cell.clock.advance(days=30)
    assert await ingest.dedupe_rotate(Task(TaskName.DEDUPE_ROTATE, SPACE_ID)) == 2
    cell.clock.advance(days=30)
    assert await ingest.dedupe_rotate(Task(TaskName.DEDUPE_ROTATE, SPACE_ID)) == 3
    assert (await send(ingest, principal(), message("old"))).accepted == 1


async def test_a_key_without_write_scopes_cannot_send_a_batch(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    with pytest.raises(Forbidden):
        await send(ingest, principal(scopes=frozenset({Scope.CONTEXT})), message("m1"))


async def test_heartbeat_items_record_what_the_sdk_sent(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    beat = {"type": "heartbeat", "window_start": "2026-09-22T16:58:10Z", "sent": 7}

    await send(ingest, principal(), beat)

    row = cell.uow.store.data["ingest"]["heartbeats"][
        (SPACE_ID, WHATSAPP_SOURCE, cell.clock.now() - timedelta(minutes=2))
    ]
    assert row["sent"] == 7


async def test_media_upload_returns_a_presigned_url(cell: Cell) -> None:
    ingest, _, _ = build(cell)

    upload = await ingest.create_media_upload(
        principal(), MediaUploadIn(content_type="audio/ogg", size_bytes=10, sha256="a" * 64)
    )

    assert upload.media_ref.startswith("med_")
    assert f"media/{SPACE_ID}/_/" in upload.upload_url
    assert upload.expires_at == cell.clock.now() + timedelta(minutes=15)


async def test_media_with_a_subject_is_stored_under_its_handle(cell: Cell) -> None:
    ingest, _, identity = build(cell)

    upload = await ingest.create_media_upload(
        principal(), MediaUploadIn(content_type="audio/ogg", size_bytes=10, sha256="a" * 64, subject=PHONE)
    )

    key = media_key(SPACE_ID, upload.media_ref)
    assert key is not None
    assert key.startswith(f"media/{SPACE_ID}/{handle_id(PHONE)}/")
    assert key in upload.upload_url
    assert identity.calls[-1].origin_event_id is None


async def test_feedback_is_a_system_event_without_object_work(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    fact_id = await held(cell, PHONE)
    request = FeedbackRequest(
        idempotency_key="f1", subject=PHONE, action="retract_fact", fact_id=UUID(fact_id), reason="wrong"
    )

    result = await ingest.record_feedback(principal(), request)

    assert result.accepted == 1
    [event] = stored(cell)
    assert event.record.canonical_type == "feedback.retract_fact"
    assert event.record.fields == {"action": "retract_fact", "fact_id": fact_id, "reason": "wrong"}
    assert not outbox(cell, TaskName.APPLY_OBJECT_EVENT)
    assert not sessions(cell)


async def test_feedback_on_what_the_subject_does_not_hold_is_refused_not_stored(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    other = Handle(type=HandleType.PHONE_E164, value="+5511966667777")
    item_id = await held(cell, other, item=True)
    fact_id = await held(cell, other)
    resolve = FeedbackRequest(
        idempotency_key="f1", subject=PHONE, action="resolve_open_item", open_item_id=UUID(item_id)
    )
    with pytest.raises(NotFound, match="no open item with this id for this subject"):
        await ingest.record_feedback(principal(), resolve)
    retract = FeedbackRequest(
        idempotency_key="f2", subject=PHONE, action="retract_fact", fact_id=UUID(fact_id)
    )
    with pytest.raises(NotFound, match="no fact"):
        await ingest.record_feedback(principal(), retract)
    assert stored(cell) == []
    # In a batch, the one not held is refused by position and the others are stored.
    mine = FeedbackRequest(
        idempotency_key="f3", subject=other, action="resolve_open_item", open_item_id=UUID(item_id)
    )
    result = await ingest.record_feedback_batch(principal(), [resolve, mine])
    assert (result.accepted, [(e.index, e.code) for e in result.errors]) == (1, [(0, "not_found")])
    [event] = stored(cell)
    assert event.record.fields["open_item_id"] == item_id


async def test_identify_needs_its_scope_and_attaches_the_handles(cell: Cell) -> None:
    ingest, _, identity = build(cell)
    identify = {
        "type": "identify",
        "idempotency_key": "i1",
        "occurred_at": "2026-09-22T16:59:00Z",
        "handles": [
            {"type": "phone_e164", "value": "+5511988887777"},
            {"type": "email", "value": "ana@example.com"},
        ],
    }

    denied = await send(ingest, principal(scopes=frozenset({Scope.TRACK})), identify)
    accepted = await send(ingest, principal(), identify)

    assert [e.code for e in denied.errors] == ["scope_missing"]
    assert accepted.accepted == 1
    assert identity.calls[-1].method is AssertionMethod.EXPLICIT_IDENTIFY


async def test_the_merge_an_identify_asks_for_runs_before_the_answer(cell: Cell) -> None:
    """The answer returns once the merge ran, so the next `context()` reads one customer; an event
    that names two customers' handles leaves its merge to the workers."""
    ingest, _, identity = build(cell)
    identity.merge = PendingMerge(UUID(int=1), (UUID(int=2),), (UUID(int=3), UUID(int=4)))
    identify = {
        "type": "identify",
        "idempotency_key": "i-merge",
        "occurred_at": "2026-09-22T16:59:00Z",
        "handles": [
            {"type": "phone_e164", "value": "+5511988887777"},
            {"type": "system_id", "value": "48213", "scope": "crm"},
        ],
    }

    await send(ingest, principal(), message("m-1"))
    assert identity.merged == []
    accepted = await send(ingest, principal(), identify)

    assert accepted.accepted == 1
    assert identity.merged == [identity.merge]


async def test_identify_takes_a_channel_rotation_and_refuses_what_only_the_cell_infers(cell: Cell) -> None:
    ingest, _, identity = build(cell)

    def rotation(method: str) -> dict[str, object]:
        return {
            "type": "identify",
            "idempotency_key": f"rot-{method}",
            "occurred_at": "2026-09-22T16:59:00Z",
            "method": method,
            "handles": [
                {"type": "wa_bsuid", "value": "BR.old", "scope": "waba-1"},
                {"type": "wa_bsuid", "value": "BR.new", "scope": "waba-1"},
            ],
        }

    rotated = await send(ingest, principal(), rotation("channel_rotation"))
    inferred = await send(ingest, principal(), rotation("same_event"))

    assert rotated.accepted == 1
    assert identity.calls[-1].method is AssertionMethod.CHANNEL_ROTATION
    assert [e.code for e in inferred.errors] == ["invalid_input"]


async def test_webhook_counts_refusals_and_unmapped_types_and_keeps_the_raw_payload(cell: Cell) -> None:
    config = sample_config().model_copy(
        update={
            "mappings": [
                Mapping(
                    source_id=BILLING_SOURCE,
                    version="m1",
                    auth=WebhookAuth(mode="url_token", secret_ref=f"spaces/{SPACE_ID}/webhook-in/wh"),
                    rules=[
                        MappingRule(
                            match="type == 'paid'",
                            canonical_type="invoice.paid",
                            idempotency_key="id",
                            occurred_at="at",
                        )
                    ],
                )
            ]
        }
    )
    cell.config.put(config)
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/wh"] = "t0k"
    ingest, _, _ = build(cell)
    window = (SPACE_ID, BILLING_SOURCE, cell.clock.now().replace(second=0))

    with pytest.raises(Unauthenticated):
        await ingest.ingest_webhook(BILLING_SOURCE, {}, b"{}", {"token": "wrong"})
    unmapped = await ingest.ingest_webhook(BILLING_SOURCE, {}, b'{"type": "refunded"}', {"token": "t0k"})

    assert unmapped == BatchResponse(accepted=0, duplicates=0, errors=[])
    row = cell.uow.store.data["ingest"]["heartbeats"][window]
    assert (row["rejected"], row["unmapped"]) == (1, 1)
    assert [k for k in cell.objects.objects if k.startswith(f"raw/{SPACE_ID}/_/2026-09-22/")]
    assert not stored(cell)


@pytest.mark.parametrize("mode", ["hmac", "bearer", "mtls", None])
async def test_a_get_challenge_is_answered_only_for_a_url_token_mapping(cell: Cell, mode: str | None) -> None:
    # Only a URL-token mapping carries a credential on the GET; under any other mode (or none) the
    # endpoint would echo attacker-chosen text from the cell's address to anyone who knew the source id.
    from niadra.domain.common.errors import NotFound

    ref = f"spaces/{SPACE_ID}/webhook-in/wh"
    rules = [MappingRule(match="type", idempotency_key="id", occurred_at="at")]
    auth = WebhookAuth(mode=mode, secret_ref=ref) if mode else None  # type: ignore[arg-type]
    guarded = Mapping(
        source_id=BILLING_SOURCE, version="m1", auth=auth, verification_challenge="hub.challenge", rules=rules
    )
    cell.config.put(sample_config().model_copy(update={"mappings": [guarded]}))
    cell.secrets.values[ref] = "t0k"
    ingest, _, _ = build(cell)

    for query in ({"hub.challenge": "c1"}, {"hub.challenge": "c1", "token": "t0k"}):
        with pytest.raises(NotFound):
            await ingest.webhook_challenge(BILLING_SOURCE, query)


async def test_a_url_token_challenge_needs_the_token(cell: Cell) -> None:
    ref = f"spaces/{SPACE_ID}/webhook-in/wh"
    mapping = Mapping(
        source_id=BILLING_SOURCE,
        version="m1",
        auth=WebhookAuth(mode="url_token", secret_ref=ref),
        verification_challenge="hub.challenge",
        rules=[MappingRule(match="type", idempotency_key="id", occurred_at="at")],
    )
    cell.config.put(sample_config().model_copy(update={"mappings": [mapping]}))
    cell.secrets.values[ref] = "t0k"
    ingest, _, _ = build(cell)

    for query in ({"hub.challenge": "c1"}, {"hub.challenge": "c1", "token": "wrong"}):
        with pytest.raises(Unauthenticated):
            await ingest.webhook_challenge(BILLING_SOURCE, query)
    query = {"hub.mode": "subscribe", "hub.challenge": "1158201444", "token": "t0k"}
    assert await ingest.webhook_challenge(BILLING_SOURCE, query) == "1158201444"


async def test_a_webhook_on_a_client_certificate_compares_the_forwarded_one_with_the_pinned_fingerprint(
    cell: Cell,
) -> None:
    # The edge's mTLS address proves the client holds the certificate and forwards it; a header
    # naming a fingerprint, or another certificate, is refused and counted as a refusal.
    import base64
    import hashlib

    from niadra.domain.common.errors import Unauthenticated

    certificate = b"the DER bytes of the ERP's client certificate"
    config = sample_config().model_copy(
        update={
            "mappings": [
                Mapping(
                    source_id=BILLING_SOURCE,
                    version="m1",
                    auth=WebhookAuth(mode="mtls", secret_ref=f"spaces/{SPACE_ID}/webhook-in/cert"),
                    rules=[MappingRule(match="type", idempotency_key="id", occurred_at="at")],
                )
            ]
        }
    )
    cell.config.put(config)
    fingerprint = hashlib.sha256(certificate).hexdigest()
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/cert"] = ":".join(
        fingerprint[i : i + 2] for i in range(0, 64, 2)
    ).upper()
    ingest, _, _ = build(cell)
    window = (SPACE_ID, BILLING_SOURCE, cell.clock.now().replace(second=0))

    other = base64.b64encode(b"someone else's certificate").decode()
    for claimed in ({"x-client-cert-sha256": fingerprint}, {"x-forwarded-tls-client-cert": other}, {}):
        with pytest.raises(Unauthenticated):
            await ingest.ingest_webhook(BILLING_SOURCE, claimed, b'{"type": "paid", "id": 1}', {})
    assert cell.uow.store.data["ingest"]["heartbeats"][window]["rejected"] == 3
    assert not stored(cell)
    pem = f"-----BEGIN CERTIFICATE-----{base64.b64encode(certificate).decode()}-----END CERTIFICATE-----"
    forwarded = {"x-forwarded-tls-client-cert": pem}
    # Authenticated: the payload reaches the mapping, which reads it (this one lacks what an event needs).
    result = await ingest.ingest_webhook(BILLING_SOURCE, forwarded, b'{"type": "paid", "id": 1}', {})
    assert [e.code for e in result.errors] == ["invalid_input"]


async def test_webhook_without_declared_authentication_is_refused(cell: Cell) -> None:
    config = sample_config().model_copy(
        update={
            "mappings": [
                Mapping(
                    source_id=BILLING_SOURCE,
                    version="m1",
                    rules=[MappingRule(match="type", idempotency_key="id", occurred_at="at")],
                )
            ]
        }
    )
    cell.config.put(config)
    ingest, _, _ = build(cell)

    with pytest.raises(Forbidden):
        await ingest.ingest_webhook(BILLING_SOURCE, {}, b"{}", {})


async def test_a_batch_with_a_system_event_keeps_its_raw_payload_before_the_commit(cell: Cell) -> None:
    # A system's event keeps the payload as it arrived, like a webhook's; an agent's turns do not.
    import orjson

    from niadra.contracts.ingest import validate_items

    ingest, _, _ = build(cell)
    caller = principal(BILLING_SOURCE)
    system = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "erp-1",
        "channel": "erp",
        "canonical_type": "invoice.credited",
        "handles": [{"type": "phone_e164", "value": "+5511988887777"}],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0823"}],
        "speaker": {"role": "system"},
        "fields": {"amount": 40},
        "occurred_at": "2026-09-22T16:59:00Z",
    }
    body = orjson.dumps({"items": [system]})
    items, errors = validate_items([system])
    await ingest.ingest_items(caller, items, errors, raw_body=body)
    [(key, stored)] = [(k, v) for k, v in cell.objects.objects.items() if k.startswith("raw/")]
    assert stored == body
    [event] = [e for e in cell.uow.store.module("ingest")["events"].values()]
    assert event.raw_ref == key

    turn = message("wa-1")
    items, errors = validate_items([turn])
    await ingest.ingest_items(principal(), items, errors, raw_body=orjson.dumps({"items": [turn]}))
    assert len([k for k in cell.objects.objects if k.startswith("raw/")]) == 1


async def test_a_mapping_never_authenticates_with_a_secret_of_another_space(cell: Cell) -> None:
    # The vault is the cell's: a mapping that names another space's secret is refused even when the
    # caller presents that secret, and nothing is stored.
    from niadra.domain.common.ids import uuid7

    other = f"spaces/{uuid7()}/webhook-in/wh"
    mapping = Mapping(
        source_id=BILLING_SOURCE,
        version="m1",
        auth=WebhookAuth(mode="url_token", secret_ref=other),
        verification_challenge="challenge",
        rules=[MappingRule(match="type == 'paid'", idempotency_key="id", occurred_at="at")],
    )
    cell.config.put(sample_config().model_copy(update={"mappings": [mapping]}))
    cell.secrets.values[other] = "t0k"
    ingest, _, _ = build(cell)
    body = b'{"type": "paid", "id": "e1", "at": "2026-09-22T12:00:00+00:00"}'

    with pytest.raises(Forbidden):
        await ingest.ingest_webhook(BILLING_SOURCE, {}, body, {"token": "t0k"})
    with pytest.raises(Forbidden):
        await ingest.webhook_challenge(BILLING_SOURCE, {"token": "t0k", "challenge": "c"})
    assert not stored(cell)


CASE_TYPE = {
    "type": "lawsuit",
    "ownership": "subject",
    "mirror_of": {"system": "cases"},
    "sources": {"cases": {"kind": "push", "precedence": 1}},
}
NOTE_TYPE = {"type": "draft", "ownership": "agent"}


async def test_a_customer_facing_agent_names_only_objects_a_system_sent(cell: Cell) -> None:
    # juridico-sandbox, 05/10/2026: the attendant sent `notify_client` on "legaldesk:<uuid>", a case no system
    # had sent; the action made a phantom case and a promise said on it became a second one.
    config = cell.config.space(SPACE_ID)
    types = ObjectTypeRegistry.model_validate({"types": [CASE_TYPE, NOTE_TYPE]})
    cell.config.put(config.model_copy(update={"object_types": types}))
    known = ObjectKey("lawsuit", "cases", "1012345")
    async with cell.uow(SPACE_ID) as uow:
        await uow.objects.add_object(BusinessObject(UUID(int=41), known, {}, T0, BILLING_SOURCE))
    ingest, _, _ = build(cell)

    def told(key: str, *refs: dict[str, str], operation: str = "notify_client") -> dict[str, Any]:
        action = {"operation": operation, "result": "avisei o cliente"}
        return message(key, kind="action", content=None, speaker={"role": "ai_agent"}, object_refs=list(refs),
                       action=action)  # fmt: skip

    case = {"type": "lawsuit", "namespace": "cases", "id": "1012345"}
    phantom = {"type": "lawsuit", "namespace": "legaldesk", "id": "legaldesk:0192f5a0"}
    result = await send(
        ingest,
        principal(WHATSAPP_SOURCE),
        told("a1", case),
        told("a2", phantom),
        told("a3", case, phantom),
        told("a4", {"type": "draft", "namespace": "agent", "id": "d1"}),
        told("a5", {"type": "undeclared", "namespace": "x", "id": "1"}),
    )
    assert result.accepted == 3
    assert [(e.index, e.code) for e in result.errors] == [(1, "unknown_object"), (2, "unknown_object")]
    assert "lawsuit legaldesk:legaldesk:0192f5a0" in (result.errors[0].detail or "")
    assert "cases" in (result.errors[0].detail or "")
    # A system's own action, or one of an internal agent, keeps naming what the system has not sent yet.
    internal = await send(ingest, principal(BILLING_SOURCE), told("b1", phantom, operation="credit"))
    assert (internal.accepted, internal.errors) == (1, [])


async def test_an_action_on_what_a_system_event_of_the_same_batch_sends_is_known(cell: Cell) -> None:
    config = cell.config.space(SPACE_ID)
    cell.config.put(
        config.model_copy(update={"object_types": ObjectTypeRegistry.model_validate({"types": [CASE_TYPE]})})
    )
    ingest, _, _ = build(cell)
    ref = {"type": "lawsuit", "namespace": "cases", "id": "2002"}
    opened = message(
        "s1", kind="system_event", canonical_type="lawsuit.opened", content=None,
        speaker={"role": "system"}, object_refs=[ref],
    )  # fmt: skip
    action = message(
        "a1", kind="action", content=None, speaker={"role": "ai_agent"}, object_refs=[ref],
        action={"operation": "notify_client", "result": "avisei"},
    )  # fmt: skip
    caller = principal(WHATSAPP_SOURCE, scopes=frozenset(Scope) - {Scope.ADMIN, Scope.ANALYTICS})
    result = await send(ingest, caller, opened, action)
    assert (result.accepted, result.errors) == (2, [])
