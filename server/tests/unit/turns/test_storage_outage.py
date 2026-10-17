"""A `pointer` or `hash_only` turn never depends on object storage (24.4.3): with the store down or hanging
the request is taken, the worker writes the objects once the store is back, and the same turn sent twice
meanwhile is still one turn. A stored turn needs the store and answers 503."""

import asyncio
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import InMemoryObjectStore
from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app import turns as turns_module
from niadra.app.turns import TurnService
from niadra.domain.common.errors import Unavailable
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.records import TakenIntake
from niadra.domain.turns.storage import HMAC_PURPOSE, Tier, by_id_key, turn_key
from niadra.ports.turns import TurnConsumers
from niadra.testing.cell import SPACE_ID
from tests.unit.turns.conftest import Taken, World
from tests.unit.turns.records import turn
from tests.unit.turns.test_otlp import TRACE, _split, json_trace

T1, T2, T3 = (f"0192f5a0-7c3e-7a10-9d2b-5e61a4c0d00{i}" for i in (1, 2, 3))
APPLY = Task(TaskName.APPLY_TURNS, SPACE_ID, {})


class Outage:
    """An object store that fails every call while `down`, or never answers while `hanging`."""

    def __init__(self, store: InMemoryObjectStore) -> None:
        self.store = store
        self.down = False
        self.hanging = False
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.store, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            if self.hanging:
                await asyncio.sleep(3600)
            if self.down:
                raise ConnectionError("object storage is down")
            return await method(*args, **kwargs)

        return call


class Parked:
    async def publish(self, tasks: Any) -> None:
        return None


def _service(world: World, store: Outage, taken: Taken) -> TurnService:
    return TurnService(
        world.cell.uow,
        TransactionTurnIntakeLog(world.cell.uow),
        world.cell.keys,
        store,  # type: ignore[arg-type]
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        consumers=TurnConsumers(observations=taken),
        workers=Parked(),  # type: ignore[arg-type]
    )


def _turns(world: World) -> int:
    return sum(row.turns for row in world.cell.uow.store.module("turns")["daily"].values())


async def _intakes(world: World) -> int:
    async with world.cell.uow(SPACE_ID) as uow:
        return len((await uow.turns.take_intake(100) or TakenIntake([])).rows)


async def test_pointer_mode_survives_an_object_storage_outage(world: World) -> None:
    world.configure(content_mode="pointer", sample_rate=0)
    store, taken = Outage(world.cell.objects), Taken()
    service = _service(world, store, taken)
    store.down = True

    first = await service.record(
        world.caller, [turn(T1, mode="pointer", flags=["handoff"]), turn(T2, mode="hash_only")]
    )
    again = await service.record(world.caller, [turn(T1, mode="pointer", flags=["handoff"])])

    assert (first.accepted, first.errors, again.accepted) == (2, [], 1)
    assert world.turn_objects() == []
    assert await _intakes(world) == 2
    with pytest.raises(Unavailable):
        await service.apply_turns(APPLY)
    assert await _intakes(world) == 2, "a store that is down costs no attempt and loses nothing"

    store.down = False
    await service.apply_turns(APPLY)

    assert await _intakes(world) == 0
    assert _turns(world) == 2, "the turn sent twice during the outage is one turn"
    assert [t.record.turn_id for t in taken.turns] == [T1, T2]
    conversation = await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")
    assert set(world.turn_objects()) == {
        turn_key(Tier.SHORT, SPACE_ID, conversation, T1),
        turn_key(Tier.KEPT, SPACE_ID, conversation, T1),
        turn_key(Tier.SHORT, SPACE_ID, conversation, T2),
        by_id_key(SPACE_ID, T1),
        by_id_key(SPACE_ID, T2),
    }
    view = await service.read(world.caller, T1)
    assert view.record.blobs["b:2"].pointer == "s3://company-turns/r.json"
    async with world.cell.uow(SPACE_ID) as uow:
        assert [k.turn_id for k in await uow.turns.kept([T1, T2])] == [T1]


