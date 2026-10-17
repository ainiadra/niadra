"""The test vectors of the contact token, the suppression key and the exposure token, run against the
reference implementations below, which follow the steps of their specs one by one.

Every vector file has the same envelope, and every case exactly the fields its spec documents: an
implementation fails, never skips, on a field it does not know, and so does this test.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import unicodedata
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors"
CASE_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
EXPECTED = {"error", "token", "claims", "canonical", "key", "exposure_id", "position"}
# A scenario's verdict (replay, section 8).
EXPECTED |= {"verdict", "completed", "infrastructure_errors", "pin_mismatches", "needs_paraphrase"}
EXPECTED |= {"assertions"}
# The tool counterfactual's overlap (counterfactual, section 4).
EXPECTED |= {"overlap"}
# A contact window at an instant (suppression list, section 6.4).
EXPECTED |= {"applies", "until"}
# Derivation by introspection (object type, section 8.9).
EXPECTED |= {"fingerprint", "type", "review", "changes"}
# The fields of each kind of case, as the specs list them.
FIELDS: dict[str, dict[str | None, set[str]]] = {
    "contact-token": {
        "issue": {"id", "op", "description", "seed", "claims", "expect"},
        "verify": {
            *("id", "op", "description", "keys", "gateway", "token", "destination", "channel", "now"),
            *("seen_jti", "expect"),
        },
    },
    "exposure-token": {
        "build": {"id", "op", "description", "exposure_id", "position", "expect"},
        "parse": {"id", "op", "description", "token", "expect"},
    },
    "suppression-key": {None: {"id", "description", "salt", "type", "value", "expect"}},
    "contact-window": {None: {"id", "description", "window", "at", "expect"}},
    "regression-stats": {None: {"id", "description", "executions", "baseline", "expect"}},
    "counterfactual-overlap": {None: {"id", "description", "a", "b", "k", "expect"}},
    "type-derive": {
        "derive": {"id", "op", "description", "catalog", "options", "expect"},
        "changes": {"id", "op", "description", "declared", "live", "expect"},
    },
}


def load(name: str) -> dict[str, Any]:
    return json.loads((VECTORS / f"{name}.v0.json").read_text())


def cases(name: str) -> list[dict[str, Any]]:
    return load(name)["cases"]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    """Strict base64url without padding: any other character, or padding, is an error."""
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text) or len(text) % 4 == 1:
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# The canonical destination and the per-reader key (suppression list, sections 3 and 4).
SEPARATORS = re.compile(r"[\s().\-/]")
DOMAIN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+"
EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@" + DOMAIN + "$")


class RefusedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


AREA_CODES = {
    *(str(n) for n in range(11, 20)),
    *("21", "22", "24", "27", "28"),
    *(str(n) for n in (31, 32, 33, 34, 35, 37, 38)),
    *(str(n) for n in range(41, 50)),
    *("51", "53", "54", "55"),
    *(str(n) for n in range(61, 70)),
    *("71", "73", "74", "75", "77", "79"),
    *(str(n) for n in range(81, 90)),
    *(str(n) for n in range(91, 100)),
}


def brazilian_national(national: str) -> bool:
    """Step 5: an area code, then 8 digits from 2 to 9 or 9 digits from 9."""
    if national[:2] not in AREA_CODES:
        return False
    if len(national) == 10:
        return national[2] in "23456789"
    return len(national) == 11 and national[2] == "9"


def phone(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value)
    text = "".join(c for c in folded if unicodedata.category(c) != "Cf").strip()
    if text[:4].lower() == "tel:":
        text = text[4:]
    international = text.startswith(("+", "00"))
    if international:
        text = text.replace("(0)", "")
    raw = SEPARATORS.sub("", text)
    digits = raw[1:] if raw.startswith("+") else raw[2:] if international else raw
    if not re.fullmatch(r"[0-9]+", digits):
        raise RefusedError("invalid_handle")
    if not international:
        national = digits.lstrip("0")
        if brazilian_national(national):
            digits = "55" + national
        elif digits.startswith("0") and len(national) in (12, 13) and brazilian_national(national[2:]):
            digits = "55" + national[2:]
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise RefusedError("invalid_handle")
    if digits.startswith("55"):
        rest = digits[2:]
        if len(rest) == 10 and rest[:2] in AREA_CODES and rest[2] in "6789":
            rest = rest[:2] + "9" + rest[2:]
        digits = "55" + rest
    if len(digits) == 13 and digits[:3] in ("521", "549"):
        digits = digits[:2] + digits[3:]
    return "phone:+" + digits


def canonical(type_: str, value: str) -> str:
    if type_ == "phone_e164":
        return phone(value)
    if type_ == "wa_id":
        text = value.strip()
        return phone(text if text.startswith("+") else "+" + text)
    if type_ == "wa_jid":
        user = value.strip().lower().partition("@")[0].split(":", 1)[0]
        return phone("+" + user)
    if type_ == "email":
        address = value.strip().lower()
        if not EMAIL.match(address):
            raise RefusedError("invalid_handle")
        return "email:" + address
    raise RefusedError("unsupported_type")


def keyed(key: str, text: str) -> str:
    return b64(hmac.new(unb64(key), text.encode(), hashlib.sha256).digest())


# The contact token (contact token, sections 3 to 6).
MEMBERS = {"kid": str, "space": str, "jti": str, "purpose": str, "channel": str, "rcpt": str, "gateway": str}
MEMBERS |= {"iat": int, "exp": int}
LEEWAY_S = 5
MAX_LIFE_S = 120


def issue(seed: str, claims: dict[str, Any]) -> str:
    head = "nct1." + b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head}.{b64(Ed25519PrivateKey.from_private_bytes(unb64(seed)).sign(head.encode()))}"


def verify(case: dict[str, Any]) -> dict[str, Any]:
    segments = case["token"].split(".")
    if len(segments) != 3 or not all(segments):
        raise RefusedError("malformed")
    if segments[0] != "nct1":
        raise RefusedError("unsupported_version")
    try:
        payload, signature = unb64(segments[1]), unb64(segments[2])
        claims = json.loads(payload)
    except (ValueError, binascii.Error) as exc:
        raise RefusedError("malformed") from exc
    if len(signature) != 64 or not isinstance(claims, dict) or set(claims) != set(MEMBERS):
        raise RefusedError("malformed")
    if any(type(claims[name]) is not kind for name, kind in MEMBERS.items()):
        raise RefusedError("malformed")
    key = next((k for k in case["keys"] if k["kid"] == claims["kid"] and k["space"] == claims["space"]), None)
    if key is None:
        raise RefusedError("unknown_key")
    try:
        public = Ed25519PublicKey.from_public_bytes(unb64(key["x"]))
        public.verify(signature, f"{segments[0]}.{segments[1]}".encode())
    except InvalidSignature as exc:
        raise RefusedError("bad_signature") from exc
    gateway, now = case["gateway"], case["now"]
    checks = [
        (claims["space"] == gateway["space"], "wrong_space"),
        (claims["gateway"] == gateway["gateway_id"], "wrong_gateway"),
        (0 < claims["exp"] - claims["iat"] <= MAX_LIFE_S, "lifetime_too_long"),
        (claims["iat"] - LEEWAY_S <= now, "not_yet_valid"),
        (now < claims["exp"] + LEEWAY_S, "expired"),
        (claims["channel"] == case["channel"], "wrong_channel"),
        (hmac.compare_digest(claims["rcpt"], keyed(gateway["key"], case["destination"])), "wrong_recipient"),
        (claims["jti"] not in case["seen_jti"], "replayed"),
    ]
    for passed, code in checks:
        if not passed:
            raise RefusedError(code)
    return claims


# The exposure token (exposure token, sections 3 and 4).
def exposure_token(exposure_id: str, position: int) -> str:
    head = f"nx1.{b64(uuid.UUID(exposure_id).bytes)}.{position}"
    return f"{head}.{b64(hashlib.sha256(head.encode()).digest())[:4]}"


def parse_exposure(token: str) -> dict[str, Any]:
    segments = token.split(".")
    if len(segments) != 4:
        raise RefusedError("malformed")
    version, ident, position, verifier = segments
    if version != "nx1":
        raise RefusedError("unsupported_version")
    try:
        raw = unb64(ident)
    except (ValueError, binascii.Error) as exc:
        raise RefusedError("malformed") from exc
    if len(ident) != 22 or len(raw) != 16 or b64(raw) != ident:
        raise RefusedError("malformed")
    if not re.fullmatch(r"[A-Za-z0-9_-]{4}", verifier):
        raise RefusedError("malformed")
    if not re.fullmatch(r"[1-9][0-9]{0,3}", position):
        raise RefusedError("bad_position")
    if exposure_token(str(uuid.UUID(bytes=raw)), int(position)) != token:
        raise RefusedError("bad_verifier")
    return {"exposure_id": str(uuid.UUID(bytes=raw)), "position": int(position)}


def outcome(run: Any) -> dict[str, Any]:
    try:
        return run()
    except RefusedError as refused:
        return {"error": refused.code}


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_every_vector_file_follows_the_shared_layout(name: str) -> None:
    document = load(name)
    assert set(document) == {"vectors", "version", "spec", "cases"}
    assert (document["vectors"], document["version"]) == (name, "v0")
    assert (ROOT / document["spec"]).exists()
    assert f"vectors/{name}.v0.json" in (ROOT / document["spec"]).read_text(), "the spec links its vectors"
    ids = [case["id"] for case in document["cases"]]
    assert len(ids) == len(set(ids))
    for case in document["cases"]:
        assert CASE_ID.match(case["id"]), case["id"]
        assert set(case) == FIELDS[name][case.get("op")], case["id"]
        assert set(case["expect"]) <= EXPECTED


CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
ZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$")


def window_end(window: dict[str, Any], at: str) -> str | None:
    """Section 6.4, step by step: the end of the window `at` falls in, in UTC, or None outside it."""
    start, end = (CLOCK.match(window[k]) for k in ("from", "to"))
    days = window["days"]
    if start is None or end is None or not ZONE.match(window["tz"]) or start.group(0) == end.group(0):
        raise RefusedError("invalid_window")
    if days is not None and any(not isinstance(d, int) or not 1 <= d <= 7 for d in days):
        raise RefusedError("invalid_window")
    try:
        zone = ZoneInfo(window["tz"])
    except ZoneInfoNotFoundError:
        raise RefusedError("invalid_window") from None
    begins = time(int(start.group(1)), int(start.group(2)))
    ends = time(int(end.group(1)), int(end.group(2)))
    local = datetime.fromisoformat(at).astimezone(zone)
    clock = local.time().replace(tzinfo=None)
    if begins < ends:  # step 1
        started = local.date() if begins <= clock < ends else None
    elif clock >= begins:  # step 2
        started = local.date()
    else:
        started = local.date() - timedelta(days=1) if clock < ends else None
    if started is None or (days is not None and started.isoweekday() not in days):  # step 3
        return None
    last = started + timedelta(days=1) if ends <= begins else started
    return datetime.combine(last, ends, tzinfo=zone).astimezone(UTC).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("case", cases("contact-window"), ids=lambda c: c["id"])
def test_contact_windows(case: dict[str, Any]) -> None:
    def run() -> dict[str, Any]:
        until = window_end(case["window"], case["at"])
        return {"applies": until is not None, "until": until}

    assert outcome(run) == case["expect"]


@pytest.mark.parametrize("case", cases("suppression-key"), ids=lambda c: c["id"])
def test_suppression_keys(case: dict[str, Any]) -> None:
    def run() -> dict[str, Any]:
        text = canonical(case["type"], case["value"])
        return {"canonical": text, "key": keyed(case["salt"], text)}

    assert outcome(run) == case["expect"]


@pytest.mark.parametrize("case", cases("contact-token"), ids=lambda c: c["id"])
def test_contact_tokens(case: dict[str, Any]) -> None:
    if case["op"] == "issue":
        assert issue(case["seed"], case["claims"]) == case["expect"]["token"]
    else:
        assert outcome(lambda: {"claims": verify(case)}) == case["expect"]


@pytest.mark.parametrize("case", cases("exposure-token"), ids=lambda c: c["id"])
def test_exposure_tokens(case: dict[str, Any]) -> None:
    if case["op"] == "build":
        token = exposure_token(case["exposure_id"], case["position"])
        assert token == case["expect"]["token"]
        assert len(token) == 32 + len(str(case["position"]))
    else:
        assert outcome(lambda: parse_exposure(case["token"])) == case["expect"]


def test_the_contact_token_vectors_cover_the_refusals_a_gateway_must_make() -> None:
    expected = {case["id"]: case["expect"] for case in cases("contact-token")}
    assert "claims" in expected["verify.valid"]
    for refusal in ("wrong_recipient", "replayed", "expired"):
        assert expected[f"verify.{refusal}"] == {"error": refusal}
    schema = json.loads((ROOT / "schemas" / "contact-token.v0.json").read_text())
    for case in cases("contact-token"):
        if "claims" in case["expect"] or case["op"] == "issue":
            claims = case["expect"].get("claims", case.get("claims"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(claims)


def test_the_first_card_token_is_33_characters_and_names_no_one() -> None:
    first = next(case for case in cases("exposure-token") if case["id"] == "build.first")
    assert len(first["expect"]["token"]) == 33
    assert set(first) - {"id", "op", "description", "expect"} == {"exposure_id", "position"}


def overlap_at_k(a: list[str], b: list[str], k: int) -> float:
    """Counterfactual 4.2, step by step."""
    first, second = list(dict.fromkeys(a)), list(dict.fromkeys(b))
    depth = min(k, max(len(first), len(second)))
    if depth == 0:
        return 1.0
    weights = [1 / math.log2(d + 1) for d in range(1, depth + 1)]
    shares = [len(set(first[:d]) & set(second[:d])) / d for d in range(1, depth + 1)]
    return sum(w * x for w, x in zip(weights, shares, strict=True)) / sum(weights)


@pytest.mark.parametrize("case", cases("counterfactual-overlap"), ids=lambda c: c["id"])
def test_counterfactual_overlap(case: dict[str, Any]) -> None:
    assert overlap_at_k(case["a"], case["b"], case["k"]) == pytest.approx(case["expect"]["overlap"], abs=1e-6)
    assert overlap_at_k(case["b"], case["a"], case["k"]) == pytest.approx(case["expect"]["overlap"], abs=1e-6)
