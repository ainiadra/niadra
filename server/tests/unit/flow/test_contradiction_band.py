"""A contradiction the nightly band finds, end to end: two conversations months apart state
free-text facts of one predicate; the night draws the pair, the conversation goes to review under its own
condition, the Console's memory and the fact's history show the pair with its origin, and the pack weighs
both a fifth less. Nothing retracts on its own: when the team retracts one side, the pair closes and the
other fact weighs as before."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.compile import load_snapshot
from niadra.config.models import PredicateSpec
from niadra.contracts.common import Handle
from niadra.contracts.events import FeedbackRequest
from niadra.contracts.review import VerdictIn, VerdictsRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.records import Fact, ProfileRecord
from niadra.domain.review.sampling import CONTRADICTION_BAND
from niadra.domain.review.verdicts import TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Environment, FactStatus, HandleType
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

PHONE = "+5511955557777"
MANAGER = "prefere ser atendida pela gerente da agência"
APP = "quer resolver tudo pelo aplicativo do banco"
SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)


def stated(value: str) -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Falou de como prefere ser atendida",
            "intent": "request",
            "category": "invoice",
            "outcome": "resolved",
            "resolution": None,
            "sentiment": None,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "contact_preference",
                "value": value,
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


def conversation(name: str, at: datetime, text: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "event",
            "idempotency_key": f"{name}-1",
            "channel": "whatsapp",
            "conversation_id": name,
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": "customer"},
            "direction": "inbound",
            "content": {"type": "text", "text": text},
            "occurred_at": at.isoformat(),
        },
        {
            "type": "conversation.ended",
            "idempotency_key": f"{name}-end",
            "conversation_id": name,
            "occurred_at": (at + timedelta(seconds=30)).isoformat(),
        },
    ]


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    config = cell.config.space(SPACE_ID)
    extraction = config.extraction.model_copy(
        update={
            "predicates": [
                *config.extraction.predicates,
                PredicateSpec(name="contact_preference", category="contact"),
            ]
        }
    )
    sampling = config.review_sampling.model_copy(
        update={"base_rate": 0.0, "boosted_rate": 1.0, "boost_on": frozenset({CONTRADICTION_BAND})}
    )
    cell.config.put(config.model_copy(update={"extraction": extraction, "review_sampling": sampling}))
    flow = Flow.build(cell, ScriptedLlm(stated(MANAGER), stated(APP)))
    now = cell.clock.now()
    items = [
        *conversation("pref-1", now - timedelta(days=90), "Prefiro ser atendida pela gerente da agência"),
        *conversation("pref-2", now - timedelta(days=2), "Quero resolver tudo pelo aplicativo"),
    ]
    assert (await flow.send(WHATSAPP_SOURCE, *items)).errors == []
    await flow.drain()
    return flow


async def customer(flow: Flow) -> ProfileRecord:
    async with flow.cell.uow(SPACE_ID) as uow:
        handle = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await uow.identity.profile_of(handle.handle_id)
    assert profile is not None
    return profile


async def preferences(flow: Flow, profile: ProfileRecord) -> dict[str, Fact]:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await uow.memory.facts_for(profile.handle_ids, tuple(FactStatus))
    return {f.value: f for f in found if f.predicate == "contact_preference"}


async def contradicted(flow: Flow, profile: ProfileRecord) -> frozenset[UUID]:
    async with flow.cell.uow(SPACE_ID) as uow:
        snapshot = await load_snapshot(uow, profile, flow.cell.clock.now())
    return snapshot.band_contradicted


async def night(flow: Flow) -> tuple[ProfileRecord, Fact, Fact]:
    """The nightly consolidation, with vectors of a known similarity (0.6) in place of the encoder's."""
    profile = await customer(flow)
    found = await preferences(flow, profile)
    manager, app = found[MANAGER], found[APP]
    async with flow.cell.uow(SPACE_ID) as uow:
        await uow.memory.set_vector(PendingVector(manager), [1.0, 0.0], "encoder-test")
        await uow.memory.set_vector(PendingVector(app), [0.6, 0.8], "encoder-test")
    await flow.memory.consolidate(Task(TaskName.CONSOLIDATE, SPACE_ID, profile_id=profile.profile_id))
    await flow.drain()
    return profile, manager, app


async def test_the_band_sends_the_pair_to_review_and_its_agreement_counts_apart(flow: Flow) -> None:
    profile, manager, app = await night(flow)
    assert {f.status for f in (await preferences(flow, profile)).values()} == {FactStatus.ACTIVE}
    assert await contradicted(flow, profile) == {manager.fact_id, app.fact_id}

    reviewer = PersonPrincipal(SPACE, uuid7(), frozenset({"review"}))
    [sample] = (await flow.review.queue(reviewer)).items
    assert sample.condition == CONTRADICTION_BAND
    [pair] = sample.contradictions
    assert (pair.fact_id, pair.other_fact_id, pair.origin) == (app.fact_id, manager.fact_id, "band")
    assert (pair.value, pair.other_value) == (APP, MANAGER)
    verdict = VerdictIn(target=TargetKind.CONTRADICTION, target_id=pair.target_id, verdict="holds")
    await flow.review.record_verdicts(reviewer, sample.sample_id, VerdictsRequest(verdicts=[verdict]))
    by_target = {t.target: t for t in (await flow.review.agreement(reviewer)).targets}
    assert (by_target["contradiction:band"].agreed, by_target["contradiction:band"].judged) == (1, 1)
    assert "contradiction:model" not in by_target
    # A verdict changes nothing in memory.
    assert {f.status for f in (await preferences(flow, profile)).values()} == {FactStatus.ACTIVE}


async def test_the_console_shows_the_pair_and_a_retraction_by_the_team_closes_it(flow: Flow) -> None:
    profile, manager, app = await night(flow)
    officer = PersonPrincipal(SPACE, uuid7(), frozenset({"security"}))
    memory = await flow.memory.profile_memory(officer, profile.profile_id)
    shown = {f.id: f for f in memory.facts}
    [conflict] = shown[str(app.fact_id)].contradicts
    assert (conflict.fact_id, conflict.origin, conflict.live) == (str(manager.fact_id), "band", True)
    assert shown[str(app.fact_id)].sessions_seen == 1
    history = await flow.memory.fact_history(officer, profile.profile_id, app.fact_id)
    assert [(r.type, r.origin) for r in history.relations] == [("contradicts", "band")]

    # The team decides the older preference no longer holds: the pair closes, nothing else moves.
    retract = FeedbackRequest(
        idempotency_key="fb-1",
        subject=Handle(type=HandleType.PHONE_E164, value=PHONE),
        action="retract_fact",
        fact_id=str(manager.fact_id),
    )
    config = flow.cell.config.space(SPACE_ID)
    assert (await flow.ingest.record_feedback(principal(WHATSAPP_SOURCE, config), retract)).errors == []
    await flow.drain()
    after = await preferences(flow, profile)
    assert (after[MANAGER].status, after[APP].status) == (FactStatus.RETRACTED, FactStatus.ACTIVE)
    assert await contradicted(flow, profile) == frozenset()
    memory = await flow.memory.profile_memory(officer, profile.profile_id)
    [closed] = next(f for f in memory.facts if f.id == str(app.fact_id)).contradicts
    assert closed.live is False
    reviewer = PersonPrincipal(SPACE, uuid7(), frozenset({"review"}))
    [sample] = (await flow.review.queue(reviewer)).items
    assert sample.contradictions == []
