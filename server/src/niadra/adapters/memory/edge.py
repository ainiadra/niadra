"""In-memory `SourceKeyRepository` and `IdempotencyRepository` for unit tests."""

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.edge.limits import IdempotencyRecord


class InMemorySourceKeyRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("edge")
        self._keys: dict[str, SourceKeyRow] = self._s.setdefault("keys", {})

    async def find_by_fast_hash(self, fast_hash: bytes) -> SourceKeyRow | None:
        return next((row for row in self._keys.values() if row.fast_hash == fast_hash), None)

    async def key_id_exists(self, key_id: str) -> bool:
        return key_id in self._keys

    async def upsert_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> int:
        written = 0
        for row in rows:
            current = self._keys.get(row.key_id)
            if current is None or current.version < row.version:
                self._keys[row.key_id] = dataclasses.replace(row, space_id=space_id)
                written += 1
        return written

    async def key_versions(self, space_id: UUID, key_ids: Sequence[str]) -> dict[str, int]:
        return {k: row.version for k in key_ids if (row := self._keys.get(k)) and row.space_id == space_id}

    async def revoke(self, key_id: str) -> bool:
        row = self._keys.get(key_id)
        if row is None or row.revoked:
            return False
        self._keys[key_id] = dataclasses.replace(row, status="revoked")
        return True

    async def source_key_ids(self, space_id: UUID, source_id: UUID) -> list[str]:
        return sorted(
            k for k, row in self._keys.items() if row.space_id == space_id and row.source_id == source_id
        )

    async def delete_space(self, space_id: UUID) -> int:
        doomed = [k for k, row in self._keys.items() if row.space_id == space_id]
        for key_id in doomed:
            del self._keys[key_id]
        return len(doomed)


class InMemoryIdempotencyRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._idem: dict[str, tuple[UUID, IdempotencyRecord]] = store.module("edge").setdefault(
            "idempotency", {}
        )

    async def get_idempotency(self, idem_key: str) -> IdempotencyRecord | None:
        found = self._idem.get(idem_key)
        return found[1] if found else None

    async def save_idempotency(
        self, space_id: UUID, record: IdempotencyRecord, expired_before: datetime
    ) -> bool:
        current = self._idem.get(record.idem_key)
        if current is not None and current[1].created_at >= expired_before:
            return False
        self._idem[record.idem_key] = (space_id, record)
        return True
