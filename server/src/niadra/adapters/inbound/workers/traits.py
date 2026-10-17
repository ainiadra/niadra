"""Task handlers of the `traits` module."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {TaskName.DERIVE_TRAITS: c.traits.derive, TaskName.REDERIVE_TRAITS: c.traits.rederive_space}
