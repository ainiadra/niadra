"""Deny by default, out of the box: a new space's starter policy keeps health and
technical matters away from the billing agent, from extraction to the pack, without any setup."""

from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.policy import PolicyService
from niadra.config.models import SourceConfig, SpaceConfig
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.policy import SimulatedItem, SimulateRequest
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, HandleType, Verification
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    principal,
)
from tests.unit.flow.harness import Flow

PHONE = "+5511977776666"
CARE_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c0a1")


def starter_space() -> SpaceConfig:
    """A space as the control plane creates it: sources only, policy and schema from the starter. A source
    reads for exactly the purposes it declares, so each declares the starter's `service`, which a space on the
    starter purposes collects everything for, as the bootstrap's first sources do."""
    return SpaceConfig(
        space_id=SPACE_ID,
        tenant_id=TENANT_ID,
        region="us-east-2",
        environment="sandbox",
        sources=[
            SourceConfig(
                source_id=WHATSAPP_SOURCE,
                name="whatsapp",
                channel="whatsapp",
                purposes=frozenset({"service", "support"}),
                audience=Audience.CUSTOMER_AGENT,
            ),
            SourceConfig(
                source_id=VOICE_SOURCE,
                name="voice",
                channel="voice",
                purposes=frozenset({"service", "support"}),
                audience=Audience.CUSTOMER_AGENT,
                verification_ceiling=Verification.V3,
            ),
            SourceConfig(
                source_id=BILLING_SOURCE,
                name="billing",
                channel="erp",
                purposes=frozenset({"billing", "service"}),
                audience=Audience.INTERNAL_AGENT,
                trusted_action_ops=frozenset({"credit"}),
            ),
            SourceConfig(
                source_id=CARE_SOURCE,
                name="care",
                channel="voice",
                purposes=frozenset({"care", "service"}),
                audience=Audience.CUSTOMER_AGENT,
                verification_ceiling=Verification.V3,
            ),
        ],
    )


