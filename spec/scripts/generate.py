"""Generates the JSON Schemas of the open specs from the server's contract models.

Usage, from the server repository and with its environment:

    uv run python ../spec/scripts/generate.py .

The server's Pydantic models are the source of truth. Each schema is written as `<name>.<version>.json`, one
version per document: the Context Pack answer and the Object State are `v1` (the pack section's `spec` is the
server's `ContextPack.spec`), the others are `v0`. Pydantic cannot express the models' cross-field validators
in JSON Schema, so the rules they enforce are added here as `if`/`then`/`oneOf` clauses, one function per
validator, each naming the validator it mirrors.
The conformance tests check both directions with valid and invalid examples.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

DIALECT = "https://json-schema.org/draft/2020-12/schema"
BASE_ID = "https://specs.niadra.com/schemas/"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

Schema = dict[str, Any]

# The server's docstrings cite sections of its internal design document, as in "(5.11)"; the
# published schemas are read without it, so the citations are dropped.
_SECTION = re.compile(r"\s*\(\d+(?:\.\d+)*(?:,\s*(?:rule\s+)?\d+(?:\.\d+)*)*\)")


def _non_empty_string() -> Schema:
    return {"type": "string", "minLength": 1}


def kind_is(kind: str) -> Schema:
    return {"required": ["kind"], "properties": {"kind": {"const": kind}}}


def event_item_rules(schema: Schema) -> None:
    """Mirrors `EventItem._shape_matches_kind`."""
    schema["allOf"] = [
        {
            "if": kind_is("action"),
            "then": {"required": ["action"], "properties": {"action": {"type": "object"}}},
        },
        {"if": {"not": kind_is("action")}, "then": {"properties": {"action": {"type": "null"}}}},
        {
            "if": kind_is("system_event"),
            "then": {"required": ["canonical_type"], "properties": {"canonical_type": _non_empty_string()}},
        },
        {
            # `kind` defaults to `message`, so an event without `kind` is a message.
            "if": {"anyOf": [{"not": {"required": ["kind"]}}, kind_is("message")]},
            "then": {
                "required": ["content"],
                "properties": {
                    "content": {
                        "type": "object",
                        "anyOf": [
                            {"required": ["text"], "properties": {"text": _non_empty_string()}},
                            {"required": ["transcript"], "properties": {"transcript": _non_empty_string()}},
                            {"required": ["media_ref"], "properties": {"media_ref": _non_empty_string()}},
                        ],
                    }
                },
            },
        },
        {
            "anyOf": [
                {"required": [name], "properties": {name: {"type": "array", "minItems": 1}}}
                for name in ("handles", "subjects", "object_refs")
            ]
        },
        {
            # `backing` rides only on an agent's message: what the SDK's backing check found in it.
            "if": {"required": ["backing"], "properties": {"backing": {"type": "object"}}},
            "then": {
                "properties": {
                    "kind": {"const": "message"},
                    "speaker": {"properties": {"role": {"enum": ["ai_agent", "human_agent"]}}},
                }
            },
        },
    ]


def closes_rules(schema: Schema) -> None:
    """Mirrors `Closes._one_way`: either `item_id`, or `object` and `operation`, never both."""
    schema["oneOf"] = [
        {"required": ["item_id"], "properties": {"item_id": _non_empty_string()}},
        {
            "required": ["object", "operation"],
            "properties": {"object": {"type": "object"}, "operation": _non_empty_string()},
        },
    ]


def _absent(*names: str) -> Schema:
    """Each of `names` absent or null, as a field that defaults to `None` in the server's model."""
    return {"properties": {name: {"type": "null"} for name in names}}


