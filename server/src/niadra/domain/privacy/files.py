"""File formats of exports and of the cold archive: Parquet and zipped JSON lines."""

import dataclasses
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import IO, Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq


def exportable(value: Any) -> Any:
    """A value Parquet and JSON can carry: ids as text, nested structures as JSON text, times as they are."""
    if value is None or isinstance(value, bool | int | float | str | bytes | datetime | date):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple) and all(isinstance(v, str | UUID) for v in value):
        return [str(v) for v in value]
    return json.dumps(value, default=_json_default, sort_keys=True, ensure_ascii=False)


def to_parquet(rows: Sequence[Mapping[str, Any]], metadata: Mapping[str, str] | None = None) -> bytes:
    table = pa.Table.from_pylist([{k: exportable(v) for k, v in row.items()} for row in rows])
    if metadata:
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def from_parquet(data: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = pq.read_table(io.BytesIO(data)).to_pylist()
    return rows


def json_line(record: Any) -> str:
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        record = dataclasses.asdict(record)
    return json.dumps(record, default=_json_default, sort_keys=True, ensure_ascii=False)


class _Chunks(io.RawIOBase):
    """A write-only, unseekable sink: zipfile then streams each entry with a data descriptor."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self.buffer += data
        return len(data)


class JsonLinesZip:
    """Zipped JSON lines written one section at a time, whose bytes leave through `drain` as they are made,
    so a package of any size never sits whole in memory."""

    def __init__(self) -> None:
        self._out = _Chunks()
        self._zip = zipfile.ZipFile(self._out, "w", compression=zipfile.ZIP_DEFLATED)
        self._entry: IO[bytes] | None = None
        self._name = ""
        self._digest = hashlib.sha256()
        self.digests: dict[str, str] = {}

    @property
    def pending(self) -> int:
        return len(self._out.buffer)

    def section(self, name: str) -> None:
        self._close_section()
        self._name = f"{name}.jsonl"
        self._digest = hashlib.sha256()
        # zip64 from the start: an entry streamed without its size may pass 4 GiB.
        self._entry = self._zip.open(self._name, "w", force_zip64=True)

    def write(self, records: Sequence[Any]) -> None:
        assert self._entry is not None, "open a section first"
        body = "".join(json_line(r) + "\n" for r in records).encode()
        self._digest.update(body)
        self._entry.write(body)

    def drain(self) -> bytes:
        data = bytes(self._out.buffer)
        self._out.buffer.clear()
        return data

    def finish(self) -> bytes:
        """Closes the archive and returns the bytes not yet drained."""
        self._close_section()
        self._zip.close()
        return self.drain()

    def _close_section(self) -> None:
        if self._entry is not None:
            self._entry.close()
            self.digests[self._name] = self._digest.hexdigest()
            self._entry = None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID | Enum):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
