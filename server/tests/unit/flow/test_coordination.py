"""Coordination across the modules (24.3.4), in memory: ownership observed from a service panel's events
through a mapping, task locks on an object, commitments that become an agent's action, a handoff whose package
is compiled at the receiving side's level and whose outcome feeds memory, signals and suppressions, the
coordination block of a context read, the shadow run, and the erasure of a subject."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import fakeredis
import pytest
from pydantic import TypeAdapter, ValidationError

from niadra.config.coordination import CoordinationSettings
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import ContextRequest
from niadra.contracts.coordinate import (
    CheckRequest,
    CheckResult,
    ClaimRequest,
    DeclareRequest,
    HandoffCreate,
    HandoffOutcome,
    ShadowRequest,
    terms_text,
)
from niadra.contracts.events import EventItem
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.errors import Conflict, Forbidden, LeaseHeld, TaskLocked
from niadra.domain.common.ids import uuid7
from niadra.domain.coordinate.model import ClaimKind
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.records import OpenItem, OpenItemStatus
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import ActionStatus, Feature, HandleType, Verification
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511988887777"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
DECLARATION: TypeAdapter[DeclareRequest] = TypeAdapter(DeclareRequest)
HEARING = ObjectRef(type="hearing", namespace="court", id="4471")

COORDINATION: dict[str, Any] = {
    "ownership": {
        "sources": [
            {
                "name": "service_panel",
                "via": "mapping",
                "max_validity_s": 3600,
                "levels": ["human_active"],
                "source_id": str(BILLING_SOURCE),
                "rules": [
                    {
                        "canonical_type": "panel.session_opened",
                        "level": "human_active",
                        "holder": "human:panel",
                    },
                    {"canonical_type": "panel.session_closed"},
                ],
            },
            {"name": "middleware", "via": "worker", "max_validity_s": 7200, "levels": ["soft_pause"]},
        ]
    },
    "commitments": [
        {"name": "discount", "compatibility": "best_wins", "max_validity_days": 7},
        {"name": "trial", "compatibility": "first_holds"},
        {"name": "settlement", "compatibility": "exclusive"},
    ],
    "handoffs": {
        "outcomes": [
            {
                "name": "declined_network",
                "suppress": [{"purpose": "marketing", "reason": "opt_out"}],
                "interactions": [
                    {
                        "kind": "preference",
                        "attr": "plan.network",
                        "op": "not_in",
                        "values": ["rede-x"],
                        "strength": "must",
                        "scope": "persistent",
                    }
                ],
            },
            {"name": "sold"},
        ]
    },
    "channels": [
        {
            "name": "whatsapp",
            "free_form_hours": 24,
            "template_outside_window": True,
            "quiet_hours": {"start": "21:00", "end": "08:00"},
        }
    ],
}


def _flow(*features: Feature, reported: list[dict[str, Any]] | None = None) -> Flow:
    cell = Cell()
    document = COORDINATION | {"handoffs": COORDINATION["handoffs"] | {"reported": reported or []}}
    config = cell.config.space(SPACE_ID).model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset(features or {Feature.COORDINATION})),
            "coordination": CoordinationSettings.model_validate(document),
        }
    )
    cell.config.put(config)
    return Flow.build(cell)


async def _check(flow: Flow, agent: str, **fields: Any) -> CheckResult:
    body = {"subject": SUBJECT.model_dump(), "agent": agent, "intent": "promo", "direction": "outbound"}
    body |= {"purpose": "service", **fields}
    return await flow.coordinate.check(principal(WHATSAPP_SOURCE), CheckRequest.model_validate(body))


async def _declare(flow: Flow, key: str, **fields: Any) -> Any:
    request = DECLARATION.validate_python({"subject": SUBJECT.model_dump(), **fields})
    return await flow.coordinate.declare(principal(WHATSAPP_SOURCE), request, key)


def _panel(flow: Flow, key: str, canonical_type: str, minutes_ago: int = 0) -> dict[str, Any]:
    at = flow.cell.clock.now() - timedelta(minutes=minutes_ago)
    return {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": key,
        "channel": "panel",
        "canonical_type": canonical_type,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "system"},
        "occurred_at": at.isoformat(),
    }


# Ownership by observation.


async def test_a_service_panel_event_through_a_mapping_beats_a_soft_pause_and_its_end_gives_it_back() -> None:
    flow = _flow()
    lease = {"level": "soft_pause", "lease_s": 3600}
    await _declare(flow, "l1", kind="lease", agent="agent:sales", detail=lease)
    assert (await _check(flow, "agent:sales")).decision == "allow"
    assert (await _check(flow, "agent:retention")).reasons == ["owner_active"]

    await flow.send(BILLING_SOURCE, _panel(flow, "p1", "panel.session_opened"))
    assert [t.name for t in flow.pending(TaskName.OBSERVE_OWNERSHIP)] == [TaskName.OBSERVE_OWNERSHIP]
    await flow.drain(only={TaskName.OBSERVE_OWNERSHIP})
    held = await _check(flow, "agent:sales")
    assert (held.decision, held.reasons) == ("defer", ["owner_active"])
    assert held.owner is not None
    assert (held.owner.holder, held.owner.level, held.owner.via) == ("human:panel", "human_active", "mapping")
    answered = await _check(flow, "agent:sales", direction="inbound")
    assert (answered.decision, answered.holder) == ("handoff_to", "human:panel")
    with pytest.raises(LeaseHeld):
        await _declare(flow, "l2", kind="lease", agent="agent:sales", detail=lease)

    flow.cell.clock.advance(minutes=5)
    await flow.send(BILLING_SOURCE, _panel(flow, "p2", "panel.session_closed"))
    # An older opening delivered late never brings the human back.
    await flow.send(BILLING_SOURCE, _panel(flow, "p0", "panel.session_opened", minutes_ago=3))
    await flow.drain(only={TaskName.OBSERVE_OWNERSHIP})
    back = await _check(flow, "agent:sales")
    assert back.decision == "allow"
    assert back.owner is not None
    assert (back.owner.holder, back.owner.level) == ("agent:sales", "soft_pause")
    await flow.coordinate.write(flow.coordination_queue.records)
    claims = flow.cell.uow.store.module("coordinate")["claims"].values()
    observed = [c for c in claims if c.via == "mapping"]
    assert [(c.observer, c.holder_kind, c.released_at_ms is not None) for c in observed] == [
        ("service_panel", "human", True)
    ]


async def test_the_worker_reports_what_it_read_and_only_a_later_report_replaces_it() -> None:
    flow = _flow()
    caller = principal(WHATSAPP_SOURCE)
    now = flow.cell.clock.now()

    def paused(at_minutes: int, holder: str = "middleware:pause") -> ClaimRequest:
        return ClaimRequest(
            subject=SUBJECT, kind=ClaimKind.OWNER, holder=holder, level="soft_pause", lease_s=86_400,
            source="middleware", observed_at=now - timedelta(minutes=at_minutes),
        )  # fmt: skip

    claim = await flow.coordinate.claim(caller, paused(1), "w1")
    assert claim.valid_until == now - timedelta(minutes=1) + timedelta(hours=2)  # the source's cap
    with pytest.raises(Conflict):
        await flow.coordinate.claim(caller, paused(5, "middleware:older"), "w2")
    assert (await _check(flow, "agent:sales")).reasons == ["owner_active"]


# Task locks.


async def test_a_task_lock_holds_its_task_and_only_its_task_on_the_object() -> None:
    flow = _flow()
    caller = principal(WHATSAPP_SOURCE)

    def lock(holder: str) -> ClaimRequest:
        return ClaimRequest(
            object=HEARING, kind=ClaimKind.TASK_LOCK, holder=holder, task="summary", lease_s=900
        )

    held = await flow.coordinate.claim(caller, lock("agent:drafts"), "k1")
    with pytest.raises(TaskLocked):
        await flow.coordinate.claim(caller, lock("human:lawyer"), "k2")
    body = {"object": HEARING.model_dump(), "intent": "draft", "task": "summary"}
    blocked = await _check(flow, "human:lawyer", subject=None, **body)
    assert (blocked.decision, blocked.reasons) == ("defer", ["lock_held"])
    assert [(lk.task, lk.holder) for lk in blocked.locks] == [("summary", "agent:drafts")]
    other_task = await _check(flow, "human:lawyer", subject=None, **{**body, "task": "filing"})
    assert other_task.decision == "allow"
    await flow.coordinate.release(caller, held.claim_id, held.epoch, "k3")
    assert (await flow.coordinate.claim(caller, lock("human:lawyer"), "k4")).holder == "human:lawyer"


# Commitments.


async def test_commitments_hold_by_their_compatibility_and_become_the_agents_action() -> None:
    flow = _flow()

    async def offer(key: str, kind: str, score: float | None = None, agent: str = "agent:sales") -> bool:
        detail = {"commitment_id": key, "type": kind, "terms": {"discount_pct": 10}, "score": score}
        result = await _declare(flow, key, kind="commitment.made", agent=agent, detail=detail)
        return bool(result.accepted)

    assert await offer("c1", "discount", 10)
    assert not await offer("c2", "discount", 5, "agent:retention")
    assert await offer("c3", "discount", 20, "agent:retention")
    assert await offer("t1", "trial")
    assert not await offer("t2", "trial")
    held = await _check(flow, "agent:other")
    assert sorted(c.id for c in held.commitments_active) == ["c3", "t1"]
    assert not await offer("s1", "settlement")  # an exclusive one holds alone
    decided = {"commitment_id": "c3", "outcome": "accepted"}
    await _declare(flow, "d1", kind="commitment.decided", agent="agent:retention", detail=decided)
    assert [c.id for c in (await _check(flow, "agent:other")).commitments_active] == ["t1"]
    await flow.coordinate.write(flow.coordination_queue.records)
    stored = flow.cell.uow.store.module("coordinate")["commitments"].values()
    assert sorted((m.external_id, m.status) for m in stored) == [
        ("c1", "superseded"),
        ("c2", "superseded"),
        ("c3", "accepted"),
        ("s1", "superseded"),
        ("t1", "active"),
        ("t2", "superseded"),
    ]
    async with flow.cell.uow(SPACE_ID) as uow:
        events = await uow.ingest.recent_events(flow.cell.clock.now() - timedelta(hours=1), 50)
    actions = [e.action["operation"] for e in events if e.action]
    assert actions.count("commitment.discount") == 3


async def test_a_commitments_terms_reach_its_action_whole() -> None:
    # Memory keeps what the agent committed to: terms used to be cut at 2,000 characters on their way to
    # the action record.
    flow = _flow()
    terms = {"conditions": "parcelas sem juros " * 300, "discount_pct": 10}
    detail = {"commitment_id": "c-long", "type": "discount", "terms": terms}
    assert (
        await _declare(flow, "c-long", kind="commitment.made", agent="agent:sales", detail=detail)
    ).accepted
    async with flow.cell.uow(SPACE_ID) as uow:
        events = await uow.ingest.recent_events(flow.cell.clock.now() - timedelta(hours=1), 50)
    [result] = [
        e.action["result"] for e in events if e.action and e.action["operation"] == "commitment.discount"
    ]
    assert result == terms_text(terms)
    assert len(result) > 5_000


def test_terms_or_an_agent_id_past_their_size_are_refused_never_cut() -> None:
    too_long = {"commitment_id": "c-x", "type": "discount", "terms": {"conditions": "x" * 200_001}}
    with pytest.raises(ValidationError):
        DECLARATION.validate_python(
            {"subject": SUBJECT.model_dump(), "kind": "commitment.made", "agent": "a", "detail": too_long}
        )
    with pytest.raises(ValidationError):
        DECLARATION.validate_python(
            {"subject": SUBJECT.model_dump(), "kind": "commitment.made", "agent": "a" * 257,
             "detail": {"commitment_id": "c-y", "type": "discount"}}
        )  # fmt: skip


# Handoffs.


async def test_a_handoff_carries_its_package_and_its_outcome_feeds_memory_signals_and_suppressions() -> None:
    flow = _flow(Feature.COORDINATION, Feature.SIGNALS)
    caller = principal(WHATSAPP_SOURCE)
    commitment = {"commitment_id": "c1", "type": "trial", "terms": {"days": 7}}
    await _declare(flow, "c1", kind="commitment.made", agent="agent:sales", detail=commitment)
    request = HandoffCreate(
        subject=SUBJECT, agent="agent:sales", target="queue:closing", reason="quer fechar hoje"
    )
    created = await flow.coordinate.create_handoff(caller, request, "h1")
    assert created.status == "created"
    package = created.package
    assert package is not None
    assert package.subject == SUBJECT
    assert (package.from_agent, package.target, package.level) == ("agent:sales", "queue:closing", "V0")
    assert package.context.text.startswith(
        "Transbordo de agent:sales para queue:closing.\nMotivo: quer fechar hoje."
    )
    assert [c.id for c in package.commitments_active] == ["c1"]
    assert package.outcome.vocabulary == ["declined_network", "sold"]
    assert package.outcome.expected_by == flow.cell.clock.now() + timedelta(hours=24)
    # While the outcome is due, the subject waits for the receiving side.
    waiting = await _check(flow, "agent:retention")
    assert (waiting.decision, waiting.owner.holder if waiting.owner else None) == ("defer", "queue:closing")
    assert (await flow.coordinate.create_handoff(caller, request, "h1")).handoff_id == created.handoff_id

    accepted = await flow.coordinate.accept_handoff(caller, created.handoff_id, "a1")
    assert accepted.status == "accepted"
    outcome = HandoffOutcome(outcome="declined_network", detail={"note": "rede x"})
    closed = await flow.coordinate.handoff_outcome(caller, created.handoff_id, outcome, "o1")
    assert (closed.status, closed.outcome) == ("closed", "declined_network")
    with pytest.raises(Conflict):
        await flow.coordinate.handoff_outcome(
            caller, created.handoff_id, HandoffOutcome(outcome="sold"), "o2"
        )

    marketing = await _check(flow, "agent:retention", purpose="marketing")
    assert (marketing.decision, marketing.reasons) == ("deny", ["suppressed"])
    assert (await _check(flow, "agent:retention")).decision == "allow"  # the hold ended with the outcome
    await flow.drain(only={TaskName.APPLY_INTERACTIONS, TaskName.APPLY_INTAKE})
    await flow.prove(VOICE_SOURCE, "next", PHONE)
    read = ContextRequest(
        subject=SUBJECT, conversation_id="next", verification=Verification.V1, include=["constraints"]
    )
    constraints = (await flow.serve.context(principal(VOICE_SOURCE), read)).constraints
    assert constraints is not None
    assert [(h.attr, h.op, h.values) for h in constraints.hard] == [("plan.network", "not_in", ["rede-x"])]
    async with flow.cell.uow(SPACE_ID) as uow:
        events = await uow.ingest.recent_events(flow.cell.clock.now() - timedelta(hours=1), 50)
    outcomes = [e for e in events if e.action and e.action["operation"] == "handoff.outcome"]
    assert [(e.speaker.value, e.speaker_id, e.action["result"]) for e in outcomes if e.action] == [
        ("human_agent", "queue:closing", "queue:closing: declined_network")
    ]
    # The receiving side's action is in the subject's memory, where the next agent reads it once the customer
    # proved who they are.
    hello = _message(flow, "m9", "customer", "Oi de novo", "c9") | {"verification_hint": "V1"}
    await flow.send(WHATSAPP_SOURCE, hello)
    await flow.drain()
    read = ContextRequest(subject=SUBJECT, conversation_id="c9", verification="V1")
    text = (await flow.serve.context(principal(WHATSAPP_SOURCE), read)).text or ""
    assert "[Outro agente] queue:closing: declined_network" in text, text
    read = await flow.coordinate.read_handoff(caller, created.handoff_id)
    assert (read.status, read.package) == ("closed", package)


async def test_a_commitment_and_a_handoffs_outcome_never_turn_divergent() -> None:
    # What an agent promised and what the receiving side decided are records, not effects on a system of
    # record: no system event confirms them, so past the window they still read as done by someone else, while
    # a credit the ERP never confirmed turns divergent (18.4).
    flow = _flow()
    caller = principal(WHATSAPP_SOURCE)
    commitment = {"commitment_id": "c1", "type": "trial", "terms": {"days": 7}}
    await _declare(flow, "c1", kind="commitment.made", agent="agent:sales", detail=commitment)
    request = HandoffCreate(subject=SUBJECT, agent="agent:sales", target="queue:closing")
    created = await flow.coordinate.create_handoff(caller, request, "h1")
    await flow.coordinate.handoff_outcome(caller, created.handoff_id, HandoffOutcome(outcome="sold"), "o1")
    credit = {
        "type": "event",
        "kind": "action",
        "idempotency_key": "credit-0823",
        "channel": "erp",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": "invoice", "namespace": "erp", "id": "0823"}],
        "speaker": {"role": "ai_agent", "id": "billing-agent"},
        "action": {"operation": "credit", "result": "credito de R$ 40"},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    await flow.send(BILLING_SOURCE, credit)
    await flow.drain()

    flow.cell.clock.advance(hours=7)
    assert await flow.objects.expire_declared(flow.cell.clock.now()) == 1
    async with flow.cell.uow(SPACE_ID) as uow:
        actions = {
            a.operation: a.status
            for obj in await uow.objects.objects_for([await _subject_handle(flow)])
            for a in await uow.objects.actions_of_object(obj.object_id)
        }
    assert actions == {
        "commitment.trial": ActionStatus.DECLARED,
        "handoff.outcome": ActionStatus.DECLARED,
        "credit": ActionStatus.DIVERGENT,
    }
    hello = _message(flow, "m9", "customer", "Oi de novo", "c9") | {"verification_hint": "V1"}
    await flow.send(WHATSAPP_SOURCE, hello)
    await flow.drain()
    read = ContextRequest(subject=SUBJECT, conversation_id="c9", verification="V1")
    text = (await flow.serve.context(principal(VOICE_SOURCE), read)).text or ""
    decided = [line for line in text.splitlines() if "days: 7" in line or "queue:closing: sold" in line]
    [credited] = [line for line in text.splitlines() if "credito de R$ 40" in line]
    assert len(decided) == 2, text
    assert not any("diverge" in line for line in decided), text
    assert "diverge do sistema" in credited, text


async def test_the_receiving_systems_event_reports_the_outcome_and_the_package_names_open_promises() -> None:
    reported = {"source_id": str(VOICE_SOURCE), "canonical_type": "ticket.solved", "outcome": "sold"}
    flow = _flow(Feature.COORDINATION, reported=[reported])
    caller = principal(WHATSAPP_SOURCE)
    await flow.send(WHATSAPP_SOURCE, _message(flow, "m1", "customer", "Quero fechar o plano"))
    await flow.drain()
    due = flow.cell.clock.now() + timedelta(days=2)
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        promise = OpenItem(
            uuid7(), handle.handle_id, "Enviar a proposta", "agent:sales", OpenItemStatus.OPEN,
            flow.cell.clock.now(), {"source_id": str(WHATSAPP_SOURCE)}, due_at=due,
        )  # fmt: skip
        await uow.memory.insert_open_item(promise)
    request = HandoffCreate(subject=SUBJECT, agent="agent:sales", target="queue:closing")
    created = await flow.coordinate.create_handoff(caller, request, "h1")
    assert created.package is not None
    promises = [(p.id, p.due, p.owner) for p in created.package.promises_open]
    assert promises == [(str(promise.item_id), due, "agent:sales")]
    assert "Enviar a proposta" not in created.package.model_dump_json()

    ticket = _panel(flow, "t1", "ticket.solved") | {"fields": {"handoff_id": created.handoff_id}}
    await flow.send(VOICE_SOURCE, ticket)
    await flow.drain(only={TaskName.OBSERVE_OWNERSHIP})
    closed = await flow.coordinate.read_handoff(caller, created.handoff_id)
    assert (closed.status, closed.outcome) == ("closed", "sold")
    # The same report again, or one naming no handoff, changes nothing.
    await flow.send(VOICE_SOURCE, _panel(flow, "t2", "ticket.solved"))
    await flow.send(VOICE_SOURCE, ticket | {"idempotency_key": "t3"})
    await flow.drain(only={TaskName.OBSERVE_OWNERSHIP})
    assert (await flow.coordinate.read_handoff(caller, created.handoff_id)).outcome == "sold"


async def test_system_events_take_only_what_coordination_owns() -> None:
    flow = _flow()
    caller = principal(WHATSAPP_SOURCE)

    def event(**fields: Any) -> EventItem:
        base = {
            "idempotency_key": f"e-{len(fields)}",
            "channel": "coordination",
            "handles": [SUBJECT.model_dump()],
            "speaker": {"role": "system"},
            "occurred_at": flow.cell.clock.now().isoformat(),
        }
        return EventItem.model_validate({**base, **fields})

    with pytest.raises(Forbidden):
        await flow.ingest.record_coordination_events(
            caller, [event(kind="system_event", canonical_type="handoff.outcome")]
        )
    with pytest.raises(Forbidden):
        await flow.ingest.record_coordination_events(
            caller, [event(kind="action", speaker={"role": "ai_agent"}, action={"operation": "credit"})]
        )
    outcome = {"operation": "handoff.outcome", "result": "sold"}
    await flow.ingest.record_coordination_events(
        caller, [event(kind="action", speaker={"role": "human_agent"}, action=outcome)]
    )


# The coordination block, the shadow run and erasure.


async def test_a_context_read_carries_the_coordination_block_with_no_decision_in_it() -> None:
    flow = _flow()
    await flow.send(WHATSAPP_SOURCE, _message(flow, "m1", "customer", "Oi, quero saber do plano"))
    await flow.drain(windows=False, only={TaskName.APPLY_INTAKE})
    await _declare(
        flow,
        "s1",
        kind="suppression.added",
        agent="agent:sales",
        detail={"purpose": "marketing", "reason": "opt_out"},
    )
    detail = {"commitment_id": "t1", "type": "trial", "terms": {}}
    await _declare(flow, "t1", kind="commitment.made", agent="agent:sales", detail=detail)
    read = ContextRequest(subject=SUBJECT, include=["coordination"])
    answer = await flow.serve.context(principal(VOICE_SOURCE), read)
    block = answer.coordination
    assert block is not None
    assert (block.suppressions, [c.id for c in block.commitments_active]) == (["marketing"], ["t1"])


async def test_the_shadow_run_counts_what_coordination_would_have_changed() -> None:
    flow = _flow()
    clock = flow.cell.clock
    clock.at = clock.now().replace(hour=15)
    await flow.send(
        WHATSAPP_SOURCE, _message(flow, "m1", "ai_agent", "Oferta nova!", "c1", speaker_id="agent:a")
    )
    clock.advance(hours=1)
    await flow.send(
        WHATSAPP_SOURCE, _message(flow, "m2", "ai_agent", "Outra oferta!", "c2", speaker_id="agent:b")
    )
    clock.advance(hours=1)
    run = await flow.coordinate.request_shadow(principal(WHATSAPP_SOURCE), ShadowRequest(days=7))
    await flow.drain(only={TaskName.COORDINATION_SHADOW})
    page = await flow.coordinate.reports(principal(WHATSAPP_SOURCE), None, 10)
    [report] = page.items
    assert (report.report_id, report.status) == (run.report_id, "done")
    conflicts = report.summary["conflicts"]
    assert conflicts["concurrent_agents"] == 1
    assert conflicts["outside_window"] == 2  # the customer never wrote: a template for each
    assert report.summary["customers"] == 1


async def test_forgetting_a_customer_erases_what_coordination_kept_but_the_suppressions() -> None:
    flow = _flow()
    await flow.send(WHATSAPP_SOURCE, _message(flow, "m1", "customer", "Oi"))
    await flow.drain(windows=False, only={TaskName.APPLY_INTAKE})
    await _declare(flow, "l1", kind="lease", agent="agent:sales", detail={"lease_s": 600})
    await _declare(
        flow,
        "s1",
        kind="suppression.added",
        agent="agent:sales",
        detail={"purpose": "marketing", "reason": "opt_out"},
    )
    detail = {"commitment_id": "t1", "type": "trial", "terms": {}}
    await _declare(flow, "t1", kind="commitment.made", agent="agent:sales", detail=detail)
    await flow.coordinate.write(flow.coordination_queue.records)
    state = flow.cell.uow.store.module("coordinate")
    assert (len(state["claims"]), len(state["commitments"]), len(state["suppressions"])) == (1, 1, 1)
    accepted = await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="handle", handle=SUBJECT),
        idempotency_key="f1",
        requested_by="user:dpo",
    )
    await flow.drain()
    out = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert out.status == ErasureStatus.COMPLETED.value
    assert (len(state["claims"]), len(state["commitments"]), len(state["suppressions"])) == (0, 0, 1)
    # The state store forgot the subject too, but that it may not be contacted.
    lease = {"level": "human_active", "lease_s": 600}
    await _declare(flow, "l2", kind="lease", agent="agent:other", detail=lease)
    marketing = await _check(flow, "agent:third", purpose="marketing")
    assert (marketing.decision, marketing.reasons) == ("deny", ["suppressed"])
    assert marketing.commitments_active == []


async def test_an_erasure_while_records_wait_in_the_writers_queue_never_brings_them_back() -> None:
    flow = _flow()
    await flow.send(WHATSAPP_SOURCE, _message(flow, "m1", "customer", "Oi"))
    await flow.drain(windows=False, only={TaskName.APPLY_INTAKE})
    await _declare(flow, "l1", kind="lease", agent="agent:sales", detail={"lease_s": 600})
    detail = {"commitment_id": "t1", "type": "trial", "terms": {}}
    await _declare(flow, "t1", kind="commitment.made", agent="agent:sales", detail=detail)
    await _declare(
        flow,
        "s1",
        kind="suppression.added",
        agent="agent:sales",
        detail={"purpose": "marketing", "reason": "opt_out"},
    )
    queued = list(flow.coordination_queue.records)
    await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="handle", handle=SUBJECT),
        idempotency_key="f1",
        requested_by="user:dpo",
    )
    await flow.drain()
    # The writer flushes what waited in its queue during the erasure: the suppression lands, nothing else.
    await flow.coordinate.write(queued)
    state = flow.cell.uow.store.module("coordinate")
    assert (len(state["claims"]), len(state["commitments"]), len(state["suppressions"])) == (0, 0, 1)
    # The state store restarts empty and reloads from the durable record: the subject has no owner again.
    fakeredis.FakeRedis(server=flow.coordination_server).flushall()
    assert await flow.coordinate.watch_state_store()
    check = await _check(flow, "agent:third")
    assert (check.owner, check.commitments_active) == (None, [])
    # What the customer does after the erasure is recorded as usual.
    flow.cell.clock.advance(seconds=1)
    await _declare(flow, "l2", kind="lease", agent="agent:sales", detail={"lease_s": 600})
    await flow.coordinate.write(flow.coordination_queue.records[len(queued) :])
    assert len(state["claims"]) == 1


def _message(
    flow: Flow, key: str, role: str, text: str, conversation: str = "c1", speaker_id: str | None = None
) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": key,
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": role, **({"id": speaker_id} if speaker_id else {})},
        "content": {"type": "text", "text": text},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }


async def _subject_handle(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
    assert handle is not None
    return handle.handle_id