def turn_record_rules(schema: Schema) -> None:
    """Mirrors `TurnRecord._consistent` where JSON Schema can: a turn belongs to a conversation or a task,
    and the content mode decides where a blob's value is (in `pointer` mode, never with Niadra). That every
    blob key the record uses exists, that no `call_id` repeats and that `ended_at` is not before
    `started_at` are rules of the prose the server enforces; a schema cannot state them."""
    blob_in_mode = {
        "stored": _absent("pointer"),
        "pointer": {
            "required": ["pointer"],
            "properties": {"pointer": {"type": "string"}, "content": {"type": "null"}},
        },
        "hash_only": _absent("pointer", "content"),
    }
    # Pydantic states the key pattern as `patternProperties`, which leaves other keys free; the server
    # refuses them, so the schema does too.
    blobs = schema["properties"]["blobs"]
    blobs["propertyNames"]["pattern"] = next(iter(blobs["patternProperties"]))
    schema["allOf"] = [
        {
            "anyOf": [
                {"required": [name], "properties": {name: _non_empty_string()}}
                for name in ("conversation_id", "task_id")
            ]
        },
        *(
            {
                "if": {"required": ["content_mode"], "properties": {"content_mode": {"const": mode}}},
                "then": {"properties": {"blobs": {"additionalProperties": rule}}},
            }
            for mode, rule in blob_in_mode.items()
        ),
    ]


def turn_call_rules(schema: Schema) -> None:
    """Mirrors `TurnCall._tokens_on_model_calls`: token counts belong to a model call."""
    schema["allOf"] = [
        {
            "if": {"required": ["tokens"], "properties": {"tokens": {"type": "object"}}},
            "then": {"properties": {"kind": {"const": "model"}}},
        }
    ]


MEASURES = ("k", "overlap", "noise", "base_count", "variant_count")


def counterfactual_case_rules(schema: Schema) -> None:
    """Mirrors `CounterfactualCase._measured_when_completed`: a completed case carries every measure, and any
    other case none, nor engaged items."""
    schema["if"] = {"properties": {"status": {"const": "completed"}}}
    schema["then"] = {
        "required": list(MEASURES),
        "properties": {
            name: {"type": "number" if name in ("overlap", "noise") else "integer"} for name in MEASURES
        },
    }
    schema["else"] = {
        "properties": {**_absent(*MEASURES)["properties"], "engaged": {"maxItems": 0}},
    }


def object_type_rules(schema: Schema) -> None:
    """Mirrors the ownership rules of `ObjectType._consistent`: a subject's or a shared type mirrors a system
    of the company, a derived type names its inputs and only a derived one has them, a working set belongs to
    a shared type and an `agent_state` policy to an agent's. Expressions, names and the lifecycle are checked
    by the implementation (spec/object-type.md, section 4)."""
    schema["allOf"] = [
        {
            "if": {"properties": {"ownership": {"enum": ["subject", "shared"]}}},
            "then": {"required": ["mirror_of"], "properties": {"mirror_of": {"type": "object"}}},
        },
        {
            "if": {"required": ["nature"], "properties": {"nature": {"const": "derived"}}},
            "then": {"required": ["inputs"], "properties": {"inputs": {"minItems": 1}}},
            "else": {"properties": {"inputs": {"maxItems": 0}}},
        },
        {
            "if": {"required": ["working_set"], "properties": {"working_set": {"type": "object"}}},
            "then": {"properties": {"ownership": {"const": "shared"}}},
        },
        {
            "if": {"required": ["agent_state"], "properties": {"agent_state": {"type": "object"}}},
            "then": {"properties": {"ownership": {"const": "agent"}}},
        },
    ]


def _arity(values: str) -> Callable[[Schema], None]:
    """Mirrors `_arity` of the signals contract: one value for a comparison, two for `between`."""

    def apply(schema: Schema) -> None:
        comparisons = ["eq", "ne", "lt", "lte", "gt", "gte"]
        schema["allOf"] = [
            {
                "if": {"required": ["op"], "properties": {"op": {"enum": comparisons}}},
                "then": {"properties": {values: {"maxItems": 1}}},
            },
            {
                "if": {"required": ["op"], "properties": {"op": {"const": "between"}}},
                "then": {"properties": {values: {"minItems": 2, "maxItems": 2}}},
            },
        ]

    return apply


def attribute_entry_rules(schema: Schema) -> None:
    """Mirrors `AttributeEntry._only_said_applies_unasked`: only what the person said applies unasked."""
    schema["if"] = {"required": ["apply"], "properties": {"apply": {"const": "always"}}}
    schema["then"] = {"properties": {"source": {"enum": ["stated", "correction"]}}}


