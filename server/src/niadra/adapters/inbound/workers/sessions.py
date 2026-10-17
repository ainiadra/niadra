"""Task handlers of the `sessions` module. The idle sweeper is not a task: the scheduler pool calls
`SessionService.sweep_idle` under a transaction advisory lock (5.4)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.CLOSE_SESSION: c.sessions.close_session}
