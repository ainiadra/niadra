"""In-memory `SpaceDirectoryRepository` for unit tests."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.spaces.directory import SpaceDatabase


class InMemorySpaceDirectory:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("directory")
        self._rows: dict[UUID, SpaceDatabase] = self._s.setdefault("rows", {})
        self._receipts: dict[UUID, dict[str, Any]] = self._s.setdefault("receipts", {})

    async def entries(self) -> list[SpaceDatabase]:
        return sorted(self._rows.values(), key=lambda r: r.db_name)

    async def get(self, space_id: UUID, *, lock: bool = False) -> SpaceDatabase | None:  # noqa: ARG002
        return self._rows.get(space_id)

    async def insert(self, entry: SpaceDatabase, now: datetime) -> bool:  # noqa: ARG002
        if entry.space_id in self._rows:
            return False
        self._rows[entry.space_id] = entry
        return True

    async def save(self, entry: SpaceDatabase, now: datetime) -> None:  # noqa: ARG002
        if entry.space_id in self._rows:
            self._rows[entry.space_id] = entry

    async def draft_receipt(self, space_id: UUID, receipt: dict[str, Any], now: datetime) -> None:
        self._receipts.setdefault(space_id, {"receipt": receipt, "created_at": now, "sent_at": None})

    async def unsent_receipts(self, limit: int) -> list[tuple[UUID, dict[str, Any]]]:
        waiting = [(k, v) for k, v in self._receipts.items() if v["sent_at"] is None]
        waiting.sort(key=lambda kv: kv[1]["created_at"])
        return [(space_id, dict(v["receipt"])) for space_id, v in waiting[:limit]]

    async def mark_receipt_sent(self, space_id: UUID, now: datetime) -> None:
        if space_id in self._receipts:
            self._receipts[space_id]["sent_at"] = now

    async def pending_receipts(self) -> int:
        return sum(1 for v in self._receipts.values() if v["sent_at"] is None)

    async def receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
        return {s: dict(self._receipts[s]["receipt"]) for s in space_ids if s in self._receipts}
