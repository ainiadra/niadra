"""In-memory `WebhookRepository` for unit tests."""

from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.notify.webhooks import Delivery, DeliveryStatus, EndpointHealth, erased_by, erasure_needles
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope

_FAILED = (DeliveryStatus.FAILING, DeliveryStatus.DEAD)


class InMemoryWebhookRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("notify")
        self._deliveries: dict[UUID, Delivery] = self._s.setdefault("deliveries", {})

    async def add_delivery(self, delivery: Delivery) -> None:
        self._deliveries.setdefault(delivery.delivery_id, delivery)

    async def get_delivery(self, delivery_id: UUID) -> Delivery | None:
        return self._deliveries.get(delivery_id)

    async def save_attempt(self, delivery: Delivery) -> None:
        self._deliveries[delivery.delivery_id] = delivery

    async def list_deliveries(
        self,
        *,
        status: DeliveryStatus | None = None,
        endpoint_id: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        found = [
            d
            for d in self._deliveries.values()
            if (status is None or d.status is status)
            and (endpoint_id is None or d.endpoint_id == endpoint_id)
            and (before is None or (d.created_at, d.delivery_id) < before)
        ]
        return sorted(found, key=lambda d: (d.created_at, d.delivery_id), reverse=True)[:limit]

    async def endpoint_health(self, endpoint_id: str) -> EndpointHealth:
        mine = [d for d in self._deliveries.values() if d.endpoint_id == endpoint_id]
        last_success = max((d.delivered_at for d in mine if d.delivered_at), default=None)
        failures = [
            d.created_at
            for d in mine
            if d.status in _FAILED and (last_success is None or d.created_at > last_success)
        ]
        return EndpointHealth(last_success, min(failures, default=None))

    async def purge_dead(self, before: datetime) -> int:
        doomed = [
            d.delivery_id
            for d in self._deliveries.values()
            if d.status is DeliveryStatus.DEAD and d.created_at < before
        ]
        for delivery_id in doomed:
            del self._deliveries[delivery_id]
        return len(doomed)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        needles = erasure_needles(scope)
        if not needles:
            return []
        doomed = sorted(d.delivery_id for d in self._deliveries.values() if erased_by(d.body, needles))[
            :limit
        ]
        for delivery_id in doomed:
            del self._deliveries[delivery_id]
        return [ErasedRow("webhook_deliveries", {"delivery_id": str(d)}) for d in doomed]

    async def purge_before(self, before: datetime) -> int:
        doomed = [d.delivery_id for d in self._deliveries.values() if d.created_at < before]
        for delivery_id in doomed:
            del self._deliveries[delivery_id]
        return len(doomed)
