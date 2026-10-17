"""Every example must validate against its schema, and every invalid example must not.

Invalid examples are named after the schema they break (`event-`, `verify-`, `pack-`,
`upload-request-`...) and each breaks exactly one rule of the prose spec.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
# Each schema and the prose spec that defines it; one spec may define several schemas.
SPECS = {
    "event-envelope": "event-envelope",
    "context-pack": "context-pack",
    "verification-assertion": "verification-assertion",
    "media-upload-request": "media-upload",
    "media-upload-response": "media-upload",
    "feedback": "feedback",
    "object-state": "object-state",
    "object-timeline": "business-object",
    "turn-record": "turn-record",
    "object-type": "object-type",
    "interaction": "interaction",
    "constraints": "constraints",
    "claim-contract": "claim-contract",
    "coordination-decision": "coordination",
    "handoff-package": "coordination",
    "contact-token": "contact-token",
    "suppression-list": "suppression-list",
    "agent-state": "agent-state",
    "replay-case": "replay",
    "replay-results": "replay",
    "scenario-run": "replay",
    "counterfactual-results": "counterfactual",
    "counterfactual-run": "counterfactual",
}
# One version per document: the Context Pack and the Object State are at v1, the others at v0.
VERSIONS = {"context-pack": "v1", "object-state": "v1"}
SCHEMAS = {
    f"{name}.{VERSIONS.get(name, 'v0')}": ROOT / "schemas" / f"{name}.{VERSIONS.get(name, 'v0')}.json"
    for name in SPECS
}
# The longest prefix that matches names the schema an invalid example breaks.
INVALID_PREFIX = {
    "event-": "event-envelope.v0",
    "pack-": "context-pack.v1",
    "verify-": "verification-assertion.v0",
    "upload-request-": "media-upload-request.v0",
    "upload-response-": "media-upload-response.v0",
    "feedback-": "feedback.v0",
    "object-state-": "object-state.v1",
    "object-type-": "object-type.v0",
    "object-timeline-": "object-timeline.v0",
    "turn-": "turn-record.v0",
    "interaction-": "interaction.v0",
    "constraints-": "constraints.v0",
    "claim-contract-": "claim-contract.v0",
    "decision-": "coordination-decision.v0",
    "handoff-": "handoff-package.v0",
    "contact-token-": "contact-token.v0",
    "suppression-": "suppression-list.v0",
    "agent-state-": "agent-state.v0",
    "replay-case-": "replay-case.v0",
    "replay-results-": "replay-results.v0",
    "scenario-run-": "scenario-run.v0",
    "counterfactual-results-": "counterfactual-results.v0",
    "counterfactual-run-": "counterfactual-run.v0",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load(SCHEMAS[name]), format_checker=FormatChecker())


def examples_of(name: str) -> Path:
    """`examples/context-pack/` holds the examples of `context-pack.v1`, and so on."""
    return ROOT / "examples" / name.rpartition(".")[0]


def valid_examples() -> list[tuple[str, Path]]:
    return [(name, path) for name in SCHEMAS for path in sorted(examples_of(name).glob("*.json"))]


def schema_of_invalid(path: Path) -> str:
    matches = [prefix for prefix in INVALID_PREFIX if path.name.startswith(prefix)]
    assert matches, f"{path.name} must start with a known prefix"
    return INVALID_PREFIX[max(matches, key=len)]


def invalid_examples() -> list[tuple[str, Path]]:
    paths = sorted((ROOT / "examples" / "invalid").glob("*.json"))
    return [(schema_of_invalid(path), path) for path in paths]


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schemas_are_well_formed_2020_12(name: str) -> None:
    schema = load(SCHEMAS[name])
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"https://specs.niadra.com/schemas/{name}.json"
    spec = ROOT / "spec" / f"{SPECS[name.rpartition('.')[0]]}.md"
    assert spec.exists(), "every schema has a prose spec"
    assert f"schemas/{name}.json" in spec.read_text(), "the spec links the schema it defines"


@pytest.mark.parametrize(("name", "path"), valid_examples(), ids=lambda v: getattr(v, "name", v))
def test_examples_conform(name: str, path: Path) -> None:
    errors = [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator(name).iter_errors(load(path))
    ]
    assert not errors


@pytest.mark.parametrize(("name", "path"), invalid_examples(), ids=lambda v: getattr(v, "name", v))
def test_invalid_examples_are_rejected(name: str, path: Path) -> None:
    assert not validator(name).is_valid(load(path))


def test_every_schema_has_examples_both_ways() -> None:
    assert {name for name, _ in valid_examples()} == set(SCHEMAS)
    assert {name for name, _ in invalid_examples()} == set(SCHEMAS)


# How the specs are built and planned is not part of them: no build front (A1 to A5), phase, wave or design
# study is named in a spec or in a schema's text.
PLANNING = re.compile(r"\bA[1-5]\b|(?i:\bfronts\b|\bphases?\s+\d|\bwave\b|\bstudy\s+\d|\bestud(?:o|os)\b)")


def schema_texts(node: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("title", "description") and isinstance(value, str):
                texts.append(value)
            texts += schema_texts(value)
    elif isinstance(node, list):
        for value in node:
            texts += schema_texts(value)
    return texts


def test_no_spec_or_schema_names_the_internal_planning() -> None:
    for path in sorted((ROOT / "spec").glob("*.md")):
        lines = [line for line in path.read_text().splitlines() if PLANNING.search(line)]
        assert lines == [], f"{path.name}: {lines}"
    for path in sorted((ROOT / "schemas").glob("*.json")):
        texts = [text for text in schema_texts(load(path)) if PLANNING.search(text)]
        assert texts == [], f"{path.name}: {texts}"
    assert PLANNING.search("its fields are the interaction spec's (front A5)")
    assert PLANNING.search("what a space turns on of the agent core wave")
    assert not PLANNING.search("a producer that puts a context pack in front of a model")


def test_the_example_pack_follows_the_text_layout() -> None:
    pack = load(ROOT / "examples" / "context-pack" / "chat.json")
    lines = pack["text"].split("\n")
    # Context Pack 4.2: the tag carries at most the year the dates below leave out.
    assert lines[0] in ("<niadra>", '<niadra year="2026">')
    assert "not instructions" in lines[1]
    assert lines[-1] == "</niadra>"
    assert all(line.startswith("[") for line in lines[2:-1])
    for offset in pack["cache"]["breakpoints"]:
        assert 0 < offset <= len(pack["text"])


def test_the_voice_example_stamps_the_context_it_carried() -> None:
    turn = load(ROOT / "examples" / "event-envelope" / "voice-turn.json")
    pack = load(ROOT / "examples" / "context-pack" / "chat.json")
    assert turn["speaker"]["role"] == "ai_agent"
    assert turn["context_stamp"]["etag"] == pack["etag"]
    assert turn["context_stamp"]["injected_at"] < turn["occurred_at"]
    assert "fields" not in turn, "measurement stamps travel in context_stamp, not in fields"


def test_the_media_examples_describe_one_upload() -> None:
    reserved = load(ROOT / "examples" / "media-upload-request" / "call-recording.json")
    response = load(ROOT / "examples" / "media-upload-response" / "reserved.json")
    turn = load(ROOT / "examples" / "event-envelope" / "voice-turn.json")
    assert turn["content"]["media_ref"] == response["media_ref"]
    assert turn["content"]["media_sha256"] == reserved["sha256"]
    assert response["upload_url"].startswith("https://")


def test_the_pack_as_data_holds_the_same_lines_as_the_text() -> None:
    # Context Pack 4.7: the preamble and every section's lines, in order and with the label put back, are
    # the text's lines.
    answer = load(ROOT / "examples" / "context-pack" / "turn-as-data.json")
    pack = answer["pack"]
    inner = answer["text"].split("\n")[1:-1]
    labeled = [f"[{s['label']}] {line}" for s in pack["sections"] for line in s["lines"]]
    assert [pack["preamble"], *labeled] == inner
    layers = [s["layer"] for s in pack["sections"]]
    assert layers == sorted(layers, key=["account", "stable", "volatile"].index)
    assert all(s["lines"] for s in pack["sections"])
    assert pack["stamp"]["etag"] == answer["etag"]


def test_the_slots_as_data_hold_the_same_lines_as_the_slots_block() -> None:
    # Context Pack 4.8: between the bare tags, the block's lines are the slots' texts, in order.
    for name in ("turn-as-data.json", "turn-count-as-data.json"):
        answer = load(ROOT / "examples" / "context-pack" / name)
        pack = answer["pack"]
        assert pack["spec"] == "context-pack.v1"
        block = answer["slots"].split("\n")
        assert block[0] == "<turn>"
        assert block[-1] == "</turn>"
        assert [slot["text"] for slot in pack["slots"]] == block[1:-1]
        assert all(slot["derived"] for slot in pack["slots"] if slot["section"] == "derived")
        assert all(slot["derived"] is None for slot in pack["slots"] if slot["section"] != "derived")
        assert answer["slots"] not in answer["text"], "the slots are never part of the pinned text"
    # A turn that asks how many times gets a derived count line.
    count = load(ROOT / "examples" / "context-pack" / "turn-count-as-data.json")
    derived = [slot for slot in count["pack"]["slots"] if slot["section"] == "derived"]
    assert derived
    assert all(slot["derived"] == "count" and slot["why"]["rule"].startswith("count_") for slot in derived)


def test_a_guard_line_is_typed_beside_the_slots_and_named_by_its_short_id() -> None:
    # Context Pack 4.9: each guard of `guards` is one line of `slots`, section `guard`, with its id.
    answer = load(ROOT / "examples" / "context-pack" / "turn-as-data.json")
    lines = {slot["id"]: slot for slot in answer["pack"]["slots"] if slot["section"] == "guard"}
    assert lines
    for guard in answer["guards"]:
        line = lines[guard["id"]]
        assert guard["value"] in line["text"]
        assert line["derived"] is None
        assert line["why"]["rule"] == f"guard_{guard['value_type']}"
        assert len(guard["id"]) == 8
    block = answer["slots"].split("\n")
    assert block[1] == next(iter(lines.values()))["text"], "guard lines come first"


def test_the_backing_report_names_kinds_and_guards_never_values() -> None:
    # Event Envelope 10: kinds, counts and short ids only.
    turn = load(ROOT / "examples" / "event-envelope" / "agent-turn-backing.json")
    backing = turn["backing"]
    assert backing["checked"] >= len(backing["unbacked_values"])
    assert all(set(v) == {"kind"} for v in backing["unbacked_values"])
    assert turn["speaker"]["role"] in ("ai_agent", "human_agent")


def es_number(x: float) -> str:
    """A number as ECMAScript writes it, which RFC 8785 requires: the shortest digits that read back the
    same double, in exponent form from 1e21 up and below 1e-6."""
    if x == 0:
        return "0"
    if x < 0:
        return "-" + es_number(-x)
    _, raw, exp = Decimal(repr(float(x))).as_tuple()
    digits = [str(d) for d in raw]
    while len(digits) > 1 and digits[-1] == "0":
        digits.pop()
        exp += 1
    s, k = "".join(digits), len(digits)
    n = int(exp) + k
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + s
    tail = f"e{'+' if n - 1 >= 0 else '-'}{abs(n - 1)}"
    return s + tail if k == 1 else f"{s[0]}.{s[1:]}{tail}"


def canonical(value: Any) -> str:
    """RFC 8785, the JSON Canonicalization Scheme: keys sorted by UTF-16 code units, no whitespace."""
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
    members = (f"{json.dumps(key, ensure_ascii=False)}:{canonical(value[key])}" for key in keys)
    return "{" + ",".join(members) + "}"


def digest(value: Any) -> tuple[str, int]:
    body = canonical(value).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest(), len(body)


def blob_keys(record: dict[str, Any]) -> set[str]:
    keys = {read.get("blob") for read in record.get("reads", [])}
    for call in record.get("calls", []):
        keys |= {call.get("args"), call.get("result_model"), call.get("result_ui")}
    keys |= {(claim.get("evidence") or {}).get("anchor") for claim in record.get("claims", [])}
    output = record.get("output") or {}
    keys |= {output.get("ui"), output.get("document")}
    return {key for key in keys if key is not None}


@pytest.mark.parametrize(
    "path", sorted((ROOT / "examples" / "turn-record").glob("*.json")), ids=lambda p: p.name
)
def test_a_turn_record_keeps_the_rules_the_schema_cannot_state(path: Path) -> None:
    # Turn Record 5.5: every blob key used exists, no call repeats, a turn never ends before it starts.
    record = load(path)
    assert blob_keys(record) <= set(record.get("blobs", {}))
    call_ids = [call["call_id"] for call in record.get("calls", [])]
    assert len(call_ids) == len(set(call_ids))
    if record.get("ended_at"):
        assert record["ended_at"] >= record["started_at"]
    # Turn Record 6.2: a stored blob's digest is over its canonical JSON, so the same value has one hash.
    for blob in record.get("blobs", {}).values():
        if "content" in blob:
            sha256, size = digest(blob["content"])
            assert blob["sha256"] == sha256
            assert blob.get("size", size) == size


def test_the_turn_record_examples_cover_every_content_mode_and_fidelity() -> None:
    records = [load(path) for path in sorted((ROOT / "examples" / "turn-record").glob("*.json"))]
    assert {r["content_mode"] for r in records} == {"stored", "pointer", "hash_only"}
    assert {r.get("fidelity", "gold") for r in records} == {"bronze", "gold"}
    pointer = [r for r in records if r["content_mode"] == "pointer"]
    assert all("content" not in blob for r in pointer for blob in r["blobs"].values())


def test_a_bronze_record_is_named_by_its_trace_as_the_conventions_say() -> None:
    # OpenTelemetry conventions 5.1: without `niadra.turn.id`, a trace is one turn named `otel-<trace id>`.
    conventions = (ROOT / "spec" / "otel-conventions.md").read_text()
    assert "`otel-<trace id>`" in conventions
    bronze = load(ROOT / "examples" / "turn-record" / "bronze-from-otel.json")
    assert bronze["fidelity"] == "bronze"
    assert bronze["completeness"] == "unknown"
    assert bronze["content_mode"] == "hash_only"
    prefix, _, trace = bronze["turn_id"].partition("-")
    assert prefix == "otel"
    assert len(trace) == 32
    assert int(trace, 16) >= 0


DIGEST_VECTORS = ROOT / "vectors" / "turn-record-digest.v0.json"


def test_the_digest_vectors_follow_the_shared_layout() -> None:
    vectors = load(DIGEST_VECTORS)
    assert set(vectors) == {"vectors", "version", "spec", "cases"}
    assert (vectors["vectors"], vectors["version"]) == ("turn-record-digest", "v0")
    assert (ROOT / vectors["spec"]).exists()
    ids = [case["id"] for case in vectors["cases"]]
    assert len(ids) == len(set(ids))
    assert all(set(case) == {"id", "note", "value", "expect"} for case in vectors["cases"])


@pytest.mark.parametrize("case", load(DIGEST_VECTORS)["cases"], ids=lambda case: case["id"])
def test_a_value_has_one_canonical_form_and_one_digest(case: dict[str, Any]) -> None:
    # Turn Record 6.2.1.
    assert canonical(case["value"]) == case["expect"]["canonical"]
    assert digest(case["value"]) == (case["expect"]["sha256"], case["expect"]["size"])


# The object type and niadra-expr (spec/object-type.md), and the object state v1 (spec/object-state.md).

EXPR_VECTORS = ROOT / "vectors" / "niadra-expr.v0.json"
EXPR_INPUT = {
    "now",
    "utc_offset",
    "fields",
    "inputs",
    "absent_names",
    "config",
    "quotes",
    "calendars",
    "state",
    "derived_status",
    "watch_count",
    "purpose",
    "presented_rank",
}
EXPR_SLOT = {"type", "v", "logic", "absent", "at", "observer", "was", "completeness"}
EXPR_LOGIC = {"yes", "no", "unobserved", "known_defect"}
EXPR_KINDS = {"bool", "number", "string", "duration", "date", "datetime", "list"}
EXPR_FUNCTIONS = {
    "now",
    "age",
    "observer",
    "changed",
    "was",
    "count",
    "business_days",
    "quote",
    "presented_in_top",
    "sha256",
}
EXPR_OPERATORS = {"==", "!=", "<", "<=", ">", ">=", "+", "-", "in", "and", "or", "not", "."}
_STRING = re.compile(r"'(?:\\.|[^'\\])*'")
_TOKEN = re.compile(r"==|!=|<=|>=|[<>+\-.]|[a-z_][a-z0-9_]*|[0-9]+(?:ms|s|min|h|d)")


def expr_tokens(text: str) -> list[str]:
    return _TOKEN.findall(_STRING.sub("''", text))


def expr_functions(text: str) -> set[str]:
    named = set(re.findall(r"([a-z_][a-z0-9_]*)\s*\(", _STRING.sub("''", text)))
    return named - {"and", "or", "not", "in"}


def result_shape_is_valid(result: dict[str, Any]) -> bool:
    if result.get("logic") not in EXPR_LOGIC or not set(result) <= {"logic", "type", "v", "absent"}:
        return False
    if "type" not in result:
        return "v" not in result and ("absent" not in result or result["logic"] == "no")
    if result["type"] not in EXPR_KINDS or "v" not in result or "absent" in result:
        return False
    if result["type"] == "bool":
        return result["v"] is (result["logic"] == "yes")
    if result["type"] == "list":
        return result["logic"] == "yes" and all(result_shape_is_valid(item) for item in result["v"])
    return result["logic"] == "yes"


def test_the_expression_vectors_follow_the_shared_layout() -> None:
    vectors = load(EXPR_VECTORS)
    assert set(vectors) == {"vectors", "version", "spec", "cases"}
    assert (vectors["vectors"], vectors["version"]) == ("niadra-expr", "v0")
    assert vectors["spec"] == "spec/object-type.md"
    ids = [case["id"] for case in vectors["cases"]]
    assert len(ids) == len(set(ids))
    for case in vectors["cases"]:
        assert set(case) == {"id", "expr", "input", "expect"}, case["id"]
        assert "now" in case["input"], case["id"]
        assert set(case["input"]) <= EXPR_INPUT, case["id"]
        slots = [*case["input"].get("fields", {}).values(), *case["input"].get("inputs", {}).values()]
        slots += [slot for quote in case["input"].get("quotes", {}).values() for slot in quote.values()]
        assert all(set(slot) <= EXPR_SLOT for slot in slots), case["id"]
        expect = case["expect"]
        assert set(expect) in ({"value"}, {"error"}), case["id"]
        if "error" in expect:
            assert expect["error"] in {"expr_invalid", "expr_type", "expr_limit"}, case["id"]
        else:
            assert result_shape_is_valid(expect["value"]), case["id"]


def test_the_expression_vectors_cover_the_language() -> None:
    cases = load(EXPR_VECTORS)["cases"]
    valued = [case for case in cases if "value" in case["expect"]]
    used = {token for case in valued for token in expr_tokens(case["expr"])}
    called = {name for case in valued for name in expr_functions(case["expr"])}
    assert called == EXPR_FUNCTIONS
    assert used >= EXPR_OPERATORS
    assert used >= EXPR_LOGIC | {"true", "false", "none"}
    units = {re.sub(r"[0-9]", "", token) for token in used if token[0].isdigit()}
    assert units == {"ms", "s", "min", "h", "d"}
    assert {case["expect"]["value"]["logic"] for case in valued} == EXPR_LOGIC
    assert {case["expect"]["value"].get("type") for case in valued} >= EXPR_KINDS
    assert any("absent" in case["expect"]["value"] for case in valued)
    assert {case["expect"]["error"] for case in cases if "error" in case["expect"]} == {
        "expr_invalid",
        "expr_type",
        "expr_limit",
    }
    groups = {case["id"].split(".")[0] for case in cases}
    assert {"logic", "missing", "absent", "time", "cmp", "arith", "fn", "decl"} <= groups


OBJECT_TYPES = {
    load(path)["type"]: load(path) for path in sorted((ROOT / "examples" / "object-type").glob("*.json"))
}


def type_expressions(declared: dict[str, Any]) -> list[str]:
    found = [declared.get("key", {}).get("fingerprint")]
    found += [source.get("active_when") for source in declared.get("sources", {}).values()]
    found += [t.get("when") for t in declared.get("lifecycle", {}).get("transitions", [])]
    found += [timer["due"] for timer in declared.get("timers", {}).values()]
    found += [reason["when"] for reason in declared.get("refetch", {}).get("reasons", [])]
    found += [reading.get("expr") for reading in declared.get("readings", {}).values()]
    found += [derived.get("where") for derived in declared.get("derived_fields", {}).values()]
    return [text for text in found if text]


def test_the_three_types_of_the_sectors_fit_the_format() -> None:
    # Object Type 7: a shared item variant, a derived quote with a known defect, and a mirrored court notice.
    variant = OBJECT_TYPES["item_variant"]
    assert variant["ownership"] == "shared"
    assert "working_set" in variant
    assert variant["sources"]["checkout_quote"]["kind"] == "quote"
    quote = OBJECT_TYPES["cotacao_plano_saude"]
    assert (quote["ownership"], quote["nature"]) == ("subject", "derived")
    assert quote["inputs"]
    assert quote["sources"]["quote_api"]["known_defects"] == [
        {"field": "enrollment_fee", "use_source": "budget_document"}
    ]
    notice = OBJECT_TYPES["intimacao_com_prazo"]
    assert notice["mirror_of"]["derived_by"] == "introspection"
    assert notice["mirror_of"]["fingerprint"]
    assert notice["time"]["timers_on"] == "published_on"
    assert len([axis for axis in notice["time"] if axis != "timers_on"]) == 5
    assert notice["values"]["due_date"]["declared_gaps"]


@pytest.mark.parametrize("name", sorted(OBJECT_TYPES))
def test_the_example_types_call_only_the_functions_of_the_language(name: str) -> None:
    texts = type_expressions(OBJECT_TYPES[name])
    assert all(expr_functions(text) <= EXPR_FUNCTIONS for text in texts)


def test_the_example_types_close_their_lifecycles() -> None:
    # Object Type 3.7.5 and 3.8.2: every state the lifecycle names is declared, and a timer's transition is
    # one the clock may make.
    for declared in OBJECT_TYPES.values():
        lifecycle, states = declared.get("lifecycle", {}), set(declared.get("states", []))
        named = {t["to"] for t in lifecycle.get("transitions", [])}
        named |= {t["from"] for t in lifecycle.get("transitions", []) if t["from"] != "*"}
        named |= set(lifecycle.get("latches", []))
        assert named <= states, declared["type"]
        for timer in declared.get("timers", {}).values():
            for action in timer["on_fire"]:
                if move := re.fullmatch(r"transition\((\w+)->(\w+)\)", action):
                    source, target = move.groups()
                    assert any(
                        t["to"] == target and t["from"] in (source, "*") and "clock" in t["by"]
                        for t in lifecycle.get("transitions", [])
                    ), action


STATES = sorted((ROOT / "examples" / "object-state").glob("*.json"))


@pytest.mark.parametrize("path", STATES, ids=lambda p: p.name)
def test_an_object_state_keeps_the_rules_the_schema_cannot_state(path: Path) -> None:
    state = load(path)
    declared = OBJECT_TYPES[state["ref"]["type"]]
    fields = declared.get("fields", {})
    claimable = {name for name, spec in fields.items() if spec.get("claim", {}).get("allowed")}
    for name, field in state.get("fields", {}).items():
        if field["claim_safe"]:
            # Object State 4.4: only a fresh, known value of a claimable field may be claimed.
            assert name in claimable, name
            assert field["status"] == "fresh", name
            assert field["logic"] in ("yes", "no"), name
        if field.get("masked"):
            assert "v" not in field, name
    prohibitions = declared.get("purposes", {}).get("claim", {}).get("prohibitions", [])
    unsafe = [n for n, f in state.get("fields", {}).items() if n in claimable and not f["claim_safe"]]
    unsafe += [n for n, v in state.get("values", {}).items() if not v["claim_safe"]]
    assert state.get("prohibitions", []) == (prohibitions if unsafe else [])
    gaps = [gap for value in state.get("values", {}).values() for gap in value["declared_gaps"]]
    assert state.get("declared_gaps", []) == gaps
    if "refusal" in state:
        decide = declared["purposes"]["decide"]
        assert decide["on_stale"] == "structured_refusal"
        assert state["refusal"] == {"reason": decide["reason"], "action": decide["action"]}
    if state.get("derived_status") == "expired_by_input":
        assert set(state["expired_by"]) <= set(declared["inputs"])
        assert all(not f["claim_safe"] for f in state["fields"].values())
