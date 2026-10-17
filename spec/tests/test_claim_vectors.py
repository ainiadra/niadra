"""The vectors of the claim contract and of the constraints block are well formed: the shared layout, cases
whose spans point at the text they name, values in the shapes the specs define, contracts and blocks that
validate against their schemas, and example contracts that carry their negative corpus. Whether a checker
gets the expected results is for the implementations to prove, running the same files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
VECTORS = {
    "claim-parser": ("spec/claim-contract.md", {"lang", "text", "roles", "evidence"}),
    "claim-detect": ("spec/claim-contract.md", {"contract", "output", "turn"}),
    "claim-evidence": ("spec/claim-contract.md", {"contract", "output", "blocks"}),
    "claim-anchor": ("spec/claim-contract.md", {"quote", "document"}),
    "constraint-render": ("spec/constraints.md", {"block", "binding", "families", "call", "mode", "results"}),
}
CLASSES = {"money", "percent", "date", "duration", "quantity", "count", "dosage", "label"}
VERDICTS = {
    "matched", "mismatch", "stale", "gap_not_stated", "role_ambiguous", "unsupported", "no_evidence",
    "quoted_found", "quoted_missing", "anchored", "below_threshold", "source_exists_claim_unverified",
    "source_missing", "not_checked",
}  # fmt: skip
ACTIONS = {"none", "block", "warn", "count", "rewrite", "discard_anchor"}
DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{0,5}[1-9])?$")
DATE = re.compile(r"^(?:[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?|--[0-9]{2}-[0-9]{2}|---[0-9]{2})$")


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def cases(name: str) -> list[dict[str, Any]]:
    return list(load(ROOT / "vectors" / f"{name}.v0.json")["cases"])


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(load(ROOT / "schemas" / f"{name}.v0.json"), format_checker=FormatChecker())


def assert_value(value: dict[str, Any]) -> None:
    """A normalized value (claim contract, section 5.3): one shape, decimals without trailing zeros."""
    shapes = [
        set(value) - {"unit"} == s for s in ({"amount"}, {"min", "max"}, {"date"}, {"date_from", "date_to"})
    ]
    assert sum(shapes) == 1, value
    assert "unit" not in value or ("date" not in value and "date_from" not in value)
    for key in ("amount", "min", "max"):
        if key in value:
            assert DECIMAL.match(value[key]), value
    for key in ("date", "date_from", "date_to"):
        if key in value:
            assert DATE.match(value[key]), value


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_each_file_follows_the_shared_layout(name: str) -> None:
    document = load(ROOT / "vectors" / f"{name}.v0.json")
    spec, fields = VECTORS[name]
    assert set(document) == {"vectors", "version", "spec", "cases"}
    assert (document["vectors"], document["version"], document["spec"]) == (name, "v0", spec)
    assert f"vectors/{name}.v0.json" in (ROOT / spec).read_text(), "the spec links its vectors"
    ids = [case["id"] for case in document["cases"]]
    assert len(set(ids)) == len(ids)
    for case in document["cases"]:
        assert re.fullmatch(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+", case["id"]), case["id"]
        assert set(case) - {"id", "expect"} <= fields, case["id"]
        assert "expect" in case, case["id"]


def test_parser_mentions_point_at_their_text() -> None:
    for case in cases("claim-parser"):
        text, mentions = case["text"], case["expect"]["mentions"]
        assert case["lang"] in ("pt", "en", "es")
        assert case["id"].startswith(case["lang"] + ".")
        ends = 0
        for m in mentions:
            start, end = m["span"]
            assert ends <= start < end <= len(text), case["id"]
            assert text[start:end] == m["text"], case["id"]
            assert m["class"] in CLASSES
            ends = end
            if m["class"] == "label":
                assert set(m) == {"span", "text", "class"}, case["id"]
                continue
            assert_value(m["value"])
            assert m["written"] in ("digits", "words")
            assert ("role" in m) == ("roles" in case) == ("role_status" in m), case["id"]
            assert ("nature" in m) == ("evidence" in case), case["id"]
            if "role_status" in m:
                assert m["role_status"] in ("matched", "ambiguous", "none")
                assert (m["role"] is not None) == (m["role_status"] == "matched")
            if "nature" in m:
                assert m["nature"] in ("computed", "quoted", "model")
        for item in case.get("evidence", ()):
            assert set(item) <= {"class", "role", "value"}
            assert_value(item["value"])


def test_the_parser_vectors_cover_every_class_range_and_words_in_each_language() -> None:
    seen: dict[str, set[str]] = {"pt": set(), "en": set(), "es": set()}
    for case in cases("claim-parser"):
        for m in case["expect"]["mentions"]:
            seen[case["lang"]].add(m["class"])
            if "min" in m.get("value", {}) or "date_from" in m.get("value", {}):
                seen[case["lang"]].add("range")
            if m.get("written") == "words":
                seen[case["lang"]].add("words")
    assert seen["pt"] >= CLASSES | {"range", "words"}
    for lang in ("en", "es"):
        assert seen[lang] >= {"money", "percent", "date", "duration", "label", "range", "words"}, lang


def test_detection_cases_use_example_contracts_and_point_at_their_text() -> None:
    contracts = schema("claim-contract")
    for case in cases("claim-detect"):
        contract = ROOT / case["contract"]
        assert contract.parent == ROOT / "examples" / "claim-contract"
        assert contracts.is_valid(load(contract)), case["id"]
        output, turn = case["output"], case["turn"]
        assert set(output) <= {"text", "lang", "context", "immutable", "agent"}
        assert set(turn) <= {"values", "tools", "documents", "anchors", "sections"}
        for value in turn.get("values", ()):
            assert_value(value["value"])
        for finding in case["expect"]["findings"]:
            start, end = finding["span"]
            assert output["text"][start:end] == finding["text"], case["id"]
            assert finding["verdict"] in VERDICTS
            assert finding["action"] in ACTIONS
            if "class" in finding:
                assert_value(finding["value"])
            evidence = finding.get("evidence")
            if evidence is not None:
                ((kind, index),) = evidence.items()
                assert 0 <= index < len(turn[{"value": "values", "anchor": "anchors"}[kind]])
            if finding["action"] == "rewrite":
                assert not output["immutable"], case["id"]
                assert finding["verdict"] == "stale", case["id"]


CHECKER = FormatChecker()


def ref_of(ref: dict[str, str]) -> str:
    return f"{ref['type']}:{ref['namespace']}:{ref['id']}"


def test_evidence_cases_carry_blocks_a_read_serves_and_cite_what_they_served() -> None:
    pack = load(ROOT / "schemas" / "context-pack.v1.json")
    models = {"state": "StateView", "constraints": "ConstraintsBlock"}
    blocks = {
        name: Draft202012Validator({"$defs": pack["$defs"], "$ref": f"#/$defs/{m}"}, format_checker=CHECKER)
        for name, m in models.items()
    }
    for case in cases("claim-evidence"):
        contract = ROOT / case["contract"]
        assert contract.parent == ROOT / "examples" / "claim-contract"
        assert set(case["output"]) <= {"text", "lang", "context", "immutable", "agent"}
        assert case["blocks"], case["id"]
        assert set(case["blocks"]) <= set(blocks), case["id"]
        served = set()
        for name, block in case["blocks"].items():
            errors = [e.message for e in blocks[name].iter_errors(block)]
            assert not errors, (case["id"], name, errors)
        state, constraints = case["blocks"].get("state", {}), case["blocks"].get("constraints", {})
        interests = [i["object"] for i in state.get("interests", ()) if "object" in i]
        for item in [*state.get("objects", ()), *interests]:
            names = [*item.get("fields", {}), *item.get("values", {})]
            served |= {(ref_of(item["ref"]), name) for name in names}
        for change in state.get("changes_since_seen", ()):
            served.add((ref_of(change["ref"]), change["field"]))
        for key in ("hard", "soft"):
            served |= {(None, entry["attr"].rsplit(".", 1)[-1]) for entry in constraints.get(key, ())}
        served |= {(None, entry["name"]) for entry in constraints.get("attributes", ())}
        for shown in constraints.get("already_presented", ()):
            served |= {(shown["ref"], name) for name in shown.get("values", {})}
        for finding in case["expect"]["findings"]:
            start, end = finding["span"]
            assert case["output"]["text"][start:end] == finding["text"], case["id"]
            assert finding["verdict"] in VERDICTS
            assert finding["action"] in ACTIONS
            if "class" in finding:
                assert_value(finding["value"])
            if "evidence" in finding:
                assert set(finding["evidence"]) <= {"ref", "field"}, case["id"]
                assert (finding["evidence"].get("ref"), finding["evidence"]["field"]) in served, case["id"]


def test_anchor_cases_hold_at_nine_tenths() -> None:
    for case in cases("claim-anchor"):
        expect = case["expect"]
        assert set(expect) == {"normalized_quote_length", "distance", "holds"}
        length, distance = expect["normalized_quote_length"], expect["distance"]
        assert 0 <= distance <= length
        assert expect["holds"] == (length > 0 and 1 - distance / length >= 0.90), case["id"]


def test_render_cases_carry_valid_blocks() -> None:
    blocks = schema("constraints")
    for case in cases("constraint-render"):
        assert blocks.is_valid(case["block"]), case["id"]
        assert case["mode"] in ("advisory", "apply")
        assert set(case["call"]) <= {"args", "for", "category", "asked"}
        keys = {
            "applies",
            "args",
            "suggested",
            "injected",
            "hard_sent",
            "residual",
            "post_filter",
            "conflicts",
        }
        assert set(case["expect"]) == keys | ({"honored"} if "results" in case else set()), case["id"]
        assert set(case["expect"]["post_filter"]) <= set(case["expect"]["residual"])
        if case["mode"] == "advisory":
            assert case["expect"]["args"] == case["call"]["args"], "advisory mode changes nothing"


@pytest.mark.parametrize(
    "path", sorted((ROOT / "examples" / "claim-contract").glob("*.json")), ids=lambda p: p.stem
)
def test_each_example_contract_carries_its_initial_negative_corpus(path: Path) -> None:
    corpus = load(path)["negative_corpus"]
    assert corpus["version"] == "2026-09-29"
    assert len(corpus["phrases"]) >= 20
    assert len(set(corpus["phrases"])) == len(corpus["phrases"])
