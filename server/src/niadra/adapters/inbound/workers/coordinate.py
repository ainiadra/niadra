"""Task handlers of the `coordinate` module (24.3.4)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.OBSERVE_OWNERSHIP: c.coordinate.observe,
        TaskName.COORDINATION_SHADOW: c.coordinate.run_shadow,
        TaskName.CONTACT_WINDOW: c.coordinate.stated_window,
    }
