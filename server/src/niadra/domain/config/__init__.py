"""Pure rules of configuration distribution: canonical JSON, signatures, stored documents (4.7)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

SPACE_DOC = "space_config"
OBSERVED_DOC = "observed_types"
"""A space's observed types: written by the cell from the space's traffic, never signed by
the control plane, and merged under the declared registry of the snapshot's document."""
OBSERVED_VERSION = "observed_version"
"""The cell counter (`cell_state`) that moves on when any space's observed types changed, so every process
reads them again within one refresh."""


@dataclass(frozen=True, slots=True)
class StoredDocument:
    space_id: UUID
    doc_type: str
    content_hash: str
    version: int
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AppliedSnapshot:
    cell_id: str
    version: int
    spaces: int
    changed: tuple[tuple[UUID, str], ...] = ()
    """Spaces whose configuration document changed with this snapshot, with the new digest."""
