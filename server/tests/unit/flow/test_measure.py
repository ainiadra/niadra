"""Context-use measurement from a real extraction answer: the agent's questions and claims name
their object and the facts they contradict, so object matching and selection misses count in
production, and a pack compiled on the read path is measured like a stored one."""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm, session_turns, wire_answer
from niadra.config.models import TriggerRuleConfig, WebhookEndpoint
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.contracts.review import VerdictIn, VerdictsRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.review.verdicts import TargetKind
from niadra.domain.vocabulary import DeliveryPath, Environment, HandleType, Verification
from niadra.ports.infra import LlmResult
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

PHONE = "+5511955554444"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
VISIT = {"type": "technical_visit", "namespace": "field", "id": "7781"}
Reader = Callable[[str], dict[str, Any]]


class ReadingLlm(ScriptedLlm):
    """Answers each extraction from the prompt it receives, as the model does: the short ids of turns,
    candidates and objects are only known once the prompt is built."""

    def __init__(self, *readers: Reader) -> None:
        super().__init__()
        self.readers = list(readers)

    async def complete_json(self, **kwargs: Any) -> LlmResult:
        # The reader already names the prompt's own ids: encoded as they are, one session per call.
        blocks = session_turns(kwargs["user"])
        assert len(blocks) == 1, "a reader answers one session"
        self.answers = [json.dumps(wire_answer([self.readers.pop(0)(kwargs["user"])], []))]
        return await super().complete_json(**kwargs)


def alias(prompt: str, needle: str) -> str:
    """The short id of the prompt line that mentions `needle`."""
    for line in prompt.splitlines():
        if needle in line and (found := re.match(r"\[([efio]\d+)", line)):
            return found.group(1)
    raise AssertionError(f"{needle!r} is not in the prompt")


def episode(summary: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "intent": "complaint",
        "category": "technical_visit",
        "outcome": "pending",
        "resolution": None,
        "sentiment": -0.3,
    }


def first_contact(prompt: str) -> dict[str, Any]:
    """A missed visit: a contact fact (V1), a billing fact (V2) and the company's promise on the visit."""
    customer, visit = alias(prompt, "veio em dobro"), alias(prompt, "technical_visit field 7781")

    def fact(predicate: str, value: str) -> dict[str, Any]:
        return {
            "op": "add",
            "predicate": predicate,
            "value": value,
            "target": None,
            "evidence": [customer],
            "valid_at": None,
            "certainty": 0.9,
            "contradicts": [],
            "about": None,
            "object": None,
        }

    return {
        "episode": episode("A visita técnica 7781 não aconteceu; contestou a fatura de agosto"),
        "facts": [fact("preferred_name", "Bia"), fact("disputed_invoice", "fatura de agosto em dobro")],
        "open_items": [
            {
                "description": "Remarcar a visita técnica 7781",
                "owner": "company",
                "due_at": None,
                "evidence": [alias(prompt, "Vou remarcar")],
                "object": visit,
                "expected_operation": None,
                "about": None,
            }
        ],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


def follow_up(prompt: str) -> dict[str, Any]:
    """What the model reads in the second conversation: a question about the visit the pack carried,
    a statement about it, and a statement that contradicts the billing fact the pack left out."""
    visit = alias(prompt, "technical_visit field 7781")
    return {
        "episode": episode("Voltou a perguntar da visita 7781"),
        "facts": [],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [
            {
                "type": "asks_data",
                "predicate": None,
                "object": visit,
                "event": alias(prompt, "Qual é o endereço"),
            }
        ],
        "agent_claims": [
            {
                "predicate": None,
                "value": "a visita está confirmada para hoje",
                "object": visit,
                "contradicts": [],
                "event": alias(prompt, "está confirmada"),
            },
            {
                "predicate": "disputed_invoice",
                "value": "nenhuma contestação de fatura",
                "object": None,
                "contradicts": [alias(prompt, "disputed_invoice =")],
                "event": alias(prompt, "Não vejo nenhuma contestação"),
            },
        ],
    }


def said(conversation: str, n: int, role: str, text: str, at: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": f"{conversation}-{n}",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": role},
        "content": {"type": "text", "text": text},
        "occurred_at": at,
        **extra,
    }


def ended(conversation: str, at: str) -> dict[str, Any]:
    return {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": at,
    }


