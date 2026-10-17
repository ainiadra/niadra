"""Exports (22.3): the incremental rule of the continuous export and the records of each run."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "1"
ON_DEMAND = "on_demand"

SETTLE = timedelta(minutes=2)
"""A run exports up to the `change_seq` observed this long ago: with the 60 s cap on write transactions
and a `CACHE 1` sequence, no lower number can still commit after it (6, 22.3)."""

# The tables of 22.3 and the module that owns and serves each one.
EXPORT_TABLES: dict[str, str] = {
    "events": "ingest",
    "conversations": "ingest",
    "episodes": "memory",
    "facts": "memory",
    "fact_mentions": "memory",
    "open_items": "memory",
    "business_objects": "objects",
    "object_events": "objects",
    "agent_actions": "objects",
    "traits": "traits",
    "trait_evidence": "traits",
    "receipts": "audit",
    "context_use": "measure",
    "handles": "identity",
    "profile_membership": "identity",
    "profiles": "identity",
    "profile_links": "identity",
    "assertions": "identity",
    "merge_log": "identity",
    "agent_notes": "agent_memory",
}


class ExportStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExportRun:
    run_id: UUID
    schedule_id: str
    from_seq: int
    to_seq: int
    status: ExportStatus
    started_at: datetime
    manifest: dict[str, Any] | None = None
    finished_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TombstoneRecord:
    change_seq: int
    table: str
    key: dict[str, Any]
    deleted_at: datetime


def settled(run: ExportRun, now: datetime) -> bool:
    return now - run.started_at >= SETTLE


def file_path(prefix: str, table: str, run: ExportRun, part: int) -> str:
    base = f"{prefix.rstrip('/')}/" if prefix else ""
    return f"{base}{table}/{run.from_seq:020d}-{run.to_seq:020d}/part-{part:05d}.parquet"


def manifest_path(prefix: str, run: ExportRun) -> str:
    base = f"{prefix.rstrip('/')}/" if prefix else ""
    return f"{base}manifests/{run.run_id}.json"


def split_erased(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows to write and keys of rows skipped as `erased` (tombstoned or key destroyed); never ciphertext."""
    keep, erased = [], []
    for row in rows:
        if row.get("erased"):
            erased.append({k: v for k, v in row.items() if k != "erased"})
        else:
            keep.append({**row, "op": "upsert"})
    return keep, erased


def corrections(tables: set[str], later: list[TombstoneRecord]) -> list[TombstoneRecord]:
    """Tombstones committed while the run was exporting, for the tables it wrote: the correction file.

    A delete for a row the lake never received is a no-op, and the next run replays them again, so the
    file errs on the side of including a tombstone rather than matching keys row by row.
    """
    return [t for t in later if t.table in tables]


DIRECT_IDENTIFIERS: dict[str, tuple[str, ...]] = {"handles": ("value",)}
"""What a pseudonymized export leaves out: the value of a phone, an e-mail, a document or a system id. Its
keyed hash (`value_hmac`) stays, so rows still join across files and runs without naming the person."""


def pseudonymized(table: str, row: dict[str, Any]) -> dict[str, Any]:
    dropped = DIRECT_IDENTIFIERS.get(table, ())
    return {k: v for k, v in row.items() if k not in dropped} if dropped else row
