"""One robust RabbitMQ connection per process, and the cell's queue topology (5.5; 06, 4.4).

Every queue is a quorum queue (replicated, durable). Task queues dead-letter to `niadra.dlx`
after `x-delivery-limit` redeliveries, which only happens when a worker crashes mid-task:
ordinary failures are retried through `scheduled_tasks` by the worker runtime.
"""

from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from niadra.domain.tasks import QueueClass

DEAD_LETTER_EXCHANGE = "niadra.dlx"
DEAD_QUEUE = "niadra.dead"
RECEIPTS_QUEUE = "niadra.receipts"
# Receipts are audit records: a batch the writer cannot take waits in its own dead queue
# instead of mixing with tasks (5.11).
RECEIPTS_DEAD_QUEUE = "niadra.receipts.dead"
# What the state store decided on its way to the durable record (24.3.4); a batch the writer cannot take
# waits in its own dead queue.
COORDINATION_QUEUE = "niadra.coordination"
COORDINATION_DEAD_QUEUE = "niadra.coordination.dead"
# The agents' working state on its way to the database (06, 15.3): a message the writer cannot store waits
# in a dead queue of its own.
AGENT_STATE_QUEUE = "niadra.agent_state"
AGENT_STATE_DEAD_QUEUE = "niadra.agent_state.dead"
DELIVERY_LIMIT = 5


def quorum_arguments(dead_letter_to: str | None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"x-queue-type": "quorum"}
    if dead_letter_to:
        arguments["x-delivery-limit"] = DELIVERY_LIMIT
        # at-least-once dead-lettering needs reject-publish overflow; without it a poison
        # message could be dropped on the way to the dead queue.
        arguments |= {
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
            "x-dead-letter-routing-key": dead_letter_to,
            "x-dead-letter-strategy": "at-least-once",
            "x-overflow": "reject-publish",
        }
    return arguments


async def declare_topology(channel: AbstractChannel) -> None:
    """Idempotent: every process declares the same queues with the same arguments at start."""
    dlx = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
    for dead_name in (DEAD_QUEUE, RECEIPTS_DEAD_QUEUE, COORDINATION_DEAD_QUEUE, AGENT_STATE_DEAD_QUEUE):
        dead = await channel.declare_queue(dead_name, durable=True, arguments=quorum_arguments(None))
        await dead.bind(dlx, routing_key=dead_name)
    for queue in QueueClass:
        await channel.declare_queue(queue.value, durable=True, arguments=quorum_arguments(DEAD_QUEUE))
    await channel.declare_queue(RECEIPTS_QUEUE, durable=True, arguments=quorum_arguments(RECEIPTS_DEAD_QUEUE))
    await channel.declare_queue(
        COORDINATION_QUEUE, durable=True, arguments=quorum_arguments(COORDINATION_DEAD_QUEUE)
    )
    await channel.declare_queue(
        AGENT_STATE_QUEUE, durable=True, arguments=quorum_arguments(AGENT_STATE_DEAD_QUEUE)
    )


class RabbitConnection:
    """Reconnects on its own (`connect_robust`) and re-opens channels and consumers after a drop."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._publisher: AbstractChannel | None = None

    async def open(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        # Publisher confirms: `publish` returns only once the broker has the message on disk.
        self._publisher = await self._connection.channel(publisher_confirms=True, on_return_raises=True)
        await declare_topology(self._publisher)

    @property
    def publisher(self) -> AbstractChannel:
        if self._publisher is None:
            raise RuntimeError("RabbitConnection.open() was not awaited")
        return self._publisher

    async def channel(self, prefetch: int) -> AbstractChannel:
        if self._connection is None:
            raise RuntimeError("RabbitConnection.open() was not awaited")
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
