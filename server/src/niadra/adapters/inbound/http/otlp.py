"""OTLP/HTTP trace bodies (3.7): JSON, or protobuf, which the Python exporter speaks. Protobuf is read into
the OTLP/JSON document, with ids in hex as OTLP/JSON writes them, so both encodings reach the mappers as the
same document; the answer goes back in the encoding the request came in."""

import base64
import binascii
from typing import Any

import orjson
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from niadra.domain.common.errors import InvalidInput

JSON = "application/json"
PROTOBUF = "application/x-protobuf"
_IDS = frozenset({"traceId", "spanId", "parentSpanId"})


def media_type(content_type: str) -> str:
    media = content_type.split(";", 1)[0].strip().lower()
    if media not in (JSON, PROTOBUF):
        raise InvalidInput("send OTLP as application/json or application/x-protobuf")
    return media


def otlp_document(body: bytes, media: str) -> dict[str, Any]:
    if media == JSON:
        try:
            document = orjson.loads(body)
        except orjson.JSONDecodeError:
            raise InvalidInput("the body is not JSON") from None
        if not isinstance(document, dict):
            raise InvalidInput("the body is not an OTLP export request")
        return document
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except DecodeError:
        raise InvalidInput("the body is not an OTLP export request") from None
    converted: dict[str, Any] = _hex_ids(MessageToDict(request, use_integers_for_enums=True))
    return converted


def otlp_answer(media: str, rejected: int, message: str) -> Response:
    if media == JSON:
        return JSONResponse({"partialSuccess": {"rejectedSpans": rejected, "errorMessage": message}})
    answer = ExportTraceServiceResponse(
        partial_success=ExportTracePartialSuccess(rejected_spans=rejected, error_message=message)
    )
    return Response(answer.SerializeToString(), media_type=PROTOBUF)


def _hex_ids(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _hex(v) if k in _IDS else _hex_ids(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_hex_ids(v) for v in node]
    return node


def _hex(value: Any) -> Any:
    try:
        return base64.b64decode(value, validate=True).hex() if isinstance(value, str) else value
    except binascii.Error:
        return value
