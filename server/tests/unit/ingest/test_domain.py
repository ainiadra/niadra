import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from niadra.app.ingest_mapping import map_payload
from niadra.config.models import HandleExpr, Mapping, MappingRule, ObjectExpr
from niadra.contracts.ingest import validate_items
from niadra.domain.common.errors import InvalidInput, Unauthenticated, VerificationNotAllowed
from niadra.domain.ingest import webhook_auth
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.ingest.otlp import otlp_items
from niadra.domain.ingest.rules import (
    OperationNotAllowed,
    ScopeMissing,
    capped_hint,
    check_action,
    check_identify_method,
    check_scope,
    default_subject_kind,
)
from niadra.domain.ingest.skew import correct_occurred_at
from niadra.domain.sessions.rules import check_verify_level
from niadra.domain.vocabulary import (
    AssertionMethod,
    Audience,
    EventKind,
    HandleType,
    Scope,
    SubjectKind,
    Verification,
)

RECEIVED = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def test_future_timestamps_beyond_tolerance_are_corrected_and_marked() -> None:
    assert correct_occurred_at(RECEIVED + timedelta(minutes=6), RECEIVED) == (RECEIVED, True)
    assert correct_occurred_at(RECEIVED + timedelta(minutes=4), RECEIVED) == (
        RECEIVED + timedelta(minutes=4),
        False,
    )


def test_late_events_keep_their_time() -> None:
    # WhatsApp resync and the batch API legitimately deliver old messages.
    old = RECEIVED - timedelta(days=3)
    assert correct_occurred_at(old, RECEIVED) == (old, False)


@given(st.integers(min_value=-(10**6), max_value=10**6))
def test_corrected_time_is_never_after_arrival_plus_tolerance(seconds: int) -> None:
    stored, _ = correct_occurred_at(RECEIVED + timedelta(seconds=seconds), RECEIVED)
    assert stored <= RECEIVED + timedelta(minutes=5)


def test_scopes_per_item_type() -> None:
    check_scope({Scope.TRACK}, "event", EventKind.MESSAGE)
    check_scope({Scope.ACT}, "event", EventKind.ACTION)
    with pytest.raises(ScopeMissing):
        check_scope({Scope.TRACK}, "event", EventKind.ACTION)  # track alone never records an action (5.1)
    with pytest.raises(ScopeMissing):
        check_scope({Scope.TRACK}, "identify")


def test_action_operations_follow_the_sources_closed_list() -> None:
    check_action("credit", frozenset())
    check_action("credit", frozenset({"credit"}))
    with pytest.raises(OperationNotAllowed):
        check_action("refund", frozenset({"credit"}))


def test_verification_ceiling() -> None:
    check_verify_level(Verification.V2, Verification.V2)
    with pytest.raises(VerificationNotAllowed):
        check_verify_level(Verification.V4, Verification.V2)
    with pytest.raises(InvalidInput):
        check_verify_level(Verification.NO_CUSTOMER, Verification.V4)
    with pytest.raises(VerificationNotAllowed):
        check_identify_method(AssertionMethod.OTP, Verification.V2)
    with pytest.raises(InvalidInput):
        check_identify_method(AssertionMethod.SAME_EVENT, Verification.V4)


def test_hints_are_capped_and_no_customer_is_for_internal_agents() -> None:
    assert capped_hint(Verification.V4, Verification.V2, Audience.CUSTOMER_AGENT) is Verification.V2
    assert (
        capped_hint(Verification.NO_CUSTOMER, Verification.V2, Audience.INTERNAL_AGENT)
        is Verification.NO_CUSTOMER
    )
    with pytest.raises(VerificationNotAllowed):
        capped_hint(Verification.NO_CUSTOMER, Verification.V2, Audience.CUSTOMER_AGENT)


def test_organization_only_handles_default_to_account() -> None:
    assert default_subject_kind(HandleType.ORG_REGISTRY_HMAC, None) is SubjectKind.ACCOUNT
    assert default_subject_kind(HandleType.PHONE_E164, None) is SubjectKind.PERSON
    assert default_subject_kind(HandleType.SYSTEM_ID, SubjectKind.PARTNER) is SubjectKind.PARTNER


