"""In-memory `ConfigRepository` for unit tests."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.config import StoredDocument


class InMemoryConfigRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("config")
        self._s.setdefault("snapshots", {})
        self._s.setdefault("documents", {})
        self._s.setdefault("state", {"cache_epoch": 1})

    async def active_version(self, cell_id: str, *, lock: bool = False) -> int | None:  # noqa: ARG002
        active = self._s["snapshots"].get((cell_id, "active"))
        return None if active is None else int(active["version"])

    async def save_snapshot(
        self, cell_id: str, version: int, manifest: dict[str, Any], signature: bytes
    ) -> None:
        snapshots = self._s["snapshots"]
        if (cell_id, "active") in snapshots:
            snapshots[(cell_id, "previous")] = snapshots[(cell_id, "active")]
        snapshots[(cell_id, "active")] = {"version": version, "manifest": manifest, "signature": signature}

    async def replace_documents(self, doc_type: str, documents: Sequence[StoredDocument]) -> None:
        kept = {k: v for k, v in self._s["documents"].items() if k[1] != doc_type}
        kept.update({(d.space_id, d.doc_type): d for d in documents})
        self._s["documents"] = kept

    async def documents(self, doc_type: str) -> list[StoredDocument]:
        return [
            d
            for (_, t), d in sorted(self._s["documents"].items(), key=lambda kv: str(kv[0][0]))
            if t == doc_type
        ]

    async def document(self, space_id: UUID, doc_type: str) -> StoredDocument | None:
        return self._s["documents"].get((space_id, doc_type))  # type: ignore[no-any-return]

    async def save_document(self, document: StoredDocument) -> None:
        self._s["documents"][(document.space_id, document.doc_type)] = document

    async def active_manifest(self, cell_id: str) -> dict[str, Any] | None:
        active = self._s["snapshots"].get((cell_id, "active"))
        return None if active is None else dict(active["manifest"])

    async def cache_epoch(self) -> int:
        return int(self._s["state"]["cache_epoch"])

    async def bump_cache_epoch(self) -> int:
        self._s["state"]["cache_epoch"] += 1
        return int(self._s["state"]["cache_epoch"])

    async def state(self, key: str) -> int | None:
        found = self._s["state"].get(key)
        return None if found is None else int(found)

    async def set_state(self, key: str, value: int) -> None:
        self._s["state"][key] = value
