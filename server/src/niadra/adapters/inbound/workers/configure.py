"""Task handlers of the `configure` module: the configuration assistant (22.4)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.ASSIST: c.configure.assist}
