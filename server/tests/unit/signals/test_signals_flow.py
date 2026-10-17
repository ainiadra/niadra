"""Subject signals across the real modules (24.3.2): interactions from turn records and from the batch, the
session overlay, the derivation window, the constraints block on the context read and on its own route, the
objection to personalization, erasure and the subject's package."""

import io
import json
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http import mcp, read
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.memory.signals import InMemorySignalRepository
from niadra.app.purposes import PurposeService
from niadra.app.serve import BlockAsk
from niadra.config.models import FeatureSettings, PurposeSettings, PurposeSpec, SpaceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.tool_bindings import ToolBindings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.events import InteractionItem
from niadra.contracts.privacy import ExportRequest, ForgetRequest
from niadra.contracts.signals import ConstraintsBlock
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import InvalidInput
from niadra.domain.privacy.purposes import LegalBasis
from niadra.domain.signals.records import SELF, beneficiary_id
from niadra.domain.signals.storage import INTAKE, raw_prefix
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature, HandleType, ReceiptSurface, Verification
from niadra.ports.signals import InteractionSink
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.state.test_http import FakeEdge

PHONE = "+5511955557777"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
NO_BLACK = {
    "kind": "preference",
    "attr": "item_variant.color",
    "op": "not_in",
    "values": ["preto"],
    "strength": "must",
    "scope": "persistent",
}


@dataclass(frozen=True)
class Applied:
    source_id: UUID
    received_at: datetime
    record: TurnRecord


def configured(*features: Feature, purposes: bool = False) -> SpaceConfig:
    base = sample_config()
    update: dict[str, Any] = {"features": FeatureSettings(enabled=frozenset(features))}
    if purposes:
        update["purposes"] = PurposeSettings(
            version="1",
            purposes=[
                PurposeSpec(name="support", legal_basis=LegalBasis.CONTRACT),
                PurposeSpec(name="personalization", legal_basis=LegalBasis.LEGITIMATE_INTEREST),
            ],
        )
    return base.model_copy(update=update)


def said(conversation: str, text: str, at: datetime, *, key: str | None = None) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": key or f"{conversation}-{text[:8]}",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": text},
        "occurred_at": at.isoformat(),
    }


def turn(turn_id: str, conversation: str, at: datetime, *interactions: dict[str, Any]) -> Applied:
    record = TurnRecord.model_validate(
        {
            "turn_id": turn_id,
            "conversation_id": conversation,
            "agent": {"name": "stylist"},
            "started_at": at.isoformat(),
            "content_mode": "hash_only",
            "interactions": list(interactions),
        }
    )
    return Applied(WHATSAPP_SOURCE, at, record)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """A customer who wrote on WhatsApp, in a space with subject signals on."""
    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-1", "Quero um vestido", cell.clock.now()))).errors == []
    await flow.drain()
    return flow


async def apply(flow: Flow, *turns: Applied) -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        await flow.signals.consume(uow, SPACE_ID, list(turns))


async def block(flow: Flow, conversation: str, source: UUID = VOICE_SOURCE, **extra: Any) -> ConstraintsBlock:
    # The block goes beside a pack only as far as the pack goes: the agent proved who it speaks with.
    await flow.prove(source, conversation, PHONE)
    request = ContextRequest(
        subject=SUBJECT,
        conversation_id=conversation,
        verification=Verification.V1,
        include=["constraints"],
        **extra,
    )
    response = await flow.serve.context(principal(source, flow.cell.config.space(SPACE_ID)), request)
    assert response.constraints is not None
    return response.constraints


def repo(flow: Flow) -> InMemorySignalRepository:
    return InMemorySignalRepository(flow.cell.uow.store)


async def test_a_declared_preference_is_a_hard_constraint_in_the_next_conversation_for_another_agent(
    flow: Flow,
) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    # The voice agent, in another conversation, before anything was derived.
    found = await block(flow, "call-2")
    assert [(h.attr, h.op, h.values, h.scope) for h in found.hard] == [
        ("item_variant.color", "not_in", ["preto"], "persistent")
    ]
    assert found.hard[0].origin.turn_id == "t1"
    assert found.version.startswith("cv_")
    # The model reads it in the space's language, the operator in words.
    assert found.text == "<restrições>\n- exigido: color diferente de preto\n</restrições>"
    # Derived later, it reads the same from the stored signals.
    flow.cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    again = await block(flow, "call-3")
    assert [(h.attr, h.op, h.values) for h in again.hard] == [("item_variant.color", "not_in", ["preto"])]
    assert flow.signal_overlay.entries.get((SPACE_ID, _handle(flow))) in (None, {})


