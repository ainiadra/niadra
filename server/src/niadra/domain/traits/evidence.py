"""Evidence pointers shared by traits and trigger firings.

Evidence always names its origin handle: items are pinned to the handle that produced them (L6),
so after an unmerge the evidence tells which half of the profile still supports a trait or a firing.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """`kind` is episode, event, action or open_item for traits; firings also point at a handle,
    a trait or a source."""

    kind: str
    id: UUID
    origin_handle_id: UUID
    at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": str(self.id), "origin_handle_id": str(self.origin_handle_id)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "EvidenceRef":
        return cls(kind=data["kind"], id=UUID(data["id"]), origin_handle_id=UUID(data["origin_handle_id"]))


def evidence_handles(evidence: Iterable[EvidenceRef]) -> frozenset[UUID]:
    return frozenset(e.origin_handle_id for e in evidence)


def evidence_keys(evidence: Iterable[EvidenceRef]) -> frozenset[tuple[str, UUID]]:
    return frozenset((e.kind, e.id) for e in evidence)
