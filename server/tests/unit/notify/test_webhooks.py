"""Standard Webhooks signatures checked with an independent verifier, the backoff and the body guard (7.5)."""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.domain.common.errors import InvalidInput
from niadra.domain.notify.webhooks import (
    DISABLE_AFTER,
    build_body,
    encode_body,
    endpoint_disabled,
    next_attempt_at,
    retry_delay,
    sign,
    signature_header,
)

NOW = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)


def reference_verify(
    secret: str, headers: Mapping[str, str], body: bytes, now: int, tolerance: int = 300
) -> bool:
    """The consumer side, written from the Standard Webhooks spec and independent of our signer."""
    msg_id, timestamp = headers["webhook-id"], headers["webhook-timestamp"]
    if abs(now - int(timestamp)) > tolerance:
        return False
    key = base64.b64decode(secret.split("_", 1)[1])
    content = f"{msg_id}.{timestamp}.{body.decode()}".encode()
    expected = base64.b64encode(hmac.new(key, content, hashlib.sha256).digest()).decode()
    for entry in headers["webhook-signature"].split(" "):
        version, _, value = entry.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


def test_matches_the_standard_webhooks_test_vector() -> None:
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    body = b'{"test": 2432232314}'
    assert sign(secret, "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330, body) == (
        "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    )


def test_every_active_secret_signs_during_rotation() -> None:
    old, new = (
        "whsec_" + base64.b64encode(b"old-key").decode(),
        "whsec_" + base64.b64encode(b"new-key").decode(),
    )
    body = encode_body({"b": 1, "a": 2})
    ts = int(NOW.timestamp())
    headers = {
        "webhook-id": "d1",
        "webhook-timestamp": str(ts),
        "webhook-signature": signature_header([old, new], "d1", ts, body),
    }
    assert headers["webhook-signature"].count("v1,") == 2
    assert reference_verify(old, headers, body, ts)
    assert reference_verify(new, headers, body, ts)
    assert not reference_verify("whsec_" + base64.b64encode(b"other").decode(), headers, body, ts)
    assert not reference_verify(new, headers, body + b" ", ts)
    assert not reference_verify(new, headers, body, ts + 301)  # stale timestamp


def test_body_bytes_are_canonical() -> None:
    assert encode_body({"b": 1, "a": {"d": 1, "c": 2}}) == b'{"a":{"c":2,"d":1},"b":1}'


def test_backoff_is_exponential_and_capped() -> None:
    delays = [retry_delay(n) for n in range(1, 12)]
    assert delays[:4] == [
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
    ]
    assert delays[-1] == timedelta(hours=4)
    assert delays == sorted(delays)


def test_retries_stop_at_the_24_hour_horizon() -> None:
    created = NOW
    assert next_attempt_at(created, 1, NOW) == NOW + timedelta(seconds=30)
    assert next_attempt_at(created, 12, NOW + timedelta(hours=23)) == created + timedelta(hours=24)
    assert next_attempt_at(created, 13, created + timedelta(hours=24)) is None
    schedule, now, attempts = [], created, 0
    while (nxt := next_attempt_at(created, attempts + 1, now)) is not None:
        attempts += 1
        schedule.append(nxt)
        now = nxt
    assert schedule[-1] == created + timedelta(hours=24)
    assert 10 < len(schedule) < 20


def test_endpoint_is_disabled_after_24_hours_of_continuous_failure() -> None:
    assert not endpoint_disabled(None, None, NOW)
    assert not endpoint_disabled(None, NOW - timedelta(hours=23), NOW)
    assert endpoint_disabled(None, NOW - DISABLE_AFTER, NOW)
    assert not endpoint_disabled(NOW - timedelta(hours=1), NOW - timedelta(hours=30), NOW)


def test_body_shape_and_the_content_guard() -> None:
    body = build_body(
        delivery_id=UUID(int=1),
        event_type="trait.added",
        created_at=NOW,
        tenant_id=UUID(int=2),
        subject=None,
        data={"trait_id": "t1", "evidence": [{"kind": "episode", "id": "e1"}]},
        links={"open": "/v1/profiles/p"},
    )
    assert set(body) == {"id", "type", "created_at", "tenant", "subject", "data", "links"}
    for leak in ({"text": "hi"}, {"evidence": [{"summary": "x"}]}, {"nested": {"transcript": "..."}}):
        with pytest.raises(InvalidInput):
            build_body(
                delivery_id=UUID(int=1),
                event_type="x",
                created_at=NOW,
                tenant_id=UUID(int=2),
                subject=None,
                data=leak,
                links=None,
            )
