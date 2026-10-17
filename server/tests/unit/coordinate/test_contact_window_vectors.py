"""A contact window against the suppression-list spec's vectors, the same file both SDKs run
(`tests/spec/vectors/contact-window.v0.json`, copied as published)."""

from datetime import datetime
from typing import Any

import pytest

from niadra.domain.coordinate.window import InvalidWindow, Window, window_until
from tests.spec import cases

CASES = cases("contact-window", {"description", "window", "at"})


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_a_vector(case: dict[str, Any]) -> None:
    expect = case["expect"]
    raw = case["window"]
    at = datetime.fromisoformat(case["at"])
    if "error" in expect:
        assert expect == {"error": "invalid_window"}
        with pytest.raises(InvalidWindow):
            window_until(Window.parse(raw["from"], raw["to"], raw["tz"], raw["days"]), at)
        return
    assert set(expect) == {"applies", "until"}
    until = window_until(Window.parse(raw["from"], raw["to"], raw["tz"], raw["days"]), at)
    assert (until is not None) is expect["applies"]
    assert (until.isoformat().replace("+00:00", "Z") if until else None) == expect["until"]


def test_every_vector_file_case_runs() -> None:
    assert len(CASES) == 16
