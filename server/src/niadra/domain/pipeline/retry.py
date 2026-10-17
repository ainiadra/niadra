"""Attempts and backoff for tasks that fail (5.5).

The broker has no per-message delay, so a retry is a row in `scheduled_tasks` that the relay
publishes when due. The attempt number travels in the payload under a reserved key, because
the task must survive that round trip through the database; handlers never see it.
"""

import dataclasses

from niadra.domain.common.errors import NiadraError
from niadra.domain.tasks import Task

ATTEMPT_KEY = "_attempt"
BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 900.0


def attempt_of(task: Task) -> int:
    return int(task.payload.get(ATTEMPT_KEY, 1))


def with_attempt(task: Task, attempt: int) -> Task:
    return dataclasses.replace(task, payload={**task.payload, ATTEMPT_KEY: attempt})


def without_attempt(task: Task) -> Task:
    if ATTEMPT_KEY not in task.payload:
        return task
    return dataclasses.replace(task, payload={k: v for k, v in task.payload.items() if k != ATTEMPT_KEY})


def retry_delay_s(
    attempt: int, jitter: float = 0.0, base_s: float = BACKOFF_BASE_S, cap_s: float = BACKOFF_CAP_S
) -> float:
    """Exponential backoff after failed attempt number `attempt`; `jitter` in [0, 1) spreads up to +20%."""
    delay = min(cap_s, base_s * 2.0 ** (attempt - 1))
    return delay * (1 + 0.2 * jitter)


def error_summary(exc: BaseException) -> str:
    # Messages of third-party exceptions can quote customer data, so only our own errors,
    # whose details are written without personal data, keep their text.
    if isinstance(exc, NiadraError):
        return f"{exc.code}: {exc.detail}"[:500]
    return type(exc).__name__
