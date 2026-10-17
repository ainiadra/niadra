"""Applied snapshot, space documents and cell state such as the cache epoch (4.7)."""

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.config import StoredDocument


class ConfigRepository(Protocol):
    """Owned by the `config` module; no other module reads or writes these tables (4.3)."""

    async def active_version(self, cell_id: str, *, lock: bool = False) -> int | None:
        """`lock` holds the active snapshot row until commit, serializing concurrent appliers."""
        ...

    async def save_snapshot(
        self, cell_id: str, version: int, manifest: dict[str, Any], signature: bytes
    ) -> None:
        """Makes this snapshot active and keeps the one it replaces as `previous`."""
        ...

    async def replace_documents(self, doc_type: str, documents: Sequence[StoredDocument]) -> None:
        """Upserts `documents` and deletes the other documents of `doc_type` (spaces that left the cell)."""
        ...

    async def documents(self, doc_type: str) -> list[StoredDocument]: ...

    async def document(self, space_id: UUID, doc_type: str) -> StoredDocument | None: ...

    async def save_document(self, document: StoredDocument) -> None:
        """Upserts one space's document of its type, leaving every other space's as it is."""
        ...

    async def active_manifest(self, cell_id: str) -> dict[str, Any] | None:
        """The manifest of the active snapshot, as signed."""
        ...

    async def cache_epoch(self) -> int: ...

    async def bump_cache_epoch(self) -> int:
        """After a cache failover: every derived-state key changes, so stale entries vanish (06, 3)."""
        ...

    async def state(self, key: str) -> int | None:
        """A cell-wide counter or cursor by name (`cell_state`); None when never set."""
        ...

    async def set_state(self, key: str, value: int) -> None: ...
