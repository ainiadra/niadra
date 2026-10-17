"""Write routes of `niadra-ingest` (7.1): batch, generic webhook, file imports, OTLP, media uploads,
feedback."""

from typing import Annotated, Any
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from niadra.adapters.inbound.http.auth import require_source
from niadra.adapters.inbound.http.bodies import Large, decoded_body
from niadra.adapters.inbound.http.deps import service
from niadra.adapters.inbound.http.otlp import media_type, otlp_answer, otlp_document
from niadra.contracts.events import (
    BatchRequest,
    BatchResponse,
    FeedbackBatchRequest,
    FeedbackRequest,
    MediaUploadResponse,
)
from niadra.contracts.ingest import (
    MAX_BATCH_BYTES,
    FileImportOut,
    IngestStatus,
    IngestStatusRequest,
    MediaUploadIn,
    batch_items,
    validate_items,
)
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.files import MAX_FILE_BYTES, FileFormat
from niadra.domain.vocabulary import Scope

router = APIRouter(tags=["ingest"])

Writer = Annotated[SourcePrincipal, Depends(require_source())]
Tracker = Annotated[SourcePrincipal, Depends(require_source(Scope.TRACK))]
Ingest = Annotated[Any, Depends(service("ingest"))]
Turns = Annotated[Any, Depends(service("turns"))]
OTLP_DECODED_BYTES = 16 * 1024 * 1024
Extract = Annotated[Any, Depends(service("extract"))]


async def _webhook_rate(source_id: UUID, request: Request) -> None:
    await request.app.state.container.edge.check_webhook_rate(source_id, request.url.path)


WebhookRate = Depends(_webhook_rate)
_FILE_TYPES = {
    "text/csv": FileFormat.CSV,
    "application/x-ndjson": FileFormat.JSONL,
    "application/jsonl": FileFormat.JSONL,
}
_FILE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "text/csv": {"schema": {"type": "string"}},
            "application/x-ndjson": {"schema": {"type": "string"}},
        },
    }
}

# The body is documented as `BatchRequest` but parsed item by item, so it is declared by hand; the
# `$id` scopes the schema's internal `#/$defs/...` references to the schema itself (JSON Schema 2020-12).
_BATCH_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$id": "urn:niadra:v1:batch-request", **BatchRequest.model_json_schema()}
            }
        },
    }
}


@router.post(
    "/v1/batch",
    response_model=BatchResponse,
    responses={207: {"model": BatchResponse, "description": "Some items were rejected; see `errors`."}},
    openapi_extra=_BATCH_BODY,
)
async def batch(request: Request, caller: Writer, ingest: Ingest) -> JSONResponse:
    # Parsed item by item: one malformed item is a 207 entry, never a 422 for the batch (5.1).
    body = await _body(request)
    try:
        parsed = orjson.loads(body)
    except orjson.JSONDecodeError:
        raise InvalidInput("the body is not JSON") from None
    items, errors = validate_items(batch_items(parsed))
    return _batch_response(await ingest.ingest_items(caller, items, errors, raw_body=body))


@router.post("/v1/ingest/webhook/{source_id}", response_model=BatchResponse, dependencies=[WebhookRate])
async def webhook(source_id: UUID, request: Request, ingest: Ingest) -> JSONResponse:
    """Authenticated by the source's mapping (bearer, HMAC, URL token or client certificate), never a key."""
    body = await _body(request)
    result = await ingest.ingest_webhook(source_id, dict(request.headers), body, dict(request.query_params))
    return _batch_response(result)


@router.get("/v1/ingest/webhook/{source_id}", response_class=PlainTextResponse, dependencies=[WebhookRate])
async def webhook_challenge(source_id: UUID, request: Request, ingest: Ingest) -> PlainTextResponse:
    """The verification some systems send by GET before the first delivery (Meta's `hub.challenge`): the
    value of the mapping's `verification_challenge` parameter comes back as plain text. Only a mapping
    authenticated by URL token answers, and only with its `token` in the query (401 otherwise); under any
    other mode the GET carries no credential, and the answer is 404."""
    return PlainTextResponse(await ingest.webhook_challenge(source_id, dict(request.query_params)))


