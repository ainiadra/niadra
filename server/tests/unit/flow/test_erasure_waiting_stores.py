"""What waits to be taken in when a person is erased (5.14.0): another customer's interactions not yet
folded into their signals, a batch set aside after a failed intake, a part of a turn that waits beside it for
a merge, and the terms of commitments and the packages and outcomes of handoffs, keep with the person redacted
out, so nothing taken in after the erasure brings them back."""

from typing import Any
from uuid import UUID, uuid4, uuid5

import orjson

from niadra.adapters.memory.signals import InMemorySignalRepository
from niadra.app.coordinate import _handoff_aad, _terms_aad
from niadra.app.intake import Intake
from niadra.contracts.coordinate import HandoffCreate, HandoffOutcome
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.ingest.intake import intake_aad
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.privacy.raw_index import ERASED
from niadra.domain.signals.body import decode
from niadra.domain.signals.records import body_aad, pending_aad
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_coordination import SUBJECT, _declare, _flow
from tests.unit.flow.test_erasure_derived_copies import (
    CLARA,
    CLARA_HANDLES,
    EMAIL,
    erase,
    from_clara,
    recordings,
    remember,
)
from tests.unit.flow.test_first_read import said
from tests.unit.ingest.fakes import stop_storing
from tests.unit.signals.test_signals_flow import apply, configured, turn
from tests.unit.signals.test_signals_flow import said as said_signals
from tests.unit.turns.records import turn as turn_record

TURN = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d0bb"


async def test_interactions_waiting_to_be_folded_are_redacted_and_the_fold_never_brings_them_back(
    cell: Cell,
) -> None:
    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    now = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, said_signals("wa-1", "Quero um vestido", now))).errors == []
    assert (await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi", now.isoformat()))).errors == []
    await flow.drain()
    contact = {
        "kind": "preference",
        "attr": "delivery.contact",
        "op": "in",
        "values": [EMAIL],
        "strength": "must",
        "scope": "persistent",
    }
    await apply(flow, turn("t-1", "wa-1", now, contact))
    signals = InMemorySignalRepository(cell.uow.store)
    assert any(EMAIL in str(o) for o in [await opened(cell, row) for row in signals.pending.values()])

    await erase(flow, *CLARA_HANDLES)

    assert signals.pending, "still waiting for its fold"
    assert all(EMAIL not in str(o) for o in [await opened(cell, row) for row in signals.pending.values()])
    cell.clock.advance(minutes=10)
    await flow.signals.derive_due(SPACE_ID)
    assert signals.rows, "folded"
    for row in signals.rows.values():
        body = decode(
            await cell.keys.decrypt(
                SPACE_ID, row.body_ct, body_aad(row.handle_id, row.beneficiary_id), row.dek_version
            )
        )
        assert EMAIL not in str(body)


async def opened(cell: Cell, row: Any) -> Any:
    return orjson.loads(
        await cell.keys.decrypt(SPACE_ID, row.body_ct, pending_aad(row.entry_key), row.dek_version)
    )


async def test_a_batch_set_aside_keeps_others_events_with_the_person_redacted(cell: Cell) -> None:
    flow = await remember(cell)
    flow.ingest._workers = cell.bus
    later = said(
        "wa-marcos", f"o e-mail da Clara é {EMAIL}", cell.clock.now().isoformat(), channel="whatsapp"
    )
    assert (await flow.send(WHATSAPP_SOURCE, later)).accepted == 1
    stop_storing(flow.ingest)
    [(key, row)] = list(cell.uow.store.data["ingest"]["intake"].items())
    async with cell.uow(SPACE_ID) as uow:
        await uow.ingest.set_aside_intake(row.intake_id, cell.clock.now())

    await erase(flow, *CLARA_HANDLES)

    kept = cell.uow.store.data["ingest"]["intake"][key]
    assert kept.failed_at is not None, "still set aside, never deleted"
    body = await cell.keys.decrypt(SPACE_ID, kept.body_ct, intake_aad(kept.intake_id), kept.dek_version)
    assert [e.text for e in Intake.from_json(orjson.loads(body)).events] == [f"o e-mail da Clara é {ERASED}"]


async def test_a_turns_part_waiting_for_its_merge_does_not_bring_the_person_back(cell: Cell) -> None:
    flow = await remember(cell)
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    first = turn_record(TURN, conversation_id="wa-marcos", fidelity="silver")
    assert (await flow.turns.record(caller, [first])).accepted == 1
    await flow.drain()
    part = turn_record(
        TURN, conversation_id="wa-marcos", fidelity="silver", args={"note": f"ligar no {CLARA}"}
    )
    part["calls"] = [{**part["calls"][0], "call_id": "k2"}, {**part["calls"][1], "call_id": "m2"}]
    assert (await flow.turns.record(caller, [part])).accepted == 1
    assert any(k.endswith(".part") for k in cell.objects.objects), "the part waits beside its turn"

    accepted = await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=CLARA_HANDLES[0]), idempotency_key=f"f-{uuid4()}",
        requested_by="user:dpo",
    )  # fmt: skip
    await flow.drain(skip={TaskName.APPLY_TURNS})
    async with cell.uow(SPACE_ID) as uow:
        request = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
    assert request is not None
    assert request.status is ErasureStatus.COMPLETED

    found = await recordings(flow)
    assert found
    assert all(CLARA not in str(r) for r in found)
    assert any("k2" in str(r) for r in found), "the part merged, without her"


async def test_commitment_terms_and_a_handoffs_package_and_outcome_are_redacted() -> None:
    flow = _flow(Feature.COORDINATION)
    cell = flow.cell
    now = cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, from_clara("wa-clara", "oi", now))).errors == []
    await flow.drain()
    caller = principal(WHATSAPP_SOURCE)
    terms = {"commitment_id": "c1", "type": "trial", "terms": {"days": 7, "notify": EMAIL}}
    await _declare(flow, "c1", kind="commitment.made", agent="agent:sales", detail=terms)
    await flow.coordinate.write(flow.coordination_queue.records)
    reason = f"avisar a Clara em {EMAIL}"
    request = HandoffCreate(subject=SUBJECT, agent="agent:sales", target="queue:closing", reason=reason)
    created = await flow.coordinate.create_handoff(caller, request, "h1")
    outcome = HandoffOutcome(outcome="sold", detail={"note": f"retornar para {EMAIL}"})
    await flow.coordinate.handoff_outcome(caller, created.handoff_id, outcome, "o1")

    await erase(flow, *CLARA_HANDLES)

    commitment_id = uuid5(SPACE_ID, "commitment:c1")
    handoff_id = UUID(created.handoff_id)
    async with cell.uow(SPACE_ID) as uow:
        [sealed] = await uow.coordinate.terms_of([commitment_id])
        row = await uow.coordinate.handoff(handoff_id)
        closed = await uow.coordinate.outcome_of(handoff_id)
    assert row is not None
    assert closed is not None
    kept_terms = await cell.keys.decrypt(
        SPACE_ID, sealed.terms_ct, _terms_aad(commitment_id), sealed.dek_version
    )
    assert orjson.loads(kept_terms) == {"days": 7, "notify": ERASED}
    package = await cell.keys.decrypt(
        SPACE_ID, row.package_ct, _handoff_aad(handoff_id, "package_ct"), row.dek_version
    )
    assert EMAIL not in package.decode()
    assert ERASED in package.decode()
    detail = await cell.keys.decrypt(
        SPACE_ID, closed.outcome_ct, _handoff_aad(handoff_id, "outcome_ct"), closed.dek_version
    )
    assert orjson.loads(detail) == {"note": f"retornar para {ERASED}"}