async def test_the_block_beside_a_pack_holds_back_only_what_the_pack_would(flow: Flow) -> None:
    now = flow.cell.clock.now()
    # A filter on an exam's field: the type's name is health data, which no level releases to a support
    # agent. The filter on the dress's color is ordinary.
    urgent = {**NO_BLACK, "attr": "exam_authorization.urgent", "op": "eq", "values": [True]}
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK, urgent))
    # The customer then says something the pack withholds: the rest of the block still reaches the agent.
    sensitive = said("wa-2", "Tenho diabetes, quero um vestido mais largo", now, key="wa-2-1")
    assert (await flow.send(WHATSAPP_SOURCE, sensitive)).errors == []
    found = await block(flow, "call-2")
    assert [h.attr for h in found.hard] == ["item_variant.color"]
    assert "exam" not in (found.text or "")
    assert "diabetes" not in found.model_dump_json()


async def test_a_constraint_says_which_options_the_customer_was_shown_meet_it(flow: Flow) -> None:
    now = flow.cell.clock.now()
    items = [
        {"pos": 1, "ref": "item_variant:loja:v-1", "shown": {"color": "preto"}},
        {"pos": 2, "ref": "item_variant:loja:v-2", "shown": {"color": "azul"}},
    ]
    shown = {
        "kind": "presented",
        "exposure_id": "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e56",
        "list_id": "results",
        "visible_k": 2,
        "items": items,
        "delivered_at": now.isoformat(),
    }
    await apply(flow, turn("t1", "wa-1", now, shown, NO_BLACK))
    found = await block(flow, "call-2")
    assert found.text == (
        "<restrições>\n- exigido: color diferente de preto; das opções mostradas, atende: v-2; "
        "não atende: v-1\n</restrições>"
    )


async def test_each_option_shown_carries_its_numbers_claimable_as_long_as_its_type_lets(flow: Flow) -> None:
    retail = json.loads((Path(__file__).parents[3] / "src/niadra/config/templates/retail.json").read_text())
    types = ObjectTypeRegistry.model_validate(retail)
    flow.cell.config.put(flow.cell.config.space(SPACE_ID).model_copy(update={"object_types": types}))
    now = flow.cell.clock.now()
    shown = {"color": "azul", "available": True, "price_sale": 199.9, "price_list": 249.9, "stock_qty": 3}
    presented = {
        "kind": "presented",
        "exposure_id": "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e57",
        "list_id": "results",
        "items": [{"pos": 1, "ref": "item_variant:loja:v-1", "shown": shown}],
        "delivered_at": now.isoformat(),
    }
    await apply(flow, turn("t1", "wa-1", now, presented))

    def numbers(found: ConstraintsBlock) -> dict[str, tuple[float, str | None, bool]]:
        values = found.already_presented[0].values
        return {name: (v.v, v.role, v.claim_safe) for name, v in values.items()}

    # A money field whose type allows claims backs one for its `claim_max_age` (15 s for the sale price), else
    # for its `max_age` (60 s for the list price); a number the type never lets be claimed is served, never
    # claimable. Text and yes-or-no fields are no numbers.
    assert numbers(await block(flow, "call-2")) == {
        "price_sale": (199.9, "price_sale", True),
        "price_list": (249.9, "price_list", True),
        "stock_qty": (3, None, False),
    }
    flow.cell.clock.advance(seconds=20)
    assert numbers(await block(flow, "call-3")) == {
        "price_sale": (199.9, "price_sale", False),
        "price_list": (249.9, "price_list", True),
        "stock_qty": (3, None, False),
    }


