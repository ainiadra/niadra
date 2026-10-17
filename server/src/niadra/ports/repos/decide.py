"""Every automatic decision with input hash, model, probability and threshold (5.6, L14)."""

from collections.abc import Sequence
from typing import Protocol

from niadra.domain.decide import DecisionRecord, DecisionTask


class DecisionRepository(Protocol):
    """Owned by the `decide` module; no other module reads or writes these tables (4.3)."""

    async def record(self, decisions: Sequence[DecisionRecord]) -> None: ...

    async def answered(self, task: DecisionTask, model: str, subject_refs: Sequence[str]) -> set[str]:
        """The subjects among `subject_refs` that `model` has answered for `task`."""
        ...
