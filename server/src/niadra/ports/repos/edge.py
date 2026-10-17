"""Source keys mirrored from the control plane (cell database), and Idempotency-Key records (space
database) (5.1, 7.0)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.edge.limits import IdempotencyRecord


class SourceKeyRepository(Protocol):
    """Owned by the `edge` module; in the cell database, because a key names its space only once it is
    found (4.3)."""

    async def find_by_fast_hash(self, fast_hash: bytes) -> SourceKeyRow | None:
        """Cell-wide lookup: the key's space is not known before the key is found."""
        ...

    async def key_id_exists(self, key_id: str) -> bool: ...

    async def upsert_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> int:
        """Rows from the snapshot; a row only replaces an older version. Returns rows written."""
        ...

    async def key_versions(self, space_id: UUID, key_ids: Sequence[str]) -> dict[str, int]:
        """The stored version of each key the cell already has."""
        ...

    async def revoke(self, key_id: str) -> bool: ...

    async def source_key_ids(self, space_id: UUID, source_id: UUID) -> list[str]:
        """Every key of one source of the space, revoked or not, in id order."""
        ...

    async def delete_space(self, space_id: UUID) -> int:
        """Forgets every key of a deleted space; returns how many rows went."""
        ...


class IdempotencyRepository(Protocol):
    """Owned by the `edge` module; governance POSTs replayed within 24 h (7.0)."""

    async def get_idempotency(self, idem_key: str) -> IdempotencyRecord | None: ...

    async def save_idempotency(
        self, space_id: UUID, record: IdempotencyRecord, expired_before: datetime
    ) -> bool:
        """Inserts, or replaces a record older than `expired_before`; False when a live one exists."""
        ...
