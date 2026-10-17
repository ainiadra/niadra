"""OTLP `gen_ai` spans to message items (5.2) and to turn records (24.3.5, the OpenTelemetry conventions).

The `gen_ai.*` conventions are still unstable and three generations of attributes coexist, so the
mapper tries them in cascade: `gen_ai.input.messages`/`gen_ai.output.messages` (current),
`gen_ai.prompt.N.*`/`gen_ai.completion.N.*` (older), then OpenInference `llm.*_messages.N.*`.
There is no OTel attribute for the channel or the customer's handles, so we define `niadra.channel`
and `niadra.handle.<type>`; `enduser.id` becomes an `app_user_id`.

Each span repeats the conversation so far, so the idempotency key is the message's position in
the conversation: re-exported history deduplicates instead of duplicating.

The spans of a turn (`niadra.turn.id`, or one trace) also become one turn record: `bronze`, whose calls are
the tool and model spans, or `silver` when a `niadra.exposure` says what the person was shown. Its
completeness is `unknown`: an exporter drops what it wants. Both OTLP encodings arrive here as the same
JSON document, so they give the same record.
"""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from niadra.domain.turns.digest import digest
from niadra.domain.vocabulary import HandleType, Speaker

_ROLES = {
    "user": Speaker.CUSTOMER,
    "human": Speaker.CUSTOMER,
    "assistant": Speaker.AI_AGENT,
    "model": Speaker.AI_AGENT,
}
_INDEXED = (
    (re.compile(r"^gen_ai\.prompt\.(\d+)\.(role|content)$"), "input"),
    (re.compile(r"^gen_ai\.completion\.(\d+)\.(role|content)$"), "output"),
    (re.compile(r"^llm\.input_messages\.(\d+)\.message\.(role|content)$"), "input"),
    (re.compile(r"^llm\.output_messages\.(\d+)\.message\.(role|content)$"), "output"),
)
_HANDLE_PREFIX = "niadra.handle."


