"""`PgObjectRepository` against a real PostgreSQL 17."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.objects import ObjectsService
from niadra.domain.common.ids import uuid7
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.state.observed import Report, latest, merge
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import ActionStatus, EventKind, HandleType
from niadra.testing.cell import SPACE_ID, PatchedUnitOfWorkFactory
from tests.unit.objects.fakes import FakeIdentity, FakeIngest, FakePipeline, config_with, event, handle

NOW = datetime(2026, 9, 22, 14, 6, tzinfo=UTC)
SOURCE = uuid7()
INVOICE = ObjectKey("invoice", "erp", "0823")


def _object(key: ObjectKey, owner: UUID) -> BusinessObject:
    return BusinessObject(uuid7(), key, {}, NOW, SOURCE, origin_handle_id=owner)


def _action(
    object_id: UUID | None, minutes: float = 0, status: ActionStatus = ActionStatus.DECLARED, **kw: object
) -> AgentAction:
    return AgentAction(
        uuid7(),
        uuid7(),
        SOURCE,
        "credit",
        status,
        NOW + timedelta(minutes=minutes),
        object_id,
        result="R$ 40",
        purpose="billing",
        **kw,  # type: ignore[arg-type]
    )


async def test_objects_state_timeline_and_participants(pg_uow: PgUnitOfWorkFactory) -> None:
    space, owner, contact = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        obj = await uow.objects.add_object(_object(INVOICE, owner))
        again = await uow.objects.add_object(_object(INVOICE, uuid7()))
        assert again.object_id == obj.object_id
        assert again.origin_handle_id == owner
        await uow.objects.set_state(
            obj.object_id,
            state={"status": "credited"},
            fields={},
            as_of=NOW,
            source_id=SOURCE,
            state_version="7",
            record_ref="erp://0823",
        )
        first, second = uuid7(), uuid7()
        assert await uow.objects.add_object_event(obj.object_id, first, NOW)
        assert not await uow.objects.add_object_event(obj.object_id, first, NOW)
        assert await uow.objects.add_object_event(obj.object_id, second, NOW + timedelta(minutes=1))
        await uow.objects.add_participants(obj.object_id, [(owner, "owner"), (contact, "contact")])
        await uow.objects.add_participants(obj.object_id, [(contact, "contact")])
        other = await uow.objects.add_object(_object(ObjectKey("order", "erp", "1"), uuid7()))
        await uow.objects.add_participants(other.object_id, [(contact, "driver")])
    async with pg_uow(space) as uow:
        stored = await uow.objects.get_object(INVOICE)
        assert stored
        assert stored.state == {"status": "credited"}
        assert stored.state_version == "7"
        assert stored.record_ref == "erp://0823"
        assert await uow.objects.object_event_ids(obj.object_id) == [second, first]
        assert [o.key for o in await uow.objects.objects_for([owner])] == [INVOICE]
        assert [o.key.type for o in await uow.objects.objects_for([contact])] == ["order", "invoice"]
    async with pg_uow(uuid7()) as uow:
        assert await uow.objects.get_object(INVOICE) is None


async def test_actions_are_encrypted_and_reconciled(pg_uow: PgUnitOfWorkFactory) -> None:
    space, owner = uuid7(), uuid7()
    async with pg_uow(space) as uow:
        obj = await uow.objects.add_object(_object(INVOICE, owner))
        declared = _action(obj.object_id)
        old = _action(obj.object_id, minutes=-600)
        loose = _action(None, closes_handle_id=owner)
        for a in (declared, old, loose):
            await uow.objects.add_action(a)
        await uow.objects.add_action(declared)  # idempotent
        raw = (
            await uow.session.execute(
                text("SELECT result_ct FROM agent_actions WHERE action_id = :a"), {"a": declared.action_id}
            )
        ).scalar_one()
        assert b"R$ 40" not in raw
    async with pg_uow(space) as uow:
        assert await uow.objects.action_for_event(declared.event_id) == declared
        window = await uow.objects.actions_on(
            obj.object_id,
            "credit",
            since=NOW - timedelta(hours=1),
            until=NOW + timedelta(hours=1),
            statuses=[ActionStatus.DECLARED],
        )
        assert window == [declared]
        assert await uow.objects.declared_before(NOW - timedelta(hours=6)) == [old]
        confirming = uuid7()
        assert not await uow.objects.is_confirming(confirming)
        await uow.objects.set_action_status(
            declared.action_id, ActionStatus.CONFIRMED, confirmed_by_event_id=confirming
        )
        await uow.objects.set_action_status(old.action_id, ActionStatus.DIVERGENT)
    async with pg_uow(space) as uow:
        assert await uow.objects.is_confirming(confirming)
        confirmed = await uow.objects.action_for_event(declared.event_id)
        assert confirmed
        assert confirmed.status is ActionStatus.CONFIRMED
        assert confirmed.confirmed_by_event_id == confirming
        assert {a.action_id for a in await uow.objects.actions_for([owner])} == {
            declared.action_id,
            old.action_id,
            loose.action_id,
        }
        recent = await uow.objects.actions_for([owner], since=NOW - timedelta(hours=1))
        assert [a.action_id for a in recent] == [
            loose.action_id,
            declared.action_id,
        ]  # same instant: newest id first
        divergent = await uow.objects.divergent_actions_for([owner], since=NOW - timedelta(days=1))
        assert [(a.action_id, origin) for a, origin in divergent] == [(old.action_id, owner)]
        assert [a.action_id for a in await uow.objects.actions_of_object(obj.object_id)] == [
            declared.action_id,
            old.action_id,
        ]


async def test_a_commitments_and_a_handoffs_actions_wait_for_no_system_event(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, owner = uuid7(), uuid7()
    async with pg_uow(space) as uow:
        invoice = await uow.objects.add_object(_object(INVOICE, owner))
        commitment = await uow.objects.add_object(
            _object(ObjectKey("commitment", "coordination", "c1"), owner)
        )
        handoff = await uow.objects.add_object(_object(ObjectKey("handoff", "coordination", "h1"), owner))
        credit = _action(invoice.object_id, minutes=-600)
        for a in (
            credit,
            _action(commitment.object_id, minutes=-600),
            _action(handoff.object_id, minutes=-600),
        ):
            await uow.objects.add_action(a)
    async with pg_uow(space) as uow:
        assert await uow.objects.declared_before(NOW - timedelta(hours=6)) == [credit]


async def test_the_18_7_reconciliation_through_the_service(pg_uow: PgUnitOfWorkFactory) -> None:
    """The service over PostgreSQL, with the other modules' ports faked."""
    identity, ingest, pipeline = FakeIdentity(), FakeIngest(), FakePipeline()
    marina = identity.add_profile(handle(HandleType.SYSTEM_ID))
    owner = [identity.handles[marina.handle_ids[0]]]
    uow = PatchedUnitOfWorkFactory(pg_uow, identity=identity, ingest=ingest, pipeline=pipeline)  # type: ignore[arg-type]
    service = ObjectsService(uow, FixedClock(NOW), StaticConfig(config_with()))
    action = event(
        EventKind.ACTION, NOW, owner, objects=[INVOICE], action={"operation": "credit", "result": "R$ 40"}
    )
    system = event(
        EventKind.SYSTEM_EVENT,
        NOW + timedelta(seconds=30),
        owner,
        objects=[INVOICE],
        canonical_type="invoice.credited",
        fields={"amount": "40.00"},
    )
    for e in (action, system):
        ingest.add(e)
        await service.apply_object_event(
            Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
        )
    async with pg_uow(SPACE_ID) as u:
        obj = await u.objects.get_object(INVOICE)
        assert obj
        assert obj.state["last_event_type"] == "invoice.credited"
        [recorded] = await u.objects.actions_of_object(obj.object_id)
        assert recorded.status is ActionStatus.CONFIRMED
        assert recorded.confirmed_by_event_id == system.event_id
        [delivery] = await u.notify.list_deliveries()
        assert delivery.event_type == "action.recorded"
    assert TaskName.CLOSE_ITEMS in {t.name for t in pipeline.outbox}


