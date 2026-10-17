"""Task handlers of the `memory` module: reconcile, close items, embed, nightly job (5.8, 5.9)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.RECONCILE: c.memory.reconcile,
        TaskName.CLOSE_ITEMS: c.memory.close_items,
        TaskName.EMBED: c.memory.embed,
        TaskName.CONSOLIDATE: c.memory.consolidate,
    }
