"""Coordination records from the read processes to the batch writer, over `niadra.coordination` (24.3.4).

A check that reserved something publishes with confirms and waits for the broker before it answers; when
the broker does not confirm in time, `publish` returns False and the check undoes its reservation. The
writer takes up to 500 records at a time, with a short linger, as the receipt writer does.
"""

import asyncio
import base64
import dataclasses
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import aio_pika
import orjson
from aio_pika.abc import AbstractIncomingMessage

from niadra.adapters.outbound.rabbitmq.connection import COORDINATION_QUEUE, RabbitConnection
from niadra.domain.coordinate.model import EffectSnapshot, EffectState, Settler
from niadra.domain.coordinate.records import (
    CommitmentRecord,
    CommitmentStatusRecord,
    ContactRecord,
    CoordinationRecord,
    EffectRecord,
    LiftRecord,
    OwnershipRecord,
    ReleaseRecord,
    SpendRecord,
    SuppressionRecord,
)

log = logging.getLogger(__name__)

LINGER_S = 0.5
TYPES: dict[str, type[Any]] = {
    "effect": EffectRecord,
    "claim": OwnershipRecord,
    "release": ReleaseRecord,
    "suppression": SuppressionRecord,
    "lift": LiftRecord,
    "contact": ContactRecord,
    "spend": SpendRecord,
    "commitment": CommitmentRecord,
    "commitment_status": CommitmentStatusRecord,
}
NAMES = {kind: name for name, kind in TYPES.items()}


def encode_records(records: Sequence[CoordinationRecord]) -> bytes:
    return orjson.dumps([{"t": NAMES[type(r)], **_plain(dataclasses.asdict(r))} for r in records])


def decode_records(body: bytes) -> list[CoordinationRecord]:
    return [_record(item) for item in orjson.loads(body)]


class RabbitCoordinationLog:
    def __init__(self, connection: RabbitConnection) -> None:
        self._rabbit = connection

    async def publish(self, records: Sequence[CoordinationRecord], timeout_s: float) -> bool:
        if not records:
            return True
        message = aio_pika.Message(
            encode_records(records),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            async with asyncio.timeout(timeout_s):
                await self._rabbit.publisher.default_exchange.publish(
                    message, routing_key=COORDINATION_QUEUE, mandatory=True
                )
        except Exception:
            return False
        return True


@dataclass
class RabbitCoordinationBatch:
    records: list[CoordinationRecord] = field(default_factory=list)
    messages: list[AbstractIncomingMessage] = field(default_factory=list)

    async def ack(self) -> None:
        for message in self.messages:
            await message.ack()

    async def retry_later(self) -> None:
        for message in self.messages:
            await message.nack(requeue=True)


class RabbitCoordinationFeed:
    def __init__(self, connection: RabbitConnection, linger_s: float = LINGER_S) -> None:
        self._rabbit = connection
        self._linger_s = linger_s

    async def batches(self, max_items: int) -> AsyncIterator[RabbitCoordinationBatch]:
        channel = await self._rabbit.channel(prefetch=max_items)
        inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
        try:
            queue = await channel.get_queue(COORDINATION_QUEUE, ensure=False)
            await queue.consume(inbox.put)
            while True:
                yield await _collect(inbox, max_items, self._linger_s)
        finally:
            await channel.close()


async def _collect(
    inbox: "asyncio.Queue[AbstractIncomingMessage]", max_items: int, linger_s: float
) -> RabbitCoordinationBatch:
    batch = RabbitCoordinationBatch()
    await _take(batch, await inbox.get())
    deadline = asyncio.get_running_loop().time() + linger_s
    while len(batch.records) < max_items:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(inbox.get(), remaining)
        except TimeoutError:
            break
        await _take(batch, message)
    return batch


async def _take(batch: RabbitCoordinationBatch, message: AbstractIncomingMessage) -> None:
    try:
        records = decode_records(message.body)
    except (ValueError, KeyError, TypeError):
        log.error("undecodable coordination message", extra={"message_id": message.message_id})
        await message.reject(requeue=False)
        return
    batch.records.extend(records)
    batch.messages.append(message)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return {"b64": base64.b64encode(value).decode()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, EffectState | Settler):
        return value.value
    return value


def _record(item: dict[str, Any]) -> CoordinationRecord:
    kind = TYPES[item.pop("t")]
    data = {name: _value(value) for name, value in item.items()}
    data["space_id"] = UUID(data["space_id"])
    if kind is EffectRecord:
        snapshot = data["snapshot"]
        snapshot["state"] = EffectState(snapshot["state"])
        snapshot["settled_by"] = Settler(snapshot["settled_by"]) if snapshot["settled_by"] else None
        data["snapshot"] = EffectSnapshot(**snapshot)
        data["source_id"] = UUID(data["source_id"])
    for name in (
        "claim_id",
        "suppression_id",
        "source_id",
        "contact_id",
        "decision_id",
        "spend_id",
        "commitment_id",
        "basis_event_id",
    ):
        if isinstance(data.get(name), str):
            data[name] = UUID(data[name])
    if "intents" in data:
        data["intents"] = tuple(data["intents"])
    if "use_ids" in data:
        data["use_ids"] = tuple(UUID(u) for u in data["use_ids"])
    if "named" in data:
        data["named"] = tuple(_value(t) for t in data["named"])
    record: CoordinationRecord = kind(**data)
    return record


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"b64"}:
            return base64.b64decode(value["b64"])
        return {k: _value(v) for k, v in value.items()}
    return value
