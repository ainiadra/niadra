"""`apply_object_event` end to end within the objects module: the 14h05/14h06/14h07 flow of 18.7."""

from dataclasses import replace
from typing import Any

import pytest

from niadra.app.objects import ObjectsService
from niadra.config.models import TaskView
from niadra.domain.common.errors import Forbidden, NotFound
from niadra.domain.records import EventRecord, ObjectKey, ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import ActionStatus, EventKind, HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.objects.fakes import FakePipeline, World, at, config_with, event, handle

INVOICE = ObjectKey("invoice", "erp", "0823")


@pytest.fixture
def world(cell: Cell) -> World:
    cell.config.put(config_with())
    return World(cell)


@pytest.fixture
def marina(world: World) -> ProfileRecord:
    return world.identity.add_profile(handle(), handle(HandleType.SYSTEM_ID))


def service(world: World) -> ObjectsService:
    return ObjectsService(world.uow, world.cell.clock, world.cell.config)


async def apply(world: World, e: EventRecord, profile: ProfileRecord) -> None:
    world.ingest.add(e)
    task = Task(
        TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)}, profile_id=profile.profile_id
    )
    await service(world).apply_object_event(task)


def handles_of(world: World, profile: ProfileRecord) -> list[Any]:
    return [world.identity.handles[h] for h in profile.handle_ids]


def credit(
    world: World, marina: ProfileRecord, minutes: float, result: str = "R$ 40", **kw: Any
) -> EventRecord:
    block = {
        "operation": "credit",
        "result": result,
        "closes": {"object": {"type": "invoice", "namespace": "erp", "id": "0823"}, "operation": "contest"},
    }
    return event(
        EventKind.ACTION, at(minutes), handles_of(world, marina), objects=[INVOICE], action=block, **kw
    )


def credited(world: World, marina: ProfileRecord, minutes: float, amount: str = "40.00") -> EventRecord:
    return event(
        EventKind.SYSTEM_EVENT,
        at(minutes),
        handles_of(world, marina),
        objects=[INVOICE],
        canonical_type="invoice.credited",
        fields={"amount": amount, "status": "credited"},
    )


async def stored(world: World) -> tuple[Any, list[Any]]:
    async with world.cell.uow(SPACE_ID) as uow:
        obj = await uow.objects.get_object(INVOICE)
        actions = await uow.objects.actions_of_object(obj.object_id) if obj else []
    return obj, actions


async def test_the_18_7_flow_contest_credit_confirm_and_read(world: World, marina: ProfileRecord) -> None:
    # 14h05: the contest arrives in the app, linked to the invoice by `object_refs`.
    message = event(
        EventKind.MESSAGE,
        at(5),
        handles_of(world, marina),
        objects=[INVOICE],
        source_id=WHATSAPP_SOURCE,
        channel="app",
    )
    await apply(world, message, marina)
    obj, _ = await stored(world)
    assert obj.state == {}  # only a system event gives an object state

    # 14h06: the billing agent records the credit; its source is trusted for `credit`, so it closes items.
    action = credit(world, marina, 6)
    await apply(world, action, marina)
    _, actions = await stored(world)
    assert [a.status for a in actions] == [ActionStatus.DECLARED]
    closing = world.pipeline.take(TaskName.CLOSE_ITEMS)
    assert len(closing) == 1
    assert closing[0].payload["action_id"] == str(actions[0].action_id)
    assert closing[0].payload["closes"]["operation"] == "contest"
    assert closing[0].profile_id == marina.profile_id

    # 14h06, ERP: `invoice.credited` confirms the action; one record, not two credits.
    system = credited(world, marina, 6.5)
    await apply(world, system, marina)
    obj, actions = await stored(world)
    assert obj.state["last_event_type"] == "invoice.credited"
    assert obj.as_of == at(6.5)
    assert [(a.status, a.confirmed_by_event_id) for a in actions] == [
        (ActionStatus.CONFIRMED, system.event_id)
    ]
    assert world.pipeline.take(TaskName.CLOSE_ITEMS)[0].payload["event_id"] == str(system.event_id)
    follow_ups = {t.name for t in world.pipeline.outbox if t.profile_id == marina.profile_id}
    assert {TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.TRIGGER} <= follow_ups

    # 14h07: the voice agent's timeline shows the confirmed credit and the system event, no content.
    read = await service(world).timeline(principal(VOICE_SOURCE), INVOICE)
    kinds = [(i.kind, i.text) for i in read.timeline.items]
    assert kinds == [
        ("system_event", "invoice.credited"),
        ("action", "credit (confirmed)"),
        ("message", "message"),
    ]
    assert read.profile_id == marina.profile_id
    assert read.object_id == obj.object_id


