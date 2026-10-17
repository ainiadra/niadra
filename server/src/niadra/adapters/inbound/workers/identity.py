"""Task handlers of the `identity` module: merges and splits under the profiles' lease, and the
offline suggester (5.3)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.IDENTITY_APPLY: c.identity.identity_apply,
        TaskName.IDENTITY_SUGGEST: c.identity.identity_suggest,
    }
