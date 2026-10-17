"""File imports (5.2, 5.3 "CRM/CSV seed"): CSV or JSONL cut into parts, then ingested in batches.

The upload is split at record boundaries while it streams, so neither the request nor the task ever
holds more than one part in memory. A part never cuts a record: in CSV a newline inside quotes is part
of the field, so the cut follows the quote parity.
"""

import csv
import io
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.vocabulary import AssertionMethod, HandleType

MAX_FILE_BYTES = 512 * 1024 * 1024
PART_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 1_000_000
MAX_HEADER_BYTES = 64 * 1024
MAX_ERRORS = 100
"""Per-row errors kept on the import; the rest are only counted in `rejected`."""

_NEWLINE, _QUOTE = b"\n", b'"'
_HANDLE_COLUMN = re.compile(r"^(?P<type>[a-z0-9_]+?)(?:_(?P<n>\d))?$")
_CSV_EXTRAS = frozenset({"idempotency_key", "occurred_at", "subject_kind"})
_HANDLE_TYPES = frozenset(t.value for t in HandleType)


class FileFormat(StrEnum):
    CSV = "csv"
    JSONL = "jsonl"


class ImportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Part:
    data: bytes
    records: int


@dataclass(frozen=True, slots=True)
class FileImport:
    """One upload and its progress. `next_part`/`next_row` point at the first record not yet ingested;
    they move in the same transaction as the events, so a retried task never counts a row twice."""

    import_id: UUID
    source_id: UUID
    key_id: str
    scopes: frozenset[str]
    format: FileFormat
    status: ImportStatus
    size_bytes: int
    sha256: str
    part_rows: tuple[int, ...]
    created_at: datetime
    header: tuple[str, ...] | None = None
    next_part: int = 0
    next_row: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    finished_at: datetime | None = None

    @property
    def records(self) -> int:
        return sum(self.part_rows)

    def first_row(self, part: int) -> int:
        """1-based number of the part's first record in the file, header excluded."""
        return sum(self.part_rows[:part]) + 1

    def progressed(
        self,
        *,
        next_part: int,
        next_row: int,
        accepted: int,
        duplicates: int,
        errors: Sequence[dict[str, Any]],
    ) -> "FileImport":
        kept = (*self.errors, *errors)[:MAX_ERRORS]
        return replace(
            self,
            status=ImportStatus.RUNNING,
            next_part=next_part,
            next_row=next_row,
            accepted=self.accepted + accepted,
            duplicates=self.duplicates + duplicates,
            rejected=self.rejected + len(errors),
            errors=kept,
        )


class RecordSplitter:
    """Feeds on the upload's chunks and returns whole parts of about `part_bytes`."""

    def __init__(self, format: FileFormat, part_bytes: int = PART_BYTES) -> None:
        self._format = format
        self._part_bytes = part_bytes
        self._buffer = bytearray()
        self._scanned = 0
        self._boundary = 0
        self._records = 0
        self._in_quotes = False
        self.header: tuple[str, ...] | None = None
        self.size = 0

    def feed(self, chunk: bytes) -> list[Part]:
        self.size += len(chunk)
        if self.size > MAX_FILE_BYTES:
            raise InvalidInput("the file exceeds 512 MB")
        self._buffer += chunk
        if self._format is FileFormat.CSV and self.header is None and not self._take_header(final=False):
            return []
        self._scan()
        parts: list[Part] = []
        while len(self._buffer) >= self._part_bytes and self._boundary:
            parts.append(self._cut())
        if len(self._buffer) - self._boundary > MAX_RECORD_BYTES:
            raise InvalidInput("a record exceeds 1 MB, or a quote is never closed")
        return parts

    def finish(self) -> list[Part]:
        if self._format is FileFormat.CSV and self.header is None:
            self._take_header(final=True)
        self._scan()
        if self._in_quotes:
            raise InvalidInput("a quote is never closed")
        parts = [self._cut()] if self._boundary else []
        if self._buffer.strip():
            parts.append(Part(bytes(self._buffer), 1))
        self._buffer.clear()
        return parts

    def _take_header(self, *, final: bool) -> bool:
        end = self._buffer.find(_NEWLINE)
        if end == -1 and not final:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise InvalidInput("the CSV header line exceeds 64 KB")
            return False
        line = bytes(self._buffer if end == -1 else self._buffer[:end])
        self.header = check_header(_header_fields(line))
        del self._buffer[: len(self._buffer) if end == -1 else end + 1]
        return True

    def _scan(self) -> None:
        buffer, pos = self._buffer, self._scanned
        while (end := buffer.find(_NEWLINE, pos)) != -1:
            if self._format is FileFormat.CSV and buffer.count(_QUOTE, pos, end) % 2:
                self._in_quotes = not self._in_quotes
            if not self._in_quotes:
                self._boundary = end + 1
                self._records += 1
            pos = end + 1
        self._scanned = pos

    def _cut(self) -> Part:
        part = Part(bytes(self._buffer[: self._boundary]), self._records)
        del self._buffer[: self._boundary]
        self._scanned -= self._boundary
        self._boundary = 0
        self._records = 0
        return part