async def test_system_event_before_the_action_confirms_it_on_arrival(
    world: World, marina: ProfileRecord
) -> None:
    system = credited(world, marina, 6)
    await apply(world, system, marina)
    await apply(world, credit(world, marina, 7), marina)
    _, actions = await stored(world)
    assert actions[0].status is ActionStatus.CONFIRMED
    assert actions[0].confirmed_by_event_id == system.event_id


async def test_a_disagreeing_system_event_makes_the_action_divergent(
    world: World, marina: ProfileRecord
) -> None:
    await apply(world, credit(world, marina, 6), marina)
    await apply(world, credited(world, marina, 7, amount="30"), marina)
    _, actions = await stored(world)
    assert actions[0].status is ActionStatus.DIVERGENT


async def test_late_event_never_moves_a_field_back_and_brings_what_no_newer_one_said(
    world: World, marina: ProfileRecord
) -> None:
    await apply(world, credited(world, marina, 10, amount="40"), marina)
    late = event(
        EventKind.SYSTEM_EVENT,
        at(1),
        handles_of(world, marina),
        objects=[INVOICE],
        canonical_type="invoice.issued",
        fields={"status": "open", "due_on": "2026-10-05"},
    )
    await apply(world, late, marina)
    obj, _ = await stored(world)
    assert obj.state == {
        "amount": "40",
        "status": "credited",
        "due_on": "2026-10-05",
        "last_event_type": "invoice.credited",
    }
    assert obj.as_of == at(10)
    async with world.cell.uow(SPACE_ID) as uow:
        assert late.event_id in await uow.objects.object_event_ids(obj.object_id)  # still on the timeline


async def test_an_event_moves_only_the_fields_it_names(world: World, marina: ProfileRecord) -> None:
    await apply(world, credited(world, marina, 10, amount="40"), marina)
    paid = event(
        EventKind.SYSTEM_EVENT,
        at(20),
        handles_of(world, marina),
        objects=[INVOICE],
        canonical_type="invoice.paid",
        fields={"status": "paid"},
    )
    await apply(world, paid, marina)
    obj, _ = await stored(world)
    assert obj.state == {"amount": "40", "status": "paid", "last_event_type": "invoice.paid"}
    assert obj.as_of == at(20)
    assert obj.fields["amount"][f"src:{BILLING_SOURCE}"].valid_at == at(10)
    assert obj.fields["status"][f"src:{BILLING_SOURCE}"].valid_at == at(20)


async def test_reapplying_an_event_is_a_no_op(world: World, marina: ProfileRecord) -> None:
    action = credit(world, marina, 6)
    await apply(world, action, marina)
    world.pipeline.outbox.clear()
    await apply(world, action, marina)
    _, actions = await stored(world)
    assert len(actions) == 1
    assert world.pipeline.outbox == []


async def test_a_declared_action_outside_its_sources_list_closes_nothing_until_confirmed(
    world: World, marina: ProfileRecord
) -> None:
    # The billing agent declared it may close `credit` only: a list it declared stays a closed one.
    reschedule = event(
        EventKind.ACTION,
        at(6),
        handles_of(world, marina),
        objects=[INVOICE],
        action={"operation": "reschedule"},
        source_id=BILLING_SOURCE,
    )
    await apply(world, reschedule, marina)
    assert world.pipeline.take(TaskName.CLOSE_ITEMS) == []


