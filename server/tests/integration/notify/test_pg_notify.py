"""`PgWebhookRepository` against a real PostgreSQL 17."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.notify.webhooks import Delivery, DeliveryStatus

NOW = datetime(2026, 9, 22, 18, tzinfo=UTC)


def _delivery(
    endpoint: str = "crm", minutes: float = 0, status: DeliveryStatus = DeliveryStatus.PENDING
) -> Delivery:
    return Delivery(
        uuid7(),
        endpoint,
        "trait.added",
        {"id": "x", "data": {"trait_id": "t"}},
        b"\x00" * 32,
        status,
        NOW + timedelta(minutes=minutes),
    )


async def test_deliveries_round_trip_and_attempts(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    d = _delivery()
    async with pg_uow(space) as uow:
        await uow.notify.add_delivery(d)
        await uow.notify.add_delivery(d)
    async with pg_uow(space) as uow:
        assert await uow.notify.get_delivery(d.delivery_id) == d
        failing = replace(
            d,
            status=DeliveryStatus.FAILING,
            attempts=1,
            next_attempt_at=NOW + timedelta(seconds=30),
            last_status_code=503,
            last_error="http_503",
        )
        await uow.notify.save_attempt(failing)
    async with pg_uow(space) as uow:
        assert await uow.notify.get_delivery(d.delivery_id) == failing
    async with pg_uow(uuid7()) as uow:
        assert await uow.notify.get_delivery(d.delivery_id) is None


async def test_listing_health_and_purge(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    old_dead = _delivery(minutes=-60 * 24 * 8, status=DeliveryStatus.DEAD)
    delivered = replace(
        _delivery(minutes=-60), status=DeliveryStatus.DELIVERED, delivered_at=NOW - timedelta(minutes=59)
    )
    failing = _delivery(minutes=-30, status=DeliveryStatus.FAILING)
    other = _delivery("support", minutes=-10)
    async with pg_uow(space) as uow:
        for d in (old_dead, delivered, failing, other):
            await uow.notify.add_delivery(d)
    async with pg_uow(space) as uow:
        crm = await uow.notify.list_deliveries(endpoint_id="crm")
        assert [d.delivery_id for d in crm] == [
            failing.delivery_id,
            delivered.delivery_id,
            old_dead.delivery_id,
        ]
        page = await uow.notify.list_deliveries(limit=1)
        rest = await uow.notify.list_deliveries(before=(page[0].created_at, page[0].delivery_id))
        assert page == [other]
        assert len(rest) == 3
        assert [d.delivery_id for d in await uow.notify.list_deliveries(status=DeliveryStatus.DEAD)] == [
            old_dead.delivery_id
        ]
        health = await uow.notify.endpoint_health("crm")
        assert health.last_success_at == delivered.delivered_at
        assert health.first_failure_at == failing.created_at
        quiet = await uow.notify.endpoint_health("support")
        assert quiet.last_success_at is None
        assert quiet.first_failure_at is None
        assert await uow.notify.purge_dead(NOW - timedelta(days=7)) == 1
    async with pg_uow(space) as uow:
        assert await uow.notify.get_delivery(old_dead.delivery_id) is None
