"""Tasks of the `insights` module: the nightly rollup behind the overview resource (5.21)."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.ROLLUP: c.insights.rollup}