async def test_a_session_constraint_holds_in_its_conversation_only(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", "wa-1", now, {**NO_BLACK, "scope": "session"}))
    assert [h.scope for h in (await block(flow, "wa-1", WHATSAPP_SOURCE)).hard] == ["session"]
    assert (await block(flow, "call-2")).hard == []


async def test_the_same_turn_applied_twice_counts_once(flow: Flow) -> None:
    now = flow.cell.clock.now()
    shown = {
        "kind": "presented",
        "exposure_id": "0192f5a0-0000-7000-8000-000000000101",
        "list_id": "results",
        "items": [{"pos": 1, "ref": "item_variant:store:1", "shown": {"color": "bege"}}],
        "delivered_at": now.isoformat(),
    }
    applied = turn("t1", "wa-1", now, shown)
    # A group that failed runs again; a committed turn is never applied twice (the turn intake's dedupe).
    await apply(flow, applied)
    await apply(flow, applied)
    assert (await block(flow, "call-2")).already_presented[0].times == 1
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert (await block(flow, "call-3")).already_presented[0].times == 1


async def test_a_turn_of_a_conversation_nobody_is_known_in_waits_for_its_subject(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t9", "wa-9", now, NO_BLACK))
    assert (await block(flow, "call-2")).hard == []
    # The conversation's turns arrive, and the loop resolves the waiting interaction.
    await flow.send(WHATSAPP_SOURCE, said("wa-9", "Oi de novo", now))
    await flow.drain()
    flow.cell.clock.advance(minutes=2)
    await flow.signals.derive_due(SPACE_ID)
    assert [h.values for h in (await block(flow, "call-2")).hard] == [["preto"]]


async def test_an_interaction_nobody_is_known_in_is_kept_until_its_conversation_names_someone(
    flow: Flow,
) -> None:
    # Never dropped: hours go by with nobody named; it reaches the person once the conversation names them.
    await apply(flow, turn("t9", "wa-9", flow.cell.clock.now(), NO_BLACK))
    for _ in range(3):
        flow.cell.clock.advance(hours=1)
        await flow.signals.derive_due(SPACE_ID)
    assert [r.handle_id for r in repo(flow).pending.values()] == [None]
    await flow.send(WHATSAPP_SOURCE, said("wa-9", "Oi de novo", flow.cell.clock.now()))
    await flow.drain()
    flow.cell.clock.advance(days=1)
    await flow.signals.derive_due(SPACE_ID)
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert [h.values for h in (await block(flow, "call-2")).hard] == [["preto"]]


async def test_erasing_the_subject_takes_the_interactions_still_waiting_for_their_conversation(
    flow: Flow,
) -> None:
    await apply(flow, turn("t9", "wa-9", flow.cell.clock.now(), NO_BLACK))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    # The conversation names the person now; the loop has not looked again when the erasure comes.
    await flow.send(WHATSAPP_SOURCE, said("wa-9", "Oi de novo", flow.cell.clock.now()))
    await flow.drain()
    assert [r.handle_id for r in repo(flow).pending.values()] == [None]
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-9"
    )
    await flow.drain()
    assert repo(flow).pending == {}


async def test_with_the_overlay_down_the_derivation_still_takes_every_interaction(flow: Flow) -> None:
    now = flow.cell.clock.now()
    flow.signal_overlay.down = True
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    flow.signal_overlay.down = False
    assert (await block(flow, "call-2")).hard == []
    flow.cell.clock.advance(minutes=2)  # the session went quiet
    assert await flow.signals.derive_due(SPACE_ID) == 1
    assert [h.values for h in (await block(flow, "call-3")).hard] == [["preto"]]


async def test_after_the_state_store_comes_back_empty_the_overlay_is_rebuilt_from_the_database(
    flow: Flow,
) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    assert await flow.signals.watch_overlay()  # a store never sealed is rebuilt once, then whole
    assert not await flow.signals.watch_overlay()
    flow.signal_overlay.restart()
    # Until the rebuild, a read holds what was derived: nothing yet.
    assert (await block(flow, "call-2")).hard == []
    assert await flow.signals.watch_overlay()
    assert [h.values for h in (await block(flow, "call-3")).hard] == [["preto"]]
    # A store that does not answer is left for the next check.
    flow.signal_overlay.restart()
    flow.signal_overlay.down = True
    assert not await flow.signals.watch_overlay()


async def test_derive_signals_reads_once_and_writes_once_per_pass(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(
        flow, turn("t1", "wa-1", now, NO_BLACK), turn("t2", "wa-1", now, {**NO_BLACK, "values": ["azul"]})
    )
    signals = repo(flow)
    flow.cell.clock.advance(minutes=6)
    signals.statements.clear()
    assert await flow.signals.derive_due(SPACE_ID) == 1
    assert signals.statements == ["due_snapshots", "store_many"]
    # Nothing new: one statement to find out.
    signals.statements.clear()
    assert await flow.signals.derive_due(SPACE_ID) == 0
    assert signals.statements == ["due_snapshots"]


async def test_consuming_a_group_of_turns_runs_two_statements(flow: Flow) -> None:
    now = flow.cell.clock.now()
    signals = repo(flow)
    signals.statements.clear()
    turns = [turn(f"t{i}", "wa-1", now, NO_BLACK) for i in range(50)]
    await apply(flow, *turns)
    # The conversations' subjects (`primary_handles`, the ingest module's) and the pending rows.
    assert signals.statements == ["add_pending"]


async def test_a_warm_block_reads_nothing_from_the_database(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    signals = repo(flow)
    signals.statements.clear()
    flow.cell.cache.values.clear()
    await block(flow, "call-2")  # cold: the cache is filled once
    assert signals.statements == ["rows_for"]
    signals.statements.clear()
    await block(flow, "call-3")
    assert signals.statements == []


async def test_a_beneficiary_keeps_their_signals_apart(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", "wa-1", now, {**NO_BLACK, "for": "beneficiary:dep-1"}))
    own = await block(flow, "call-2")
    assert own.hard == []
    assert own.ask == ["for_whom"]
    theirs = await flow.serve.read_block(
        principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID)),
        "constraints",
        SUBJECT,
        BlockAsk("call-2", None, "chat", "beneficiary:dep-1"),
    )
    assert [h.values for h in theirs.hard] == [["preto"]]
    assert theirs.subject.for_ == "beneficiary:dep-1"
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    rows = await repo(flow).signals_for([_handle(flow)])
    assert {r.beneficiary_id for r in rows} == {beneficiary_id(SPACE_ID, "beneficiary:dep-1")}
    [kept] = await repo(flow).beneficiaries_for([_handle(flow)])
    assert (kept.relation, kept.status) == ("other", "active")


async def test_the_block_leaves_a_receipt_of_its_own_with_counts_only(flow: Flow) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    flow.receipts.sent.clear()
    found = await block(flow, "call-2")
    [receipt] = [r for r in flow.receipts.sent if r.surface is ReceiptSurface.CONSTRAINTS]
    assert receipt.detail == {
        "version": found.version,
        "for": "self",
        "hard": "1",
        "soft": "0",
        "attributes": "0",
    }


async def test_an_objection_to_personalization_erases_the_signals_and_stops_their_derivation(
    flow: Flow,
) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert (await block(flow, "call-2")).hard != []
    purposes = PurposeService(flow.cell.uow, flow.cell.clock, flow.cell.config)
    await purposes.object(
        SPACE_ID,
        ForgetRequest(target="handle", handle=SUBJECT, purpose="personalization"),
        idempotency_key="objection-1",
    )
    await flow.drain(only={TaskName.WITHDRAW_SIGNALS})
    rows = await repo(flow).signals_for([_handle(flow)])
    assert [(r.beneficiary_id, r.opted_out_at is not None) for r in rows] == [(SELF, True)]
    assert (await block(flow, "call-3")).hard == []
    # What the person says later is neither shown nor derived.
    await apply(flow, turn("t2", "wa-1", flow.cell.clock.now(), NO_BLACK))
    assert (await block(flow, "call-4")).hard == []
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert repo(flow).pending == {}
    rows = await repo(flow).signals_for([_handle(flow)])
    assert [(r.beneficiary_id, r.opted_out_at is not None) for r in rows] == [(SELF, True)]


async def test_an_interaction_item_is_one_statement_and_its_raw_payload_in_object_storage(flow: Flow) -> None:
    item = {
        "type": "interaction",
        "idempotency_key": "i-1",
        "handle": {"type": "phone_e164", "value": PHONE},
        "conversation_id": "web-1",
        "occurred_at": flow.cell.clock.now().isoformat(),
        "interaction": NO_BLACK,
    }
    response = await flow.send(WHATSAPP_SOURCE, item)
    assert (response.accepted, response.errors) == (1, [])
    assert [t.name for t in flow.pending()] == [TaskName.APPLY_INTERACTIONS]
    assert [k for k in flow.cell.objects.objects if k.startswith(INTAKE)] != []
    await flow.drain()
    assert [k for k in flow.cell.objects.objects if k.startswith(INTAKE)] == []
    assert [h.values for h in (await block(flow, "call-2")).hard] == [["preto"]]
    # The same item again is a duplicate.
    await flow.send(WHATSAPP_SOURCE, item)
    await flow.drain()
    assert len(repo(flow).pending) == 1


async def test_an_interaction_item_needs_the_signals_feature(cell: Cell) -> None:
    flow = Flow.build(cell)
    item = {
        "type": "interaction",
        "idempotency_key": "i-1",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": cell.clock.now().isoformat(),
        "interaction": NO_BLACK,
    }
    response = await flow.send(WHATSAPP_SOURCE, item)
    assert response.accepted == 0
    assert [e.code for e in response.errors] == ["invalid_input"]
    assert flow.pending() == []


async def test_another_module_records_interactions_as_a_standalone_batch(flow: Flow) -> None:
    # A declined handoff's outcome, for example, becomes a preference outside any turn record.
    item = InteractionItem.model_validate(
        {
            "idempotency_key": "handoff-h1-declined",
            "handle": {"type": "phone_e164", "value": PHONE},
            "occurred_at": flow.cell.clock.now().isoformat(),
            "interaction": NO_BLACK,
        }
    )
    sink: InteractionSink = flow.signals
    await sink.record(SPACE_ID, WHATSAPP_SOURCE, [item])
    assert [t.name for t in flow.pending()] == [TaskName.APPLY_INTERACTIONS]
    await flow.drain()
    assert [h.values for h in (await block(flow, "call-2")).hard] == [["preto"]]
    await sink.record(SPACE_ID, WHATSAPP_SOURCE, [item])
    await flow.drain()
    assert len(repo(flow).pending) == 1


async def test_a_space_without_signals_records_nothing_another_module_sends(cell: Cell) -> None:
    flow = Flow.build(cell)
    item = InteractionItem.model_validate(
        {
            "idempotency_key": "handoff-h1-declined",
            "handle": {"type": "phone_e164", "value": PHONE},
            "occurred_at": cell.clock.now().isoformat(),
            "interaction": NO_BLACK,
        }
    )
    await flow.signals.record(SPACE_ID, WHATSAPP_SOURCE, [item])
    assert flow.pending() == []
    assert [k for k in cell.objects.objects if k.startswith(INTAKE)] == []


class EnteredSet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def enter_working_set(self, space_id: UUID, refs: Sequence[str], reason: str) -> int:
        self.calls.append((reason, list(refs)))
        return len(refs)


async def test_standalone_interactions_bring_what_they_showed_into_the_working_set(flow: Flow) -> None:
    entered = EnteredSet()
    flow.signals._working_set = entered  # type: ignore[attr-defined]
    exposure = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"
    items = [{"pos": 1, "ref": "item_variant:loja:1"}, {"pos": 2, "ref": "item_variant:loja:2"}]
    interactions = [
        {"kind": "presented", "exposure_id": exposure, "list_id": "r", "items": items,
         "delivered_at": flow.cell.clock.now().isoformat()},
        {"kind": "engaged", "exposure_id": exposure, "ref": "item_variant:loja:2", "how": "detail"},
        {"kind": "watch", "ref": "item_variant:loja:2", "condition": "price_sale < 100",
         "consent_event_key": "c1"},
    ]  # fmt: skip
    for i, interaction in enumerate(interactions):
        item = {
            "type": "interaction",
            "idempotency_key": f"i-{i}",
            "handle": {"type": "phone_e164", "value": PHONE},
            "occurred_at": flow.cell.clock.now().isoformat(),
            "interaction": interaction,
        }
        await flow.send(WHATSAPP_SOURCE, item)
    await flow.drain()
    # One call per reason and batch, as a turn's interactions enter through the objects module.
    assert sorted(entered.calls) == [
        ("engaged", ["item_variant:loja:2"]),
        ("presented", ["item_variant:loja:1", "item_variant:loja:2"]),
        ("watched", ["item_variant:loja:2"]),
    ]


async def test_the_now_view_reads_the_subjects_interests_with_no_statement_once_warm(flow: Flow) -> None:
    exposure = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"
    items = [
        {"pos": 1, "ref": "item_variant:loja:1", "shown": {"color": "bege"}},
        {"pos": 2, "ref": "item_variant:loja:2", "shown": {"color": "azul"}},
    ]
    shown = {"kind": "presented", "exposure_id": exposure, "list_id": "r", "items": items,
             "delivered_at": flow.cell.clock.now().isoformat()}  # fmt: skip
    engaged = {"kind": "engaged", "exposure_id": exposure, "ref": "item_variant:loja:2", "how": "detail"}
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), shown, engaged))
    handle = _handle(flow)
    first = await flow.signals.interests(SPACE_ID, [handle])
    assert [(i.ref, i.reason) for i in first] == [
        ("item_variant:loja:2", "engaged"),
        ("item_variant:loja:1", "presented"),
    ]
    before = list(repo(flow).statements)
    again = await flow.signals.interests(SPACE_ID, [handle])
    assert again == first
    assert repo(flow).statements == before
    # Derived, they read the same from the stored body.
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert [i.ref for i in await flow.signals.interests(SPACE_ID, [handle])] == [
        "item_variant:loja:2", "item_variant:loja:1"
    ]  # fmt: skip


