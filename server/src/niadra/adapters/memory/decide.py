"""In-memory `DecisionRepository` for unit tests."""

from collections.abc import Sequence

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.decide import DecisionRecord, DecisionTask


class InMemoryDecisionRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("decide")
        self._s.setdefault("decisions", {})

    @property
    def decisions(self) -> dict[object, DecisionRecord]:
        return self._s["decisions"]  # type: ignore[no-any-return]

    async def record(self, decisions: Sequence[DecisionRecord]) -> None:
        for d in decisions:
            self.decisions.setdefault(d.decision_id, d)

    async def answered(self, task: DecisionTask, model: str, subject_refs: Sequence[str]) -> set[str]:
        wanted = set(subject_refs)
        return {
            d.subject_ref
            for d in self.decisions.values()
            if d.task == task and d.model == model and d.subject_ref in wanted
        }