def check_header(columns: Sequence[str]) -> tuple[str, ...]:
    """CSV rows seed identity (5.3): one column per handle type, `<type>_scope` for its namespace and
    `<type>_2`...`<type>_9` for more values of the same type."""
    names = tuple(c.strip().lower() for c in columns)
    if len(set(names)) != len(names):
        raise InvalidInput("the CSV header repeats a column")
    unknown = [n for n in names if n not in _CSV_EXTRAS and not _is_scope(n) and _handle_type(n) is None]
    if unknown:
        # Counted, never echoed: a file without a header would put a customer's values here.
        raise InvalidInput(
            f"{len(unknown)} unknown CSV columns; the header takes handle types, <type>_scope, "
            "idempotency_key, occurred_at and subject_kind"
        )
    if not any(_handle_type(n) for n in names):
        raise InvalidInput("the CSV header names no handle column")
    return names


def split_records(format: FileFormat, data: bytes, first_row: int) -> Iterator[tuple[int, bytes]]:
    """The part's records with their row numbers, cut like `RecordSplitter` cuts parts; blank lines
    keep their number and are skipped."""
    start = pos = 0
    in_quotes = False
    row = first_row
    while pos < len(data):
        end = data.find(_NEWLINE, pos)
        end = len(data) if end == -1 else end
        if format is FileFormat.CSV and data.count(_QUOTE, pos, end) % 2:
            in_quotes = not in_quotes
        if not in_quotes or end == len(data):
            if data[start:end].strip():
                yield row, data[start:end]
            row += 1
            start = end + 1
            in_quotes = False
        pos = end + 1


def csv_item(
    header: Sequence[str], record: bytes, *, row: int, file_sha256: str, occurred_at: datetime
) -> dict[str, Any]:
    """One `identify` item per row, method `system_import`, under the same protections as any
    identify (5.3). Raises `InvalidInput` for a row that cannot become one."""
    values = next(csv.reader(io.StringIO(_decode(record), newline="")), [])
    if len(values) != len(header):
        raise InvalidInput(f"expected {len(header)} columns, got {len(values)}")
    cells = {name: value.strip() for name, value in zip(header, values, strict=True)}
    handles = []
    for name, value in cells.items():
        type_ = _handle_type(name)
        if type_ is not None and value:
            handle = {"type": type_, "value": value}
            if scope := cells.get(f"{type_}_scope"):
                handle["scope"] = scope
            handles.append(handle)
    return {
        "type": "identify",
        "idempotency_key": cells.get("idempotency_key") or f"file:{file_sha256[:32]}:{row}",
        "handles": handles,
        "method": AssertionMethod.SYSTEM_IMPORT.value,
        "subject_kind": cells.get("subject_kind") or "person",
        "occurred_at": cells.get("occurred_at") or occurred_at.isoformat(),
    }


def jsonl_item(record: bytes) -> Any:
    """Each line is one item of `POST /v1/batch`, validated the same way."""
    try:
        return json.loads(_decode(record))
    except json.JSONDecodeError:
        raise InvalidInput("the line is not JSON") from None


def row_error(row: int, code: str, detail: str | None) -> dict[str, Any]:
    return {"row": row, "code": code, "detail": detail}


def _header_fields(line: bytes) -> list[str]:
    return next(csv.reader([_decode(line).removeprefix("﻿").rstrip("\r")]), [])


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8").rstrip("\r")
    except UnicodeDecodeError:
        raise InvalidInput("the record is not UTF-8") from None


def _handle_type(name: str) -> str | None:
    match = _HANDLE_COLUMN.match(name)
    if match is None or match["type"] not in _HANDLE_TYPES:
        return None
    return match["type"]


def _is_scope(name: str) -> bool:
    return name.endswith("_scope") and name.removesuffix("_scope") in _HANDLE_TYPES
