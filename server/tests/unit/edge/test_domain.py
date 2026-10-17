from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from niadra.domain.common.errors import Conflict, Unauthenticated
from niadra.domain.edge.keys import fast_hash, parse_source_key, slow_hash, verify_slow_hash
from niadra.domain.edge.limits import IdempotencyRecord, rate_window_key, replayable, route_group
from niadra.domain.edge.tokens import verify_person_token
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import mint_source_key

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def test_key_format_round_trips() -> None:
    token, parsed = mint_source_key(Environment.PRODUCTION, "us-east-2", "acme-prod")
    assert token.startswith("nia_sk_live_us-east-2_acme-prod_")
    assert parse_source_key(token) == parsed
    assert parsed.environment is Environment.PRODUCTION


@pytest.mark.parametrize(
    "token",
    [
        "nia_sk_live_us-east-2_acme_short_" + "s" * 40,
        "nia_sk_prod_us-east-2_acme_" + "k" * 16 + "_" + "s" * 40,
        "nia_pk_live_us-east-2_acme_" + "k" * 16 + "_" + "s" * 40,
        "nia_sk_live_us-east-2_acme_" + "k" * 16 + "_" + "s" * 10,
        "",
    ],
)
def test_malformed_keys_are_refused(token: str) -> None:
    with pytest.raises(Unauthenticated):
        parse_source_key(token)


def test_fast_hash_depends_on_pepper_and_is_unambiguous() -> None:
    assert fast_hash(b"p1", "abc", "def") == fast_hash(b"p1", "abc", "def")
    assert fast_hash(b"p1", "abc", "def") != fast_hash(b"p2", "abc", "def")
    assert fast_hash(b"p1", "ab", "cdef") != fast_hash(b"p1", "abc", "def")


def test_slow_hash_verifies_only_the_right_secret() -> None:
    stored = slow_hash("keyid0001", "secret" * 5)
    assert verify_slow_hash(stored, "keyid0001", "secret" * 5)
    assert not verify_slow_hash(stored, "keyid0001", "other" * 6)
    assert not verify_slow_hash("bcrypt$garbage", "keyid0001", "secret" * 5)


def test_routes_split_writes_from_reads() -> None:
    assert route_group("/v1/batch") == "write"
    assert route_group("/v1/ingest/webhook/x") == "write"
    assert route_group("/v1/context") == "read"
    assert rate_window_key("k1", "/v1/batch", NOW) != rate_window_key("k1", "/v1/context", NOW)
    assert rate_window_key("k1", "/v1/batch", NOW) != rate_window_key(
        "k1", "/v1/batch", NOW + timedelta(seconds=1)
    )


def test_idempotency_replays_the_same_body_and_refuses_another() -> None:
    record = IdempotencyRecord("idem-1", b"h1", 201, {"ok": True}, NOW)
    assert replayable(record, b"h1", NOW + timedelta(hours=1))
    with pytest.raises(Conflict):
        replayable(record, b"h2", NOW + timedelta(hours=1))
    assert not replayable(record, b"h2", NOW + timedelta(hours=24))
    assert not replayable(None, b"h1", NOW)


def _signed(claims: dict[str, object], key: Ed25519PrivateKey, kid: str = "k1") -> str:
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid})


def _jwks(key: Ed25519PrivateKey) -> dict[str, bytes]:
    return {"k1": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}


def test_person_token_is_verified_offline() -> None:
    key = Ed25519PrivateKey.generate()
    user, space, source = uuid4(), uuid4(), uuid4()
    claims = {
        "sub": str(user),
        "space": str(space),
        "roles": ["reviewer"],
        "sources": [str(source)],
        "aud": "niadra-cell",
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    parsed = verify_person_token(_signed(claims, key), _jwks(key), NOW)
    assert (parsed.user_id, parsed.space_id, parsed.roles, parsed.sources) == (
        user,
        space,
        frozenset({"reviewer"}),
        frozenset({source}),
    )
    assert parsed.label is None
    named = verify_person_token(_signed({**claims, "email": "ana@acme.com"}, key), _jwks(key), NOW)
    assert named.label == "ana@acme.com"


@pytest.mark.parametrize(
    ("change", "kid"),
    [
        ({"exp": int((NOW - timedelta(minutes=5)).timestamp())}, "k1"),
        ({"aud": "niadra-control"}, "k1"),
        ({}, "unknown"),
    ],
)
def test_person_token_refused_when_expired_misaddressed_or_unknown_key(
    change: dict[str, object], kid: str
) -> None:
    key = Ed25519PrivateKey.generate()
    claims = {
        "sub": str(uuid4()),
        "space": str(uuid4()),
        "aud": "niadra-cell",
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
        **change,
    }
    with pytest.raises(Unauthenticated):
        verify_person_token(_signed(claims, key, kid), _jwks(key), NOW)


def test_person_token_signed_by_another_key_is_refused() -> None:
    claims = {
        "sub": str(uuid4()),
        "space": str(uuid4()),
        "aud": "niadra-cell",
        "exp": int(NOW.timestamp()) + 60,
    }
    with pytest.raises(Unauthenticated):
        verify_person_token(
            _signed(claims, Ed25519PrivateKey.generate()), _jwks(Ed25519PrivateKey.generate()), NOW
        )
