"""An erasure's fence on the turns module (24.5.5): turns of the target's conversation still in the intake
when the erasure runs, or arriving after it, never come back: they count for nothing, reach no module, and
their objects go; and an event naming the target that was still queued never joins the notification feed."""

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest

from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.turns import TurnService
from niadra.domain.common.ids import uuid7
from niadra.domain.notify.feed import NewFeedEvent
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import HMAC_PURPOSE
from niadra.ports.turns import TurnConsumers
from niadra.testing.cell import SPACE_ID
from tests.unit.turns.conftest import Taken, World
from tests.unit.turns.records import turn

T1, T2, T3 = (f"0192f5a0-7c3e-7a10-9d2b-5e61a4c0d00{i}" for i in (1, 2, 3))


class Parked:
    """Workers that take the hand-over and do nothing: the turns wait in the intake."""

    async def publish(self, tasks: Any) -> None:
        return None


def _service(world: World, taken: Taken) -> TurnService:
    return TurnService(
        world.cell.uow,
        TransactionTurnIntakeLog(world.cell.uow),
        world.cell.keys,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        consumers=TurnConsumers(observations=taken),
        workers=Parked(),  # type: ignore[arg-type]
    )


async def test_turns_waiting_in_the_intake_or_arriving_after_the_erasure_never_come_back(
    world: World,
) -> None:
    world.configure(sample_rate=0)
    taken = Taken()
    service = _service(world, taken)
    await service.record(world.caller, [turn(T1, flags=["handoff"]), turn(T2, conversation_id="c-other")])
    conversation = await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")
    scope = ErasureScope(
        uuid7(), TargetKind.CONVERSATION, (), None, "c-8812", world.cell.clock.now(),
        conversation_hashes=(conversation,),
    )  # fmt: skip

    await service.fence(SPACE_ID, scope)

    assert [t.record.turn_id for t in taken.turns] == [T2], "the other conversation's turn goes on"
    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.turns.kept([T1]) == []
        [daily] = await uow.turns.daily(date(2026, 9, 29), date(2026, 9, 29), None)
    assert daily.turns == 1
    assert not [k for k in world.turn_objects() if T1 in k and "/by-id/" not in k]

    world.cell.clock.advance(minutes=5)
    await service.record(world.caller, [turn(T3, flags=["handoff"])])  # during the cascade
    await service.purge(SPACE_ID, scope)
    assert [t.record.turn_id for t in taken.turns] == [T2]
    await service.record(world.caller, [turn(T3, flags=["handoff"])])  # a producer's late copy
    await service.apply_turns(Task(TaskName.APPLY_TURNS, SPACE_ID, {}))
    assert [t.record.turn_id for t in taken.turns] == [T2]
    assert not [k for k in world.turn_objects() if T3 in k and "/by-id/" not in k]

    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.turns.purge_tombstones(world.cell.clock.now() + timedelta(days=9)) == 1


async def test_an_event_naming_the_target_still_queued_never_joins_the_feed(world: World) -> None:
    handle = uuid4()
    service = _service(world, Taken())
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (handle,), None, None, world.cell.clock.now())
    await service.fence(SPACE_ID, scope)

    def event(**data: str) -> NewFeedEvent:
        return NewFeedEvent(uuid7(), "trait.added", world.cell.clock.now(), {"id": "e", "data": data})

    async with world.cell.uow(SPACE_ID) as uow:
        assert await uow.feed.append(event(handle=str(handle))) is None
        assert await uow.feed.append(event(handle=str(uuid4()))) == 1


async def test_a_late_copy_whose_object_fails_to_go_is_applied_again_until_it_goes(world: World) -> None:
    # No tier expires a turn any more: an object of a fenced conversation left behind by a failed delete would
    # stay for good. The delete runs before the commit, so the intake is applied again and the object goes.
    world.configure(sample_rate=0)
    taken = Taken()
    service = _service(world, taken)
    conversation = await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")
    scope = ErasureScope(
        uuid7(), TargetKind.CONVERSATION, (), None, "c-8812", world.cell.clock.now(),
        conversation_hashes=(conversation,),
    )  # fmt: skip
    await service.fence(SPACE_ID, scope)
    await service.purge(SPACE_ID, scope)
    await service.record(world.caller, [turn(T3, flags=["handoff"])])  # a producer's late copy
    delete = world.cell.objects.delete
    failed: list[str] = []

    async def once(key: str) -> None:
        if T3 in key and not failed:
            failed.append(key)
            raise OSError("the store did not answer")
        await delete(key)

    world.cell.objects.delete = once  # type: ignore[method-assign]
    with pytest.raises(OSError, match="did not answer"):
        await service.apply_turns(Task(TaskName.APPLY_TURNS, SPACE_ID, {}))
    assert failed, "the first delete failed"
    await service.apply_turns(Task(TaskName.APPLY_TURNS, SPACE_ID, {}))

    assert not [k for k in world.turn_objects() if T3 in k and "/by-id/" not in k]
    assert taken.turns == []
