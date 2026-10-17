"""A restart of the state store, which keeps nothing, as a read sees it (06, 3).

The real hot-layer adapter on fakeredis: the store is wiped mid-life, the read says `live` is incomplete
instead of answering with the turns it lost, the loops' check rebuilds the hot layer from the event log,
and the read is complete again once the rebuild seals the marker.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import fakeredis

from niadra.adapters.outbound.redis.hot import RedisHotLayer
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import EventRecord
from niadra.domain.vocabulary import EventKind, Speaker, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.compile.factories import PHONE, T0
from tests.unit.ingest.fakes import build
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()
MISSED_CALL = "liguei e ninguém atendeu"


async def read(h: Harness) -> ContextResponse:
    ask = ContextRequest(subject=SUBJECT, verification=Verification.V1, conversation_id="c1")
    return await h.serve.context(AGENT, ask)


async def a_turn_in_the_event_log(cell: Cell) -> None:
    at = T0 - timedelta(minutes=10)
    record = EventRecord(
        event_id=uuid7(int(at.timestamp() * 1000)),
        kind=EventKind.MESSAGE,
        source_id=WHATSAPP_SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=at,
        received_at=at,
        handle_ids=(PHONE,),
        text=MISSED_CALL,
        conversation_id="voice-1",
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.ingest.insert_events([NewEvent(SPACE_ID, record, "voice-1:1")])


async def test_a_wiped_state_store_is_never_read_as_complete_and_comes_back_after_the_rebuild(
    cell: Cell,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    valkey = replace(cell, hot=RedisHotLayer(redis, cell.clock))
    h = Harness(valkey)
    ingest, _, _ = build(valkey)
    await h.compile()
    await a_turn_in_the_event_log(valkey)
    assert await ingest.watch_hot_layer()  # a store never sealed is rebuilt, as after the first deploy

    before = await read(h)
    assert ([t.text for t in before.live], before.live_complete) == ([MISSED_CALL], True)

    await redis.flushall()  # the pod restarted: no persistence, nothing came back

    lost = await read(h)
    assert (lost.live, lost.live_complete) == ([], False)

    # Mid-rebuild the turn is back, but the marker is not sealed yet: still incomplete.
    seal = valkey.hot.seal
    during: list[ContextResponse] = []

    async def read_before_sealing(*args: Any) -> bool:
        during.append(await read(h))
        return await seal(*args)

    valkey.hot.seal = read_before_sealing  # type: ignore[method-assign]
    assert await ingest.watch_hot_layer()
    [mid] = during
    assert ([t.text for t in mid.live], mid.live_complete) == ([MISSED_CALL], False)

    after = await read(h)
    assert ([t.text for t in after.live], after.live_complete) == ([MISSED_CALL], True)
    await redis.aclose()
