"""The agents' working state, from the read processes to its batch writer, over `niadra.agent_state` (06,
15.3).

A stored write answers only once the broker confirmed its message; the writer in `loops` takes up to 500 at a
time and stores them in one statement per space. The message carries the state sealed with the space's key.
"""

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import aio_pika
import orjson
from aio_pika.abc import AbstractIncomingMessage

from niadra.adapters.outbound.rabbitmq.connection import AGENT_STATE_QUEUE, RabbitConnection
from niadra.domain.state.agent_state import DurableState

log = logging.getLogger(__name__)

LINGER_S = 0.5
"""How long the writer gathers states after the first one of a batch: the batch is one statement per space."""


def encode_states(states: Sequence[DurableState]) -> bytes:
    return orjson.dumps([_json(s) for s in states])


def decode_states(body: bytes) -> list[DurableState]:
    return [_state(item) for item in orjson.loads(body)]


class RabbitAgentStateLog:
    def __init__(self, connection: RabbitConnection, linger_s: float = LINGER_S) -> None:
        self._rabbit = connection
        self._linger_s = linger_s

    async def publish(self, states: Sequence[DurableState], timeout_s: float) -> bool:
        if not states:
            return True
        message = aio_pika.Message(
            encode_states(states),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            async with asyncio.timeout(timeout_s):
                await self._rabbit.publisher.default_exchange.publish(
                    message, routing_key=AGENT_STATE_QUEUE, mandatory=True
                )
        except Exception:
            return False
        return True

    async def batches(self, max_items: int) -> AsyncIterator["RabbitStateBatch"]:
        channel = await self._rabbit.channel(prefetch=max_items)
        inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
        try:
            queue = await channel.get_queue(AGENT_STATE_QUEUE, ensure=False)
            await queue.consume(inbox.put)
            while True:
                yield await _collect(inbox, max_items, self._linger_s)
        finally:
            await channel.close()


@dataclass
class RabbitStateBatch:
    states: list[DurableState] = field(default_factory=list)
    messages: list[AbstractIncomingMessage] = field(default_factory=list)

    async def ack(self) -> None:
        for message in self.messages:
            await message.ack()

    async def retry_later(self) -> None:
        for message in self.messages:
            await message.nack(requeue=True)


async def _collect(
    inbox: "asyncio.Queue[AbstractIncomingMessage]", max_items: int, linger_s: float
) -> RabbitStateBatch:
    batch = RabbitStateBatch()
    await _take(batch, await inbox.get())
    deadline = asyncio.get_running_loop().time() + linger_s
    while len(batch.states) < max_items:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(inbox.get(), remaining)
        except TimeoutError:
            break
        await _take(batch, message)
    return batch


async def _take(batch: RabbitStateBatch, message: AbstractIncomingMessage) -> None:
    try:
        states = decode_states(message.body)
    except (ValueError, KeyError, TypeError):
        log.error("undecodable agent state message", extra={"message_id": message.message_id})
        await message.reject(requeue=False)
        return
    batch.states.extend(states)
    batch.messages.append(message)


def _json(s: DurableState) -> dict[str, Any]:
    return {
        "space_id": str(s.space_id),
        "scope_key": base64.b64encode(s.scope_key).decode(),
        "scope_kind": s.scope_kind,
        "source_id": str(s.source_id),
        "agent": s.agent,
        "version": s.version,
        "body_ct": base64.b64encode(s.body_ct).decode(),
        "dek_version": s.dek_version,
        "body_bytes": s.body_bytes,
        "updated_at": s.updated_at.isoformat(),
        "valid_until": s.valid_until.isoformat() if s.valid_until else None,
        "schema_version": s.schema_version,
        "subject_key": base64.b64encode(s.subject_key).decode() if s.subject_key else None,
        "named": sorted(t.hex() for t in s.named),
    }


def _state(item: dict[str, Any]) -> DurableState:
    return DurableState(
        space_id=UUID(item["space_id"]),
        scope_key=base64.b64decode(item["scope_key"]),
        scope_kind=item["scope_kind"],
        source_id=UUID(item["source_id"]),
        agent=item["agent"],
        version=int(item["version"]),
        body_ct=base64.b64decode(item["body_ct"]),
        dek_version=int(item["dek_version"]),
        body_bytes=int(item["body_bytes"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
        valid_until=datetime.fromisoformat(item["valid_until"]) if item.get("valid_until") else None,
        schema_version=item.get("schema_version"),
        subject_key=base64.b64decode(item["subject_key"]) if item.get("subject_key") else None,
        named=frozenset(bytes.fromhex(t) for t in item.get("named") or ()),
    )
