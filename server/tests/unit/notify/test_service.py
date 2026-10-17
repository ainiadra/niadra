"""`enqueue_event` and `NotifyService`: signing, retries through `scheduled_tasks`, dead letters (7.5)."""

import base64
from collections.abc import Mapping
from datetime import timedelta
from uuid import UUID

import orjson
import pytest

from niadra.adapters.memory.fakes import DictSecrets, RecordingEgress
from niadra.app.notify import NotifyService, enqueue_event
from niadra.config.models import WebhookEndpoint
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.notify.webhooks import Delivery, DeliveryStatus, WebhookSubject, body_hash, encode_body
from niadra.domain.tasks import Task, TaskName
from niadra.ports.infra import HttpResponse
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell
from tests.unit.notify.test_webhooks import reference_verify
from tests.unit.objects.fakes import CRM, CRM_REF, CRM_SECRET, T0, World, config_with

NEW_SECRET = "whsec_" + base64.b64encode(b"rotated-key").decode()
SUPPORT_REF = f"spaces/{SPACE_ID}/webhook/support"
SUPPORT = WebhookEndpoint(
    endpoint_id="support",
    url="https://support.example/hooks",
    source_id=WHATSAPP_SOURCE,
    event_types=["memory.updated"],
    secret_refs=[SUPPORT_REF],
)
SUBJECT = WebhookSubject("pse_123", "person")


class BrokenEgress:
    async def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        raise TimeoutError


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(config_with(webhook_endpoints=[CRM, SUPPORT]))
    cell.clock.at = T0
    cell.secrets = DictSecrets({CRM_REF: CRM_SECRET, SUPPORT_REF: NEW_SECRET})
    return World(cell)


def service(world: World, egress: object | None = None) -> NotifyService:
    cell = world.cell
    return NotifyService(world.uow, egress or cell.egress, cell.secrets, cell.clock, cell.config)  # type: ignore[arg-type]


async def announce(world: World, event_type: str = "trait.added") -> UUID:
    async with world.uow(SPACE_ID) as uow:
        [delivery_id] = await enqueue_event(
            uow, world.cell.config.space(SPACE_ID), event_type, SUBJECT, {"trait_id": "t1"}, at=T0
        )
    return delivery_id


async def deliver(
    world: World, delivery_id: UUID, egress: object | None = None, **payload: object
) -> Delivery:
    task = Task(TaskName.DELIVER_WEBHOOK, SPACE_ID, {"delivery_id": str(delivery_id), **payload})
    await service(world, egress).deliver_webhook(task)
    async with world.cell.uow(SPACE_ID) as uow:
        found = await uow.notify.get_delivery(delivery_id)
    assert found
    return found


async def test_enqueue_creates_one_delivery_per_subscribed_endpoint(world: World) -> None:
    await announce(world)
    async with world.uow(SPACE_ID) as uow:
        ids = await enqueue_event(
            uow, world.cell.config.space(SPACE_ID), "memory.updated", SUBJECT, {"profile_id": "p"}
        )
        rows = await uow.notify.list_deliveries()
    assert len(ids) == 2
    assert sorted(d.endpoint_id for d in rows if d.event_type == "memory.updated") == [
        "crm",
        "support",
    ]
    body = rows[0].body
    assert body["tenant"] == str(TENANT_ID)
    assert body["subject"] == {"pseudonym": "pse_123", "kind": "person"}
    assert rows[0].body_hash == body_hash(encode_body(body))
    assert all(t.payload.keys() == {"delivery_id"} for t in world.pipeline.take(TaskName.DELIVER_WEBHOOK))
    async with world.uow(SPACE_ID) as uow:
        with pytest.raises(InvalidInput):
            await enqueue_event(
                uow, world.cell.config.space(SPACE_ID), "trait.added", SUBJECT, {"text": "hello"}
            )


async def test_a_delivery_is_signed_with_standard_webhooks(world: World) -> None:
    delivery_id = await announce(world)
    delivered = await deliver(world, delivery_id)
    [(url, body, headers)] = world.cell.egress.sent
    assert url == CRM.url
    assert headers["webhook-id"] == str(delivery_id)
    assert reference_verify(CRM_SECRET, headers, body, int(T0.timestamp()))
    assert delivered.status is DeliveryStatus.DELIVERED
    assert delivered.attempts == 1
    assert delivered.delivered_at == T0


async def test_both_secrets_sign_during_rotation(world: World, cell: Cell) -> None:
    rotating = CRM.model_copy(update={"secret_refs": [CRM_REF, SUPPORT_REF]})
    cell.config.put(config_with(webhook_endpoints=[rotating]))
    await deliver(world, await announce(world))
    [(_, body, headers)] = world.cell.egress.sent
    now = int(T0.timestamp())
    assert reference_verify(CRM_SECRET, headers, body, now)
    assert reference_verify(NEW_SECRET, headers, body, now)


async def test_failures_retry_with_backoff_until_24_hours_then_die(world: World) -> None:
    delivery_id = await announce(world)
    world.cell.egress.status = 503
    failing = await deliver(world, delivery_id)
    assert failing.status is DeliveryStatus.FAILING
    assert failing.last_error == "http_503"
    [(task, due, reason)] = world.pipeline.scheduled
    assert (task.payload["delivery_id"], due, reason) == (
        str(delivery_id),
        T0 + timedelta(seconds=30),
        "retry",
    )
    world.cell.clock.at = T0 + timedelta(hours=24)
    dead = await deliver(world, delivery_id)
    assert dead.status is DeliveryStatus.DEAD
    assert dead.attempts == 2
    assert len(world.pipeline.scheduled) == 1


