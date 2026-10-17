"""Business objects and agent actions in the erasure cascade (8.4) and the continuous export (22.3)."""

from datetime import UTC, datetime
from uuid import UUID

from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.vocabulary import ActionStatus

T0 = datetime(2026, 9, 22, 14, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c003")


def obj(owner: UUID | None, number: str) -> BusinessObject:
    return BusinessObject(uuid7(), ObjectKey("invoice", "erp", number), {"amount": "40"}, T0, SOURCE, owner)


def action(object_id: UUID | None, *, closes_handle: UUID | None = None) -> AgentAction:
    return AgentAction(
        action_id=uuid7(),
        event_id=uuid7(),
        source_id=SOURCE,
        operation="credit",
        status=ActionStatus.DECLARED,
        occurred_at=T0,
        object_id=object_id,
        result="creditou R$ 40",
        closes_handle_id=closes_handle,
    )


async def test_the_owner_erasure_takes_owned_objects_their_trail_and_participations() -> None:
    repo = InMemoryObjectRepository(MemoryStore())
    marina, other = uuid7(), uuid7()
    owned = await repo.add_object(obj(marina, "0823"))
    theirs = await repo.add_object(obj(other, "0999"))
    await repo.add_object_event(owned.object_id, uuid7(), T0)
    await repo.add_participants(owned.object_id, [(other, "payer")])
    await repo.add_participants(theirs.object_id, [(marina, "payer")])
    await repo.add_action(action(owned.object_id))
    closing = action(None, closes_handle=marina)
    await repo.add_action(closing)
    kept = action(theirs.object_id)
    await repo.add_action(kept)
    target = ErasureScope(uuid7(), TargetKind.HANDLE, (marina,), None, None, T0)
    rows = []
    while len(batch := await repo.erase_for(target, limit=2)) == 2:
        rows += batch
    rows += batch
    assert sorted(r.table for r in rows) == [
        "agent_actions",
        "agent_actions",
        "business_objects",
        "object_events",
        "object_participants",
        "object_participants",
    ]
    assert await repo.get_object_by_id(owned.object_id) is None
    assert await repo.objects_for([marina]) == []
    assert [a.action_id for a in await repo.actions_for([other])] == [kept.action_id]


async def test_erased_events_take_the_actions_and_object_events_they_recorded() -> None:
    repo = InMemoryObjectRepository(MemoryStore())
    invoice = await repo.add_object(obj(uuid7(), "0823"))
    recorded = action(invoice.object_id)
    await repo.add_action(recorded)
    applied = uuid7()
    await repo.add_object_event(invoice.object_id, applied, T0)
    target = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), None, "c1", T0, (recorded.event_id, applied))
    assert sorted(r.table for r in await repo.erase_for(target, limit=10)) == [
        "agent_actions",
        "object_events",
    ]
    assert await repo.get_object_by_id(invoice.object_id) is not None


async def test_export_follows_inserts_and_updates() -> None:
    repo = InMemoryObjectRepository(MemoryStore())
    invoice = await repo.add_object(obj(uuid7(), "0823"))
    credit = action(invoice.object_id)
    await repo.add_action(credit)
    await repo.set_action_status(credit.action_id, ActionStatus.CONFIRMED)
    [row] = await repo.export_rows("agent_actions", after_seq=0, upto_seq=10**6, limit=10)
    assert (row["action_id"], row["status"], row["result"]) == (
        credit.action_id,
        ActionStatus.CONFIRMED,
        "creditou R$ 40",
    )
    [state] = await repo.export_rows("business_objects", after_seq=0, upto_seq=10**6, limit=10)
    assert state["key"] == {"type": "invoice", "namespace": "erp", "external_id": "0823"}
    assert state["change_seq"] < row["change_seq"]
