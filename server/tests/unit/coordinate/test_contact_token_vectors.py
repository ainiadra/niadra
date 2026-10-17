"""The contact token against the contact-token spec's vectors (`tests/spec/vectors/contact-token.v0.json`,
copied as published), the same file both SDKs run: issuing is the same bytes, and the offline verification
refuses with the first code of the spec's section 6 that applies."""

from typing import Any

import pytest

from niadra.domain.coordinate.destination import unb64url
from niadra.domain.coordinate.token import (
    PublicKey,
    TokenClaims,
    TokenRefused,
    issue,
    public_x,
    signing_key,
    verify,
)
from tests.spec import cases

FIELDS = {
    "op",
    "description",
    "seed",
    "claims",
    "keys",
    "gateway",
    "token",
    "destination",
    "channel",
    "now",
    "seen_jti",
}
CASES = cases("contact-token", FIELDS)


def _keys(found: list[dict[str, Any]]) -> list[PublicKey]:
    keys = []
    for key in found:
        assert set(key) <= {"kid", "kty", "crv", "x", "space", "status", "not_after"}
        assert (key["kty"], key["crv"]) == ("OKP", "Ed25519")
        keys.append(PublicKey(key["kid"], key["x"], key["space"], key.get("status", "active")))
    return keys


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_a_vector(case: dict[str, Any]) -> None:
    expect = case["expect"]
    if case["op"] == "issue":
        assert set(expect) == {"token"}
        key = signing_key(unb64url(case["seed"]))
        assert issue(TokenClaims(**case["claims"]), key) == expect["token"]
        return
    assert case["op"] == "verify"
    gateway = case["gateway"]

    def run() -> Any:
        return verify(
            case["token"],
            keys=_keys(case["keys"]),
            gateway_id=gateway["gateway_id"],
            space=gateway["space"],
            gateway_key=unb64url(gateway["key"]),
            destination=case["destination"],
            channel=case["channel"],
            now=case["now"],
            seen=set(case["seen_jti"]),
        )

    if "error" in expect:
        assert set(expect) == {"error"}
        with pytest.raises(TokenRefused) as refused:
            run()
        assert refused.value.code == expect["error"]
        return
    assert set(expect) == {"claims"}
    assert run() == TokenClaims(**expect["claims"])


def test_the_issuing_key_publishes_the_public_key_the_vectors_verify_with() -> None:
    [issued] = [c for c in CASES if c["id"] == "issue.marketing"]
    [valid] = [c for c in CASES if c["id"] == "verify.valid"]
    assert public_x(signing_key(unb64url(issued["seed"]))) == valid["keys"][0]["x"]


def test_every_vector_file_case_runs() -> None:
    assert len(CASES) == 18


def test_the_published_contract_names_the_payload_the_server_signs() -> None:
    # The contact-token schema is generated from ContactTokenClaims: its members are the signed payload's,
    # in the same order, so the published format and the issued tokens cannot drift apart.
    from dataclasses import fields

    from niadra.contracts.coordinate import ContactTokenClaims

    assert list(ContactTokenClaims.model_fields) == [f.name for f in fields(TokenClaims)]


@pytest.mark.parametrize("case", [c for c in CASES if c["op"] == "issue"], ids=lambda c: c["description"])
def test_every_issued_payload_is_valid_under_the_published_contract(case: dict[str, Any]) -> None:
    from niadra.contracts.coordinate import ContactTokenClaims

    ContactTokenClaims.model_validate(case["claims"])


def test_a_payload_the_contract_refuses_is_never_signed() -> None:
    from niadra.contracts.coordinate import ContactTokenClaims

    long_lived = {
        "kid": "kid12345", "space": "00000000-0000-0000-0000-000000000001",
        "jti": "00000000-0000-0000-0000-000000000002", "purpose": "service", "channel": "whatsapp",
        "rcpt": "A" * 43, "gateway": "gw", "iat": 0, "exp": 10_000,
    }  # fmt: skip
    with pytest.raises(ValueError, match="lives more than 0"):
        ContactTokenClaims.model_validate(long_lived)
