"""Task handlers of the `signals` module (24.3.2), of the measurement of outcomes (24.7), and the release
of a legal hold its erasures wait on (24.5.1)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    async def purge(task: Task) -> None:
        # What an erasure leaves outside the database: the signals', and the context budget's counters.
        await c.signals.purge(task)
        await c.budget.purge(task)

    return {
        TaskName.APPLY_INTERACTIONS: c.signals.apply_interactions,
        TaskName.WITHDRAW_SIGNALS: c.signals.withdraw,
        TaskName.PURGE_SIGNALS: purge,
        TaskName.ATTRIBUTE_OUTCOMES: c.outcomes.attribute,
        TaskName.RELEASE_HELD: c.privacy.release_held,
    }
