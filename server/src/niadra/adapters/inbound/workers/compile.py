"""Task handlers of the `compile` module: compilation after memory changes, the reseed after a failover."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.COMPILE: c.compile.compile, TaskName.SEED_GEN: c.compile.seed_generation}
