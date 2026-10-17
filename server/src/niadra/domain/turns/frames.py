"""A turn's frame (24.3.5): the record as the edge stored it, without the recorded values. The frame is what
the intake carries, what the workers add up and what other modules take; the values stay in the object."""

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from niadra.domain.turns.digest import canonical_json

_ERRORS = frozenset({"error", "timeout"})


def frame_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The record without any blob's content: pointers, digests and sizes stay."""
    blobs = {
        key: {k: v for k, v in blob.items() if k != "content"}
        for key, blob in record.get("blobs", {}).items()
    }
    return {**record, "blobs": blobs}


def derived_flags(frame: Mapping[str, Any]) -> set[str]:
    """The flags the record implies whether or not the producer set them: a failed call is an error, a
    recording that failed is incomplete."""
    flags = set(frame.get("flags") or ())
    if any(call.get("status", "ok") in _ERRORS for call in frame.get("calls") or ()):
        flags.add("error")
    if frame.get("completeness") == "incomplete":
        flags.add("incomplete")
    return flags


def part_digest(frame: Mapping[str, Any]) -> str:
    """Names one export's part of a turn: the same spans sent again are the same part."""
    return hashlib.sha256(canonical_json(dict(frame))).hexdigest()[:32]


def merge_part(
    existing: Mapping[str, Any], part: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """A turn built from spans of one trace that arrived in more than one export (OpenTelemetry conventions,
    6): the calls and interactions the record lacks join it, with their blobs, and its times widen. Returns
    the merged record and what the part added, or None when it added nothing."""
    known = {call["call_id"] for call in existing.get("calls") or ()}
    calls = [call for call in part.get("calls") or () if call["call_id"] not in known]
    interactions = [
        i for i in part.get("interactions") or () if i not in (existing.get("interactions") or ())
    ]
    if not calls and not interactions:
        return None
    used = {call.get(field) for call in calls for field in ("args", "result_model", "result_ui")}
    blobs = {k: v for k, v in (part.get("blobs") or {}).items() if k in used}
    merged = dict(existing)
    merged["calls"] = [*(existing.get("calls") or ()), *calls]
    merged["interactions"] = [*(existing.get("interactions") or ()), *interactions]
    merged["blobs"] = {**(existing.get("blobs") or {}), **blobs}
    started = min(existing["started_at"], part["started_at"], key=_instant)
    ended = max(existing.get("ended_at") or started, part.get("ended_at") or started, key=_instant)
    merged.update(started_at=started, ended_at=ended)
    merged["latency_ms"] = round((_instant(ended) - _instant(started)).total_seconds() * 1000)
    if merged["interactions"] and merged.get("fidelity") == "bronze":
        merged["fidelity"] = "silver"
    return merged, part_of(existing, {**part, "calls": calls, "interactions": interactions, "blobs": blobs})


def part_of(existing: Mapping[str, Any], added: Mapping[str, Any]) -> dict[str, Any]:
    """What a part added, under the turn's own agent, build and start: its counts join the turn's row."""
    return {
        **added,
        "agent": existing["agent"],
        "build": existing.get("build") or {},
        "started_at": existing["started_at"],
    }


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)