async def test_turns_of_a_space_without_signals_leave_nothing(cell: Cell) -> None:
    flow = Flow.build(cell)
    await flow.send(WHATSAPP_SOURCE, said("wa-1", "Oi", cell.clock.now()))
    await flow.drain()
    await apply(flow, turn("t1", "wa-1", cell.clock.now(), NO_BLACK))
    assert repo(flow).pending == {}
    assert await flow.signals.derive_due(SPACE_ID) == 0


async def test_erasing_the_subject_takes_signals_overlay_cache_and_raw_interactions(flow: Flow) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    await apply(flow, turn("t2", "wa-1", flow.cell.clock.now(), {**NO_BLACK, "values": ["azul"]}))
    handle = _handle(flow)
    assert [k for k in flow.cell.objects.objects if k.startswith(raw_prefix(SPACE_ID, handle))] != []
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-1"
    )
    await flow.drain()
    assert await repo(flow).signals_for([handle]) == []
    assert repo(flow).pending == {}
    assert flow.signal_overlay.entries.get((SPACE_ID, handle)) is None
    assert [k for k in flow.cell.objects.objects if k.startswith(raw_prefix(SPACE_ID, handle))] == []
    assert not [k for k in flow.cell.cache.values if k.startswith("sg:")]


async def test_thirteen_months_without_activity_decay_what_was_derived_and_keep_what_was_said(
    flow: Flow,
) -> None:
    # A restriction the person stated never expires by itself: only an erasure or a retention the customer
    # set takes it. The affinity derived from what they were shown and opened decays.
    exposure = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"
    items = [{"pos": 1, "ref": "item_variant:loja:1", "shown": {"color": "bege"}}]
    shown = {"kind": "presented", "exposure_id": exposure, "list_id": "r", "items": items,
             "delivered_at": flow.cell.clock.now().isoformat()}  # fmt: skip
    engaged = {"kind": "engaged", "exposure_id": exposure, "ref": "item_variant:loja:1", "how": "detail"}
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK, shown, engaged))
    flow.cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    handle = _handle(flow)
    assert await flow.signals.interests(SPACE_ID, [handle]) != []
    flow.cell.clock.advance(days=14 * 31)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert await flow.signals.interests(SPACE_ID, [handle]) == []
    found = await block(flow, "call-later")
    assert [(h.attr, h.op, h.values) for h in found.hard] == [("item_variant.color", "not_in", ["preto"])]
    # Decayed once: the next sweep leaves the row alone until new activity.
    stamped = dict(repo(flow).decayed)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert repo(flow).decayed == stamped


