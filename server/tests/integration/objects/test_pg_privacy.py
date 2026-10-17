"""`PgObjectRepository` in the erasure cascade (8.4) and the continuous export (22.3)."""

from datetime import UTC, datetime
from uuid import UUID

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.vocabulary import ActionStatus

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = uuid7()


def obj(owner: UUID, number: str) -> BusinessObject:
    return BusinessObject(uuid7(), ObjectKey("invoice", "erp", number), {"amount": "40"}, T0, SOURCE, owner)


def action(object_id: UUID | None, closes_handle: UUID | None = None) -> AgentAction:
    return AgentAction(
        uuid7(),
        uuid7(),
        SOURCE,
        "credit",
        ActionStatus.DECLARED,
        T0,
        object_id,
        result="creditou R$ 40",
        closes_handle_id=closes_handle,
    )


async def test_the_owner_erasure_takes_owned_objects_their_trail_and_participations(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, marina, other = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        owned = await uow.objects.add_object(obj(marina, "0823"))
        theirs = await uow.objects.add_object(obj(other, "0999"))
        await uow.objects.add_object_event(owned.object_id, uuid7(), T0)
        await uow.objects.add_participants(owned.object_id, [(other, "payer")])
        await uow.objects.add_participants(theirs.object_id, [(marina, "payer")])
        await uow.objects.add_action(action(owned.object_id))
        await uow.objects.add_action(action(None, closes_handle=marina))
        kept = action(theirs.object_id)
        await uow.objects.add_action(kept)
    scope = ErasureScope(uuid7(), TargetKind.PROFILE, (marina,), uuid7(), None, T0)
    tables: list[str] = []
    while True:
        async with pg_uow(space) as uow:
            batch = await uow.objects.erase_for(scope, limit=2)
        tables += [r.table for r in batch]
        if len(batch) < 2:
            break
    assert sorted(tables) == [
        "agent_actions",
        "agent_actions",
        "business_objects",
        "object_events",
        "object_participants",
        "object_participants",
    ]
    async with pg_uow(space) as uow:
        assert await uow.objects.get_object_by_id(owned.object_id) is None
        assert await uow.objects.objects_for([marina]) == []
        assert [a.action_id for a in await uow.objects.actions_for([other])] == [kept.action_id]


async def test_erased_events_take_their_actions_and_object_events(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    recorded, applied = action(None), uuid7()
    async with pg_uow(space) as uow:
        invoice = await uow.objects.add_object(obj(uuid7(), "0823"))
        await uow.objects.add_action(recorded)
        await uow.objects.add_object_event(invoice.object_id, applied, T0)
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), None, "c1", T0, (recorded.event_id, applied))
    async with pg_uow(space) as uow:
        rows = await uow.objects.erase_for(scope, limit=10)
        assert sorted(r.table for r in rows) == ["agent_actions", "object_events"]
        assert all(r.change_seq for r in rows)


async def test_export_reads_objects_events_and_decrypted_actions(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    credit = action(None)
    async with pg_uow(space) as uow:
        invoice = await uow.objects.add_object(obj(uuid7(), "0823"))
        await uow.objects.add_object_event(invoice.object_id, uuid7(), T0)
        await uow.objects.add_action(credit)
        await uow.objects.set_action_status(credit.action_id, ActionStatus.CONFIRMED)
        rows = {
            t: await uow.objects.export_rows(t, after_seq=0, upto_seq=2**62, limit=10)
            for t in ("business_objects", "object_events", "agent_actions")
        }
    assert rows["business_objects"][0]["key"] == {
        "type": "invoice",
        "namespace": "erp",
        "external_id": "0823",
    }
    assert rows["object_events"][0]["object_id"] == invoice.object_id
    [exported] = rows["agent_actions"]
    assert (exported["result"], exported["status"]) == ("creditou R$ 40", ActionStatus.CONFIRMED)
