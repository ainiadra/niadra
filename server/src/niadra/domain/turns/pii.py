"""The model pass over stored tool arguments (24.3.5): after the edge masked a stored record's text leaves by
the rules events get, the small PII model reads the text leaves of its tools' arguments, and a record where
it finds something is written again with those values replaced. Only `stored` records have values here to
read, and only the arguments: what the company's agent sent its tools, where a customer's words land. Pure.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from niadra.domain.extract.redaction import Redactor, Span
from niadra.domain.turns.digest import digest

SCAN_BYTES = 64 * 1024
"""The arguments one scan task reads at most, by their recorded sizes: a few requests of the models pod,
queued behind the realtime work."""
MIN_LEAF = 3
"""Shorter text leaves (codes, flags) are never read: no value of a person fits in them."""


@dataclass(frozen=True, slots=True)
class Scan:
    turn_id: str
    conversation: str
    size: int


def to_scan(frame: Mapping[str, Any], conversation: bytes) -> Scan | None:
    """A stored record with tool arguments, by their recorded size; None for anything else."""
    if frame.get("content_mode") != "stored":
        return None
    blobs = frame.get("blobs") or {}
    keys = {c["args"] for c in frame.get("calls") or () if c.get("kind") == "tool" and c.get("args")}
    size = sum(int(blobs.get(k, {}).get("size") or 0) for k in keys)
    return Scan(frame["turn_id"], conversation.hex(), size) if keys else None


def batches(scans: Sequence[Scan]) -> list[list[Scan]]:
    """The group's records in tasks of at most `SCAN_BYTES` of arguments; a larger record goes alone."""
    out: list[list[Scan]] = []
    size = 0
    for scan in scans:
        if not out or size + scan.size > SCAN_BYTES:
            out.append([])
            size = 0
        out[-1].append(scan)
        size += scan.size
    return out


def argument_leaves(record: Mapping[str, Any]) -> list[tuple[str, tuple[Any, ...], str]]:
    """(blob, path, text) of every text leaf the record's tool arguments hold."""
    blobs = record.get("blobs") or {}
    keys = sorted({c["args"] for c in record.get("calls") or () if c.get("kind") == "tool" and c.get("args")})
    return [
        (key, path, text)
        for key in keys
        if "content" in blobs.get(key, {})
        for path, text in _leaves(blobs[key]["content"], ())
        if len(text) >= MIN_LEAF
    ]


def redacted(
    record: dict[str, Any],
    leaves: Sequence[tuple[str, tuple[Any, ...], str]],
    spans: Sequence[Sequence[Span]],
) -> bool:
    """Replaces what the model found with placeholders (`[person_1]`), each blob taking the digest of what it
    keeps (Turn Record 6.2.3); False when it found nothing, and the record stays as it was."""
    redactor = Redactor()
    changed: set[str] = set()
    for (key, path, text), found in zip(leaves, spans, strict=True):
        if found:
            _put(record["blobs"][key], ("content", *path), redactor.redact(text, found))
            changed.add(key)
    for key in changed:
        blob = record["blobs"][key]
        blob["sha256"], blob["size"] = digest(blob["content"])
    return bool(changed)


def _leaves(value: Any, path: tuple[Any, ...]) -> Iterator[tuple[tuple[Any, ...], str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _leaves(v, (*path, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, (*path, i))


def _put(node: Any, path: tuple[Any, ...], value: str) -> None:
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