async def test_a_subject_whose_signals_held_only_derived_accumulators_leaves_none_after_thirteen_months(
    flow: Flow,
) -> None:
    exposure = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"
    items = [{"pos": 1, "ref": "item_variant:loja:1", "shown": {"color": "bege"}}]
    shown = {"kind": "presented", "exposure_id": exposure, "list_id": "r", "items": items,
             "delivered_at": flow.cell.clock.now().isoformat()}  # fmt: skip
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), shown))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    handle = _handle(flow)
    assert await repo(flow).signals_for([handle]) != []
    flow.cell.clock.advance(days=14 * 31)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert await repo(flow).signals_for([handle]) == []


async def test_the_subjects_package_holds_their_signals_beneficiaries_and_interactions(flow: Flow) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    await apply(flow, turn("t2", "wa-1", flow.cell.clock.now(), {**NO_BLACK, "for": "beneficiary:dep-1"}))
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    await apply(flow, turn("t3", "wa-1", flow.cell.clock.now(), {**NO_BLACK, "values": ["azul"]}))
    out = await flow.privacy.export_package(SPACE_ID, ExportRequest(handle=SUBJECT), requested_by="user:dpo")
    data = await flow.privacy.download_package(SPACE_ID, UUID(out.run_id))
    sections = _sections(data)
    [own, dependent] = sorted(sections["subject_signals"], key=lambda r: r["beneficiary_id"])
    assert own["preferences"][0]["values"] == ["preto"]
    assert dependent["preferences"][0]["values"] == ["preto"]
    assert [b["label"] for b in sections["beneficiaries"]] == ["dep-1"]
    states = sorted(i["state"] for i in sections["interactions"])
    assert states == ["derived", "derived", "pending"]