def now(flow: Flow) -> str:
    return flow.cell.clock.now().isoformat()


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ReadingLlm(first_contact, follow_up))
    missed = said(
        "wa-1", 1, "customer", "Me chama de Bia. A visita 7781 não veio e a fatura veio em dobro", now(flow)
    )
    missed["object_refs"] = [VISIT]
    cell.clock.advance(minutes=1)
    promise = said("wa-1", 2, "ai_agent", "Vou remarcar a visita técnica 7781 para amanhã, Bia", now(flow))
    assert (await flow.send(WHATSAPP_SOURCE, missed, promise, ended("wa-1", now(flow)))).errors == []
    await flow.drain()
    cell.clock.advance(hours=20)
    return flow


async def second_conversation(flow: Flow, level: Verification = Verification.V1) -> ContextResponse:
    """The customer comes back; the agent reads the context and then talks. Above V1, a code sent by
    WhatsApp proves the level in this conversation first."""
    clock = flow.cell.clock
    opening = said(
        "wa-2", 1, "customer", "Oi, é sobre a visita 7781 de novo", now(flow), verification_hint="V1"
    )
    opening["object_refs"] = [VISIT]
    items: list[dict[str, Any]] = [opening]
    if level is not Verification.V1:
        items.append(
            {
                "type": "verify",
                "idempotency_key": "wa-2-otp",
                "method": "otp_whatsapp",
                "level": level.value,
                "conversation_id": "wa-2",
                "handle": {"type": "phone_e164", "value": PHONE},
                "occurred_at": now(flow),
            }
        )
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    request = ContextRequest(subject=SUBJECT, verification=level, conversation_id="wa-2")
    response = await flow.serve.context(principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID)), request)
    turns = []
    for n, text in enumerate(
        [
            "Qual é o endereço da visita 7781, por favor?",
            "Bia, a visita 7781 está confirmada para hoje à tarde",
            "Não vejo nenhuma contestação de fatura sua no sistema",
        ],
        start=2,
    ):
        clock.advance(minutes=1)
        turns.append(said("wa-2", n, "ai_agent", text, now(flow)))
    clock.advance(minutes=1)
    assert (await flow.send(WHATSAPP_SOURCE, *turns, ended("wa-2", now(flow)))).errors == []
    await flow.serve.heartbeat()  # the receipt writer has stored everything read until now
    await flow.drain()
    return response


async def test_object_matching_and_selection_misses_count_from_the_extraction(flow: Flow) -> None:
    response = await second_conversation(flow)
    assert response.withheld >= 1  # the billing fact asks for V2: the pack left it out

    source = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    [entry] = (await flow.measure.conversation_detail(source, "wa-2")).entries
    assert entry.not_measured_reason is None
    assert entry.manifest_hash == response.manifest_hash
    # "Qual é o endereço da visita 7781?" names no predicate: only the object ties it to the pack.
    assert (entry.questions, entry.repeated) == (1, 1)
    # "a visita está confirmada" is about the visit the pack carried: use, by object.
    assert entry.used >= 1
    assert entry.contradicted == 0  # the contradicted fact was never delivered
    [miss] = [i for i in entry.items if i.get("selection_miss")]
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        facts = await uow.memory.facts_for([handle.handle_id])
    [disputed] = [f for f in facts if f.predicate == "disputed_invoice"]
    assert miss["id"] == str(disputed.fact_id)

    report = await flow.measure.context_use(source, group_by=["source_id"])
    [bucket] = report.buckets
    assert (bucket.questions, bucket.repeated, bucket.selection_misses) == (1, 1, 1)
    assert bucket.deliveries_used == 1


async def test_a_pack_compiled_on_the_read_path_is_measured(flow: Flow) -> None:
    # After the code, the pack is compiled for V2 on the read path. Its manifest used to be kept
    # nowhere, so the delivery read as `manifest_missing` and none of its signals counted.
    response = await second_conversation(flow, Verification.V2)
    assert response.verification.effective is Verification.V2
    assert response.path is DeliveryPath.T2
    async with flow.cell.uow(SPACE_ID) as uow:
        assert response.manifest_hash is not None
        assert await uow.compile.get_manifest(response.manifest_hash) is not None

    source = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    [entry] = (await flow.measure.conversation_detail(source, "wa-2")).entries
    assert entry.not_measured_reason is None
    assert (entry.questions, entry.repeated) == (1, 1)
    # At V2 the pack carried the billing fact: denying it is now the agent's contradiction.
    assert entry.contradicted == 1
    assert not any(i.get("selection_miss") for i in entry.items)
    # How long after the session's end its measurement landed, once per session measured.
    assert flow.telemetry.measurements
    assert all(seconds >= 0 for seconds in flow.telemetry.measurements)


