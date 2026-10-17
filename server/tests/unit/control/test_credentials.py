import re
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.credentials import (
    RECOVERY_ALPHABET,
    check_password,
    format_key,
    hash_password,
    matching_hash,
    new_key_id,
    new_recovery_codes,
    new_secret,
    normalize_recovery_code,
    recovery_code_hash,
    totp_code,
    totp_step,
)
from niadra.domain.edge.keys import fast_hash, parse_source_key, slow_hash, verify_slow_hash
from niadra.domain.vocabulary import Environment

# The key format of 7.0 spelled out once more, independently of both implementations.
EDGE_KEY = re.compile(
    r"^nia_sk_(?P<env>live|test)_(?P<region>[a-z]{2}(?:-[a-z]+)+-\d{1,2})_(?P<slug>[a-z][a-z0-9-]{1,38}[a-z0-9])"
    r"_(?P<key_id>[a-z0-9]{16})_(?P<secret>[A-Za-z0-9]{40})$"
)


def test_a_formatted_key_parses_back_with_the_edge_rules() -> None:
    key_id, secret = new_key_id(), new_secret()
    key = format_key(Environment.SANDBOX, "sa-east-1", "acme-sandbox", key_id, secret)

    match = EDGE_KEY.match(key)
    assert match is not None
    assert match["env"] == "test"
    assert match["region"] == "sa-east-1"
    assert match["slug"] == "acme-sandbox"
    assert (match["key_id"], match["secret"]) == (key_id, secret)
    parsed = parse_source_key(key)
    assert (parsed.environment, parsed.space_slug, parsed.key_id, parsed.secret) == (
        Environment.SANDBOX,
        "acme-sandbox",
        key_id,
        secret,
    )


def test_production_keys_are_live() -> None:
    key = format_key(Environment.PRODUCTION, "us-east-2", "acme", new_key_id(), new_secret())
    assert key.startswith("nia_sk_live_us-east-2_acme_")


@pytest.mark.parametrize("slug", ["acme_x", "Acme", "a", "-acme"])
def test_a_slug_that_would_break_parsing_or_dns_is_never_issued(slug: str) -> None:
    with pytest.raises(InvalidInput):
        format_key(Environment.SANDBOX, "sa-east-1", slug, new_key_id(), new_secret())


def test_issued_ids_and_secrets_never_contain_the_separator() -> None:
    for _ in range(200):
        assert "_" not in new_key_id() + new_secret()


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=16, max_size=16))
def test_the_fast_hash_is_keyed_by_the_pepper(key_id: str) -> None:
    secret = "S" * 40
    assert fast_hash(b"p" * 32, key_id, secret) == fast_hash(b"p" * 32, key_id, secret)
    assert fast_hash(b"p" * 32, key_id, secret) != fast_hash(b"q" * 32, key_id, secret)


def test_the_slow_key_hash_binds_the_key_id() -> None:
    stored = slow_hash("k" * 16, "s" * 40)
    assert verify_slow_hash(stored, "k" * 16, "s" * 40)
    assert not verify_slow_hash(stored, "j" * 16, "s" * 40)


def test_passwords_verify_only_themselves() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert check_password("correct horse battery staple", encoded)
    assert not check_password("correct horse battery stapler", encoded)
    assert not check_password("anything", "not-a-hash")


def test_totp_matches_the_rfc_6238_vector() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b"12345678901234567890"
    assert totp_code(secret, 59) == "287082"
    assert totp_code(secret, 1111111109) == "081804"


def test_totp_accepts_one_step_of_drift_and_no_more() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    code = totp_code(secret, 1_000_000)
    assert totp_step(secret, code, 1_000_000 + 30) is not None
    assert totp_step(secret, code, 1_000_000 + 90) is None


# Crockford's base32 without the dash: digits and the letters other than I, L, O and U.
RECOVERY_CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{5}-[0-9A-HJKMNP-TV-Z]{5}$")


def test_recovery_codes_are_ten_different_codes_of_fifty_bits() -> None:
    codes = new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(RECOVERY_CODE.match(c) for c in codes)
    assert len(set(RECOVERY_ALPHABET)) == 32  # 5 bits a symbol, ten symbols
    assert not set("ILOU") & set(RECOVERY_ALPHABET)


@pytest.mark.parametrize(
    ("typed", "issued"),
    [
        ("ABCDE-FGHJK", "ABCDE-FGHJK"),
        ("abcde fghjk", "ABCDE-FGHJK"),
        (" AbCdEfGhJk\n", "ABCDE-FGHJK"),
        ("0o1il-23456", "00111-23456"),
    ],
)
def test_a_typed_recovery_code_reads_as_issued(typed: str, issued: str) -> None:
    assert normalize_recovery_code(typed) == issued


@pytest.mark.parametrize(
    "typed", ["", "ABCDE-FGHJ", "ABCDE-FGHJKM", "ABCDE-FGHJU", "ABCDE_FGHJK", "ÁBCDE-FGHJK"]
)
def test_what_cannot_be_a_recovery_code_reads_as_none(typed: str) -> None:
    assert normalize_recovery_code(typed) is None


@given(st.integers(min_value=0, max_value=10), st.booleans())
def test_case_and_spacing_never_change_a_recovery_code(cut: int, lower: bool) -> None:
    code = new_recovery_codes(1)[0]
    raw = code.replace("-", "")
    typed = f"{raw[:cut]} {raw[cut:]}"
    assert normalize_recovery_code(typed.lower() if lower else typed) == code


def test_a_recovery_code_hash_needs_the_pepper_and_belongs_to_one_person() -> None:
    person, other = uuid4(), uuid4()
    code = new_recovery_codes(1)[0]
    digest = recovery_code_hash(b"p" * 32, person, code)
    assert len(digest) == 32
    assert digest == recovery_code_hash(b"p" * 32, person, code)
    assert digest != recovery_code_hash(b"q" * 32, person, code)
    assert digest != recovery_code_hash(b"p" * 32, other, code)


def test_matching_hash_finds_only_an_equal_digest() -> None:
    stored = [bytes([n]) * 32 for n in range(3)]
    assert matching_hash(bytes([1]) * 32, stored) == stored[1]
    assert matching_hash(bytes([9]) * 32, stored) is None
    assert matching_hash(stored[0], []) is None