def _sections(data: bytes) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            lines = archive.read(name).splitlines()
            out[name.removesuffix(".jsonl")] = [json.loads(line) for line in lines if line]
    return out


def _handle(flow: Flow) -> UUID:
    store = flow.cell.uow.store.module("identity")
    handles = store.get("handles", {})
    for handle in handles.values():
        if getattr(handle, "value", None) == PHONE:
            return UUID(str(handle.handle_id))
    raise AssertionError("the customer's handle")


def test_the_window_is_five_minutes() -> None:
    from niadra.app.signals import QUIET, WINDOW

    assert (timedelta(minutes=5), timedelta(minutes=1)) == (WINDOW, QUIET)


async def test_a_turn_record_sent_by_the_sdk_reaches_the_next_agents_block(cell: Cell) -> None:
    cell.config.put(configured(Feature.SIGNALS, Feature.TURNS))
    flow = Flow.build(cell)
    now = cell.clock.now()
    await flow.send(WHATSAPP_SOURCE, said("wa-1", "Nada de preto, por favor", now))
    record = {
        "turn_id": "t1",
        "conversation_id": "wa-1",
        "agent": {"name": "stylist"},
        "started_at": now.isoformat(),
        "content_mode": "hash_only",
        "interactions": [NO_BLACK],
    }
    result = await flow.turns.record(principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID)), [record])
    assert (result.accepted, result.errors) == (1, [])
    await flow.drain()
    assert [h.values for h in (await block(flow, "call-2")).hard] == [["preto"]]


