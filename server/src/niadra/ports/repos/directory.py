"""The space directory (`space_databases`) and the deletion receipts on their way to the control plane,
both in the cell database."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.spaces.directory import SpaceDatabase


class SpaceDirectoryRepository(Protocol):
    """Owned by the `directory` module (the provisioner, the setup runner, the restore tooling)."""

    async def entries(self) -> list[SpaceDatabase]:
        """Every row, deleted ones included, in database-name order."""
        ...

    async def get(self, space_id: UUID, *, lock: bool = False) -> SpaceDatabase | None: ...

    async def insert(self, entry: SpaceDatabase, now: datetime) -> bool:
        """False when the space already has a row."""
        ...

    async def save(self, entry: SpaceDatabase, now: datetime) -> None:
        """Writes the row's state (status, schema version, detail, failures, times)."""
        ...

    async def draft_receipt(self, space_id: UUID, receipt: dict[str, Any], now: datetime) -> None:
        """Keeps a deletion receipt until the control plane acknowledges it; idempotent per space."""
        ...

    async def unsent_receipts(self, limit: int) -> list[tuple[UUID, dict[str, Any]]]: ...

    async def mark_receipt_sent(self, space_id: UUID, now: datetime) -> None: ...

    async def pending_receipts(self) -> int: ...

    async def receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
        """The drafted receipts of these spaces, sent or not."""
        ...
