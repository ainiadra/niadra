"""Receipts from the read processes to the receipt writer, over `niadra.receipts` (5.11).

The queue, not the cache, carries receipts, so a cache failover never loses one (11). A read
process publishes with confirms and a short timeout; when the broker does not confirm in time,
`emit` returns False and the read process buffers.
"""

import asyncio
import base64
import dataclasses
import logging
import types
import typing
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

import aio_pika
import orjson
from aio_pika.abc import AbstractIncomingMessage

from niadra.adapters.outbound.rabbitmq.connection import RECEIPTS_QUEUE, RabbitConnection
from niadra.domain.receipts import ProducerMark, ReceiptDraft

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

log = logging.getLogger(__name__)

MARK_TIMEOUT_S = 1.0
LINGER_S = 0.5
"""How long the writer gathers receipts after the first one of a batch. Each batch is a transaction of
about eight statements on the space's database and one on the cell's, whatever its size: at 10 reads a
second, 50 ms stored them one by one, the receipt writer's transactions outnumbered the reads' own, and they
took turns on the same two server connections (28/09/2026). A receipt is durable in the queue meanwhile."""


def encode_receipts(drafts: Sequence[ReceiptDraft] = (), marks: Sequence[ProducerMark] = ()) -> bytes:
    return orjson.dumps({"drafts": list(drafts), "marks": list(marks)}, default=_bytes)


def decode_receipts(body: bytes) -> tuple[list[ReceiptDraft], list[ProducerMark]]:
    data = orjson.loads(body)
    drafts = [_from_json(ReceiptDraft, d) for d in data.get("drafts", [])]
    marks = [_from_json(ProducerMark, m) for m in data.get("marks", [])]
    return drafts, marks


class RabbitReceiptSink:
    def __init__(self, connection: RabbitConnection) -> None:
        self._rabbit = connection

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        if not drafts:
            return True
        try:
            async with asyncio.timeout(timeout_s):
                await self._publish(encode_receipts(drafts=drafts))
        except Exception:
            return False
        return True

    async def mark(self, mark: ProducerMark) -> None:
        try:
            async with asyncio.timeout(MARK_TIMEOUT_S):
                await self._publish(encode_receipts(marks=[mark]))
        except Exception:
            # Marks only let the watermark advance sooner; the next one supersedes this.
            log.warning("producer mark not confirmed")

    async def _publish(self, body: bytes) -> None:
        message = aio_pika.Message(
            body, content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        )
        await self._rabbit.publisher.default_exchange.publish(
            message, routing_key=RECEIPTS_QUEUE, mandatory=True
        )


@dataclass
class RabbitReceiptBatch:
    drafts: list[ReceiptDraft] = field(default_factory=list)
    marks: list[ProducerMark] = field(default_factory=list)
    messages: list[AbstractIncomingMessage] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.drafts) + len(self.marks)

    async def ack(self) -> None:
        for message in self.messages:
            await message.ack()

    async def retry_later(self) -> None:
        for message in self.messages:
            await message.nack(requeue=True)


class RabbitReceiptSource:
    def __init__(self, connection: RabbitConnection, linger_s: float = LINGER_S) -> None:
        self._rabbit = connection
        self._linger_s = linger_s

    async def batches(self, max_items: int) -> AsyncIterator[RabbitReceiptBatch]:
        channel = await self._rabbit.channel(prefetch=max_items)
        inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
        try:
            queue = await channel.get_queue(RECEIPTS_QUEUE, ensure=False)
            await queue.consume(inbox.put)
            while True:
                yield await collect(inbox, max_items, self._linger_s)
        finally:
            await channel.close()


async def collect(
    inbox: "asyncio.Queue[AbstractIncomingMessage]", max_items: int, linger_s: float
) -> RabbitReceiptBatch:
    """Waits for one message, then lingers briefly so the writer chains receipts in bulk."""
    batch = RabbitReceiptBatch()
    await _take(batch, await inbox.get())
    deadline = asyncio.get_running_loop().time() + linger_s
    while batch.size < max_items:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(inbox.get(), remaining)
        except TimeoutError:
            break
        await _take(batch, message)
    return batch


async def _take(batch: RabbitReceiptBatch, message: AbstractIncomingMessage) -> None:
    try:
        drafts, marks = decode_receipts(message.body)
    except (ValueError, KeyError, TypeError):
        log.error("undecodable receipt message", extra={"message_id": message.message_id})
        await message.reject(requeue=False)
        return
    batch.drafts.extend(drafts)
    batch.marks.extend(marks)
    batch.messages.append(message)


def _bytes(value: Any) -> str:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    raise TypeError(type(value).__name__)


def _from_json[T: DataclassInstance](cls: type[T], data: dict[str, Any]) -> T:
    hints = typing.get_type_hints(cls)
    names = [f.name for f in dataclasses.fields(cls) if f.name in data]
    return cls(**{name: _convert(hints[name], data[name]) for name in names})


def _convert(hint: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(hint, types.UnionType):
        hint = next(arg for arg in typing.get_args(hint) if arg is not type(None))
    origin = typing.get_origin(hint) or hint
    if origin is UUID:
        return UUID(value)
    if origin is datetime:
        return datetime.fromisoformat(value)
    if origin is bytes:
        return base64.b64decode(value)
    if origin is tuple:
        return tuple(value)
    if isinstance(origin, type) and issubclass(origin, Enum):
        return origin(value)
    return value
