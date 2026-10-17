"""OpenTelemetry spans to turn records (3.7 and the OpenTelemetry conventions): bronze from `gen_ai` spans,
silver with an exposure, and the same trace in OTLP/JSON and in protobuf gives the same record."""

import json
from typing import Any

import orjson
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from niadra.adapters.inbound.http.otlp import JSON, PROTOBUF, otlp_document
from niadra.config.models import FeatureSettings
from niadra.contracts.turns import TurnRecord
from niadra.domain.ingest.otlp import otlp_turns
from tests.unit.turns.conftest import World

TRACE = "5b8efff798038103d269b633813fc60c"
TOOL_SPAN, MODEL_SPAN, ROOT_SPAN = "eee19b7ec3c1b174", "e1b49f1c85f4d0a2", "a0b1c2d3e4f50617"
EXPOSURE = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0e001"
START = 1_790_000_000_000_000_000
ITEMS = json.dumps([{"pos": 1, "ref": "health_quote:operator:q-77", "shown": {"price_full": 511.06}}])

RESOURCE = {"service.name": "closing-agent", "service.version": "3.2.0"}
TOOL = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "quote",
    "gen_ai.tool.call.id": "call_1",
    "gen_ai.tool.call.arguments": '{"plan": "family", "ages": [34, 36, 5]}',
    "gen_ai.tool.call.result": '{"quote_id": "q-77", "price_full": 511.06}',
    "gen_ai.conversation.id": "c-8812",
    "niadra.build.prompt.core": "v16",
    "niadra.pack.hash": "ph-1",
}
MODEL = {
    "gen_ai.operation.name": "chat",
    "gen_ai.response.model": "model-2026-08",
    "gen_ai.usage.input_tokens": 2953,
    "gen_ai.usage.output_tokens": 138,
    "gen_ai.conversation.id": "c-8812",
    "niadra.build.prompt.core": "v16",
    "niadra.pack.hash": "ph-1",
}
EXPOSED = {
    "niadra.exposure.id": EXPOSURE,
    "niadra.exposure.list": "plans",
    "niadra.exposure.page": 1,
    "niadra.exposure.offset": 0,
    "niadra.exposure.visible_k": 1,
    "niadra.exposure.items": ITEMS,
}


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    return {"stringValue": value}


def _json_attributes(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _json_value(v)} for k, v in attrs.items()]


def json_trace() -> bytes:
    def span(span_id: str, parent: str, attrs: dict[str, Any], offset: int, **extra: Any) -> dict[str, Any]:
        return {
            "traceId": TRACE,
            "spanId": span_id,
            "parentSpanId": parent,
            "name": attrs.get("gen_ai.operation.name", "turn"),
            "startTimeUnixNano": str(START + offset),
            "endTimeUnixNano": str(START + offset + 740_000_000),
            "attributes": _json_attributes(attrs),
            **extra,
        }

    exposure = {
        "name": "niadra.exposure",
        "timeUnixNano": str(START + 2_000_000_000),
        "attributes": _json_attributes(EXPOSED),
    }
    document = {
        "resourceSpans": [
            {
                "resource": {"attributes": _json_attributes(RESOURCE)},
                "scopeSpans": [
                    {
                        "spans": [
                            span(TOOL_SPAN, MODEL_SPAN, TOOL, 1_000_000_000, status={"code": 2}),
                            span(MODEL_SPAN, ROOT_SPAN, MODEL, 0, events=[exposure]),
                            span(ROOT_SPAN, "", {"http.route": "/chat"}, 0),
                        ]
                    }
                ],
            }
        ]
    }
    return orjson.dumps(document)


def _proto_value(value: Any) -> AnyValue:
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    return AnyValue(string_value=value)


def _proto_attributes(attrs: dict[str, Any]) -> list[KeyValue]:
    return [KeyValue(key=k, value=_proto_value(v)) for k, v in attrs.items()]


def proto_trace() -> bytes:
    def span(span_id: str, parent: str, attrs: dict[str, Any], offset: int, **extra: Any) -> Span:
        return Span(
            trace_id=bytes.fromhex(TRACE),
            span_id=bytes.fromhex(span_id),
            parent_span_id=bytes.fromhex(parent),
            name=attrs.get("gen_ai.operation.name", "turn"),
            start_time_unix_nano=START + offset,
            end_time_unix_nano=START + offset + 740_000_000,
            attributes=_proto_attributes(attrs),
            **extra,
        )

    exposure = Span.Event(
        name="niadra.exposure", time_unix_nano=START + 2_000_000_000, attributes=_proto_attributes(EXPOSED)
    )
    request = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=_proto_attributes(RESOURCE)),
                scope_spans=[
                    ScopeSpans(
                        spans=[
                            span(
                                TOOL_SPAN,
                                MODEL_SPAN,
                                TOOL,
                                1_000_000_000,
                                status=Status(code=Status.STATUS_CODE_ERROR),
                            ),
                            span(MODEL_SPAN, ROOT_SPAN, MODEL, 0, events=[exposure]),
                            span(ROOT_SPAN, "", {"http.route": "/chat"}, 0),
                        ]
                    )
                ],
            )
        ]
    )
    return request.SerializeToString()


