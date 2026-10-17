"""RabbitMQ adapters against a small stand-in for the aio-pika channel (no broker in unit tests)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aio_pika

from niadra.adapters.outbound.rabbitmq.connection import (
    DEAD_LETTER_EXCHANGE,
    DEAD_QUEUE,
    RECEIPTS_QUEUE,
    quorum_arguments,
)
from niadra.adapters.outbound.rabbitmq.receipts import (
    RabbitReceiptSink,
    collect,
    decode_receipts,
    encode_receipts,
)
from niadra.adapters.outbound.rabbitmq.tasks import RabbitTaskBus, decode_task, delivery_of, encode_task
from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.retry import with_attempt
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE


@dataclass
class FakeMessage:
    body: bytes
    headers: dict[str, Any] = field(default_factory=dict)
    message_id: str | None = None
    settled: list[str] = field(default_factory=list)

    async def ack(self) -> None:
        self.settled.append("ack")

    async def nack(self, requeue: bool = True) -> None:
        self.settled.append(f"nack:{requeue}")

    async def reject(self, requeue: bool = False) -> None:
        self.settled.append(f"reject:{requeue}")


class FakeExchange:
    def __init__(self) -> None:
        self.sent: list[tuple[aio_pika.Message, str]] = []

    async def publish(self, message: aio_pika.Message, routing_key: str, mandatory: bool = True) -> None:
        self.sent.append((message, routing_key))


class FakeQueue:
    def __init__(self) -> None:
        self.callback: Callable[[Any], Awaitable[None]] | None = None

    async def consume(self, callback: Callable[[Any], Awaitable[None]]) -> str:
        self.callback = callback
        return "tag"


class FakeChannel:
    def __init__(self) -> None:
        self.queues: dict[str, FakeQueue] = {}
        self.closed = False
        self.default_exchange = FakeExchange()

    async def get_queue(self, name: str, ensure: bool = False) -> FakeQueue:
        return self.queues.setdefault(name, FakeQueue())

    async def close(self) -> None:
        self.closed = True


class FakeRabbit:
    def __init__(self) -> None:
        self.publisher = FakeChannel()
        self.consumer = FakeChannel()
        self.prefetch = 0

    async def channel(self, prefetch: int) -> FakeChannel:
        self.prefetch = prefetch
        return self.consumer


def draft() -> ReceiptDraft:
    return ReceiptDraft(
        receipt_id=uuid7(),
        space_id=SPACE_ID,
        producer_id="read-1",
        producer_seq=7,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.PACK,
        audience=Audience.CUSTOMER_AGENT,
        read_at=datetime(2026, 9, 22, tzinfo=UTC),
        source_id=WHATSAPP_SOURCE,
        item_ids=("f1", "e2"),
        query_hash=b"\x00\xff",
        extra={"k": "v"},
    )


def test_task_queues_are_quorum_and_dead_letter_to_the_dead_queue() -> None:
    arguments = quorum_arguments(DEAD_QUEUE)
    assert arguments["x-queue-type"] == "quorum"
    assert arguments["x-dead-letter-exchange"] == DEAD_LETTER_EXCHANGE
    assert arguments["x-dead-letter-routing-key"] == DEAD_QUEUE
    assert arguments["x-dead-letter-strategy"] == "at-least-once"
    assert "x-dead-letter-exchange" not in quorum_arguments(None)


def test_task_messages_round_trip() -> None:
    task = Task(TaskName.COMPILE, SPACE_ID, payload={"view": "default"}, profile_id=uuid7())
    assert decode_task(encode_task(task)) == task


def test_delivery_attempt_adds_broker_redeliveries_to_runtime_retries() -> None:
    task = with_attempt(Task(TaskName.EXTRACT, SPACE_ID), 2)
    delivery = delivery_of(FakeMessage(encode_task(task), headers={"x-delivery-count": 1}))  # type: ignore[arg-type]
    assert delivery.attempt == 3
    assert delivery.task == task


async def test_publish_routes_each_task_to_its_class_queue_as_persistent_json() -> None:
    rabbit = FakeRabbit()
    tasks = [Task(TaskName.EXTRACT, SPACE_ID), Task(TaskName.COMPILE, SPACE_ID, profile_id=uuid7())]
    await RabbitTaskBus(rabbit).publish(tasks)  # type: ignore[arg-type]
    sent = rabbit.publisher.default_exchange.sent
    assert [key for _, key in sent] == [QueueClass.EXTRACTION.value, QueueClass.REALTIME.value]
    message = sent[0][0]
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.message_id == str(tasks[0].id)
    assert decode_task(message.body) == tasks[0]


async def test_consume_yields_deliveries_rejects_garbage_and_closes_its_channel() -> None:
    rabbit = FakeRabbit()
    bus = RabbitTaskBus(rabbit)  # type: ignore[arg-type]
    task = Task(TaskName.EXTRACT, SPACE_ID)
    stream = bus.consume([QueueClass.EXTRACTION.value], prefetch=4)
    first = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)
    callback = rabbit.consumer.queues[QueueClass.EXTRACTION.value].callback
    assert callback is not None
    garbage, good = FakeMessage(b"not json"), FakeMessage(encode_task(task))
    await callback(garbage)
    await callback(good)
    delivery = await first
    assert delivery.task == task
    assert garbage.settled == ["reject:False"]
    await delivery.retry_later("db down")
    assert good.settled == ["nack:True"]
    assert rabbit.prefetch == 4
    await stream.aclose()
    assert rabbit.consumer.closed


def test_receipts_round_trip_with_every_field_type() -> None:
    mark = ProducerMark("read-1", last_seq=7, at=datetime(2026, 9, 22, 1, tzinfo=UTC), final=True)
    drafts, marks = decode_receipts(encode_receipts([draft()], [mark]))
    original = draft()
    assert drafts[0].item_ids == original.item_ids
    assert drafts[0].query_hash == b"\x00\xff"
    assert drafts[0].kind is ReceiptKind.READ
    assert drafts[0].read_at == original.read_at
    assert marks == [mark]


async def test_sink_reports_false_when_the_broker_does_not_confirm_in_time() -> None:
    class SlowExchange(FakeExchange):
        async def publish(self, message: aio_pika.Message, routing_key: str, mandatory: bool = True) -> None:
            await asyncio.sleep(1)

    rabbit = FakeRabbit()
    sink = RabbitReceiptSink(rabbit)  # type: ignore[arg-type]
    assert await sink.emit([draft()], timeout_s=1.0) is True
    [(_, key)] = rabbit.publisher.default_exchange.sent
    assert key == RECEIPTS_QUEUE
    rabbit.publisher.default_exchange = SlowExchange()
    assert await sink.emit([draft()], timeout_s=0.01) is False


async def test_source_batches_messages_up_to_the_limit() -> None:
    inbox: asyncio.Queue[Any] = asyncio.Queue()
    messages = [FakeMessage(encode_receipts([draft(), draft()])) for _ in range(3)]
    for m in messages:
        inbox.put_nowait(m)
    batch = await collect(inbox, max_items=4, linger_s=0.05)
    assert (len(batch.drafts), len(batch.messages)) == (4, 2)
    await batch.ack()
    assert [m.settled for m in messages] == [["ack"], ["ack"], []]
    rest = await collect(inbox, max_items=4, linger_s=0.01)
    await rest.retry_later()
    assert messages[2].settled == ["nack:True"]
