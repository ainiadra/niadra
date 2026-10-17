"""Outcomes across the real modules (24.7): an order the objects module writes reaches the
attribution inside its transaction, its lines are attributed to the card the agent showed, provisional until
the exchange window ends, the size kept becomes the subject's, and erasure and the subject's package cover the
links."""

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.app.objects import ObjectsService
from niadra.config.measurement import MeasurementSettings
from niadra.config.models import FeatureSettings, PurposeSettings, PurposeSpec, SpaceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ExportRequest, ForgetRequest
from niadra.contracts.signals import BiFile, BiUploadRequest, ReconcileRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import InvalidInput
from niadra.domain.privacy.purposes import LegalBasis
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.signals.storage import bi_key
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, Feature, HandleType
from niadra.ports.turns import AppliedTurn
from niadra.ports.uow import UnitOfWork
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.measure.tokens import build_token
from tests.unit.objects.fakes import World, at, config_with, event, handle

PHONE = "+5511955554444"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
EXPOSURE = UUID("0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55")
DRESS, BAG = "item_variant:loja:e2e-101", "item_variant:loja:e2e-102"
ORDER_TYPE = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "order_management"},
    "fields": {"lines": {"type": "list"}},
    "sources": {"order_management": {"kind": "push", "precedence": 1}},
    "states": ["placed", "delivered", "kept", "returned", "cancelled"],
    "lifecycle": {
        "transitions": [
            {"from": "placed", "to": "delivered", "by": ["source"]},
            {"from": "delivered", "to": "kept", "by": ["source", "clock"]},
            {"from": "delivered", "to": "returned", "by": ["source"]},
            {"from": "*", "to": "cancelled", "by": ["source"]},
        ],
        "outcome": {
            "provisional": ["delivered"],
            "final": ["kept", "returned", "cancelled"],
            "expired_without_outcome": {"after": "60d"},
        },
    },
}
PURCHASE = {
    "name": "purchase",
    "object_type": "order",
    "success_states": ["delivered", "kept"],
    "lines": {"size": "size_label", "size_attribute": "size.dress", "return_reason": "return_reason"},
    "final_after": "30d",
}


def configured(measurement: bool = True) -> SpaceConfig:
    features = {Feature.SIGNALS, Feature.STATE}
    features |= {Feature.MEASUREMENT} if measurement else set()
    return sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset(features)),
            "object_types": ObjectTypeRegistry.model_validate({"types": [ORDER_TYPE]}),
            "measurement": MeasurementSettings.model_validate(
                {
                    "revenue": {"currency": "BRL"},
                    "outcomes": [PURCHASE],
                    "attribution": {"methods": ["line", "order", "identity", "assisted_handoff"]},
                }
            ),
            "purposes": PurposeSettings(
                version="1",
                purposes=[
                    PurposeSpec(name="support", legal_basis=LegalBasis.CONTRACT),
                    PurposeSpec(name="personalization", legal_basis=LegalBasis.LEGITIMATE_INTEREST),
                ],
            ),
        }
    )


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    cell.config.put(configured())
    flow = Flow.build(cell)
    said = {
        "type": "event",
        "idempotency_key": "wa-1-hello",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Quero um vestido"},
        "occurred_at": cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, said)).errors == []
    await flow.drain()
    return flow


async def shown(flow: Flow) -> None:
    """A stylist's turn shows two cards and the person opens the second one."""
    now = flow.cell.clock.now()
    items = [{"pos": 1, "ref": DRESS, "shown": {"color": "bege"}}, {"pos": 2, "ref": BAG}]
    record = TurnRecord.model_validate(
        {
            "turn_id": "t-stylist-1",
            "conversation_id": "wa-1",
            "agent": {"name": "stylist"},
            "started_at": now.isoformat(),
            "content_mode": "hash_only",
            "interactions": [
                {"kind": "presented", "exposure_id": str(EXPOSURE), "list_id": "results", "items": items,
                 "visible_k": 2, "delivered_at": now.isoformat()},
                {"kind": "engaged", "exposure_id": str(EXPOSURE), "ref": BAG, "how": "detail"},
            ],
        }
    )  # fmt: skip
    async with flow.cell.uow(SPACE_ID) as uow:
        await flow.signals.consume(uow, SPACE_ID, [AppliedTurn(WHATSAPP_SOURCE, now, record)])


