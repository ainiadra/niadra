"""The canonical destination and the suppression key against the suppression-list spec's vectors, the same
file both SDKs run (`tests/spec/vectors/suppression-key.v0.json`, copied as published)."""

import base64
from typing import Any

import pytest

from niadra.domain.common.errors import InvalidHandle, UnsupportedHandleType
from niadra.domain.coordinate.destination import canonical_destination, suppression_key
from tests.spec import cases

CASES = cases("suppression-key", {"description", "salt", "type", "value"})
ERRORS = {"invalid_handle": InvalidHandle, "unsupported_type": UnsupportedHandleType}


def _salt(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_a_vector(case: dict[str, Any]) -> None:
    expect = case["expect"]
    if "error" in expect:
        assert set(expect) == {"error"}
        with pytest.raises(ERRORS[expect["error"]]):
            canonical_destination(case["type"], case["value"])
        return
    assert set(expect) == {"canonical", "key"}
    canonical = canonical_destination(case["type"], case["value"])
    assert canonical == expect["canonical"]
    assert suppression_key(_salt(case["salt"]), canonical) == expect["key"]


def test_every_vector_file_case_runs() -> None:
    assert len(CASES) == 25