async def test_a_turn_already_stored_before_the_outage_stays_one_turn(world: World) -> None:
    world.configure(content_mode="pointer", sample_rate=0)
    store, taken = Outage(world.cell.objects), Taken()
    service = _service(world, store, taken)
    await service.record(world.caller, [turn(T1, mode="pointer")])
    await service.apply_turns(APPLY)
    store.down = True
    deferred = await service.record(world.caller, [turn(T1, mode="pointer"), turn(T2, mode="pointer")])
    store.down = False
    await service.apply_turns(APPLY)
    assert deferred.accepted == 2
    assert _turns(world) == 2
    assert [t.record.turn_id for t in taken.turns] == [T1, T2]


async def test_a_stored_turn_needs_the_store_and_answers_503(world: World) -> None:
    store = Outage(world.cell.objects)
    service = _service(world, store, Taken())
    store.down = True
    with pytest.raises(Unavailable):
        await service.record(world.caller, [turn(T1), turn(T2, mode="pointer")])
    assert await _intakes(world) == 0


async def test_a_hanging_store_is_not_waited_for_and_then_left_alone_for_a_while(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turns_module, "FRAME_WRITE_TIMEOUT_S", 0.05)
    world.configure(content_mode="pointer", sample_rate=0)
    store = Outage(world.cell.objects)
    service = _service(world, store, Taken())
    store.hanging = True

    async with asyncio.timeout(2):
        first = await service.record(world.caller, [turn(T1, mode="pointer")])
    calls = store.calls
    second = await service.record(world.caller, [turn(T2, mode="pointer")])

    assert (first.accepted, second.accepted) == (1, 1)
    assert store.calls == calls, "the next request defers without trying the store"
    world.cell.clock.advance(seconds=31)
    store.hanging = False
    third = await service.record(world.caller, [turn(T3, mode="pointer")])
    assert third.accepted == 1
    assert by_id_key(SPACE_ID, T3) in world.turn_objects(), "the store is tried again after the pause"


async def test_a_worker_that_wrote_the_objects_and_failed_counts_them_on_its_retry(world: World) -> None:
    world.configure(content_mode="pointer", sample_rate=0)
    store = Outage(world.cell.objects)

    class FailsOnce(Taken):
        failed = False

        async def consume(self, uow: Any, space_id: UUID, turns: Any) -> None:
            if not self.failed:
                self.failed = True
                raise ValueError("the consumer failed once")
            await super().consume(uow, space_id, turns)

    taken = FailsOnce()
    service = _service(world, store, taken)
    store.down = True
    await service.record(world.caller, [turn(T1, mode="pointer")])
    store.down = False
    with pytest.raises(ValueError, match="failed once"):
        await service.apply_turns(APPLY)
    assert any(key.endswith(f"{T1}.bin") for key in world.turn_objects()), "the objects were written"
    await service.apply_turns(APPLY)
    assert _turns(world) == 1
    assert [t.record.turn_id for t in taken.turns] == [T1]


async def test_a_later_part_of_a_trace_taken_during_an_outage_is_merged_once_even_on_a_retry(
    world: World,
) -> None:
    store = Outage(world.cell.objects)

    class FailsOnSecond(Taken):
        calls = 0

        async def consume(self, uow: Any, space_id: UUID, turns: Any) -> None:
            self.calls += 1
            if self.calls == 2:
                raise ValueError("the consumer failed once")
            await super().consume(uow, space_id, turns)

    world.configure(content_mode="pointer", sample_rate=0)
    taken = FailsOnSecond()
    service = _service(world, store, taken)
    first, later = _split(json_trace())
    await service.record_otlp(world.caller, first)
    await service.apply_turns(APPLY)
    store.down = True
    deferred = await service.record_otlp(world.caller, later)
    store.down = False
    with pytest.raises(ValueError, match="failed once"):
        await service.apply_turns(APPLY)
    await service.apply_turns(APPLY)

    assert deferred.accepted == 1
    view = await service.read(world.caller, f"otel-{TRACE}")
    assert len(view.record.calls) == 2
    rows = list(world.cell.uow.store.module("turns")["daily"].values())
    assert [(r.turns, r.tool_calls, r.model_calls) for r in rows] == [(1, 1, 1)]
    assert [len(t.record.calls) for t in taken.turns] == [1, 1]