def answer(category: str, promise: str, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "episode": {
            "summary": f"Conversa sobre {category}",
            "intent": "request",
            "category": category,
            "outcome": "pending",
            "resolution": None,
            "sentiment": 0,
        },
        "facts": facts or [],
        "open_items": [
            {
                "description": promise,
                "owner": "company",
                "due_at": None,
                "evidence": ["e2"],
                "object": None,
                "expected_operation": None,
                "about": None,
            }
        ],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def billing_fact() -> dict[str, Any]:
    return {
        "op": "add",
        "predicate": "billing_issue",
        "value": "cobrança em dobro na fatura 0823",
        "target": None,
        "evidence": ["e1"],
        "valid_at": None,
        "certainty": 0.9,
        "contradicts": [],
        "about": None,
        "object": None,
    }


async def conversation(flow: Flow, key: str, customer: str, agent: str) -> None:
    clock = flow.cell.clock
    items = []
    for n, (role, text) in enumerate((("customer", customer), ("ai_agent", agent))):
        items.append(
            {
                "type": "event",
                "idempotency_key": f"{key}-{n}",
                "channel": "whatsapp",
                "conversation_id": key,
                "handles": [{"type": "phone_e164", "value": PHONE}],
                "speaker": {"role": role},
                "content": {"type": "text", "text": text},
                "occurred_at": clock.now().isoformat(),
            }
        )
        clock.advance(minutes=1)
    items.append(
        {
            "type": "conversation.ended",
            "idempotency_key": f"{key}-end",
            "conversation_id": key,
            "occurred_at": clock.now().isoformat(),
        }
    )
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    clock.advance(hours=1)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    cell.config.put(starter_space())
    flow = Flow.build(
        cell,
        ScriptedLlm(
            answer("health", "Enviar o resultado do exame de sangue"),
            answer("billing", "Estornar a cobrança em dobro", [billing_fact()]),
        ),
    )
    await conversation(
        flow,
        "wa-exam",
        "Vocês já têm o resultado do meu exame de sangue?",
        "Vou te mandar o resultado amanhã.",
    )
    await conversation(
        flow, "wa-invoice", "A fatura 0823 veio com cobrança em dobro", "Vou pedir o estorno da cobrança."
    )
    # The nightly job writes the tail summaries, which quote both conversations.
    await flow.memory.consolidate(Task(TaskName.CONSOLIDATE, SPACE_ID, profile_id=(await profile_of(flow))))
    return flow


async def profile_of(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
        assert profile is not None
        return profile.profile_id


async def simulated(flow: Flow, source_id: UUID, level: Verification) -> list[SimulatedItem]:
    simulator = PolicyService(flow.cell.uow, flow.cell.config)
    config = flow.cell.config.space(SPACE_ID)
    request = SimulateRequest(profile_id=await profile_of(flow), source_id=source_id, verification=level)
    return (await simulator.simulate(principal(config=config), request)).items


async def seen_by(flow: Flow, source_id: UUID, level: Verification) -> list[SimulatedItem]:
    return [i for i in await simulated(flow, source_id, level) if i.delivered]


def topics(items: list[SimulatedItem]) -> set[tuple[str, str]]:
    return {(i.kind, i.category) for i in items}


async def test_the_billing_agent_never_sees_health_even_at_the_top_level(flow: Flow) -> None:
    seen = await seen_by(flow, BILLING_SOURCE, Verification.V4)
    assert ("episode", "billing") in topics(seen)
    assert ("fact", "billing") in topics(seen)
    assert ("episode", "health") not in topics(seen)
    # The two promises are open items; only the billing one reaches it, the exam stays with care.
    assert [i.kind for i in seen].count("open_item") == 1
    # The summary quotes the health conversation too, so it stays out as a whole.
    summaries = [i for i in await simulated(flow, BILLING_SOURCE, Verification.V4) if i.kind == "summary"]
    assert [(s.delivered, s.reason, s.rule) for s in summaries] == [(False, "policy", "purpose")] * 2


async def test_the_support_agent_hears_the_customers_own_billing_words_at_v1_and_never_gets_health(
    flow: Flow,
) -> None:
    # Billing asks for V2, but the dispute is what the customer wrote: the mirror rule hands it back from
    # V1, and the simulation says so. Nothing of it reaches V0; health never mirrors.
    at_v1 = await seen_by(flow, WHATSAPP_SOURCE, Verification.V1)
    mirrored = {(i.kind, i.category) for i in at_v1 if i.rule == "mirror"}
    assert {("episode", "billing"), ("fact", "billing")} <= mirrored
    assert ("episode", "health") not in topics(at_v1)
    at_v0 = topics(await seen_by(flow, WHATSAPP_SOURCE, Verification.V0))
    assert ("episode", "billing") not in at_v0
    assert ("fact", "billing") not in at_v0
    at_v4 = await seen_by(flow, VOICE_SOURCE, Verification.V4)
    assert ("episode", "billing") in topics(at_v4)
    assert ("episode", "health") not in topics(at_v4)
    assert [i.kind for i in at_v4].count("open_item") == 1


async def test_the_care_agent_reads_health_once_the_customer_proved_v3(flow: Flow) -> None:
    assert ("episode", "health") not in topics(await seen_by(flow, CARE_SOURCE, Verification.V2))
    at_v3 = await seen_by(flow, CARE_SOURCE, Verification.V3)
    assert ("episode", "health") in topics(at_v3)
    assert [i.kind for i in at_v3].count("open_item") == 1
    # Care is not billing: the invoice dispute stays with billing and support.
    assert ("fact", "billing") not in topics(at_v3)


async def reads_live(
    flow: Flow,
    source_id: UUID,
    level: Verification,
    *,
    conversation: str | None = None,
    task: str | None = None,
) -> list[str]:
    """The `live` lines a source reads after its own conversation or task proved `level`."""
    config = flow.cell.config.space(SPACE_ID)
    at = flow.cell.clock.now().isoformat()
    proof = {
        "type": "verify",
        "idempotency_key": f"{conversation or task}-proof",
        "method": "otp_sms",
        "level": level.value,
        "conversation_id": conversation,
        "task_id": task,
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": at,
    }
    assert (await flow.send(source_id, proof)).errors == []
    request = ContextRequest(
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        verification=level,
        conversation_id=conversation,
        task_id=task,
    )
    response = await flow.serve.context(principal(source_id, config), request)
    assert response.verification.effective is level
    return [t.text for t in response.live]


async def test_a_health_conversation_in_progress_reaches_only_the_care_agent(flow: Flow) -> None:
    # On the `live` block: before extraction files the conversation, its raw lines already
    # carry health, the second one too; billing and support never read them, care does at V3.
    clock = flow.cell.clock
    lines = ("Minha cirurgia foi remarcada para sexta", "Então preciso adiar a entrega")
    items = []
    for n, text in enumerate(lines):
        items.append(
            {
                "type": "event",
                "idempotency_key": f"wa-surgery-{n}",
                "channel": "whatsapp",
                "conversation_id": "wa-surgery",
                "handles": [{"type": "phone_e164", "value": PHONE}],
                "speaker": {"role": "customer"},
                "content": {"type": "text", "text": text},
                "occurred_at": clock.now().isoformat(),
            }
        )
        clock.advance(minutes=1)
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []

    assert await reads_live(flow, BILLING_SOURCE, Verification.V2, task="close-invoice") == []
    assert await reads_live(flow, VOICE_SOURCE, Verification.V3, conversation="call-support") == []
    assert await reads_live(flow, CARE_SOURCE, Verification.V3, conversation="call-care") == list(lines)