def detect_rules(schema: Schema) -> None:
    """Mirrors `Detect._detects_something`: numbers, terms, patterns or sections; roles qualify numbers."""
    schema["anyOf"] = [
        {"required": [name], "properties": {name: {"minItems": 1}}}
        for name in ("classes", "terms", "patterns", "document_sections")
    ]
    schema["if"] = {"required": ["roles"], "properties": {"roles": {"minProperties": 1}}}
    schema["then"] = {"required": ["classes"], "properties": {"classes": {"minItems": 1}}}


def evidence_rules(schema: Schema) -> None:
    """Mirrors `ClaimEvidence._one_kind`: exactly one of `value`, `tool`, `tool_any` or `anchor`."""
    schema["oneOf"] = [
        {"required": ["value"], "properties": {"value": {"type": "object"}}},
        {"required": ["tool"], "properties": {"tool": {"type": "string"}}},
        {"required": ["tool_any"], "properties": {"tool_any": {"minItems": 1}}},
        {"required": ["anchor"], "properties": {"anchor": {"type": "object"}}},
    ]


def _closed_keys(node: Any) -> Any:
    """A map whose keys follow a pattern refuses any other key, as the model does."""
    if isinstance(node, dict):
        closed = {key: _closed_keys(value) for key, value in node.items()}
        if "patternProperties" in closed and "additionalProperties" not in closed:
            closed["additionalProperties"] = False
        return closed
    if isinstance(node, list):
        return [_closed_keys(item) for item in node]
    return node


def decision_rules(schema: Schema) -> None:
    """Mirrors `CheckResult._shape_matches_decision`: a handoff names the holder, and only an allowed contact
    carries a token."""
    schema["allOf"] = [
        {
            "if": {"required": ["decision"], "properties": {"decision": {"const": "handoff_to"}}},
            "then": {"required": ["holder"], "properties": {"holder": _non_empty_string()}},
        },
        {
            "if": {"required": ["contact_token"], "properties": {"contact_token": {"type": "string"}}},
            "then": {"properties": {"decision": {"const": "allow"}}},
        },
    ]


def agent_state_write_rules(schema: Schema) -> None:
    """Mirrors `AgentStateWrite._mode` (the agent-state spec, section 4): a compare-and-swap names the version
    it replaces and removes nothing; a merge removes a key only with `{"$delete": true}`."""
    marker = {"type": "object", "required": ["$delete"]}
    schema["allOf"] = [
        {
            "if": {"required": ["mode"], "properties": {"mode": {"const": "cas"}}},
            "then": {
                "required": ["if_version"],
                "properties": {
                    "if_version": {"type": "integer"},
                    "body": {"additionalProperties": {"not": marker}},
                },
            },
        },
        {
            "if": {"required": ["mode"], "properties": {"mode": {"const": "merge_by_key"}}},
            "then": {
                "properties": {
                    "body": {"additionalProperties": {"if": marker, "then": {"const": {"$delete": True}}}}
                }
            },
        },
    ]


