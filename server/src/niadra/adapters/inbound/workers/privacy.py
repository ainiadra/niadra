"""Task handlers of the `privacy` module."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.ERASE: c.privacy.erase,
        TaskName.ERASE_COLD: c.privacy.erase_cold,
        TaskName.RETENTION_SWEEP: c.privacy.retention_sweep,
        TaskName.ARCHIVE_EVENTS: c.privacy.archive_events,
        TaskName.EXPORT_RUN: c.privacy.export_run,
    }
