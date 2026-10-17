"""Task handlers of the `measure` module (19.3): the measurement and its transfer phase."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.MEASURE: c.measure.measure, TaskName.MEASURE_TRANSFER: c.measure.measure_transfer}