def order(
    flow: Flow, state: str, key: str, lines: list[dict[str, Any]], order_id: str = "8812"
) -> dict[str, Any]:
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": key,
        "channel": "erp",
        "canonical_type": f"order.{state}",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": "order", "namespace": "shop", "id": order_id}],
        "speaker": {"role": "system"},
        "fields": {"state": state, "lines": lines},
        "occurred_at": (flow.cell.clock.now() + timedelta(hours=1)).isoformat(),
    }


LINES = [
    {"line_id": "1", "ref": DRESS, "amount": "199.90", "exposure_token": build_token(EXPOSURE, 1),
     "size_label": "38"},
    {"line_id": "2", "ref": BAG, "amount": "149.90"},
]  # fmt: skip


async def links(flow: Flow) -> dict[tuple[str | None, str], Any]:
    async with flow.cell.uow(SPACE_ID) as uow:
        return {
            (s.link.line_ref, s.link.method): s.link for s in await uow.outcomes.links(before=None, limit=50)
        }


async def test_an_order_is_attributed_by_line_provisional_then_final_after_the_exchange_window(
    flow: Flow,
) -> None:
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    ran = await flow.drain()
    assert TaskName.ATTRIBUTE_OUTCOMES in [t.name for t in ran]
    found = await links(flow)
    line = found[("1", "line")]
    assert (line.band, line.finality, line.value_minor, line.currency) == (
        "deterministic",
        "provisional",
        19990,
        "BRL",
    )
    assert (line.exposure_id, line.position, line.turn_id, line.agent) == (
        str(EXPOSURE),
        1,
        "t-stylist-1",
        "stylist",
    )
    # The bag has no token: the person opened it, a probable link by identity, its value whole.
    bag = found[("2", "identity")]
    assert (bag.band, bag.value_minor, bag.confidence) == ("probable", 14990, None)
    assert ("2", "line") not in found

    # The loop knows when the window ends; once the exchange window of the signed definition passes, it makes
    # the link final.
    ahead = await flow.outcomes.step(SPACE_ID)
    assert ahead.found == 0
    assert ahead.due_in_s is not None
    assert 29 * 86_400 < ahead.due_in_s <= 30 * 86_400
    flow.cell.clock.advance(days=31)
    assert (await flow.outcomes.step(SPACE_ID)).found > 0
    final = await links(flow)
    assert final[("1", "line")].finality == "final"

    caller = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    day = line.valid_at.date()
    report = await flow.outcomes.attribution(caller, day, day)
    by_method = {(t.method, t.band): t for t in report.totals}
    assert by_method[("line", "deterministic")].value_minor == 19990
    assert by_method[("identity", "probable")].value_minor == 14990 + 19990  # both lines, each whole
    assert (report.identity.compared, report.identity.agreeing) == (1, 1)


async def test_a_returned_line_is_final_and_worth_nothing_and_the_rest_keeps_its_value(flow: Flow) -> None:
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    flow.cell.clock.advance(hours=2)  # the order system reports the return later
    returned = [LINES[0], {**LINES[1], "state": "returned", "return_reason": "not_liked"}]
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-returned-bag", returned))
    await flow.drain()
    found = await links(flow)
    assert (found[("2", "identity")].finality, found[("2", "identity")].value_minor) == ("final", None)
    assert found[("1", "line")].value_minor == 19990


async def test_the_size_kept_becomes_the_subjects_and_a_return_for_size_takes_it_back(flow: Flow) -> None:
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "kept", "o-kept", LINES))
    await flow.drain()
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    block = await flow.signals.block(SPACE_ID, await target(flow))
    assert [(a.name, a.value, a.source, a.apply) for a in block.attributes] == [
        ("size.dress", "38", "kept", "when_asked")
    ]
    flow.cell.clock.advance(hours=2)
    returned = [{**LINES[0], "state": "returned", "return_reason": "size_small"}, LINES[1]]
    await flow.send(WHATSAPP_SOURCE, order(flow, "kept", "o-returned-dress", returned))
    await flow.drain()
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    block = await flow.signals.block(SPACE_ID, await target(flow))
    assert block.attributes == []


