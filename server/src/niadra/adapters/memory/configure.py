"""In-memory `AssistRepository` for unit tests."""

from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.configure.model import AssistRun


class InMemoryAssistRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("configure")
        self._runs: dict[UUID, AssistRun] = self._s.setdefault("runs", {})

    async def add_run(self, run: AssistRun) -> None:
        self._runs[run.run_id] = run

    async def get_run(self, run_id: UUID) -> AssistRun | None:
        return self._runs.get(run_id)

    async def save_run(self, run: AssistRun) -> None:
        if run.run_id in self._runs:
            self._runs[run.run_id] = run

    async def count_since(self, at: datetime) -> int:
        return sum(1 for r in self._runs.values() if r.created_at >= at)

    async def list_runs(self, *, before_id: UUID | None, limit: int) -> list[AssistRun]:
        ids = sorted((i for i in self._runs if before_id is None or i < before_id), reverse=True)
        return [self._runs[i] for i in ids[:limit]]
