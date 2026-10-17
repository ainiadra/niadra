"""Correction as a data subject's right, by a person with the privacy role: the same event an
agent's feedback makes, from the space's governance source, applied by the memory module."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.governance import router
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.domain.common.space import PersonPrincipal, SpaceRef, governance_source
from niadra.domain.vocabulary import Environment, FactStatus, HandleType
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, remembered, said

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class Edge:
    async def authenticate_key(self, token: str) -> Any:
        raise AssertionError("people only")

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space, uuid4(), frozenset({token}))


@dataclass
class Container:
    edge: Edge
    ingest: Any
    identity: Any
    admin_audit: RecordingAdminAudit


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    ended = {"type": "conversation.ended", "idempotency_key": "wa-1-end", "conversation_id": "wa-1"}
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    return flow


async def test_a_privacy_officer_corrects_a_fact_and_the_lineage_keeps_who(flow: Flow) -> None:
    actions = RecordingAdminAudit()
    app = create_app("admin", Container(Edge(), flow.ingest, flow.identity, actions), [router])
    async with flow.cell.uow(SPACE_ID) as uow:
        phone = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert phone is not None
        profile = await uow.identity.profile_of(phone.handle_id)
        [fact] = await uow.memory.facts_for([phone.handle_id])
    assert profile is not None
    body = {
        "profile_id": str(profile.profile_id),
        "action": "correct_fact",
        "fact_id": str(fact.fact_id),
        "value": "Rafaela",
        "reason": "o titular pediu",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        refused = await http.post(
            "/v1/corrections", json=body, headers={"Authorization": "Bearer review", "Idempotency-Key": "c1"}
        )
        headers = {"Authorization": "Bearer security", "Idempotency-Key": "c1"}
        done = await http.post("/v1/corrections", json=body, headers=headers)
        again = await http.post("/v1/corrections", json=body, headers=headers)

    assert refused.status_code == 403
    assert (done.status_code, done.json()["accepted"]) == (202, 1)
    assert (again.json()["accepted"], again.json()["duplicates"]) == (0, 1)
    await flow.drain()
    async with flow.cell.uow(SPACE_ID) as uow:
        facts = {
            (f.value, f.status) for f in await uow.memory.facts_for([phone.handle_id], tuple(FactStatus))
        }
        recent = await uow.ingest.events_for_handles([phone.handle_id], EPOCH, 50)
    events = [e for e in recent if e.channel == "feedback"]
    assert facts == {("Rafa", FactStatus.RETRACTED), ("Rafaela", FactStatus.ACTIVE)}
    [event] = events
    assert event.source_id == governance_source(SPACE_ID)
    assert event.fields["requested_by"].startswith("user:")
    assert [r["action"] for r in actions.records] == ["memory.correct_fact"]


async def test_a_batch_of_corrections_applies_each_and_lists_what_it_could_not(flow: Flow) -> None:
    actions = RecordingAdminAudit()
    app = create_app("admin", Container(Edge(), flow.ingest, flow.identity, actions), [router])
    async with flow.cell.uow(SPACE_ID) as uow:
        phone = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert phone is not None
        profile = await uow.identity.profile_of(phone.handle_id)
        [fact] = await uow.memory.facts_for([phone.handle_id])
    assert profile is not None
    good = {"profile_id": str(profile.profile_id), "action": "retract_fact", "fact_id": str(fact.fact_id)}
    unknown = {**good, "profile_id": str(uuid4())}
    body = {"items": [unknown, good]}
    headers = {"Authorization": "Bearer security", "Idempotency-Key": "b1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        done = await http.post("/v1/corrections/batch", json=body, headers=headers)
        again = await http.post("/v1/corrections/batch", json=body, headers=headers)
        too_many = await http.post("/v1/corrections/batch", json={"items": [good] * 101}, headers=headers)

    assert done.status_code == 202
    assert done.json()["accepted"] == 1
    assert [(e["index"], e["code"]) for e in done.json()["errors"]] == [(0, "not_found")]
    assert (again.json()["accepted"], again.json()["duplicates"]) == (0, 1)
    assert too_many.status_code == 422
    await flow.drain()
    async with flow.cell.uow(SPACE_ID) as uow:
        [retracted] = await uow.memory.facts_for([phone.handle_id], tuple(FactStatus))
    assert retracted.status is FactStatus.RETRACTED
    assert [r["action"] for r in actions.records] == ["memory.retract_fact"]
