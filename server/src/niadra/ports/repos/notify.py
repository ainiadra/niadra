"""Outbound webhook deliveries (7.5)."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.notify.webhooks import Delivery, DeliveryStatus, EndpointHealth
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class WebhookRepository(Protocol):
    """Owned by the `notify` module; no other module reads or writes these tables (4.3)."""

    async def add_delivery(self, delivery: Delivery) -> None: ...

    async def get_delivery(self, delivery_id: UUID) -> Delivery | None: ...

    async def save_attempt(self, delivery: Delivery) -> None:
        """Stores status, attempts, next attempt, last response and delivery time."""
        ...

    async def list_deliveries(
        self,
        *,
        status: DeliveryStatus | None = None,
        endpoint_id: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        """Newest first; `before` is the (created_at, delivery_id) of the last row of the previous page."""
        ...

    async def endpoint_health(self, endpoint_id: str) -> EndpointHealth: ...

    async def purge_dead(self, before: datetime) -> int:
        """Deletes dead deliveries created before `before` (kept 7 days, 22.5)."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): deletes at most `limit` rows of the target, idempotent."""
        ...

    async def purge_before(self, before: datetime) -> int:
        """Retention (22.5): every delivery created before `before`, whatever its status."""
        ...