async def target(flow: Flow) -> Any:
    async with flow.cell.uow(SPACE_ID) as uow:
        return await flow.serve._reads.resolve_subject(uow, SUBJECT)  # type: ignore[attr-defined]


async def test_an_order_past_its_deadline_without_an_outcome_is_attributed_as_expired(flow: Flow) -> None:
    # The objects module's clock moves it to `expired_without_outcome` at its deadline and hands it over.
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "placed", "o-placed", LINES))
    await flow.drain()
    assert await links(flow) == {}
    flow.cell.clock.advance(days=61)
    assert await flow.objects.fire_timers(SPACE_ID) != []
    await flow.drain()
    found = await links(flow)
    assert (found[("1", "line")].finality, found[("1", "line")].value_minor) == (
        "expired_without_outcome",
        None,
    )
    assert found[("1", "line")].state == "expired_without_outcome"


async def test_the_objects_module_hands_over_inside_its_transaction_and_nothing_without_measurement(
    cell: Cell,
) -> None:
    # Measurement off: the hook is there and does nothing for an order without a token, no task and no link.
    cell.config.put(configured(measurement=False))
    flow = Flow.build(cell)
    untokened = [{k: v for k, v in line.items() if k != "exposure_token"} for line in LINES]
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-off", untokened))
    ran = await flow.drain()
    assert TaskName.ATTRIBUTE_OUTCOMES not in [t.name for t in ran]
    assert await links(flow) == {}
    # On: the task is enqueued by the same transaction that wrote the order (the outbox row commits with it).
    cell.config.put(configured())
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-on", LINES, order_id="8813"))
    await flow.drain(only={TaskName.APPLY_INTAKE, TaskName.APPLY_OBJECT_EVENT})
    assert [t.name for t in flow.pending(TaskName.ATTRIBUTE_OUTCOMES)] == [TaskName.ATTRIBUTE_OUTCOMES]


async def test_reconciliation_keeps_its_report(flow: Flow) -> None:
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    found = await links(flow)
    day = found[("1", "line")].valid_at.date()
    caller = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    bi = "\ufefforder,line,value,currency\n8812,1,199.90,BRL\n8899,1,10.00,BRL\n".encode()
    upload = await flow.outcomes.bi_upload(
        caller, BiUploadRequest(size_bytes=len(bi), sha256=hashlib.sha256(bi).hexdigest())
    )
    assert upload.upload_headers["x-amz-checksum-sha256"] == hashlib.sha256(bi).hexdigest()
    key = bi_key(SPACE_ID, upload.upload_id)
    flow.cell.objects.objects[key] = bi  # what the company's PUT to `upload_url` leaves
    request = ReconcileRequest(since=day, until=day, file=BiFile(upload_id=upload.upload_id))
    result = await flow.outcomes.reconcile(caller, request)
    assert (result.matched, result.only_ours, result.only_bi, result.deviation) == (1, 0, 1, 0.0)
    assert result.within_tolerance
    assert key not in flow.cell.objects.objects  # read once, then deleted
    with pytest.raises(InvalidInput, match="upload_id"):
        await flow.outcomes.reconcile(caller, request)
    async with flow.cell.uow(SPACE_ID) as uow:
        assert [r[0] for r in uow.outcomes.report_rows.values()] == ["reconcile"]  # type: ignore[attr-defined]


