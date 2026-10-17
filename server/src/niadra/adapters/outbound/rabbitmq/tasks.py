"""`TaskBus` on RabbitMQ: one quorum queue per `QueueClass` (5.5; 06, 4.4).

RabbitMQ has no per-profile groups: exclusivity per profile comes from the lease in
`queue_leases`, and delays come from `scheduled_tasks`. Messages are persistent JSON, published
with confirms, and the task id doubles as the message id for tracing duplicates.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import aio_pika
import orjson
from aio_pika.abc import AbstractIncomingMessage

from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.domain.pipeline.retry import attempt_of
from niadra.domain.tasks import Task

log = logging.getLogger(__name__)


def encode_task(task: Task) -> bytes:
    return orjson.dumps(task.to_message())


def decode_task(body: bytes) -> Task:
    return Task.from_message(orjson.loads(body))


def to_message(task: Task) -> aio_pika.Message:
    return aio_pika.Message(
        encode_task(task),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=str(task.id),
        type=task.name.value,
    )


@dataclass
class RabbitDelivery:
    task: Task
    attempt: int
    message: AbstractIncomingMessage

    async def ack(self) -> None:
        await self.message.ack()

    async def retry_later(self, reason: str) -> None:  # noqa: ARG002
        # Back to the queue; past `x-delivery-limit` the broker dead-letters it.
        await self.message.nack(requeue=True)


def delivery_of(message: AbstractIncomingMessage) -> RabbitDelivery:
    task = decode_task(message.body)
    # Quorum queues count redeliveries (worker crashes) in this header; retries by the runtime
    # count in the payload.
    redelivered = int(str((message.headers or {}).get("x-delivery-count") or 0))
    return RabbitDelivery(task, attempt_of(task) + redelivered, message)


class RabbitTaskBus:
    def __init__(self, connection: RabbitConnection) -> None:
        self._rabbit = connection

    async def publish(self, tasks: Sequence[Task]) -> None:
        exchange = self._rabbit.publisher.default_exchange
        # Concurrent publishes on one confirm channel: the broker confirms each by delivery tag.
        await asyncio.gather(
            *(exchange.publish(to_message(t), routing_key=t.queue, mandatory=True) for t in tasks)
        )

    async def consume(self, queues: Sequence[str], prefetch: int) -> AsyncIterator[RabbitDelivery]:
        channel = await self._rabbit.channel(prefetch)
        inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
        try:
            for name in queues:
                queue = await channel.get_queue(name, ensure=False)
                await queue.consume(inbox.put)
            while True:
                message = await inbox.get()
                try:
                    delivery = delivery_of(message)
                except (ValueError, KeyError, TypeError):
                    # Not a task we can read: straight to the dead-letter exchange, never retried.
                    log.error("undecodable task message", extra={"message_id": message.message_id})
                    await message.reject(requeue=False)
                    continue
                yield delivery
        finally:
            # Closing the channel returns every unacked prefetched message to its queue.
            await channel.close()
