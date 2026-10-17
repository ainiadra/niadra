"""A session of no customer closes like any other (5.3, 5.4): one whose only handle is a test or placeholder
value, or whose customer was erased, gathers no memory and leaves no dead task. Every task here goes
through the worker runtime, which takes the lease its spec asks for."""

from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import RecordedDelivery, ScriptedLlm
from niadra.adapters.memory.pipeline import InMemoryDeadTaskRepository
from niadra.app.pipeline import PipelineService
from niadra.contracts.ingest import IngestStatusRequest
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.records import SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_blocked_values import extraction
from tests.unit.flow.test_first_read import said
from tests.unit.flow.test_restore_fact import customer

PLACEHOLDER = "qa@example.com"  # the domain is on the fixed list of test values


def typed(conversation: str, at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "email", "value": PLACEHOLDER}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Me chama de Pessoa de teste, quero trocar o endereço"},
        "occurred_at": at,
    }


def ended(conversation: str, at: str) -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at,
    }


async def dead(cell: Cell) -> list[Task]:
    return await InMemoryDeadTaskRepository(cell.uow.store).dead(SPACE_ID, None, None, 10)


def sessions(cell: Cell) -> list[SessionRecord]:
    return [s for _, s in cell.uow.store.data["sessions"]["sessions"].values()]


def waiting_retries(cell: Cell) -> list[Task]:
    scheduled = cell.uow.store.module("pipeline").get("scheduled", [])
    return [task for task, _, reason in scheduled if reason == DelayReason.RETRY]


def nothing_remembered(cell: Cell) -> bool:
    memory = cell.uow.store.module("memory")
    return not memory.get("facts") and not memory.get("episodes")


async def test_a_session_of_a_placeholder_value_closes_and_leaves_no_dead_task(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(extraction()))
    now = cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, typed("qa-1", now), ended("qa-1", now))).errors == []
    [close] = flow.pending(TaskName.CLOSE_SESSION)
    assert close.profile_id is None

    ran = await flow.drain(leased=True)

    assert await dead(cell) == []
    assert waiting_retries(cell) == []
    # It closed, which is what bills it once an agent read in it, and its extraction applied to no one.
    [session] = sessions(cell)
    assert (session.profile_id, session.close_reason) == (None, "ended")
    assert session.closed_at is not None
    assert {TaskName.CLOSE_SESSION, TaskName.GATE, TaskName.RECONCILE} <= {t.name for t in ran}
    assert TaskName.COMPILE not in {t.name for t in ran}
    assert nothing_remembered(cell)
    status = await flow.extract.status(principal(), IngestStatusRequest(conversation_id="qa-1"))
    assert status.state == "ready"


async def test_a_close_buried_for_having_no_profile_closes_when_it_is_sent_again(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(extraction()))
    now = cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, typed("qa-1", now), ended("qa-1", now))).errors == []
    [close] = flow.pending(TaskName.CLOSE_SESSION)
    pipeline = PipelineService(cell.uow, cell.bus, cell.clock)
    await pipeline.bury(close, "InvalidInput")  # as the runtime left it before sessions of nobody closed

    assert await pipeline.requeue_dead(SPACE_ID, TaskName.CLOSE_SESSION) == 1
    [again] = cell.bus.take()
    delivery = RecordedDelivery(again)
    await flow.worker().handle(delivery)

    assert delivery.acked
    assert await dead(cell) == []
    [session] = sessions(cell)
    assert session.closed_at is not None
    # The copy still in the outbox finds the session closed and does nothing.
    await flow.drain(leased=True)
    assert await dead(cell) == []
    assert nothing_remembered(cell)


async def test_the_close_of_an_erased_customers_session_writes_nothing_back(cell: Cell) -> None:
    flow = Flow.build(cell, ScriptedLlm(extraction()))
    now = cell.clock.now().isoformat()
    turn = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, turn, ended("wa-1", now))).errors == []
    profile_id, _ = await customer(flow)
    [close] = flow.pending(TaskName.CLOSE_SESSION)
    assert close.profile_id == profile_id

    # The customer is forgotten while the close waits in the queue.
    await forget(flow, profile_id, skip={TaskName.CLOSE_SESSION})
    assert sessions(cell) == []
    ran = await flow.drain(leased=True)

    assert [t.name for t in ran] == [TaskName.CLOSE_SESSION]
    assert await dead(cell) == []
    assert waiting_retries(cell) == []
    assert sessions(cell) == []
    assert cell.uow.store.module("extract").get("runs", {}) == {}
    assert nothing_remembered(cell)


async def test_a_session_whose_placeholder_value_was_erased_closes_with_nothing_of_it(cell: Cell) -> None:
    llm = ScriptedLlm(extraction())
    flow = Flow.build(cell, llm)
    now = cell.clock.now().isoformat()
    assert (await flow.send(WHATSAPP_SOURCE, typed("qa-1", now))).errors == []
    await flow.drain(leased=True)
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.EMAIL, "", PLACEHOLDER)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
    assert profile is not None

    # The value's own profile is forgotten: its events go, and the session, which was nobody's, stays open.
    await forget(flow, profile.profile_id)
    [session] = sessions(cell)
    assert (session.profile_id, session.closed_at) == (None, None)
    assert (await flow.send(WHATSAPP_SOURCE, ended("qa-1", now))).errors == []
    flow.ran.clear()
    await flow.drain(leased=True)

    assert await dead(cell) == []
    assert waiting_retries(cell) == []
    [session] = sessions(cell)
    assert session.closed_at is not None
    # No model read it, and what the run keeps is the template of a session with no turns.
    assert llm.calls == []
    assert TaskName.COMPILE not in {t.name for t in flow.ran}
    assert nothing_remembered(cell)
    assert "Pessoa de teste" not in repr(cell.uow.store.data)


async def forget(flow: Flow, profile_id: UUID, skip: set[TaskName] | None = None) -> None:
    request = ForgetRequest(target="profile", profile_id=profile_id)
    accepted = await flow.privacy.forget(SPACE_ID, request, idempotency_key="f1", requested_by="user:dpo")
    await flow.drain(skip=skip or set())
    out = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert out.status == ErasureStatus.COMPLETED.value