def test_the_same_trace_in_json_and_in_protobuf_gives_the_same_record() -> None:
    from_json = otlp_turns(otlp_document(json_trace(), JSON), content_mode="hash_only", default_agent="agent")
    from_proto = otlp_turns(
        otlp_document(proto_trace(), PROTOBUF), content_mode="hash_only", default_agent="agent"
    )
    assert from_json == from_proto
    [record] = from_json
    turn = TurnRecord.model_validate(record)
    assert turn.turn_id == f"otel-{TRACE}", "a trace without `niadra.turn.id` is one turn named by it"
    assert (turn.fidelity, turn.completeness, turn.content_mode) == ("silver", "unknown", "hash_only")
    assert turn.conversation_id == "c-8812"
    model, tool = turn.calls
    assert (model.kind, model.name, model.tokens and model.tokens.input) == ("model", "model-2026-08", 2953)
    assert (tool.kind, tool.name, tool.call_id, tool.parent_call_id) == (
        "tool",
        "quote",
        "call_1",
        MODEL_SPAN,
    )
    assert (tool.status, tool.latency_ms) == ("error", 740)
    assert tool.args is not None
    assert tool.args_hash == turn.blobs[tool.args].sha256
    assert all(b.content is None for b in turn.blobs.values()), "a bronze record keeps digests, not values"
    assert turn.build.pins.prompts == {"core": "v16"}
    assert turn.build.pins.model == "model-2026-08"
    assert turn.build.adapter == "closing-agent/3.2.0"
    [presented] = turn.interactions
    assert presented.kind == "presented"
    assert str(presented.exposure_id) == EXPOSURE  # type: ignore[union-attr]
    assert presented.method == "otel"  # type: ignore[union-attr]


def test_without_an_exposure_the_record_is_bronze_and_a_stored_source_keeps_the_values() -> None:
    document = orjson.loads(json_trace())
    del document["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["events"]
    [record] = otlp_turns(document, content_mode="stored", default_agent="agent")
    turn = TurnRecord.model_validate(record)
    assert (turn.fidelity, turn.interactions) == ("bronze", [])
    tool = turn.calls[1]
    assert tool.result_model is not None
    assert turn.blobs[tool.result_model].content == {"quote_id": "q-77", "price_full": 511.06}


async def test_both_encodings_of_one_trace_are_one_turn(world: World) -> None:
    first = await world.service.record_otlp(world.caller, otlp_document(json_trace(), JSON))
    again = await world.service.record_otlp(world.caller, otlp_document(proto_trace(), PROTOBUF))
    assert (first.accepted, first.errors) == (1, [])
    assert (again.accepted, again.duplicates) == (0, 1)
    [applied] = world.taken.turns
    assert applied.record.fidelity == "silver"


async def test_a_space_that_records_no_turns_takes_no_spans(world: World) -> None:
    config = world.cell.config.space(world.caller.space.space_id)
    world.cell.config.put(config.model_copy(update={"features": FeatureSettings(enabled=frozenset())}))
    result = await world.service.record_otlp(world.caller, otlp_document(json_trace(), JSON))
    assert (result.accepted, result.duplicates) == (0, 0)
    assert world.turn_objects() == []


def _split(trace: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """The trace as two exports: the model call and the root first, the tool call later."""
    document = orjson.loads(trace)
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    first, later = orjson.loads(trace), orjson.loads(trace)
    first["resourceSpans"][0]["scopeSpans"][0]["spans"] = spans[1:]
    later["resourceSpans"][0]["scopeSpans"][0]["spans"] = spans[:1]
    return first, later


def _daily(world: World) -> list[Any]:
    return list(world.cell.uow.store.module("turns")["daily"].values())


async def test_spans_of_one_trace_split_across_exports_are_merged_into_one_turn_once(world: World) -> None:
    first, later = _split(json_trace())

    one = await world.service.record_otlp(world.caller, first)
    two = await world.service.record_otlp(world.caller, later)
    again = await world.service.record_otlp(world.caller, later)
    whole = await world.service.record_otlp(world.caller, orjson.loads(json_trace()))

    assert [(r.accepted, r.duplicates) for r in (one, two, again, whole)] == [(1, 0), (1, 0), (0, 1), (0, 1)]
    turn_id = f"otel-{TRACE}"
    view = await world.service.read(world.caller, turn_id)
    assert sorted(c.call_id for c in view.record.calls) == ["call_1", MODEL_SPAN]
    assert view.record.fidelity == "silver"
    assert view.record.latency_ms == 1740
    [row] = _daily(world)
    assert (row.turns, row.tool_calls, row.model_calls, row.errors) == (1, 1, 1, 1)
    assert not any(k.endswith(".part") for k in world.turn_objects()), "a merged part leaves"
    assert [len(t.record.calls) for t in world.taken.turns] == [1, 1], "each module gets what each part added"


async def test_a_stored_sources_later_part_brings_its_values(world: World) -> None:
    world.configure(content_mode="stored", sample_rate=0)
    first, later = _split(json_trace())
    await world.service.record_otlp(world.caller, first)
    await world.service.record_otlp(world.caller, later)
    view = await world.service.read(world.caller, f"otel-{TRACE}")
    tool = next(c for c in view.record.calls if c.kind == "tool")
    assert tool.result_model == f"r:{TOOL_SPAN}"
    assert view.record.blobs[tool.result_model].content == {"quote_id": "q-77", "price_full": 511.06}
