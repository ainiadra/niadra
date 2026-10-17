"""Free text from inside a system is read like a conversation: a ticket's description that a
webhook mapping turns into a message becomes memory, without the system naming a conversation."""

import json
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.models import HandleExpr, Mapping, MappingRule, ObjectExpr, WebhookAuth
from niadra.domain.vocabulary import EventKind, HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, sample_config
from tests.unit.flow.harness import Flow


def extraction() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Abriu chamado sobre cobrança em dobro",
            "intent": "complaint",
            "category": "invoice",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.3,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "disputed_invoice",
                "value": "fatura 0823 em dobro",
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


async def test_a_ticket_description_becomes_memory(cell: Cell) -> None:
    rule = MappingRule(
        match="type == 'ticket.created'",
        kind=EventKind.MESSAGE,
        idempotency_key="id",
        occurred_at="created",
        channel="helpdesk",
        text="ticket.description",
        handles=[HandleExpr(type=HandleType.PHONE_E164, value="customer.phone")],
        object_refs=[ObjectExpr(type="ticket", namespace="crm", id="ticket.id")],
    )
    mapping = Mapping(
        source_id=BILLING_SOURCE,
        version="m1",
        auth=WebhookAuth(mode="url_token", secret_ref=f"spaces/{SPACE_ID}/webhook-in/wh"),
        rules=[rule],
    )
    cell.config.put(sample_config().model_copy(update={"mappings": [mapping]}))
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/wh"] = "t0k"
    flow = Flow.build(cell, ScriptedLlm(extraction()))
    payload = {
        "type": "ticket.created",
        "id": "evt_1",
        "created": cell.clock.now().isoformat(),
        "customer": {"phone": "+5511944441111"},
        "ticket": {"id": "T-5", "description": "Minha fatura 0823 veio em dobro, quero o estorno"},
    }
    body = json.dumps(payload).encode()
    assert (await flow.ingest.ingest_webhook(BILLING_SOURCE, {}, body, {"token": "t0k"})).accepted == 1
    await flow.drain()
    cell.clock.advance(minutes=31)
    space = cell.config.spaces()[0].space_id
    assert await flow.sessions.sweep_idle(space) == 1
    await flow.drain()
    facts = cell.uow.store.module("memory")["facts"].values()
    assert [f.value for f in facts] == ["fatura 0823 em dobro"]
    assert [f.origin_conversation_id for f in facts] == ["obj:ticket:crm:T-5"]