@given(st.integers(min_value=1, max_value=2**31), st.binary(max_size=64))
def test_hot_envelope_round_trips(version: int, ciphertext: bytes) -> None:
    assert unpack_envelope(pack_envelope(version, ciphertext)) == (version, ciphertext)


def test_hmac_webhook_signature_with_timestamp() -> None:
    body = b'{"id": 1}'
    stamp = str(int(RECEIVED.timestamp()))
    signature = hmac.new(b"s3cret", stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"x-signature": f"sha256={signature}", "x-timestamp": stamp}
    kwargs = {
        "secret": "s3cret",
        "header": "X-Signature",
        "algorithm": "sha256",
        "signed_payload": "timestamp.body",
        "timestamp_header": "X-Timestamp",
        "tolerance_seconds": 300,
    }
    webhook_auth.check_hmac(headers=headers, body=body, now=RECEIVED, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(Unauthenticated):
        webhook_auth.check_hmac(headers=headers, body=b'{"id": 2}', now=RECEIVED, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(Unauthenticated):
        webhook_auth.check_hmac(headers=headers, body=body, now=RECEIVED + timedelta(minutes=6), **kwargs)  # type: ignore[arg-type]


def test_bearer_and_url_token() -> None:
    webhook_auth.check_bearer({"authorization": "Bearer abc"}, "Authorization", "abc")
    webhook_auth.check_url_token({"token": "abc"}, "abc")
    for check in (
        lambda: webhook_auth.check_bearer({"authorization": "Bearer abd"}, "authorization", "abc"),
        lambda: webhook_auth.check_url_token({}, "abc"),
    ):
        with pytest.raises(Unauthenticated):
            check()


def test_mapping_turns_a_payload_into_items() -> None:
    source = uuid4()
    mapping = Mapping(
        source_id=source,
        version="m1",
        rules=[
            MappingRule(
                match="type == 'invoice.disputed'",
                canonical_type="invoice.disputed",
                idempotency_key="id",
                occurred_at="created",
                channel="erp",
                handles=[HandleExpr(type=HandleType.SYSTEM_ID, value="customer.id", scope="erp")],
                object_refs=[ObjectExpr(type="invoice", namespace="erp", id="invoice.number")],
                fields={"amount": "invoice.amount", "missing": "nothing.here"},
            ),
            MappingRule(match="type == 'never'", idempotency_key="id", occurred_at="created"),
        ],
    )
    payload = {
        "type": "invoice.disputed",
        "id": "evt_9",
        "created": 1790000000,
        "customer": {"id": "C-77"},
        "invoice": {"number": "0823", "amount": 120.5},
    }

    items, errors = validate_items(map_payload(mapping, payload))

    assert errors == []
    [(_, item)] = items
    assert item.idempotency_key == f"wh:{source}:evt_9"  # type: ignore[union-attr]
    assert item.kind is EventKind.SYSTEM_EVENT  # type: ignore[union-attr]
    assert item.fields == {"amount": 120.5}  # type: ignore[union-attr]
    assert item.object_refs[0].id == "0823"  # type: ignore[union-attr]
    assert item.occurred_at == datetime.fromtimestamp(1790000000, UTC)  # type: ignore[union-attr]


def test_a_system_text_without_a_conversation_is_read_as_one() -> None:
    # Only a conversation opens a session that gets extracted; a ticket's description had none.
    source = uuid4()

    def rule(**extra: Any) -> MappingRule:
        return MappingRule(
            match="type == 'ticket.created'",
            kind=EventKind.MESSAGE,
            idempotency_key="id",
            occurred_at="created",
            channel="helpdesk",
            text="ticket.description",
            handles=[HandleExpr(type=HandleType.SYSTEM_ID, value="customer.id", scope="crm")],
            **extra,
        )

    payload = {
        "type": "ticket.created",
        "id": "evt_1",
        "created": 1790000000,
        "customer": {"id": "C-77"},
        "ticket": {"id": "T-5", "description": "A fatura veio em dobro"},
    }

    def conversation(mapping_rule: MappingRule) -> str | None:
        mapping = Mapping(source_id=source, version="m1", rules=[mapping_rule])
        [(_, item)] = validate_items(map_payload(mapping, payload))[0]
        return item.conversation_id  # type: ignore[union-attr]

    # The texts about one ticket are one conversation; without an object, each text is its own.
    by_ticket = rule(object_refs=[ObjectExpr(type="ticket", namespace="crm", id="ticket.id")])
    assert conversation(by_ticket) == "obj:ticket:crm:T-5"
    assert conversation(rule()) == f"wh:{source}:evt_1"
    assert conversation(rule(conversation_id="ticket.id")) == "T-5"


def _span(attrs: dict[str, str], start: int = 1_790_000_000_000_000_000) -> dict[str, object]:
    return {
        "traceId": "t1",
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 2_000_000_000),
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()],
    }


