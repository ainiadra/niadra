"""Deleting a space: everything it leaves in a cell, and the receipt the control plane keeps.

A deleted space's database is dropped and its data keys destroyed, which makes any backup copy still
held by the instance unreadable; its objects, dumps, secrets and cache keys go too. The receipt says
what was deleted, when, and when the last instance snapshot that may still hold ciphertext expires.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.domain.config.signing import canonical_json


def space_object_prefixes(space_id: UUID) -> tuple[str, ...]:
    """Every object-storage prefix that holds one space's objects. A new kind of object belongs here,
    or a deleted space leaves it behind (a unit test checks the object keys the code writes)."""
    return (
        f"raw/{space_id}/",
        f"media/{space_id}/",
        f"imports/{space_id}/",
        f"audit/{space_id}/",
        f"archive/events/{space_id}/",
        f"exports/{space_id}/",
        f"privacy/erasures/{space_id}/",
        # Turn records put the tier first, so each tier is one prefix.
        f"turns/short/{space_id}/",
        f"turns/kept/{space_id}/",
        # A scenario's turns and a legal hold's copies, which no bucket rule expires.
        f"turns/scenario/{space_id}/",
        f"turns/held/{space_id}/",
        # Raw interactions of subject signals, standalone interaction batches waiting for their worker, and
        # the daily aggregates of measurement and of unmet demand, and BI files waiting for a reconciliation.
        f"measure/raw/{space_id}/",
        f"measure/intake/{space_id}/",
        f"measure/daily/{space_id}/",
        f"measure/demand/{space_id}/",
        f"measure/bi/{space_id}/",
    )


@dataclass(frozen=True, slots=True)
class DeletionReceipt:
    space_id: UUID
    cell_id: str
    db_name: str
    dropped_at: datetime
    dek_versions_destroyed: Sequence[int]
    objects_deleted: int
    backups_deleted: int
    secrets_deleted: int
    instance_snapshot_expiry: datetime

    def document(self) -> dict[str, Any]:
        """The receipt as sent and stored: JSON types only, UTC times, stable field names."""
        return {
            "space_id": str(self.space_id),
            "cell_id": self.cell_id,
            "db_name": self.db_name,
            "dropped_at": _utc(self.dropped_at),
            "dek_versions_destroyed": sorted(self.dek_versions_destroyed),
            "objects_deleted": self.objects_deleted,
            "backups_deleted": self.backups_deleted,
            "secrets_deleted": self.secrets_deleted,
            "instance_snapshot_expiry": _utc(self.instance_snapshot_expiry),
        }


RECEIPT_FIELDS = frozenset(
    {
        "space_id",
        "cell_id",
        "db_name",
        "dropped_at",
        "dek_versions_destroyed",
        "objects_deleted",
        "backups_deleted",
        "secrets_deleted",
        "instance_snapshot_expiry",
    }
)


def receipt_hash(document: dict[str, Any]) -> bytes:
    """SHA-256 over the canonical JSON: the cell and the control plane compute the same bytes."""
    return hashlib.sha256(canonical_json(document)).digest()


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()
