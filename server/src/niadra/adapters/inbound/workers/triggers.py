"""Task handlers of the `triggers` module and the 5-minute sweep the scheduler calls (22.1).

`trigger_sweep(c, now)` is not a queued task: the scheduler runs it every 5 minutes under its
transaction advisory lock (4.5), one instance at a time. It is also where the other deadline work
of the operate modules happens, because all of it runs on the same cadence: declared actions whose
window ran out turn divergent (18.4), expired traits are re-derived (20.2), open items past their
deadline turn overdue, due deadlines request trigger evaluations, silent sources fire, and
dead deliveries older than 7 days are purged (22.5).
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.TRIGGER: c.triggers.evaluate}


async def trigger_sweep(c: Any, now: datetime) -> None:
    await c.objects.expire_declared(now)
    for space_id, written in (await c.objects.fire_all_timers(now)).items():
        await c.state.publish(space_id, written)
    await c.shared.fire_all_timers(now)
    await c.traits.expire_due(now)
    await c.memory.sweep_due(now)
    await c.triggers.sweep(now)
    await c.notify.purge_dead(now)