async def test_an_agent_that_asks_again_what_it_received_trips_the_repetition_rule(flow: Flow) -> None:
    # "when an agent starts ignoring the context, a rule of the company fires a signed webhook".
    config = flow.cell.config.space(SPACE_ID)
    ops = WebhookEndpoint(
        endpoint_id="ops",
        url="https://ops.example/hooks",
        source_id=BILLING_SOURCE,
        event_types=["trigger.fired"],
        secret_refs=[f"spaces/{SPACE_ID}/webhook/ops"],
    )
    rule = TriggerRuleConfig(
        rule_id="agent_ignores_context",
        condition="context_use.repetition_rate",
        version="v1",
        params={"above": 0.5, "window_days": 7, "min_questions": 1},
        endpoint_id="ops",
    )
    flow.cell.config.put(config.model_copy(update={"webhook_endpoints": [ops], "trigger_rules": [rule]}))
    flow.cell.secrets.values[f"spaces/{SPACE_ID}/webhook/ops"] = "whsec_" + "c2VjcmV0LWtleS0xMjM0NTY3OA=="

    await second_conversation(flow)
    assert await flow.triggers.sweep() == 1
    await flow.drain()
    [(_, body, headers)] = [sent for sent in flow.cell.egress.sent if sent[0] == ops.url]
    fired = json.loads(body)
    assert (fired["type"], fired["subject"]["kind"]) == ("trigger.fired", "source")
    assert fired["data"]["condition"] == "context_use.repetition_rate"
    assert (fired["data"]["detail"]["questions"], fired["data"]["detail"]["repeated"]) == (1, 1)
    assert fired["links"]["open"] == f"/v1/context-use?source_id={WHATSAPP_SOURCE}"
    assert headers["webhook-signature"].startswith("v1,")
    assert await flow.triggers.sweep() == 0  # the same run above the threshold warns once


async def test_verdicts_on_the_signals_feed_the_agreement_beside_the_numbers(flow: Flow) -> None:
    # A reviewer judges the marks the measurement made in a real conversation; the agreement
    # per signal comes back in the review report and beside the context-use numbers.
    config = flow.cell.config.space(SPACE_ID)
    sampling = config.review_sampling.model_copy(
        update={"boosted_rate": 1.0, "boost_on": frozenset({"measure_signal"})}
    )
    flow.cell.config.put(config.model_copy(update={"review_sampling": sampling}))
    await second_conversation(flow)

    space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
    reviewer = PersonPrincipal(space, uuid7(), frozenset({"review"}))
    [sample] = [s for s in (await flow.review.queue(reviewer)).items if s.signals]
    marks = {s.signal: s.target_id for s in sample.signals}
    assert {"used", "repeated", "selection_miss"} <= marks.keys()
    verdicts = [
        VerdictIn(target=TargetKind.SIGNAL, target_id=marks["repeated"], verdict="holds"),
        VerdictIn(target=TargetKind.SIGNAL, target_id=marks["selection_miss"], verdict="does_not_hold"),
    ]
    await flow.review.record_verdicts(reviewer, sample.sample_id, VerdictsRequest(verdicts=verdicts))

    by_target = {t.target: t for t in (await flow.review.agreement(reviewer)).targets}
    assert (by_target["signal:repeated"].agreed, by_target["signal:repeated"].judged) == (1, 1)
    assert (by_target["signal:selection_miss"].agreed, by_target["signal:selection_miss"].judged) == (0, 1)
    review = {r.signal: r for r in (await flow.measure.context_use(reviewer)).review}
    assert (review["repeated"].judged, review["repeated"].confirmed) == (1, 1)
    assert review["selection_miss"].agreement is not None
    assert review["selection_miss"].agreement.value == 0.0
    assert review["contradicted"].agreement is None  # nothing judged: not measured, never zero
    # A vendor whose reviewers only see another agent reads nothing of this one.
    other = replace(reviewer, allowed_sources=frozenset({VOICE_SOURCE}))
    assert (await flow.review.agreement(other)).targets == []
    assert all(r.judged == 0 for r in (await flow.measure.context_use(other)).review)
