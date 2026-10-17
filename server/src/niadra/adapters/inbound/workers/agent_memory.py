"""Task handlers of the `agent_memory` module: distillation of a proposed note (10, C.5)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.DISTILL_NOTE: c.agent_memory.distill_task}
