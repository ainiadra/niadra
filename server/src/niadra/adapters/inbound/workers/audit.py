"""Task handlers of the `audit` module; the receipt writer loop runs apart, as `AuditService.run_writer`."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    return {
        TaskName.AUDIT_ANCHOR: c.audit.audit_anchor,
        TaskName.RECORD_RECEIPTS: c.audit.record_receipts,
    }
