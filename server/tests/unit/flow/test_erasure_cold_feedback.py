"""The company's feedback in the cold archive (5.14.0, 8.4): once a month of events left for the archive, an
erasure of what that feedback acted on leaves the archived feedback rows with ids and kinds only, as it
does to the live ones. No value of a correction and no reason stays in any archived row, and a second
`erase_cold` changes nothing."""

import json
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.archive import content_aad
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import FactStatus, OpenItemStatus
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_erasure_feedback import ended, forget
from tests.unit.flow.test_erasure_leftovers import (
    archive_month_of,
    archived_rows,
    later,
    phone_handle,
    with_item_and_value,
)
from tests.unit.flow.test_first_read import PHONE, remembered, said

REASON = "a cliente pediu pelo telefone, disse que mora na Rua das Flores 12"
VALUE = "Rafaela Souza"


async def archived_contents(flow: Flow) -> list[dict[str, Any]]:
    """Every archived row decrypted as stored, without the masking a reader applies."""
    found = []
    for row in archived_rows(flow):
        plain = await flow.cell.keys.decrypt(
            SPACE_ID, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
        )
        found.append(json.loads(plain))
    return found


def archive_bytes(flow: Flow) -> dict[str, bytes]:
    return {k: v for k, v in flow.cell.objects.objects.items() if k.startswith(f"archive/events/{SPACE_ID}/")}


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(with_item_and_value(remembered()), later()))
    now = cell.clock.now().isoformat()
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via, CEP 88221-615", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, ended("wa-1", now))).errors == []
    await flow.drain()
    return flow


async def test_an_erasure_leaves_no_value_or_reason_of_the_feedback_in_the_cold_archive(flow: Flow) -> None:
    handle = await phone_handle(flow)
    started = flow.cell.clock.now()
    async with flow.cell.uow(SPACE_ID) as uow:
        [item] = await uow.memory.open_items_for([handle], tuple(OpenItemStatus))
        [fact] = await uow.memory.facts_for([handle], tuple(FactStatus))
    subject = Handle.model_validate({"type": "phone_e164", "value": PHONE})
    config = flow.cell.config.space(SPACE_ID)
    for request in (
        FeedbackRequest(
            idempotency_key="fb-resolve",
            subject=subject,
            action="resolve_open_item",
            open_item_id=str(item.item_id),
            reason=REASON,
        ),
        FeedbackRequest(
            idempotency_key="fb-correct",
            subject=subject,
            action="correct_fact",
            fact_id=str(fact.fact_id),
            value=VALUE,
            reason=REASON,
        ),
    ):
        assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), request)).accepted == 1
    await flow.drain()
    # The month leaves the hot window: the conversation and the feedback live only in the archive now.
    flow.cell.clock.advance(days=45)
    await archive_month_of(flow, started)
    before = await archived_contents(flow)
    assert {c["canonical_type"] for c in before if c["conversation_id"] is None} == {
        "feedback.resolve_open_item",
        "feedback.correct_fact",
    }
    assert REASON in str(before)
    assert VALUE in str(before)

    out = await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-1"))

    after = await archived_contents(flow)
    # The conversation's rows left; the feedback rows stay as audit records, ids and kinds only.
    kept = {c["canonical_type"]: c for c in after}
    assert set(kept) == {"feedback.resolve_open_item", "feedback.correct_fact"}
    assert kept["feedback.resolve_open_item"]["fields"] == {
        "action": "resolve_open_item",
        "open_item_id": str(item.item_id),
    }
    assert kept["feedback.correct_fact"]["fields"] == {"action": "correct_fact", "fact_id": str(fact.fact_id)}
    assert all(c["text"] is None for c in after)
    assert REASON not in str(after)
    assert VALUE not in str(after)
    async with flow.cell.uow(SPACE_ID) as uow:
        request = await uow.privacy.get_erasure(SPACE_ID, UUID(out.request_id))
    assert request is not None
    assert request.counts["cold_feedback_values"] == 2
    # Never on the receipt: the ids only guide `erase_cold`.
    assert "cold_feedback" not in out.erased

    # A retried `erase_cold` finds nothing left to change.
    stored = archive_bytes(flow)
    await flow.privacy.erase_cold(Task(TaskName.ERASE_COLD, SPACE_ID, {"request_id": out.request_id}))
    assert archive_bytes(flow) == stored
    async with flow.cell.uow(SPACE_ID) as uow:
        again = await uow.privacy.get_erasure(SPACE_ID, UUID(out.request_id))
    assert again is not None
    assert again.counts["cold_feedback_values"] == 2


async def test_archived_feedback_on_what_stays_keeps_its_reason(flow: Flow) -> None:
    """Values are not stripped before archiving: the company's reason on a row nobody erased stays in the
    archive, for access and portability past the hot window."""
    handle = await phone_handle(flow)
    started = flow.cell.clock.now()
    async with flow.cell.uow(SPACE_ID) as uow:
        [item] = await uow.memory.open_items_for([handle], tuple(OpenItemStatus))
    request = FeedbackRequest(
        idempotency_key="fb-resolve",
        subject=Handle.model_validate({"type": "phone_e164", "value": PHONE}),
        action="resolve_open_item",
        open_item_id=str(item.item_id),
        reason=REASON,
    )
    config = flow.cell.config.space(SPACE_ID)
    assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), request)).accepted == 1
    await flow.drain()
    flow.cell.clock.advance(days=45)
    await archive_month_of(flow, started)

    # Another conversation goes; this item and its resolution are not part of it.
    now = flow.cell.clock.now().isoformat()
    other = said("wa-2", "Qual o horário da loja?", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, other, ended("wa-2", now))).errors == []
    await flow.drain()
    await forget(flow, ForgetRequest(target="conversation", conversation_id="wa-2"))

    [resolution] = [
        c for c in await archived_contents(flow) if c["canonical_type"] == "feedback.resolve_open_item"
    ]
    assert resolution["fields"]["reason"] == REASON