async def _stored(uow: Any, object_id: UUID) -> bytes | None:
    row = (
        await uow.session.execute(
            text("SELECT state_ct FROM business_objects WHERE object_id = :o"), {"o": object_id}
        )
    ).one()
    return row.state_ct


async def test_the_state_is_sealed(pg_uow: PgUnitOfWorkFactory) -> None:
    space, owner = uuid7(), uuid7()
    async with pg_uow(space) as uow:
        obj = await uow.objects.add_object(
            BusinessObject(uuid7(), INVOICE, {"status": "open"}, NOW, SOURCE, origin_handle_id=owner)
        )
        assert obj.state == {"status": "open"}
        sealed = await _stored(uow, obj.object_id)
        assert sealed is not None
        assert b"open" not in sealed
        await uow.objects.set_state(
            obj.object_id,
            state={"status": "credited", "amount": "40.00"},
            fields={},
            as_of=NOW,
            source_id=SOURCE,
            state_version="2",
            record_ref=None,
        )
        sealed = await _stored(uow, obj.object_id)
        assert sealed is not None
        assert b"credited" not in sealed
        assert b"40.00" not in sealed
    async with pg_uow(space) as uow:
        stored = await uow.objects.get_object(INVOICE)
        assert stored is not None
        assert stored.state == {"status": "credited", "amount": "40.00"}
        [exported] = await uow.objects.export_rows("business_objects", after_seq=0, upto_seq=2**62, limit=10)
        assert exported["state"] == {"status": "credited", "amount": "40.00"}


async def test_the_fields_keep_their_stamps_apart_from_their_sealed_values(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid7()
    held, _ = merge({}, Report("src:erp", {"status": "open", "amount": 40}, NOW, NOW, NOW, version="3"))
    later = NOW + timedelta(minutes=5)
    held, _ = merge(held, Report("tool:quote", {"amount": 41}, later, later, later, provenance="live"))
    async with pg_uow(space) as uow:
        obj = await uow.objects.add_object(_object(INVOICE, uuid7()))
        await uow.objects.set_state(
            obj.object_id,
            state=latest(held),
            fields=held,
            as_of=later,
            source_id=SOURCE,
            state_version="3",
            record_ref=None,
        )
        meta = (
            await uow.session.execute(
                text("SELECT field_meta::text FROM business_objects WHERE object_id = :o"),
                {"o": obj.object_id},
            )
        ).scalar_one()
        assert "open" not in meta
        assert "41" not in meta
        assert '"provenance": "live"' in meta
    async with pg_uow(space) as uow:
        stored = await uow.objects.get_object(INVOICE)
        assert stored is not None
        assert stored.fields == held
        assert stored.state == {"status": "open", "amount": 41}


async def test_an_object_without_an_owner_takes_the_first_one_and_keeps_it(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, first, second = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        obj = await uow.objects.add_object(BusinessObject(uuid7(), INVOICE, {}, NOW, SOURCE))
        assert obj.origin_handle_id is None
        await uow.objects.set_owner(obj.object_id, first)
        await uow.objects.set_owner(obj.object_id, second)
    async with pg_uow(space) as uow:
        stored = await uow.objects.get_object(INVOICE)
        assert stored is not None
        assert stored.origin_handle_id == first
        assert [o.key for o in await uow.objects.objects_for([first])] == [INVOICE]