@router.post("/v1/ingest/files", response_model=FileImportOut, status_code=202, openapi_extra=_FILE_BODY)
async def upload_file(request: Request, caller: Tracker, ingest: Ingest) -> FileImportOut:
    """A CSV that seeds identity (one `identify` per row, 5.3) or JSONL with one batch item per line.
    The body streams into object storage; `import_file` ingests it and the GET below follows it."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    format = _FILE_TYPES.get(media_type)
    if format is None:
        raise InvalidInput("send text/csv or application/x-ndjson")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise InvalidInput("the file exceeds 512 MB")
    return FileImportOut.of(await ingest.upload_file(caller, format, request.stream()))


@router.post("/v1/ingest/status", response_model=IngestStatus, response_model_exclude_none=True)
async def ingest_status(body: IngestStatusRequest, caller: Tracker, extract: Extract) -> IngestStatus:
    """Whether what was sent for a conversation or task became memory yet: `open`, `processing`,
    `ready`, `failed` or `unknown`. States only, never content."""
    result: IngestStatus = await extract.status(caller, body)
    return result


@router.get("/v1/ingest/files/{import_id}", response_model=FileImportOut)
async def file_import(import_id: UUID, caller: Tracker, ingest: Ingest) -> FileImportOut:
    return FileImportOut.of(await ingest.file_import(caller, import_id))


@router.post("/v1/otel/v1/traces")
async def otel_traces(
    request: Request, caller: Tracker, ingest: Ingest, turns: Turns, large: Large
) -> Response:
    """OTLP/HTTP, as JSON or protobuf, gzipped or not. The conversation's messages become events; with turn
    records on, each turn of the spans also becomes a bronze record, silver with an exposure (3.7)."""
    media = media_type(request.headers.get("content-type", ""))
    async with decoded_body(request, large, MAX_BATCH_BYTES, OTLP_DECODED_BYTES) as raw:
        body = otlp_document(raw, media)
        result = await ingest.ingest_otlp(caller, body)
        recorded = await turns.record_otlp(caller, body)
    problems = [f"{e.index}: {e.code}" for e in result.errors]
    problems += [f"turn {e.index}: {e.code}" for e in recorded.errors]
    return otlp_answer(media, len(result.errors) + len(recorded.errors), "; ".join(problems)[:500])


@router.post("/v1/media/uploads", response_model=MediaUploadResponse, status_code=201)
async def media_upload(body: MediaUploadIn, caller: Tracker, ingest: Ingest) -> MediaUploadResponse:
    result: MediaUploadResponse = await ingest.create_media_upload(caller, body)
    return result


@router.post("/v1/feedback", response_model=BatchResponse)
async def feedback(body: FeedbackRequest, caller: Tracker, ingest: Ingest) -> JSONResponse:
    return _batch_response(await ingest.record_feedback(caller, body))


@router.post("/v1/feedback/batch", response_model=BatchResponse)
async def feedback_batch(body: FeedbackBatchRequest, caller: Tracker, ingest: Ingest) -> JSONResponse:
    """Up to 500 corrections in one call: 200, or 207 with one error per refused item, like `/v1/batch`."""
    return _batch_response(await ingest.record_feedback_batch(caller, body.items))


def _batch_response(result: BatchResponse) -> JSONResponse:
    return JSONResponse(result.model_dump(mode="json"), status_code=207 if result.errors else 200)


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BATCH_BYTES:
        raise InvalidInput("the body exceeds 2.5 MB")
    body = await request.body()
    if len(body) > MAX_BATCH_BYTES:
        raise InvalidInput("the body exceeds 2.5 MB")
    return body
