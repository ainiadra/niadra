"""Task handlers of the `turns` module: adding up the recorded turns a request acknowledged, and telling
the subscribed endpoints of the ones kept by their flags and of the data issues just opened (24.3.5), the
PII model's pass over stored arguments, and the notification feed's copy of each announced event."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.APPLY_TURNS: c.turns.apply_turns,
        TaskName.NOTIFY_FLAGGED_TURNS: c.turns.notify_flagged,
        TaskName.NOTIFY_DATA_ISSUE: c.data_issues.notify_opened,
        TaskName.APPEND_NOTIFICATION: c.notifications.append,
        TaskName.SCAN_TURN_ARGUMENTS: c.turns.scan_arguments,
    }