async def test_a_declared_action_of_a_source_with_no_list_is_put_to_the_closing_gates(
    world: World, marina: ProfileRecord
) -> None:
    # Zero touch: with no list declared, every operation may close, and the closing's own gates
    # decide (the words, the decision model, the delivery gate). In juridico-zero (07/10/2026) the
    # attendant's `notify_client` never reached them, and no legitimate notice closed anything.
    notify = event(
        EventKind.ACTION,
        at(6),
        handles_of(world, marina),
        objects=[INVOICE],
        action={"operation": "reschedule"},
        source_id=VOICE_SOURCE,
    )
    await apply(world, notify, marina)
    assert [t.payload["operation"] for t in world.pipeline.take(TaskName.CLOSE_ITEMS)] == ["reschedule"]


async def test_actions_of_a_flagged_session_are_quarantined(world: World, marina: ProfileRecord) -> None:
    plain = credit(world, marina, 6)
    flagged = replace(plain, content_meta={"injection": True})
    await apply(world, flagged, marina)
    await apply(world, credited(world, marina, 6.5), marina)
    _, actions = await stored(world)
    assert actions[0].status is ActionStatus.QUARANTINED
    # the system event still closes items as the system of record, but never through the quarantined action
    assert all(t.payload["action_id"] is None for t in world.pipeline.take(TaskName.CLOSE_ITEMS))


async def test_declared_actions_turn_divergent_when_the_window_runs_out(
    world: World, marina: ProfileRecord
) -> None:
    await apply(world, credit(world, marina, 6), marina)
    world.pipeline = FakePipeline()  # the follow-ups of the apply ran
    svc = service(world)
    assert await svc.expire_declared(at(60)) == 0
    assert await svc.expire_declared(at(6, days=1)) == 1
    _, actions = await stored(world)
    assert actions[0].status is ActionStatus.DIVERGENT
    assert TaskName.TRIGGER in {t.name for t in world.pipeline.outbox}


async def test_action_recorded_webhook_carries_ids_only(world: World, marina: ProfileRecord) -> None:
    await apply(world, credit(world, marina, 6), marina)
    async with world.cell.uow(SPACE_ID) as uow:
        [delivery] = await uow.notify.list_deliveries()
    assert delivery.event_type == "action.recorded"
    assert delivery.body["data"]["operation"] == "credit"
    assert "result" not in delivery.body["data"]
    assert delivery.body["subject"] == {"pseudonym": marina.pseudonym, "kind": "person"}


async def test_read_object_serves_the_typed_state_of_an_undeclared_type(
    world: World, marina: ProfileRecord
) -> None:
    await apply(world, credited(world, marina, 6), marina)
    read = await service(world).read_object(principal(BILLING_SOURCE), INVOICE)
    assert read.state.fields["amount"].v == "40.00"
    assert read.state.ref.id == INVOICE.external_id


async def test_reading_needs_the_purpose_of_the_task_view(
    world: World, marina: ProfileRecord, cell: Cell
) -> None:
    cell.config.put(
        config_with(
            views=[TaskView(name="task:billing", object_types=["invoice"], purposes=frozenset({"billing"}))]
        )
    )
    await apply(world, credited(world, marina, 6), marina)
    await service(world).read_object(principal(BILLING_SOURCE, config=cell.config.space(SPACE_ID)), INVOICE)
    with pytest.raises(Forbidden):
        await service(world).read_object(
            principal(WHATSAPP_SOURCE, config=cell.config.space(SPACE_ID)), INVOICE
        )
    with pytest.raises(NotFound):
        await service(world).read_object(principal(BILLING_SOURCE), ObjectKey("invoice", "erp", "missing"))


