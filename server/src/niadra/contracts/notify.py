"""Webhook deliveries as the governance API shows them (7.1, 7.5). Bodies are never listed."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import Model


class DeliveryView(Model):
    delivery_id: str
    endpoint_id: str
    event_type: str
    status: str
    attempts: int
    created_at: datetime
    next_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    last_status_code: int | None = None
    last_error: str | None = None


class DeliveryPage(Model):
    items: list[DeliveryView]
    next_cursor: str | None = None


class DeadTaskOut(Model):
    """A task that exhausted its attempts; ids only, never content (L2)."""

    task_id: UUID
    name: str
    profile_id: UUID | None = None


class DeadTasks(Model):
    items: list[DeadTaskOut]


class ReplayRequest(Model):
    task: str | None = Field(
        default=None, description="Only tasks of this name, e.g. `erase`; all when absent."
    )


class ReplayResult(Model):
    replayed: int