SEARCH = {
    "tool": "search_products",
    "args": [
        {
            "attr": "item_variant.color",
            "param": "color",
            "transform": "lower",
            "negation": {"param": "not_color"},
        },
        {"attr": "item_variant.price_sale", "param": "max_price", "ops": ["lte"]},
    ],
    "results": [{"path": "ui.cards[*]", "type": "item_variant", "namespace": "store", "id": "variant_id"}],
    "capabilities": {"overfetch": True, "dry_run_param": "dry_run"},
}


def bound(flow: Flow, *bindings: dict[str, Any]) -> None:
    space = flow.cell.config.space(SPACE_ID)
    flow.cell.config.put(
        space.model_copy(update={"tool_bindings": ToolBindings.model_validate({"bindings": list(bindings)})})
    )


async def test_the_block_for_a_tool_renders_through_the_binding_of_the_callers_source(flow: Flow) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    bound(flow, SEARCH | {"sources": [str(VOICE_SOURCE)]})
    voice = principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID))
    ask = BlockAsk("call-2", None, "chat", "self")
    block = await flow.serve.read_block(voice, "constraints", SUBJECT, ask)
    rendered = flow.signals.for_tool(voice, block, "search_products")
    assert rendered.rendered is not None
    assert (rendered.rendered.mode, rendered.rendered.args) == ("advisory", {"not_color": "preto"})
    assert (rendered.rendered.residual, rendered.rendered.post_filter) == ([], [])
    # The rendering never changes what the block says, nor the version the turn record cites.
    assert rendered.model_copy(update={"rendered": None}) == block
    # A tool the space did not bind, or bound for another source's agents, is refused.
    with pytest.raises(InvalidInput, match="no binding"):
        flow.signals.for_tool(voice, block, "book_visit")
    whatsapp = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    with pytest.raises(InvalidInput, match="no binding"):
        flow.signals.for_tool(whatsapp, block, "search_products")


