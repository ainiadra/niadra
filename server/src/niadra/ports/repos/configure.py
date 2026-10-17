"""Configuration assistant runs (22.4)."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.configure.model import AssistRun


class AssistRepository(Protocol):
    """Owned by the `configure` module; no other module reads or writes these tables (4.3)."""

    async def add_run(self, run: AssistRun) -> None: ...

    async def get_run(self, run_id: UUID) -> AssistRun | None: ...

    async def save_run(self, run: AssistRun) -> None:
        """Replaces status, samples, diff hash and proposal of an existing run."""
        ...

    async def count_since(self, at: datetime) -> int: ...

    async def list_runs(self, *, before_id: UUID | None, limit: int) -> list[AssistRun]:
        """Newest first by `run_id` (UUIDv7, so creation order)."""
        ...
