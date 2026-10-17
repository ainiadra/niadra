"""Task handlers of the `extract` module: the decision models' gate and the one LLM call per session
(5.7)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.GATE: c.extract.gate,
        TaskName.EXTRACT: c.extract.extract,
        TaskName.REEXTRACT: c.extract.reextract,
        TaskName.WITHHOLD_STORED: c.extract.withhold_stored,
    }