async def test_the_sdk_profile_serves_each_source_its_own_bindings(flow: Flow) -> None:
    only_voice = {"tool": "book_visit", "sources": [str(VOICE_SOURCE)]}
    bound(flow, SEARCH, only_voice)
    profile = flow.state.profile(principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID)))
    assert [b.tool for b in profile.tool_bindings] == ["search_products"]
    assert profile.tool_bindings[0].model_dump(mode="json", exclude_defaults=True) == SEARCH
    voice = flow.state.profile(principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID)))
    assert [b.tool for b in voice.tool_bindings] == ["search_products", "book_visit"]
    assert "sources" not in voice.model_dump(mode="json")["tool_bindings"][1]


@pytest.mark.parametrize(
    ("bindings", "error"),
    [
        ([SEARCH, SEARCH], "bound twice for one source"),
        ([SEARCH | {"sources": [str(VOICE_SOURCE)]}, SEARCH], "bound twice for one source"),
        (
            [{"tool": "t", "args": [{"attr": "a.b", "param": "x"}, {"attr": "a.c", "param": "x"}]}],
            "bound twice",
        ),
        ([{"tool": "t", "args": [{"attr": "a.b", "param": "x", "negation": {"param": "x"}}]}], "bound twice"),
        ([{"tool": "t", "args": [{"attr": "color", "param": "x"}]}], "String should match"),
    ],
)
def test_the_document_refuses_what_would_be_ambiguous(bindings: list[dict[str, Any]], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        ToolBindings.model_validate({"bindings": bindings})
    both = [SEARCH | {"sources": [str(VOICE_SOURCE)]}, SEARCH | {"sources": [str(WHATSAPP_SOURCE)]}]
    assert len(ToolBindings.model_validate({"bindings": both}).bindings) == 2


async def test_the_mcp_get_constraints_renders_through_the_tool_bindings(flow: Flow) -> None:
    await apply(flow, turn("t1", "wa-1", flow.cell.clock.now(), NO_BLACK))
    bound(flow, SEARCH)
    await flow.prove(VOICE_SOURCE, "call-2", PHONE)
    voice = principal(VOICE_SOURCE, flow.cell.config.space(SPACE_ID))
    container = SimpleNamespace(
        edge=FakeEdge({"nia_sk_voice": voice}),
        serve=flow.serve,
        signals=flow.signals,
        config=flow.cell.config,
        clock=flow.cell.clock,
    )
    asgi = create_app("read", container, [read.router, mcp.router])
    headers = {"authorization": "Bearer nia_sk_voice"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://cell") as client:
        minted = await client.post(
            "/v1/subject-tokens",
            json={
                "subject": SUBJECT.model_dump(mode="json", exclude_none=True),
                "conversation_id": "call-2",
                "verification": "V1",
            },
            headers=headers,
        )
        headers["niadra-subject-token"] = minted.json()["token"]

        async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
            body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            result: dict[str, Any] = (await client.post("/mcp", json=body, headers=headers)).json()["result"]
            return result

        listed = await call("tools/list", {})
        assert "get_constraints" in [t["name"] for t in listed["tools"]]
        plain = await call("tools/call", {"name": "get_constraints", "arguments": {}})
        assert plain["structuredContent"]["hard"][0]["values"] == ["preto"]
        assert "rendered" not in plain["structuredContent"]
        rendered = await call(
            "tools/call", {"name": "get_constraints", "arguments": {"tool": "search_products"}}
        )
        assert rendered["structuredContent"]["rendered"]["args"] == {"not_color": "preto"}
        assert 'search_products arguments: {"not_color":"preto"}' in rendered["content"][0]["text"]
        # A tool the space does not bind comes back as an error the model can read.
        unbound = await call("tools/call", {"name": "get_constraints", "arguments": {"tool": "book_visit"}})
        assert (unbound["isError"], unbound["content"][0]["text"]) == (True, "error: invalid_input")
        # Without the subject signals, the tool is not offered.
        space = flow.cell.config.space(SPACE_ID)
        flow.cell.config.put(space.model_copy(update={"features": FeatureSettings(enabled=frozenset())}))
        listed = await call("tools/list", {})
        assert "get_constraints" not in [t["name"] for t in listed["tools"]]