def _body(*spans: dict[str, object]) -> dict[str, object]:
    return {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": list(spans)}]}]}


@pytest.mark.parametrize(
    "attrs",
    [
        {
            "gen_ai.input.messages": json.dumps(
                [
                    {"role": "system", "parts": [{"type": "text", "content": "be nice"}]},
                    {"role": "user", "parts": [{"type": "text", "content": "oi"}]},
                ]
            ),
            "gen_ai.output.messages": json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": "olá"}]}]
            ),
        },
        {
            "gen_ai.prompt.0.role": "system",
            "gen_ai.prompt.0.content": "be nice",
            "gen_ai.prompt.1.role": "user",
            "gen_ai.prompt.1.content": "oi",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "olá",
        },
        {
            "llm.input_messages.0.message.role": "user",
            "llm.input_messages.0.message.content": "oi",
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": "olá",
        },
    ],
    ids=["current", "legacy", "openinference"],
)
def test_otlp_cascade_of_attribute_generations(attrs: dict[str, str]) -> None:
    common = {
        "gen_ai.conversation.id": "c-1",
        "niadra.channel": "chat",
        "niadra.handle.email": "ana@example.com",
    }

    items = otlp_items(_body(_span({**common, **attrs})), default_channel="otel", user_scope="bot")

    assert [(i["speaker"]["role"], i["content"]["text"]) for i in items] == [
        ("customer", "oi"),
        ("ai_agent", "olá"),
    ]
    assert [i["idempotency_key"] for i in items] == ["otel:c-1:1", "otel:c-1:2"]
    assert {i["channel"] for i in items} == {"chat"}
    assert items[0]["handles"] == [{"type": "email", "value": "ana@example.com"}]


def test_otlp_history_repeated_in_the_next_span_keeps_its_keys() -> None:
    first = {
        "gen_ai.conversation.id": "c-1",
        "enduser.id": "u-1",
        "gen_ai.prompt.0.role": "user",
        "gen_ai.prompt.0.content": "oi",
    }
    second = {
        **first,
        "gen_ai.prompt.1.role": "assistant",
        "gen_ai.prompt.1.content": "olá",
        "gen_ai.prompt.2.role": "user",
        "gen_ai.prompt.2.content": "e a fatura?",
    }
    keys_first = [
        i["idempotency_key"]
        for i in otlp_items(_body(_span(first)), default_channel="otel", user_scope="bot")
    ]
    keys_second = [
        i["idempotency_key"]
        for i in otlp_items(_body(_span(second)), default_channel="otel", user_scope="bot")
    ]
    assert keys_second[: len(keys_first)] == keys_first
    assert keys_second == ["otel:c-1:1", "otel:c-1:2", "otel:c-1:3"]


def test_a_forwarded_client_certificate_is_compared_by_its_fingerprint() -> None:
    # Traefik forwards each certificate as URL-escaped base64 DER, the client's first.
    import base64
    import hashlib
    from urllib.parse import quote

    from niadra.domain.common.errors import Unauthenticated
    from niadra.domain.ingest.webhook_auth import check_mtls

    leaf, chain = b"\x30\x82leaf certificate", b"\x30\x82intermediate"
    forwarded = ",".join(quote(base64.b64encode(c).decode()) for c in (leaf, chain))
    pinned = hashlib.sha256(leaf).hexdigest()
    check_mtls({"x-forwarded-tls-client-cert": forwarded}, pinned.upper())
    with pytest.raises(Unauthenticated):
        check_mtls({"x-forwarded-tls-client-cert": forwarded}, hashlib.sha256(chain).hexdigest())
    with pytest.raises(Unauthenticated):
        check_mtls({"x-forwarded-tls-client-cert": "%%%not base64"}, pinned)
    with pytest.raises(Unauthenticated):
        check_mtls({}, pinned)