async def test_the_timeline_reads_events_that_left_for_the_cold_archive(
    world: World, marina: ProfileRecord
) -> None:
    # Past the hot window the system event lives only in the archive; the timeline still has it.
    import json
    from datetime import UTC, datetime

    from niadra.app.archive import ArchiveReader, content_aad, index_key
    from niadra.domain.common.ids import uuid7_ms
    from niadra.domain.privacy.files import json_line, to_parquet
    from niadra.domain.privacy.retention import month_key

    system = credited(world, marina, 6.5)
    await apply(world, system, marina)
    del world.ingest.events[system.event_id]
    cell = world.cell
    content, dek = await cell.keys.encrypt(SPACE_ID, json_line(system).encode(), content_aad(system.event_id))
    row = {"event_id": str(system.event_id), "handle_ids": [], "dek_version": dek, "content_ct": content}
    # The part is the month of the id, minted at the real arrival; the test clock's `received_at` can sit in
    # another month (it did on the night of 30/09 to 01/10 UTC).
    month = month_key(datetime.fromtimestamp(uuid7_ms(system.event_id) / 1000, UTC).date())
    part = f"archive/events/{SPACE_ID}/{month}/part-00000.parquet"
    cell.objects.objects[part] = to_parquet([row])
    cell.objects.objects[index_key(SPACE_ID)] = json.dumps({"months": {month: [{"key": part}]}}).encode()

    without = await service(world).timeline(principal(VOICE_SOURCE), INVOICE)
    assert without.timeline.items == []
    archived = ObjectsService(
        world.uow, cell.clock, cell.config, archive=ArchiveReader(cell.objects, cell.keys)
    )
    read = await archived.timeline(principal(VOICE_SOURCE), INVOICE)
    assert [(i.kind, i.text, i.id) for i in read.timeline.items] == [
        ("system_event", "invoice.credited", str(system.event_id))
    ]


async def test_a_split_takes_the_invoice_to_the_person_its_system_id_belongs_to(
    world: World, marina: ProfileRecord
) -> None:
    # An object stays with the handle it came from, so separating two people moves it with them.
    phone, account = marina.handle_ids
    await apply(world, credited(world, marina, 6.5), marina)
    del world.identity.profiles[marina.profile_id]
    caller = world.identity.add_profile(world.identity.handles[phone])
    holder = world.identity.add_profile(world.identity.handles[account])

    read = await service(world).timeline(principal(VOICE_SOURCE), INVOICE)
    assert read.profile_id == holder.profile_id
    async with world.cell.uow(SPACE_ID) as uow:
        [owned] = await uow.objects.objects_for(holder.handle_ids)
        # The caller took part in it and still finds it, but it is the holder's invoice.
        [involved] = await uow.objects.objects_for(caller.handle_ids)
    assert owned.key == involved.key == INVOICE
    assert owned.origin_handle_id == account


def status_only(minutes: float, status: str) -> EventRecord:
    """A system event that names the invoice and no one: a batch export of the ERP, say."""
    return event(
        EventKind.SYSTEM_EVENT,
        at(minutes),
        [],
        objects=[INVOICE],
        canonical_type="invoice.credited",
        fields={"status": status},
    )


async def apply_unowned(world: World, e: EventRecord) -> None:
    world.ingest.add(e)
    await service(world).apply_object_event(
        Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
    )


async def test_an_event_that_names_only_a_known_object_reaches_its_owner(
    world: World, marina: ProfileRecord
) -> None:
    await apply(world, credit(world, marina, 6), marina)
    world.pipeline.take(TaskName.CLOSE_ITEMS)
    await apply_unowned(world, status_only(7, "credited"))
    obj, actions = await stored(world)
    assert obj.state["status"] == "credited"
    assert obj.origin_handle_id in marina.handle_ids
    # The owner's pack, patterns and triggers are asked for again, and the action it confirms closes items.
    for name in (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.TRIGGER):
        assert world.pipeline.work[(SPACE_ID, marina.profile_id, name)][0] == 2
    assert [a.status for a in actions] == [ActionStatus.CONFIRMED]
    assert world.pipeline.take(TaskName.CLOSE_ITEMS)[0].profile_id == marina.profile_id


async def test_a_new_object_without_a_handle_waits_for_an_owner(world: World, marina: ProfileRecord) -> None:
    await apply_unowned(world, status_only(5, "open"))
    obj, _ = await stored(world)
    assert (obj.origin_handle_id, obj.state["status"]) == (None, "open")
    assert not [t for t in world.pipeline.outbox if t.name is TaskName.COMPILE]

    # The next event that names a customer links the object to them, and their pack takes it.
    await apply(world, credited(world, marina, 6), marina)
    obj, _ = await stored(world)
    assert obj.origin_handle_id in marina.handle_ids
    assert obj.state["status"] == "credited"
    async with world.cell.uow(SPACE_ID) as uow:
        assert [o.key for o in await uow.objects.objects_for(list(marina.handle_ids))] == [INVOICE]
    assert TaskName.COMPILE in {t.name for t in world.pipeline.outbox if t.profile_id == marina.profile_id}
