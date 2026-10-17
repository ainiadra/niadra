"""The pass over what memory stored before the withholding rules (`app/withhold.py`): a conversation
extracted the old way left a document number in a fact, the summary, a promise, a profile slot, a settled
value and the stored result, and a diagnosis under an ordinary predicate. The pass scrubs all of it, keeps
the protocol and the amount, and changes nothing when it runs again."""

from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app import extract as extraction
from niadra.app.withhold import KINDS, STATE_KEY
from niadra.domain.extract import validate as validation
from niadra.domain.extract.withhold import WITHHOLD_VERSION
from niadra.domain.records import ProfileAttr
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow

DOCUMENT = "82661763"
PHONE = "+5511955554444"
CONVERSATION = "wa-old"


def fact(predicate: str, value: str) -> dict[str, Any]:
    return {
        "op": "add",
        "predicate": predicate,
        "value": value,
        "target": None,
        "evidence": ["e1"],
        "valid_at": None,
        "certainty": 1.0,
        "contradicts": [],
        "about": None,
        "object": None,
    }


ANSWER = {
    "episode": {
        "summary": f"Cliente deu o documento {DOCUMENT} e a asma crônica; protocolo 45778204, R$ 171,61.",
        "intent": "complaint",
        "category": "invoice",
        "outcome": "pending",
        "resolution": f"Documento {DOCUMENT} anotado.",
        "sentiment": 0.0,
    },
    "facts": [
        fact("disputed_invoice", DOCUMENT),
        fact("disputed_invoice", "asma crônica"),
        fact("disputed_invoice", "cobrança de R$ 171,61"),
        fact("preferred_name", "Rafa"),
    ],
    "open_items": [
        {
            "description": f"Conferir o documento {DOCUMENT}",
            "owner": "company",
            "evidence": ["e1"],
            "due_at": None,
            "object": None,
            "expected_operation": None,
            "about": None,
        }
    ],
    "resolve": [],
    "profile": [],
    "agent_questions": [],
    "agent_claims": [],
    "values": [f"code {DOCUMENT}", "protocol 45778204"],
}


def turn(n: int, text: str, at: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{CONVERSATION}-{n}",
        "channel": "whatsapp",
        "conversation_id": CONVERSATION,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text},
        "occurred_at": at,
    }


def memory(cell: Cell) -> str:
    data = cell.uow.store.data
    return repr({name: rows for name, rows in data.items() if name not in ("ingest", "pipeline")})


@pytest.fixture
async def old_memory(cell: Cell, monkeypatch: pytest.MonkeyPatch) -> Flow:
    """Memory as the extraction wrote it before the rules: validation without the withholding step."""
    flow = Flow.build(cell, ScriptedLlm(ANSWER))
    with monkeypatch.context() as patched:
        patched.setattr(validation, "withhold_result", lambda result, *_: result)
        patched.setattr(extraction, "withhold_result", lambda result, *_: result)
        at = cell.clock.now().isoformat()
        sent = await flow.send(
            WHATSAPP_SOURCE,
            turn(1, f"Sou o Rafa, o número do meu documento é {DOCUMENT} e tenho asma crônica.", at),
            turn(2, "O protocolo é 45778204 e cobraram R$ 171,61.", at),
        )
        assert sent.errors == []
        ended = {"type": "conversation.ended", "idempotency_key": "end", "conversation_id": CONVERSATION}
        await flow.send(WHATSAPP_SOURCE, {**ended, "occurred_at": at})
        await flow.drain()
    space_id = cell.config.spaces()[0].space_id
    handle, _ = next(iter(cell.uow.store.module("memory")["facts"]))
    slot = ProfileAttr(handle, "preferred_channel", f"doc {DOCUMENT}", WHATSAPP_SOURCE, cell.clock.now())
    async with cell.uow(space_id) as uow:
        await uow.memory.put_attr(slot)
    assert DOCUMENT in memory(cell)
    return flow


def release_pages(cell: Cell) -> int:
    """The pages the pass scheduled, published as the relay would once they are due."""
    pipeline = cell.uow.store.module("pipeline")
    pages = [row[0] for row in pipeline.get("scheduled", []) if row[0].name is TaskName.WITHHOLD_STORED]
    pipeline["scheduled"] = [
        r for r in pipeline.get("scheduled", []) if r[0].name is not TaskName.WITHHOLD_STORED
    ]
    pipeline.setdefault("outbox", []).extend(pages)
    return len(pages)


async def run_pass(flow: Flow) -> int:
    """The loop step, then every page it schedules."""
    space_id = flow.cell.config.spaces()[0].space_id
    started = await flow.extract.watch_withholding(space_id)
    for _ in range(3 * len(KINDS)):
        await flow.drain()
        if not release_pages(flow.cell):
            break
    return started


async def test_the_pass_scrubs_what_was_stored_before_the_rules(old_memory: Flow) -> None:
    flow = old_memory
    cell = flow.cell

    assert await run_pass(flow) == 1

    after = memory(cell)
    assert DOCUMENT not in after
    stored = cell.uow.store.module("memory")
    assert sorted(f.value for f in stored["facts"].values()) == ["Rafa", "cobrança de R$ 171,61"]
    assert list(stored["attrs"].values()) == []
    assert "[retido:documento]" in after
    for kept in ("45778204", "171,61", "Rafa"):
        assert kept in after, kept
    # Only the numbers of an episode change: its category is the extraction's.
    assert {e.category for e, _ in stored["episodes"].values()} == {"invoice"}
    assert DOCUMENT in repr(cell.uow.store.data["ingest"])  # the log is not this pass's to change


async def test_the_pass_runs_once_per_version_and_changes_nothing_the_second_time(old_memory: Flow) -> None:
    flow = old_memory
    cell = flow.cell
    space_id = cell.config.spaces()[0].space_id
    await run_pass(flow)
    once = memory(cell)
    async with cell.uow(space_id) as uow:
        assert await uow.memory.purpose_state(STATE_KEY) == WITHHOLD_VERSION

    assert await run_pass(flow) == 0
    for kind in KINDS:
        payload = {"version": WITHHOLD_VERSION, "kind": kind, "after": None, "changed": 0}
        await flow.extract.withhold_stored(Task(TaskName.WITHHOLD_STORED, space_id, payload))
    await flow.drain(skip={TaskName.WITHHOLD_STORED})

    assert memory(cell) == once
