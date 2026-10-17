"""Outbound webhooks: enqueue, sign, deliver, retry and dead-letter (7.5).

Every module announces what happened through `enqueue_event`, inside its own transaction, so a
delivery exists if and only if the change it announces committed. The HTTP call itself happens
outside any transaction: a slow customer endpoint must never hold a database connection.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.config.models import SpaceConfig, WebhookEndpoint
from niadra.contracts.notify import DeliveryPage, DeliveryView
from niadra.domain.audit.chain import StoredReceipt
from niadra.domain.common.errors import Conflict, NotFound, Unavailable
from niadra.domain.common.ids import uuid7, uuid7_ms
from niadra.domain.common.space import owns_secret
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.notify.ocsf import SIEM_FORMATS, chunks, receipt_event, siem_body, test_event
from niadra.domain.notify.webhooks import (
    DEAD_RETENTION,
    Delivery,
    DeliveryStatus,
    WebhookSubject,
    assert_ids_only,
    body_hash,
    build_body,
    encode_body,
    endpoint_disabled,
    is_success,
    next_attempt_at,
    signature_header,
)
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.policy import ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.ports.infra import Clock, ConfigProvider, EgressClient, SecretStore
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

DELIVERY_TIMEOUT_S = 10.0


RECEIPTS_EVENT = "receipts"
TEST_EVENT = "webhook.test"
"""A delivery sent on request to check an endpoint or a SIEM stream: signed like the rest, one attempt."""


def _subscribed(endpoint: WebhookEndpoint, event_type: str) -> bool:
    # A SIEM stream takes receipts only, in its own format; everything else is a Standard Webhook.
    return (
        endpoint.enabled
        and endpoint.format == "standard"
        and (event_type in endpoint.event_types or "*" in endpoint.event_types)
    )


def subscribes(space: SpaceConfig, event_types: Collection[str]) -> bool:
    """Whether an endpoint, or the space's notification feed, takes one of these types; callers skip the
    reads an event needs when none does."""
    return feeds(space) or any(_subscribed(e, t) for e in space.webhook_endpoints for t in event_types)


def feeds(space: SpaceConfig) -> bool:
    """The space keeps the notification feed (`GET /v1/notifications`) for the companies that pull."""
    return Feature.NOTIFICATIONS in space.features.enabled


def _streams_kind(endpoint: WebhookEndpoint, kind: str) -> bool:
    return (
        endpoint.enabled
        and endpoint.format in SIEM_FORMATS
        and (kind in endpoint.event_types or "*" in endpoint.event_types)
    )


async def enqueue_receipts(uow: UnitOfWork, space: SpaceConfig, receipts: Sequence[StoredReceipt]) -> int:
    """The receipts stream to the tenant's SIEM (5.13): in the writer's transaction, one delivery per
    stream and batch, so it leaves seconds after the chain holds the receipts, signed and retried like
    any webhook, and dead-lettered after 24 hours for a manual resend. The hourly export is untouched."""
    streams = [e for e in space.webhook_endpoints if e.enabled and e.format in SIEM_FORMATS]
    if not streams or not receipts:
        return 0
    names = {s.source_id: s.name for s in space.sources}
    events = [(r.draft.kind.value, receipt_event(r, names)) for r in receipts]
    tasks: list[Task] = []
    for endpoint in streams:
        mine = [event for kind, event in events if _streams_kind(endpoint, kind)]
        for batch in chunks(mine):
            for event in batch:
                assert_ids_only(event)
            delivery_id = uuid7()
            body = {"events": list(batch)}
            await uow.notify.add_delivery(
                Delivery(
                    delivery_id=delivery_id,
                    endpoint_id=endpoint.endpoint_id,
                    event_type=RECEIPTS_EVENT,
                    body=body,
                    body_hash=body_hash(encode_body(body)),
                    status=DeliveryStatus.PENDING,
                    created_at=datetime.fromtimestamp(uuid7_ms(delivery_id) / 1000, UTC),
                )
            )
            tasks.append(Task(TaskName.DELIVER_WEBHOOK, space.space_id, {"delivery_id": str(delivery_id)}))
    if tasks:
        await uow.pipeline.enqueue(tasks)
    return len(tasks)


async def announce_receipts(uow: UnitOfWork, space: SpaceConfig, receipts: Sequence[StoredReceipt]) -> None:
    """`receipt.recorded` (24.5.4): the chain links the writer just stored, for the company's own audit chain,
    in the writer's transaction; a receipt's ids, kind, surface and hashes, never who or what it read."""
    if not receipts or not subscribes(space, ("receipt.recorded",)):
        return
    for batch in chunks([_receipt_link(r) for r in receipts]):
        await enqueue_event(uow, space, "receipt.recorded", None, {"receipts": list(batch)})


def _receipt_link(receipt: StoredReceipt) -> dict[str, Any]:
    d = receipt.draft
    return {
        "receipt_id": str(d.receipt_id),
        "kind": d.kind.value,
        "surface": d.surface.value,
        "read_at": d.read_at.isoformat(),
        "source_id": str(d.source_id) if d.source_id else None,
        "slice": receipt.slice,
        "day": receipt.day.isoformat(),
        "seq": receipt.seq,
        "prev_hash": receipt.prev_hash.hex(),
        "hash": receipt.hash.hex(),
    }


def _purpose_allows(space: SpaceConfig, endpoint: WebhookEndpoint, purposes: frozenset[str] | None) -> bool:
    # A payload restricted to some purposes (a billing trait, say) only reaches sources that hold one.
    if not purposes:
        return True
    source = space.source(endpoint.source_id)
    return source is not None and bool(source.purposes & purposes)


def _policy_allows(space: SpaceConfig, endpoint: WebhookEndpoint, item: ItemAttrs | None) -> bool:
    """An event about a memory item only reaches a source the policy lets read that item, at the best level
    the source can reach (5.12): the outcome of a health conversation never reaches the billing CRM."""
    if item is None:
        return True
    source = space.source(endpoint.source_id)
    if source is None:
        return False
    reader = Reader(source.audience, source.purposes, source.source_id)
    return evaluate(space.policy, reader, item, PolicyContext(source.verification_ceiling)).included


async def enqueue_event(
    uow: UnitOfWork,
    space: SpaceConfig,
    event_type: str,
    subject: WebhookSubject | None,
    data: Mapping[str, Any],
    *,
    endpoint_ids: Collection[str] | None = None,
    purposes: frozenset[str] | None = None,
    links: Mapping[str, str] | None = None,
    at: datetime | None = None,
    item: ItemAttrs | None = None,
) -> list[UUID]:
    """Creates one delivery per subscribed endpoint and enqueues `deliver_webhook` for each.

    `data` carries ids, rules, versions, state and evidence pointers, never conversation content.
    `created_at` defaults to the delivery id's own timestamp, which is minted now. `item` describes the
    memory item the event is about, for the policy check of each endpoint's source.
    """
    targets = [
        e
        for e in space.webhook_endpoints
        if _subscribed(e, event_type)
        and (endpoint_ids is None or e.endpoint_id in endpoint_ids)
        and _purpose_allows(space, e, purposes)
        and _policy_allows(space, e, item)
    ]
    tasks, ids = [], []
    for endpoint in targets:
        delivery_id = uuid7()
        created_at = at or datetime.fromtimestamp(uuid7_ms(delivery_id) / 1000, UTC)
        body = build_body(
            delivery_id=delivery_id,
            event_type=event_type,
            created_at=created_at,
            tenant_id=space.tenant_id,
            subject=subject,
            data=data,
            links=links,
        )
        await uow.notify.add_delivery(
            Delivery(
                delivery_id=delivery_id,
                endpoint_id=endpoint.endpoint_id,
                event_type=event_type,
                body=body,
                body_hash=body_hash(encode_body(body)),
                status=DeliveryStatus.PENDING,
                created_at=created_at,
            )
        )
        tasks.append(Task(TaskName.DELIVER_WEBHOOK, space.space_id, {"delivery_id": str(delivery_id)}))
        ids.append(delivery_id)
    if feeds(space) and endpoint_ids is None and purposes is None and item is None:
        # The feed's copy joins it once this transaction committed (`app/notifications.py`). An event aimed
        # at one endpoint, or readable only by the sources a policy lets see its item, stays a webhook's.
        event_id = uuid7()
        created_at = at or datetime.fromtimestamp(uuid7_ms(event_id) / 1000, UTC)
        body = build_body(
            delivery_id=event_id,
            event_type=event_type,
            created_at=created_at,
            tenant_id=space.tenant_id,
            subject=subject,
            data=data,
            links=links,
        )
        payload = {"event_id": str(event_id), "type": event_type, "at": created_at.isoformat(), "body": body}
        tasks.append(Task(TaskName.APPEND_NOTIFICATION, space.space_id, payload))
    if tasks:
        await uow.pipeline.enqueue(tasks)
    return ids


class NotifyService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        egress: EgressClient,
        secrets: SecretStore,
        clock: Clock,
        config: ConfigProvider,
    ) -> None:
        self._uow = uow
        self._egress = egress
        self._secrets = secrets
        self._clock = clock
        self._config = config

    async def deliver_webhook(self, task: Task) -> None:
        delivery_id = UUID(task.payload["delivery_id"])
        manual = bool(task.payload.get("manual"))
        space = self._config.space(task.space_id)
        now = self._clock.now()
        async with self._uow(task.space_id) as uow:
            delivery = await uow.notify.get_delivery(delivery_id)
            if delivery is None or delivery.status in (DeliveryStatus.DELIVERED, DeliveryStatus.DEAD):
                return
            endpoint = space.endpoint(delivery.endpoint_id)
            health = await uow.notify.endpoint_health(delivery.endpoint_id)
        if endpoint is None or not endpoint.enabled:
            await self._finish(
                task.space_id, delivery, now, status_code=None, error="endpoint_disabled", final=True
            )
            return
        if not manual and endpoint_disabled(health.last_success_at, health.first_failure_at, now):
            await self._finish(
                task.space_id, delivery, now, status_code=None, error="endpoint_failing_24h", final=True
            )
            return
        status_code, error = await self._post(task.space_id, endpoint, delivery, now)
        await self._finish(task.space_id, delivery, now, status_code=status_code, error=error, final=manual)

    async def _post(
        self, space_id: UUID, endpoint: WebhookEndpoint, delivery: Delivery, now: datetime
    ) -> tuple[int | None, str | None]:
        refs = [*endpoint.secret_refs, *([endpoint.authorization_ref] if endpoint.authorization_ref else [])]
        if not all(owns_secret(space_id, ref) for ref in refs):
            # A secret of another space is never read, let alone sent (4.7).
            return None, "secret_outside_space"
        secrets = [await self._secrets.get(ref) for ref in endpoint.secret_refs]
        if endpoint.format == "standard":
            body, content_type = encode_body(delivery.body), "application/json"
        else:
            body, content_type = siem_body(endpoint.format, delivery.body.get("events", []))
        msg_id, timestamp = str(delivery.delivery_id), int(now.timestamp())
        headers = {
            "content-type": content_type,
            "webhook-id": msg_id,
            "webhook-timestamp": str(timestamp),
            "webhook-signature": signature_header(secrets, msg_id, timestamp, body),
        }
        if endpoint.authorization_ref:
            headers["authorization"] = await self._secrets.get(endpoint.authorization_ref)
        try:
            response = await self._egress.post(endpoint.url, body, headers, DELIVERY_TIMEOUT_S)
        except Exception as exc:  # the endpoint's failure is data here, never the worker's failure
            return None, type(exc).__name__
        return response.status, None if is_success(response.status) else f"http_{response.status}"

    async def _finish(
        self,
        space_id: UUID,
        delivery: Delivery,
        now: datetime,
        *,
        status_code: int | None,
        error: str | None,
        final: bool,
    ) -> None:
        attempts = delivery.attempts + 1
        if status_code is not None and error is None:
            updated = replace(
                delivery,
                status=DeliveryStatus.DELIVERED,
                attempts=attempts,
                delivered_at=now,
                next_attempt_at=None,
                last_status_code=status_code,
                last_error=None,
            )
        else:
            retry_at = None if final else next_attempt_at(delivery.created_at, attempts, now)
            updated = replace(
                delivery,
                status=DeliveryStatus.FAILING if retry_at else DeliveryStatus.DEAD,
                attempts=attempts,
                next_attempt_at=retry_at,
                last_status_code=status_code,
                last_error=error,
            )
        async with self._uow(space_id) as uow:
            await uow.notify.save_attempt(updated)
            if updated.next_attempt_at:
                task = Task(TaskName.DELIVER_WEBHOOK, space_id, {"delivery_id": str(delivery.delivery_id)})
                await uow.pipeline.schedule(task, updated.next_attempt_at, reason=DelayReason.RETRY)

    async def list_deliveries(
        self,
        space_id: UUID,
        *,
        status: DeliveryStatus | None = None,
        endpoint_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> DeliveryPage:
        async with self._uow(space_id) as uow:
            rows = await uow.notify.list_deliveries(
                status=status, endpoint_id=endpoint_id, before=decode_cursor(cursor), limit=limit + 1
            )
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].created_at, page[-1].delivery_id) if len(rows) > limit else None
        return DeliveryPage(items=[_view(d) for d in page], next_cursor=next_cursor)

    async def redeliver(self, space_id: UUID, delivery_id: UUID) -> DeliveryView:
        """Manual resend from the dead-letter list: one immediate attempt, no new retry cycle (7.5)."""
        async with self._uow(space_id) as uow:
            delivery = await uow.notify.get_delivery(delivery_id)
            if delivery is None:
                raise NotFound("delivery not found")
            pending = replace(delivery, status=DeliveryStatus.PENDING, next_attempt_at=None)
            await uow.notify.save_attempt(pending)
            await uow.pipeline.enqueue(
                [Task(TaskName.DELIVER_WEBHOOK, space_id, {"delivery_id": str(delivery_id), "manual": True})]
            )
        return _view(pending)

    async def send_test(self, space_id: UUID, endpoint_id: str) -> DeliveryView:
        """A signed delivery marked as a test (`webhook.test`), to an endpoint whatever it subscribes to, or
        to a SIEM stream as one OCSF event. One immediate attempt, no retries, recorded in the deliveries
        like any other; it carries no customer data."""
        space = self._config.space(space_id)
        endpoint = space.endpoint(endpoint_id)
        if endpoint is None:
            raise NotFound("endpoint not found")
        if not endpoint.enabled:
            raise Conflict("the endpoint is disabled")
        delivery_id = uuid7()
        created_at = datetime.fromtimestamp(uuid7_ms(delivery_id) / 1000, UTC)
        if endpoint.format == "standard":
            data = {"test": True, "endpoint_id": endpoint.endpoint_id}
            body = build_body(
                delivery_id=delivery_id,
                event_type=TEST_EVENT,
                created_at=created_at,
                tenant_id=space.tenant_id,
                subject=None,
                data=data,
                links=None,
            )
        else:
            body = {"events": [test_event(space_id, delivery_id, uuid7_ms(delivery_id))]}
        delivery = Delivery(
            delivery_id=delivery_id,
            endpoint_id=endpoint.endpoint_id,
            event_type=TEST_EVENT,
            body=body,
            body_hash=body_hash(encode_body(body)),
            status=DeliveryStatus.PENDING,
            created_at=created_at,
        )
        async with self._uow(space_id) as uow:
            await uow.notify.add_delivery(delivery)
            await uow.pipeline.enqueue(
                [Task(TaskName.DELIVER_WEBHOOK, space_id, {"delivery_id": str(delivery_id), "manual": True})]
            )
        return _view(delivery)

    async def purge_dead(self, now: datetime | None = None) -> int:
        cutoff = (now or self._clock.now()) - DEAD_RETENTION
        purged = 0
        for space in self._config.spaces():
            try:
                async with self._uow(space.space_id) as uow:
                    purged += await uow.notify.purge_dead(cutoff)
            except Unavailable:
                continue  # the space's database is being created or restored; the next sweep takes it
        return purged


def _view(d: Delivery) -> DeliveryView:
    return DeliveryView(
        delivery_id=str(d.delivery_id),
        endpoint_id=d.endpoint_id,
        event_type=d.event_type,
        status=d.status.value,
        attempts=d.attempts,
        created_at=d.created_at,
        next_attempt_at=d.next_attempt_at,
        delivered_at=d.delivered_at,
        last_status_code=d.last_status_code,
        last_error=d.last_error,
    )