async def test_the_package_holds_the_subjects_links_and_erasure_takes_them(flow: Flow) -> None:
    await shown(flow)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    out = await flow.privacy.export_package(SPACE_ID, ExportRequest(handle=SUBJECT), requested_by="user:dpo")
    data = await flow.privacy.download_package(SPACE_ID, UUID(out.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = [json.loads(line) for line in archive.read("outcomes.jsonl").splitlines() if line]
    assert sorted((r["line"], r["method"]) for r in rows) == [
        ("1", "identity"),
        ("1", "line"),
        ("2", "identity"),
    ]
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-1"
    )
    await flow.drain()
    assert await links(flow) == {}


class Recording:
    """An outcome consumer that reads, through the transaction it is handed, the objects it is told about."""

    def __init__(self, *, fail: bool = False) -> None:
        self.seen: list[list[ObjectKey]] = []
        self.fail = fail

    async def consume(self, uow: UnitOfWork, space_id: UUID, objects: Sequence[BusinessObject]) -> None:
        found = await uow.objects.get_objects([o.key for o in objects])
        self.seen.append([o.key for o in found if o.state.get("status") == "credited"])
        if self.fail:
            raise RuntimeError("the measurement failed")


async def test_the_hook_runs_inside_the_objects_transaction_and_its_default_changes_nothing(
    cell: Cell,
) -> None:
    cell.config.put(config_with())
    world = World(cell)
    marina = world.identity.add_profile(handle(), handle(HandleType.SYSTEM_ID))
    key = ObjectKey("invoice", "erp", "0823")

    def credited(minutes: float) -> Any:
        handles = [world.identity.handles[h] for h in marina.handle_ids]
        fields = {"amount": "40.00", "status": "credited"}
        return event(
            EventKind.SYSTEM_EVENT, at(minutes), handles, objects=[key], canonical_type="invoice.credited",
            fields=fields,
        )  # fmt: skip

    async def apply(objects: ObjectsService, minutes: float) -> None:
        e = credited(minutes)
        world.ingest.add(e)
        task = Task(TaskName.APPLY_OBJECT_EVENT, SPACE_ID, {"event_id": str(e.event_id)})
        await objects.apply_object_event(task)

    async def state() -> dict[str, Any]:
        async with cell.uow(SPACE_ID) as uow:
            found = await uow.objects.get_object(key)
        return dict(found.state) if found else {}

    # The object the consumer reads through the transaction it is handed is the one just written.
    recording = Recording()
    await apply(ObjectsService(world.uow, cell.clock, cell.config, outcomes=recording), 1)
    assert recording.seen == [[key]]
    written = await state()
    # A failing consumer fails the write with it: they are one transaction.
    failing = Recording(fail=True)
    with pytest.raises(RuntimeError):
        await apply(ObjectsService(world.uow, cell.clock, cell.config, outcomes=failing), 2)
    assert await state() == written
    # Without a consumer (the default), the write is what it always was.
    await apply(ObjectsService(world.uow, cell.clock, cell.config), 3)
    assert (await state())["status"] == "credited"


async def test_identity_links_carry_the_spaces_calibration_read_once_an_hour(flow: Flow) -> None:
    from dataclasses import replace
    from uuid import uuid4

    from niadra.domain.measure.attribution import DETERMINISTIC, FINAL, PROBABLE, Link, link_id

    service, now = flow.outcomes, flow.cell.clock.now()
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await service._identity_confidence(uow, SPACE_ID, now) is None  # no line has both yet
        for i in range(20):
            order = uuid4()
            token = Link(
                link_id(order, "1", "line", "e"), "purchase", order, f"order:shop:{i}", uuid4(), "delivered",
                "1", "line", DETERMINISTIC, FINAL, now, 100, "BRL", exposure_id="e",
            )  # fmt: skip
            named = "e" if i < 17 else "other"
            probable = replace(
                token, link_id=link_id(order, "1", "identity", named), method="identity", band=PROBABLE,
                exposure_id=named,
            )  # fmt: skip
            await uow.outcomes.write([order], [token, probable], now)
    async with flow.cell.uow(SPACE_ID) as uow:
        assert await service._identity_confidence(uow, SPACE_ID, now) is None  # kept for the hour
        assert await service._identity_confidence(uow, SPACE_ID, now + timedelta(hours=1)) == 0.85


def scheduled(flow: Flow) -> list[Task]:
    rows = flow.cell.uow.store.module("pipeline").setdefault("scheduled", [])
    tasks = [task for task, _, _ in rows if task.name == TaskName.ATTRIBUTE_OUTCOMES]
    rows[:] = [row for row in rows if row[0].name != TaskName.ATTRIBUTE_OUTCOMES]
    return tasks


def unmeasured(measured: SpaceConfig) -> SpaceConfig:
    """A configuration that measures no order: no definition, and no final state to infer one from (a type
    with final states is measured with no declaration)."""
    lifecycle = {k: v for k, v in ORDER_TYPE["lifecycle"].items() if k != "outcome"}  # type: ignore[attr-defined]
    return measured.model_copy(
        update={
            "measurement": MeasurementSettings(),
            "object_types": ObjectTypeRegistry.model_validate(
                {"types": [{**ORDER_TYPE, "lifecycle": lifecycle}]}
            ),
        }
    )


async def test_an_order_with_a_token_waits_for_a_configuration_that_measures_it(
    flow: Flow, caplog: pytest.LogCaptureFixture
) -> None:
    await shown(flow)
    measured = flow.cell.config.space(SPACE_ID)
    # The worker applying the order has not heard of the definition yet: the token sends it on anyway.
    flow.cell.config.put(unmeasured(measured))
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    assert await links(flow) == {}
    [later] = scheduled(flow)
    # The definition arrives; the retry attributes the order to the card.
    flow.cell.config.put(measured)
    flow.cell.clock.advance(seconds=15)
    await flow.outcomes.attribute(later)
    assert ("1", "line") in await links(flow)

    # An order the space never measures waits two minutes, then says so, and nothing is attributed.
    flow.cell.config.put(unmeasured(measured))
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-other", LINES, order_id="8899"))
    await flow.drain()
    for _ in range(9):
        tasks = scheduled(flow)
        if not tasks:
            break
        flow.cell.clock.advance(seconds=15)
        await flow.outcomes.attribute(tasks[0])
    assert scheduled(flow) == []
    assert "the space does not measure their type" in caplog.text


async def test_a_revised_definition_attributes_again_what_a_stale_process_attributed(flow: Flow) -> None:
    # A definition approved seconds before an order arrives may not have reached the worker that attributes
    # it: the order is attributed under the old one, coupon and all. Once the outcomes loop sees the new
    # definition, the objects attributed in the last minutes are attributed again under it.
    await shown(flow)
    assert (await flow.outcomes.step(SPACE_ID)).found == 0  # the loop's first look at the definition
    delivered = order(flow, "delivered", "o-delivered", LINES)
    delivered["fields"]["coupon"] = "9.99"
    await flow.send(WHATSAPP_SOURCE, delivered)
    await flow.drain()
    assert (await links(flow))[("1", "line")].value_minor == 19990  # the old definition names no coupon
    config = flow.cell.config.space(SPACE_ID)
    revised = config.measurement.model_dump(mode="json")
    revised["outcomes"][0]["coupon_field"] = "coupon"
    flow.cell.config.put(
        config.model_copy(update={"measurement": MeasurementSettings.model_validate(revised)})
    )
    await flow.outcomes.step(SPACE_ID)
    scheduled = flow.cell.uow.store.module("pipeline")["scheduled"]
    [(again, at, _)] = [row for row in scheduled if row[0].name == TaskName.ATTRIBUTE_OUTCOMES]
    assert at == flow.cell.clock.now() + timedelta(seconds=15)
    assert again.payload["objects"] == []
    await flow.outcomes.attribute(again)
    found = await links(flow)
    # R$ 9,99 over R$ 199,90 and R$ 149,90: 5,71 and 4,28.
    assert (found[("1", "line")].value_minor, found[("2", "identity")].value_minor) == (19419, 14562)
    # The same definition again schedules nothing more.
    await flow.outcomes.step(SPACE_ID)
    assert len([row for row in scheduled if row[0].name == TaskName.ATTRIBUTE_OUTCOMES]) == 1
