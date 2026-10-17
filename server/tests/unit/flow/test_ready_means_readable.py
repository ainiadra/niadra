"""`POST /v1/ingest/status` says `ready` only when a read made then sees the conversation (7.1): memory
applied the extraction run and the compilation that covers it published its generation. QA on production
read 5 s after `ready` and missed a new open item in a pinned call's slots and delta; at 15 s it was there."""

from datetime import UTC, datetime

import pytest

from niadra.adapters.inbound.workers.runtime import registry
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.ingest import IngestStatusRequest
from niadra.domain.tasks import Task, TaskName
from niadra.entrypoints.serve import worker_modules
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_pinned_learns import extraction, read, said

START = datetime(2026, 9, 27, 4, 40, tzinfo=UTC)
ITEM = "Envio de roteador novo"


async def step(flow: Flow) -> Task | None:
    """Runs the next outbox task alone, as one worker would, so the status is asked between any two."""
    outbox: list[Task] = flow.cell.uow.store.module("pipeline").setdefault("outbox", [])
    if (
        not outbox and not flow.close_windows()
    ):  # the extraction's batch window closes when nothing else is left
        return None
    task = outbox.pop(0)
    await registry(flow, [m.handlers for m in worker_modules()])[task.name](task)
    return task


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(
        cell,
        ScriptedLlm(
            extraction("A internet caiu de novo ontem à noite"),
            extraction("Pediu um roteador novo", pending=ITEM),
        ),
    )
    cell.clock.at = START
    assert (
        await flow.send(WHATSAPP_SOURCE, *said("wa-1", "A internet caiu de novo ontem", START))
    ).errors == []
    await flow.drain()
    return flow


async def test_a_read_right_after_ready_sees_the_new_item_in_a_pinned_call(flow: Flow) -> None:
    clock = flow.cell.clock
    clock.advance(minutes=5)
    call = {
        "type": "verify",
        "idempotency_key": "call-1-attestation",
        "method": "network_attestation",
        "level": "V1",
        "conversation_id": "call-1",
        "handle": {"type": "phone_e164", "value": "+5511955553333"},
        "occurred_at": clock.now().isoformat(),
    }
    assert (await flow.send(VOICE_SOURCE, call)).errors == []
    await flow.drain()
    pinned = await read(flow)
    assert ITEM not in (pinned.text or "")

    # Another conversation leaves the open item while the call goes on; the workers take one task at a time.
    clock.advance(minutes=10)
    assert (
        await flow.send(WHATSAPP_SOURCE, *said("wa-2", "Preciso de um roteador novo", clock.now()))
    ).errors == []
    agent = principal()
    ask = IngestStatusRequest(conversation_id="wa-2")
    states: list[tuple[str, str]] = []
    while (task := await step(flow)) is not None:
        status = await flow.extract.status(agent, ask)
        states.append((task.name.value, status.state))
        if status.state != "ready":
            continue
        # Immediately: no other task runs between the answer and the reads.
        asked = await read(flow, "e o roteador?")
        assert asked.text == pinned.text  # the pinned body keeps its bytes
        assert ITEM in (asked.slots or "")
        changed = await read(flow, "e o roteador?", delta=True)
        assert changed.text == pinned.text
        assert ITEM in (changed.delta or "")
        break
    else:
        pytest.fail(f"never ready: {states}")

    ran = [name for name, _ in states]
    # The run was extracted and applied, and still `processing` until the compilation published.
    assert TaskName.RECONCILE.value in ran
    assert states[ran.index(TaskName.RECONCILE.value)][1] == "processing"
    assert states[-1][0] == TaskName.COMPILE.value


async def test_a_run_memory_has_not_applied_is_still_processing(flow: Flow) -> None:
    clock = flow.cell.clock
    clock.advance(minutes=10)
    assert (
        await flow.send(WHATSAPP_SOURCE, *said("wa-2", "Preciso de um roteador novo", clock.now()))
    ).errors == []
    ask = IngestStatusRequest(conversation_id="wa-2")
    await flow.drain(skip={TaskName.RECONCILE})
    held = await flow.extract.status(principal(), ask)
    assert (held.state, held.extraction) == ("processing", "ok")
    await flow.drain(skip={TaskName.COMPILE})
    assert (await flow.extract.status(principal(), ask)).state == "processing"
    await flow.drain()
    assert (await flow.extract.status(principal(), ask)).state == "ready"