def _public(node: Any) -> Any:
    """Descriptions as a reader of the spec sees them: no internal citations, one line."""
    if isinstance(node, dict):
        return {
            key: " ".join(_SECTION.sub("", value).split())
            if key == "description" and isinstance(value, str)
            else _public(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_public(item) for item in node]
    return node


def _build(
    model: Any,
    name: str,
    title: str,
    description: str,
    rules: dict[str, Callable[[Schema], None]],
    version: str = "v0",
    *,
    closed_keys: bool = False,
) -> Schema:
    """`model` is a Pydantic model, or a `TypeAdapter` of a union whose members are the `$defs`."""
    if hasattr(model, "model_json_schema"):
        body, root = _public(model.model_json_schema(mode="validation")), model.__name__
    else:
        body, root = _public(model.json_schema(mode="validation")), None
    if closed_keys:
        body = _closed_keys(body)
    for target, apply in rules.items():
        apply(body if target == root else body["$defs"][target])
    schema: Schema = {
        "$schema": DIALECT,
        "$id": BASE_ID + f"{name}.{version}.json",
        "title": title,
        "description": description,
    }
    schema.update({k: v for k, v in body.items() if k not in {"title", "description"}})
    return schema


def generate(server: Path) -> dict[str, Schema]:
    sys.path.insert(0, str(server / "src"))
    from niadra.config.claims import ClaimContract
    from niadra.config.object_types import ObjectType
    from niadra.contracts.bindings import ToolBinding
    from niadra.contracts.context import ContextPack, ContextResponse
    from niadra.contracts.coordinate import CheckResult, ContactTokenClaims, HandoffPackage, SuppressionPage
    from niadra.contracts.events import EventItem, FeedbackRequest, MediaUploadResponse, VerifyItem
    from niadra.contracts.ingest import MediaUploadIn
    from niadra.contracts.objects import ObjectTimeline
    from niadra.contracts.signals import (
        ConstraintsBlock,
        CounterfactualRun,
        CounterfactualRunCreate,
        Interaction,
    )
    from niadra.contracts.state import AgentStateWrite, ObjectRead
    from niadra.contracts.turns import ReplayCase, ScenarioRun, ScenarioRunCreate, TurnRecord
    from pydantic import TypeAdapter

    assert str(ContextPack.model_fields["spec"].default) == "context-pack.v1", "the pack section is v1"
    return {
        "event-envelope": _build(
            EventItem,
            "event-envelope",
            "Niadra Event Envelope v0",
            "One thing that happened between a company and a subject: a message, a system event or an"
            " agent action. See spec/event-envelope.md.",
            {"EventItem": event_item_rules, "Closes": closes_rules},
        ),
        "context-pack": _build(
            ContextResponse,
            "context-pack",
            "Niadra Context Pack v1",
            "What an agent should know about one subject now: text for the model and metadata for the"
            " program. See spec/context-pack.md.",
            {},
            "v1",
        ),
        "verification-assertion": _build(
            VerifyItem,
            "verification-assertion",
            "Niadra Verification Assertion v0",
            "A statement that one conversation or task reached a verification level by a named method."
            " See spec/verification-assertion.md.",
            {},
        ),
        "media-upload-request": _build(
            MediaUploadIn,
            "media-upload-request",
            "Niadra Media Upload Request v0",
            "Reserves an upload for one file, which events then reference by `media_ref` and digest."
            " See spec/media-upload.md.",
            {},
        ),
        "media-upload-response": _build(
            MediaUploadResponse,
            "media-upload-response",
            "Niadra Media Upload Response v0",
            "The reference for a reserved file and the short-lived signed URL its bytes go to."
            " See spec/media-upload.md.",
            {},
        ),
        "feedback": _build(
            FeedbackRequest,
            "feedback",
            "Niadra Feedback v0",
            "A correction of what a memory derived about a subject, recorded as an event."
            " See spec/feedback.md.",
            {},
        ),
        "object-state": _build(
            ObjectRead,
            "object-state",
            "Niadra Object State v1",
            "One object as a state read serves it: fields with four logical values, their stamps and source,"
            " freshness computed at the read, computed values, lifecycle, timers, readings and what may not"
            " be claimed. See spec/object-state.md.",
            {},
            "v1",
        ),
        "object-type": _build(
            ObjectType,
            "object-type",
            "Niadra Object Type v0",
            "One object type of a company's type registry: fields, sources, freshness, lifecycle, timers,"
            " readings and relations, declared or derived from the company's schema."
            " See spec/object-type.md.",
            {"ObjectType": object_type_rules},
        ),
        "tool-binding": _build(
            ToolBinding,
            "tool-binding",
            "Niadra Tool Binding v0",
            "One tool bound to the fields of a space's object types: the arguments that carry them, where its"
            " results carry objects, and what the tool can do. See spec/object-type.md, section 9.",
            {},
            closed_keys=True,
        ),
        "object-timeline": _build(
            ObjectTimeline,
            "object-timeline",
            "Niadra Object Timeline v0",
            "System events and agent actions about one business object, newest first."
            " See spec/business-object.md.",
            {},
        ),
        "turn-record": _build(
            TurnRecord,
            "turn-record",
            "Niadra Turn Record v0",
            "What an AI agent read, called, showed, claimed and decided in one turn, with its build pinned."
            " See spec/turn-record.md.",
            {"TurnRecord": turn_record_rules, "TurnCall": turn_call_rules},
        ),
        "interaction": _build(
            TypeAdapter(Interaction),
            "interaction",
            "Niadra Interaction v0",
            "What a subject was shown, looked at, engaged with, said they want or refuse, or is: one"
            " interaction, by `kind`. See spec/interaction.md.",
            {"Preference": _arity("values")},
            closed_keys=True,
        ),
        "constraints": _build(
            ConstraintsBlock,
            "constraints",
            "Niadra Constraints Block v0",
            "What one subject wants, refuses and is, for the tools of one turn. See spec/constraints.md.",
            {"HardConstraint": _arity("values"), "AttributeEntry": attribute_entry_rules},
            closed_keys=True,
        ),
        "claim-contract": _build(
            ClaimContract,
            "claim-contract",
            "Niadra Claim Contract v0",
            "What an agent may state, and the evidence each claim needs in the turn record, checked without a"
            " model. See spec/claim-contract.md.",
            {"Detect": detect_rules, "ClaimEvidence": evidence_rules},
            closed_keys=True,
        ),
        "coordination-decision": _build(
            CheckResult,
            "coordination-decision",
            "Niadra Coordination Decision v0",
            "Whether an agent may act on a subject or an object now, who holds it, and why."
            " See spec/coordination.md.",
            {"CheckResult": decision_rules},
        ),
        "handoff-package": _build(
            HandoffPackage,
            "handoff-package",
            "Niadra Handoff Package v0",
            "What a handoff carries to whoever receives the subject. See spec/coordination.md.",
            {},
        ),
        "contact-token": _build(
            ContactTokenClaims,
            "contact-token",
            "Niadra Contact Token v0",
            "The payload of a contact token a gateway checks offline before an outbound message leaves."
            " See spec/contact-token.md.",
            {},
        ),
        "suppression-list": _build(
            SuppressionPage,
            "suppression-list",
            "Niadra Suppression List v0",
            "A page of changes to the list of subjects not to be contacted, keyed per reading source."
            " See spec/suppression-list.md.",
            {},
        ),
        "agent-state": _build(
            AgentStateWrite,
            "agent-state",
            "Niadra Agent State v0",
            "A write of an agent's working state: compare-and-swap or merge by key, under a declared schema"
            " and a size cap. See spec/agent-state.md.",
            {"AgentStateWrite": agent_state_write_rules},
        ),
        "replay-case": _build(
            ReplayCase,
            "replay-case",
            "Niadra Replay Case v0",
            "What a replay runner needs to run one recorded turn again inside the company's boundary: the"
            " record, its pins, the masked history and the assertions. See spec/replay.md.",
            {"TurnRecord": turn_record_rules, "TurnCall": turn_call_rules},
        ),
        "replay-results": _build(
            ScenarioRunCreate,
            "replay-results",
            "Niadra Replay Results v0",
            "What a replay runner reports after running scenarios N times: one entry per execution of a case,"
            " with its assertion outcomes. See spec/replay.md.",
            {},
        ),
        "counterfactual-results": _build(
            CounterfactualRunCreate,
            "counterfactual-results",
            "Niadra Counterfactual Results v0",
            "What a runner measured running recorded tool calls live with and without one element: overlaps"
            " and positions per case, never an item. See spec/counterfactual.md.",
            {"CounterfactualCase": counterfactual_case_rules},
        ),
        "counterfactual-run": _build(
            CounterfactualRun,
            "counterfactual-run",
            "Niadra Counterfactual Run v0",
            "Whether one element changes what a tool returns beyond the tool's own noise, and what holds the"
            " answer back. See spec/counterfactual.md.",
            {},
        ),
        "scenario-run": _build(
            ScenarioRun,
            "scenario-run",
            "Niadra Scenario Run v0",
            "The statistical verdict of a replay run, per scenario and per assertion, against its baseline."
            " See spec/replay.md.",
            {},
        ),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("server", type=Path, help="path to the niadra server repository")
    parser.add_argument("--out", type=Path, default=SCHEMAS, help="output directory (default: schemas/)")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for schema in generate(args.server.resolve()).values():
        path = args.out / str(schema["$id"]).removeprefix(BASE_ID)
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