async def test_network_errors_are_recorded_without_detail(world: World) -> None:
    failing = await deliver(world, await announce(world), BrokenEgress())
    assert failing.status is DeliveryStatus.FAILING
    assert failing.last_error == "TimeoutError"


async def test_an_endpoint_failing_for_24_hours_stops_receiving(world: World) -> None:
    old = Delivery(uuid7(), "crm", "trait.added", {}, b"", DeliveryStatus.FAILING, T0 - timedelta(hours=25))
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.notify.add_delivery(old)
    dead = await deliver(world, await announce(world))
    assert dead.status is DeliveryStatus.DEAD
    assert dead.last_error == "endpoint_failing_24h"
    assert world.cell.egress.sent == []


async def test_manual_redelivery_is_one_attempt(world: World) -> None:
    delivery_id = await announce(world)
    world.cell.egress.status = 500
    world.cell.clock.at = T0 + timedelta(hours=25)
    await deliver(world, delivery_id)
    view = await service(world).redeliver(SPACE_ID, delivery_id)
    assert view.status == "pending"
    [task] = world.pipeline.take(TaskName.DELIVER_WEBHOOK)[-1:]
    assert task.payload["manual"] is True
    again = await deliver(world, delivery_id, manual=task.payload["manual"])
    assert again.status is DeliveryStatus.DEAD
    assert len(world.pipeline.scheduled) == 0
    world.cell.egress.status = 200
    await service(world).redeliver(SPACE_ID, delivery_id)
    assert (await deliver(world, delivery_id, manual=True)).status is DeliveryStatus.DELIVERED
    with pytest.raises(NotFound):
        await service(world).redeliver(SPACE_ID, uuid7())


async def test_dead_deliveries_are_kept_seven_days(world: World) -> None:
    world.cell.egress.status = 500
    await deliver(world, await announce(world), manual=True)
    assert await service(world).purge_dead(T0 + timedelta(days=6)) == 0
    assert await service(world).purge_dead(T0 + timedelta(days=7, seconds=1)) == 1


async def test_deliveries_are_listed_with_filters_and_a_cursor(world: World) -> None:
    for _ in range(3):
        await announce(world)
    first = await service(world).list_deliveries(SPACE_ID, endpoint_id="crm", limit=2)
    rest = await service(world).list_deliveries(
        SPACE_ID, endpoint_id="crm", limit=2, cursor=first.next_cursor
    )
    assert len(first.items) == 2
    assert len(rest.items) == 1
    assert rest.next_cursor is None
    assert (await service(world).list_deliveries(SPACE_ID, status=DeliveryStatus.DEAD)).items == []
    assert isinstance(world.cell.egress, RecordingEgress)


async def test_a_test_delivery_is_signed_marked_and_tried_once(world: World) -> None:
    view = await service(world).send_test(SPACE_ID, "crm")
    assert (view.endpoint_id, view.event_type, view.status) == ("crm", "webhook.test", "pending")
    [task] = world.pipeline.take(TaskName.DELIVER_WEBHOOK)
    assert task.payload["manual"] is True
    delivered = await deliver(world, UUID(view.delivery_id), manual=True)
    [(_, body, headers)] = world.cell.egress.sent
    assert reference_verify(CRM_SECRET, headers, body, int(T0.timestamp()))
    sent = orjson.loads(body)
    assert (sent["type"], sent["data"]) == ("webhook.test", {"test": True, "endpoint_id": "crm"})
    assert (delivered.event_type, delivered.status, delivered.attempts) == (
        "webhook.test",
        DeliveryStatus.DELIVERED,
        1,
    )
    with pytest.raises(NotFound):
        await service(world).send_test(SPACE_ID, "nope")


async def test_a_failing_test_delivery_is_not_retried(world: World) -> None:
    view = await service(world).send_test(SPACE_ID, "crm")
    failed = await deliver(world, UUID(view.delivery_id), BrokenEgress(), manual=True)
    assert (failed.status, failed.last_error) == (DeliveryStatus.DEAD, "TimeoutError")
    assert world.pipeline.scheduled == []


async def test_an_endpoint_never_reads_or_sends_a_secret_of_another_space(world: World, cell: Cell) -> None:
    # The vault is the cell's: an endpoint of this space that names another space's secret as its
    # Authorization header (or its signing key) would send that secret to the tenant's own URL.
    other = f"spaces/{uuid7()}/model/llm"
    cell.secrets.values[other] = "sk-another-tenant"
    stealing = CRM.model_copy(update={"authorization_ref": other})
    signing = CRM.model_copy(update={"endpoint_id": "crm2", "secret_refs": [other]})
    cell.config.put(config_with(webhook_endpoints=[stealing, signing]))
    for delivery_id in await enqueue_ids(world):
        failed = await deliver(world, delivery_id)
        assert (failed.status, failed.last_error) == (DeliveryStatus.FAILING, "secret_outside_space")
    assert world.cell.egress.sent == []


async def enqueue_ids(world: World) -> list[UUID]:
    async with world.uow(SPACE_ID) as uow:
        return await enqueue_event(
            uow, world.cell.config.space(SPACE_ID), "trait.added", SUBJECT, {"trait_id": "t1"}, at=T0
        )
