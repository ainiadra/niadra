"""Item-by-item validation of the write side (5.1: the edge checks the envelope, then each item).

A batch is never parsed as one model: one malformed item would turn the whole request into a
422. Errors carry field paths and error types only, never the values, which may be personal data.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import orjson
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from niadra.contracts.common import Handle, IdStr, Model
from niadra.contracts.events import MAX_BATCH_ITEMS, BatchItem, ItemError, MediaUploadRequest
from niadra.domain.common.errors import InvalidInput
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.ingest.files import MAX_RECORD_BYTES, FileImport

MAX_ITEM_BYTES = MAX_RECORD_BYTES
MAX_BATCH_BYTES = 2_500_000

_ITEM: TypeAdapter[BatchItem] = TypeAdapter(BatchItem, config=ConfigDict(defer_build=True))


def validate_items(raw_items: Sequence[Any]) -> tuple[list[tuple[int, BatchItem]], list[ItemError]]:
    items: list[tuple[int, BatchItem]] = []
    errors: list[ItemError] = []
    for index, raw in enumerate(raw_items):
        if len(orjson.dumps(raw)) > MAX_ITEM_BYTES:
            errors.append(ItemError(index=index, code="too_large", detail="the item exceeds 1 MB"))
            continue
        try:
            items.append((index, _ITEM.validate_python(raw)))
        except ValidationError as exc:
            errors.append(ItemError(index=index, code="invalid_input", detail=describe(exc)))
    return items, errors


def batch_items(body: Any) -> list[Any]:
    """The envelope check: `items` must be a non-empty list within the batch limit."""
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH_ITEMS:
        raise InvalidInput(f"`items` must be a list of 1 to {MAX_BATCH_ITEMS} items")
    return items


def describe(exc: ValidationError) -> str:
    fields = [".".join(str(p) for p in err.get("loc", ())) + f": {err.get('type')}" for err in exc.errors()]
    return "; ".join(fields)[:500]


class MediaUploadIn(MediaUploadRequest):
    subject: Handle | None = Field(
        default=None,
        description="Whose media it is. The object is stored under the subject's handle, so `forget` "
        "reaches it even if no event ever references it (5.14.0).",
    )


class RowError(Model):
    row: int = Field(description="1-based row of the file, header excluded; 0 for the whole import.")
    code: str
    detail: str | None = None


class FileImportOut(Model):
    """`POST /v1/ingest/files` answers 202 with this; `GET /v1/ingest/files/{import_id}` follows it."""

    import_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    format: Literal["csv", "jsonl"]
    size_bytes: int
    sha256: str
    records: int = Field(description="Records in the file, blank lines included.")
    accepted: int
    duplicates: int
    rejected: int
    errors: list[RowError] = Field(description="The first 100 rejected rows; values are never echoed.")
    created_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def of(cls, record: FileImport) -> "FileImportOut":
        return cls(
            import_id=record.import_id,
            status=record.status.value,
            format=record.format.value,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            records=record.records,
            accepted=record.accepted,
            duplicates=record.duplicates,
            rejected=record.rejected,
            errors=[RowError(**e) for e in record.errors],
            created_at=record.created_at,
            finished_at=record.finished_at,
        )


class CoverageDay(Model):
    day: date
    sent: int = Field(description="What the SDK's heartbeats say it sent; 0 for sources without an SDK.")
    received: int
    rejected: int
    unmapped: int
    last_event_at: datetime | None = None

    @classmethod
    def of(cls, day: SourceDay) -> "CoverageDay":
        return cls(
            day=day.day,
            sent=day.sent,
            received=day.received,
            rejected=day.rejected,
            unmapped=day.unmapped,
            last_event_at=day.last_event_at,
        )


class SourceCoverage(Model):
    """Totals over the window plus one row per UTC day with traffic; a configured source that sent
    nothing is listed with zeros, since a mute source is what coverage exists to show (5.2)."""

    source_id: UUID
    name: str | None = Field(description="None for a source no longer in the configuration.")
    channel: str | None = None
    sent: int
    received: int
    rejected: int
    unmapped: int
    last_event_at: datetime | None = None
    days: list[CoverageDay]


class CoverageOut(Model):
    since: datetime
    items: list[SourceCoverage]


class EventsArrived(Model):
    """Whether the space received any event in the window: the whole answer, with no source and no count."""

    arrived: bool
    since: datetime


class IngestStatusRequest(Model):
    """`POST /v1/ingest/status`: the conversation or task goes in the body, never in a URL (7.0)."""

    conversation_id: IdStr | None = None
    task_id: IdStr | None = None

    @model_validator(mode="after")
    def _one_thread(self) -> "IngestStatusRequest":
        if (self.conversation_id is None) == (self.task_id is None):
            raise ValueError("pass exactly one of `conversation_id` or `task_id`")
        return self


class IngestStatus(Model):
    """Whether what was sent for a conversation or task became memory yet. No content, only states."""

    state: Literal["unknown", "open", "processing", "ready", "failed"] = Field(
        description="`open`: still receiving turns, memory comes after it closes by inactivity or "
        "`conversation.ended`. `processing`: closed, extraction queued or running, or memory still "
        "applying it. `ready`: extracted and applied; a context read made now sees it, in the pack and in "
        "a pinned conversation's `delta` and `slots`. `failed`: the extraction failed and a template "
        "episode stands in; the nightly pass extracts again. `unknown`: nothing was received under this id."
    )
    last_event_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    extraction: Literal["ok", "minimal", "invalid", "failed"] | None = Field(
        default=None, description="The latest extraction run of the last closed session, when there is one."
    )
    masked: dict[str, int] | None = Field(
        default=None,
        description="Values held back from this conversation's or task's events before storage, by type "
        "(`card`, `cvv`, `password`, `secret`); absent when none was.",
    )