def otlp_items(body: Mapping[str, Any], *, default_channel: str, user_scope: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for resource_spans in body.get("resourceSpans") or ():
        resource = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        for scope_spans in resource_spans.get("scopeSpans") or ():
            for span in scope_spans.get("spans") or ():
                attrs = {**resource, **_attributes(span.get("attributes"))}
                items.extend(_span_items(span, attrs, default_channel, user_scope))
    return items


def _span_items(
    span: Mapping[str, Any], attrs: dict[str, Any], default_channel: str, user_scope: str
) -> Iterator[dict[str, Any]]:
    inputs, outputs = _messages(attrs)
    if not inputs and not outputs:
        return
    conversation = str(
        attrs.get("gen_ai.conversation.id") or attrs.get("session.id") or span.get("traceId") or ""
    )
    base = {
        "type": "event",
        "kind": "message",
        "channel": str(attrs.get("niadra.channel") or default_channel),
        "conversation_id": conversation or None,
        "handles": _handles(attrs, user_scope),
    }
    started = _nanos(span.get("startTimeUnixNano"))
    ended = _nanos(span.get("endTimeUnixNano")) or started
    position = 0
    for group, at in ((inputs, started), (outputs, ended)):
        for role, text in group:
            speaker = _ROLES.get(role)
            if speaker is None:
                continue  # system, developer and tool messages are not part of the conversation
            position += 1
            if not text or at is None:
                continue
            yield {
                **base,
                "idempotency_key": f"otel:{conversation}:{position}",
                "speaker": {"role": speaker.value},
                "direction": "inbound" if speaker is Speaker.CUSTOMER else "outbound",
                "content": {"type": "text", "text": text},
                "occurred_at": at.isoformat(),
            }


def _messages(attrs: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    current = (
        _json_messages(attrs.get("gen_ai.input.messages")),
        _json_messages(attrs.get("gen_ai.output.messages")),
    )
    if current[0] or current[1]:
        return current
    legacy = (_json_messages(attrs.get("gen_ai.prompt")), _json_messages(attrs.get("gen_ai.completion")))
    if legacy[0] or legacy[1]:
        return legacy
    indexed: dict[str, dict[int, dict[str, str]]] = {"input": {}, "output": {}}
    for key, value in attrs.items():
        for pattern, group in _INDEXED:
            if match := pattern.match(key):
                indexed[group].setdefault(int(match[1]), {})[match[2]] = str(value)
    return (
        [(m.get("role", ""), m.get("content", "")) for _, m in sorted(indexed["input"].items())],
        [(m.get("role", "assistant"), m.get("content", "")) for _, m in sorted(indexed["output"].items())],
    )


def _json_messages(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [(str(m.get("role", "")), _message_text(m)) for m in value if isinstance(m, dict)]


def _message_text(message: Mapping[str, Any]) -> str:
    if isinstance(message.get("content"), str):
        return str(message["content"])
    parts = message.get("parts") or message.get("content") or []
    texts = [
        str(p.get("content") or p.get("text") or "")
        for p in parts
        if isinstance(p, dict) and p.get("type", "text") == "text"
    ]
    return "\n".join(t for t in texts if t)


def _handles(attrs: Mapping[str, Any], user_scope: str) -> list[dict[str, str]]:
    known = {t.value for t in HandleType}
    handles = [
        {"type": key[len(_HANDLE_PREFIX) :], "value": str(value)}
        for key, value in attrs.items()
        if key.startswith(_HANDLE_PREFIX) and key[len(_HANDLE_PREFIX) :] in known and value
    ]
    if user := attrs.get("enduser.id"):
        handles.append({"type": HandleType.APP_USER_ID.value, "value": str(user), "scope": user_scope})
    return handles


def _attributes(raw: Any) -> dict[str, Any]:
    return {str(a.get("key")): _value(a.get("value") or {}) for a in raw or () if isinstance(a, dict)}


def _value(value: Mapping[str, Any]) -> Any:
    for kind in ("stringValue", "boolValue", "doubleValue"):
        if kind in value:
            return value[kind]
    if "intValue" in value:
        return int(value["intValue"])
    if "arrayValue" in value:
        return [_value(v) for v in (value["arrayValue"] or {}).get("values") or ()]
    if "kvlistValue" in value:
        return _attributes((value["kvlistValue"] or {}).get("values"))
    return None


def _nanos(value: Any) -> datetime | None:
    try:
        nanos = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(nanos / 1e9, UTC) if nanos > 0 else None


_TOOL = "execute_tool"
_EXPOSURE = "niadra.exposure"
_ERROR_STATUS = (2, "STATUS_CODE_ERROR")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROMPT = "niadra.build.prompt."
_TOOL_SCHEMA = "niadra.build.tool_schema."
_CACHED_TOKENS = ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens")


def otlp_turns(body: Mapping[str, Any], *, content_mode: str, default_agent: str) -> list[dict[str, Any]]:
    """One turn record per turn of the spans, as the Turn Record's JSON; spans with no `gen_ai` operation
    and no exposure are left out. `content_mode` is `hash_only` unless the source keeps values (`stored`)."""
    turns: dict[str, list[tuple[dict[str, Any], Mapping[str, Any]]]] = {}
    for resource_spans in body.get("resourceSpans") or ():
        resource = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        for scope_spans in resource_spans.get("scopeSpans") or ():
            for span in scope_spans.get("spans") or ():
                attrs = {**resource, **_attributes(span.get("attributes"))}
                if "gen_ai.operation.name" not in attrs and not _exposures(span, attrs):
                    continue
                turn_id = str(attrs.get("niadra.turn.id") or f"otel-{span.get('traceId', '')}")
                turns.setdefault(turn_id, []).append((attrs, span))
    return [_turn(turn_id, spans, content_mode, default_agent) for turn_id, spans in turns.items()]


def _turn(
    turn_id: str, spans: list[tuple[dict[str, Any], Mapping[str, Any]]], content_mode: str, default_agent: str
) -> dict[str, Any]:
    spans = sorted(spans, key=lambda s: (_nanos_int(s[1].get("startTimeUnixNano")), str(s[1].get("spanId"))))
    first = spans[0][0]
    calls: list[dict[str, Any]] = []
    blobs: dict[str, dict[str, Any]] = {}
    by_span: dict[str, str] = {}
    interactions: list[dict[str, Any]] = []
    for attrs, span in spans:
        if "gen_ai.operation.name" in attrs:
            call = _call(attrs, span, by_span, blobs, content_mode, len(calls) + 1)
            if call is not None:
                calls.append(call)
        interactions += _exposures(span, attrs)
    starts = [_nanos(span.get("startTimeUnixNano")) for _, span in spans]
    ends = [_nanos(span.get("endTimeUnixNano")) for _, span in spans]
    started = min(t for t in starts if t is not None)
    ended = max((t for t in ends if t is not None), default=started)
    conversation = first.get("gen_ai.conversation.id") or first.get("session.id")
    trace = str(spans[0][1].get("traceId", ""))
    model = first.get("niadra.build.model") or next((c["name"] for c in calls if c["kind"] == "model"), None)
    record: dict[str, Any] = {
        "spec": "turn-record.v0",
        "turn_id": turn_id,
        "agent": {"name": str(first.get("gen_ai.agent.name") or first.get("service.name") or default_agent)},
        "kind": "message",
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "fidelity": "silver" if interactions else "bronze",
        "completeness": "unknown",
        "content_mode": content_mode,
        "build": {"pins": _pins(first, model), "sdk": "opentelemetry"},
        "calls": calls,
        "interactions": interactions,
        "latency_ms": round((ended - started).total_seconds() * 1000),
        "blobs": blobs,
    }
    if conversation:
        record["conversation_id"] = str(conversation)
    else:
        record["task_id"] = f"otel-{trace}"
    if service := first.get("service.name"):
        # The instrumented service and its code version stand in for the framework adapter.
        record["build"]["adapter"] = f"{service}/{first.get('service.version') or 'unknown'}"
    if pack := first.get("niadra.pack.hash"):
        record["reads"] = [{"surface": "pack", "etag": str(pack)}]
    return record


def _call(
    attrs: Mapping[str, Any],
    span: Mapping[str, Any],
    by_span: dict[str, str],
    blobs: dict[str, dict[str, Any]],
    content_mode: str,
    number: int,
) -> dict[str, Any] | None:
    tool = attrs["gen_ai.operation.name"] == _TOOL
    span_id = str(span.get("spanId", ""))
    call_id = str((attrs.get("gen_ai.tool.call.id") if tool else None) or span_id or f"c{number}")
    if call_id in by_span.values():
        return None  # the same call exported twice
    by_span[span_id] = call_id
    call: dict[str, Any] = {"call_id": call_id, "kind": "tool" if tool else "model"}
    parent = by_span.get(str(span.get("parentSpanId", "")))
    if parent is not None and parent != call_id:
        call["parent_call_id"] = parent
    name = (
        attrs.get("gen_ai.tool.name")
        if tool
        else attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
    )
    if name:
        call["name"] = str(name)
    if (span.get("status") or {}).get("code") in _ERROR_STATUS:
        call["status"] = "error"
    start, end = _nanos_int(span.get("startTimeUnixNano")), _nanos_int(span.get("endTimeUnixNano"))
    if end >= start > 0:
        call["latency_ms"] = round((end - start) / 1_000_000)
    if int(span.get("droppedAttributesCount") or 0) > 0:
        call["truncated"] = True
    if tool:
        for field, attribute, prefix in (
            ("args", "gen_ai.tool.call.arguments", "a"),
            ("result_model", "gen_ai.tool.call.result", "r"),
        ):
            if (value := attrs.get(attribute)) is not None:
                # Named by the span, so the parts of one trace exported apart never share a key.
                key = f"{prefix}:{span_id or number}"
                blobs[key] = _blob(_json_value(value), content_mode)
                call[field] = key
                if field == "args":
                    call["args_hash"] = blobs[key]["sha256"]
    elif "gen_ai.usage.input_tokens" in attrs or "gen_ai.usage.output_tokens" in attrs:
        cached = next((attrs[k] for k in _CACHED_TOKENS if k in attrs), 0)
        call["tokens"] = {
            "in": int(attrs.get("gen_ai.usage.input_tokens") or 0),
            "cached": int(cached or 0),
            "out": int(attrs.get("gen_ai.usage.output_tokens") or 0),
        }
    return call


def _blob(value: Any, content_mode: str) -> dict[str, Any]:
    sha256, size = digest(value)
    blob: dict[str, Any] = {"sha256": sha256, "size": size}
    if content_mode == "stored":
        blob["content"] = value
    return blob


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _pins(attrs: Mapping[str, Any], model: Any) -> dict[str, Any]:
    pins: dict[str, Any] = {
        "prompts": {k[len(_PROMPT) :]: str(v) for k, v in sorted(attrs.items()) if k.startswith(_PROMPT)},
        "tool_schemas": {
            k[len(_TOOL_SCHEMA) :]: str(v)
            for k, v in sorted(attrs.items())
            if k.startswith(_TOOL_SCHEMA) and _SHA256.match(str(v))
        },
    }
    if model:
        pins["model"] = str(model)
    if (corpus := attrs.get("niadra.build.corpus_digest")) and _SHA256.match(str(corpus)):
        pins["corpus_digest"] = str(corpus)
    if assembler := attrs.get("niadra.build.assembler"):
        pins["assembler"] = str(assembler)
    if pack := attrs.get("niadra.pack.hash"):
        pins["niadra"] = {"pack_hash": str(pack)}
    return pins


def _exposures(span: Mapping[str, Any], attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A `niadra.exposure` span, or span events of that name: a `presented` interaction produced by `otel`."""
    found: list[tuple[Mapping[str, Any], Any]] = []
    if span.get("name") == _EXPOSURE:
        found.append((attrs, span.get("startTimeUnixNano")))
    for event in span.get("events") or ():
        if event.get("name") == _EXPOSURE:
            found.append(({**attrs, **_attributes(event.get("attributes"))}, event.get("timeUnixNano")))
    exposures = []
    for exposure, at in found:
        delivered = _nanos(at)
        if "niadra.exposure.id" not in exposure or delivered is None:
            continue
        presented: dict[str, Any] = {
            "kind": "presented",
            "exposure_id": str(exposure["niadra.exposure.id"]),
            "list_id": str(exposure.get("niadra.exposure.list") or ""),
            "page": int(exposure.get("niadra.exposure.page") or 1),
            "offset": int(exposure.get("niadra.exposure.offset") or 0),
            "items": _json_value(exposure.get("niadra.exposure.items") or "[]"),
            "delivered_at": _iso(delivered),
            "method": "otel",
        }
        if (visible := exposure.get("niadra.exposure.visible_k")) is not None:
            presented["visible_k"] = int(visible)
        exposures.append(presented)
    return exposures


def _nanos_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")
