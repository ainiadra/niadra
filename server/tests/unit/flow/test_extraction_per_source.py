"""Each agent extracts what its task asks for: the schema's per-source predicates reach the
extraction of that source's sessions only."""

from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.extract import rules_for
from niadra.config.models import PredicateSpec
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.flow.harness import Flow

PHONE = {"type": "phone_e164", "value": "+5511955557777"}


def with_overrides(cell: Cell) -> None:
    config = sample_config()
    extraction = config.extraction.model_copy(
        update={
            "source_overrides": {
                # By source name or by id, as the schema allows.
                "billing-agent": [PredicateSpec(name="dispute_reason", category="billing")],
                str(WHATSAPP_SOURCE): [PredicateSpec(name="delivery_change", category="contract")],
            }
        }
    )
    cell.config.put(config.model_copy(update={"extraction": extraction}))


def answer(turn: int) -> dict[str, Any]:
    def fact(predicate: str, value: str) -> dict[str, Any]:
        return {
            "op": "add",
            "predicate": predicate,
            "value": value,
            "target": None,
            "evidence": ["e1"],
            "valid_at": None,
            "certainty": 0.9,
            "contradicts": [],
            "about": None,
            "object": None,
        }

    return {
        "episode": {
            "summary": "Contestou a cobrança e pediu outra data de entrega",
            "intent": "complaint",
            "category": "invoice",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.2,
        },
        "facts": [
            fact("dispute_reason", f"cobrança em dobro {turn}"),
            fact("delivery_change", f"entrega na sexta {turn}"),
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def stored(flow: Flow) -> list[str]:
    return sorted(f.value for f in flow.cell.uow.store.module("memory")["facts"].values())


async def test_each_source_extracts_the_predicates_its_task_asks_for(cell: Cell) -> None:
    with_overrides(cell)
    config = cell.config.spaces()[0]
    billing = {p.name for p in rules_for(config, BILLING_SOURCE).predicates}
    chat = {p.name for p in rules_for(config, WHATSAPP_SOURCE).predicates}
    assert "dispute_reason" in billing - chat
    assert "delivery_change" in chat - billing
    # The overrides add to the space's schema, never replace it.
    assert {"preferred_name", "disputed_invoice"} <= billing & chat

    flow = Flow.build(cell, ScriptedLlm(answer(1), answer(2)))
    now = cell.clock.now().isoformat()
    task = {
        "type": "event",
        "idempotency_key": "t1-1",
        "channel": "erp",
        "task_id": "dispute-0823",
        "handles": [PHONE],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "A fatura veio em dobro, quero contestar"},
        "occurred_at": now,
    }
    ended = {"type": "task.ended", "idempotency_key": "t1-end", "task_id": "dispute-0823", "occurred_at": now}
    assert (await flow.send(BILLING_SOURCE, task, ended)).errors == []
    await flow.drain()
    assert stored(flow) == ["cobrança em dobro 1"]
    assert "dispute_reason" in str(flow.llm.calls[0])
    assert "delivery_change" not in str(flow.llm.calls[0])

    chat_turn = {
        **task,
        "idempotency_key": "wa-1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "task_id": None,
    }
    wa_end = {"type": "conversation.ended", "idempotency_key": "wa-1-end", "conversation_id": "wa-1"}
    assert (await flow.send(WHATSAPP_SOURCE, chat_turn, {**wa_end, "occurred_at": now})).errors == []
    await flow.drain()
    assert stored(flow) == ["cobrança em dobro 1", "entrega na sexta 2"]
    assert "delivery_change" in str(flow.llm.calls[1])


async def test_an_internal_agents_task_that_only_acted_is_no_conversation_of_the_customer(cell: Cell) -> None:
    # The billing agent credits an invoice in a task and ends it: no model is asked,
    # no episode is written (it was neither a conversation nor abandoned), and the actions stay memory.
    llm = ScriptedLlm()
    flow = Flow.build(cell, llm)
    now = cell.clock.now().isoformat()
    acted = {
        "type": "event",
        "kind": "action",
        "idempotency_key": "credit-task-1",
        "channel": "erp",
        "task_id": "credit-0823",
        "handles": [PHONE],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0823"}],
        "speaker": {"role": "ai_agent", "id": "billing-agent"},
        "action": {"operation": "credit", "result": "crédito lançado"},
        "occurred_at": now,
    }
    ended = {
        "type": "task.ended",
        "idempotency_key": "credit-task-end",
        "task_id": "credit-0823",
        "occurred_at": now,
    }
    assert (await flow.send(BILLING_SOURCE, acted, ended)).errors == []
    await flow.drain()
    assert llm.calls == []
    memory = flow.cell.uow.store.module("memory")
    assert not memory.get("episodes")
    async with cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE["value"])
        assert handle is not None
        [action] = await uow.objects.actions_for([handle.handle_id], limit=10)
    assert action.operation == "credit"
