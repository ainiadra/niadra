"""Affinity and the constraints block under the rules of retail round 5 (`vectors/signal-affinity.v0.json`):
the same derivation a stored body and a read's view run, from interactions to the block."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from niadra.contracts.signals import ConstraintsBlock
from niadra.domain.compile.constraints import build
from niadra.domain.signals.derive import Catalog, Entry, fold

DOCUMENT = json.loads((Path(__file__).parent / "vectors" / "signal-affinity.v0.json").read_text())
CASE_FIELDS = {"id", "now", "entries", "read", "catalog", "prior", "expect"}
EXPECT_FIELDS = {
    "values", "no_values", "hard", "soft", "attributes", "conflicts", "ask", "exclude", "already_presented",
    "relaxation_order", "for",
}  # fmt: skip
TOLERANCE = 1e-3


def _cases() -> list[dict[str, Any]]:
    assert DOCUMENT["vectors"] == "signal-affinity"
    assert DOCUMENT["version"] == "v0"
    for case in DOCUMENT["cases"]:
        assert not set(case) - CASE_FIELDS, case["id"]
        assert not set(case["expect"]) - EXPECT_FIELDS, case["id"]
    ids = [case["id"] for case in DOCUMENT["cases"]]
    assert len(set(ids)) == len(ids)
    return list(DOCUMENT["cases"])


def _catalog(raw: dict[str, Any]) -> Catalog:
    assert not set(raw) - {"families", "hidden", "sensitive_types"}
    return Catalog(
        raw.get("families", {}), frozenset(raw.get("hidden", ())), frozenset(raw.get("sensitive_types", ()))
    )


def _entries(raw: list[dict[str, Any]]) -> list[Entry]:
    out = []
    for i, item in enumerate(raw):
        assert not set(item) - {"at", "session", "interaction", "turn_id"}
        out.append(
            Entry(
                f"e{i:03d}",
                datetime.fromisoformat(item["at"]),
                item["interaction"],
                item["session"],
                item.get("turn_id"),
            )
        )
    return out


def _close(got: Any, want: Any) -> bool:
    if isinstance(want, float) and isinstance(got, int | float):
        return abs(got - want) <= TOLERANCE
    return bool(got == want)


def _matches(got: dict[str, Any], want: dict[str, Any]) -> bool:
    for key, value in want.items():
        found = got.get("origin", {}).get("turn_id") if key == "turn_id" else got.get(key)
        if not _close(found, value):
            return False
    return True


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_signals_derive_as_the_vector_says(case: dict[str, Any]) -> None:
    now = datetime.fromisoformat(case["now"])
    catalog = _catalog(case.get("catalog", {}))
    priors = case.get("prior", {})
    by_for: dict[str, list[Entry]] = defaultdict(list)
    for entry in _entries(case["entries"]):
        by_for[entry.for_].append(entry)
    read = case["read"]
    assert not set(read) - {"for", "session"}
    bodies = {for_: fold(None, entries, now, catalog) for for_, entries in by_for.items()}
    body = bodies.get(read["for"])
    others = any(for_ != "self" for for_ in bodies)
    block = build(
        body,
        for_=read["for"],
        session=read["session"],
        now=now,
        catalog=catalog,
        prior=lambda attr, value: float(priors.get(f"{attr}|{value}", 0.0)),
        others=others,
    )
    ConstraintsBlock.model_validate({**block, "text": ""})
    expect = case["expect"]
    values = {f"{v.attr}|{v.value}": v for v in (body.values.values() if body else ())}
    for key, want in expect.get("values", {}).items():
        assert key in values, f"{key} has no accumulators"
        for name, number in want.items():
            assert _close(getattr(values[key], name), number), (key, name, getattr(values[key], name))
    for key in expect.get("no_values", ()):
        assert key not in values, key
    for name in ("hard", "soft", "attributes", "conflicts"):
        if name in expect:
            got = block.get(name, [])
            assert len(got) == len(expect[name]), (name, got)
            for found, want in zip(got, expect[name], strict=True):
                assert _matches(found, want), (name, found, want)
    for name in ("ask", "exclude", "relaxation_order"):
        if name in expect:
            assert block.get(name, []) == expect[name], (name, block.get(name))
    if "already_presented" in expect:
        assert [p["ref"] for p in block.get("already_presented", [])] == sorted(
            expect["already_presented"], reverse=True
        )
    if "for" in expect:
        assert block["subject"]["for"] == expect["for"]


def test_the_same_content_has_the_same_version_and_other_content_another() -> None:
    now = datetime.fromisoformat(DOCUMENT["cases"][0]["now"])
    said = Entry("e1", now, {"kind": "preference", "attr": "item_variant.color", "op": "not_in",
                             "values": ["preto"], "strength": "must", "scope": "persistent"})  # fmt: skip
    first = build(fold(None, [said], now, Catalog()), for_="self", session=None, now=now, catalog=Catalog())
    again = build(fold(None, [said], now, Catalog()), for_="self", session=None, now=now, catalog=Catalog())
    other = build(None, for_="self", session=None, now=now, catalog=Catalog())
    assert first["version"] == again["version"] != other["version"]
    assert first["version"].startswith("cv_")
