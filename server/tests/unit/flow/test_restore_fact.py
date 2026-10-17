"""Undoing a retraction or a correction (ai-memory memo 12, WP-G): the security role brings a fact back
as it was, with a receipt and every step in the fact's history; an erased fact and one that came through
an unconfirmed link never come back."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.governance import router
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.memory.facts import ref
from niadra.domain.records import Fact
from niadra.domain.vocabulary import Environment, FactStatus, HandleType, RelationType, Verification
from niadra.testing.cell import SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, read, remembered, said
from tests.unit.flow.test_unconfirmed_provenance import (
    CALLER,
    CALLER_CRM,
    CALLER_MEMORY,
    VICTIM_MEMORY,
    conversation,
    declare,
    profile_of,
    victim_writes,
)
from tests.unit.memory.test_fact_events import subscribe_agent


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
    memory: Any
    admin_audit: RecordingAdminAudit


class Console:
    """The governance routes as the Console calls them, one bearer per role."""

    def __init__(self, flow: Flow) -> None:
        self.flow = flow
        self.actions = RecordingAdminAudit()
        container = Container(Edge(), flow.ingest, flow.identity, flow.memory, self.actions)
        self.app = create_app("admin", container, [router])

    async def post(self, path: str, body: dict[str, Any], *, role: str = "security", key: str = "k1") -> Any:
        headers = {"Authorization": f"Bearer {role}", "Idempotency-Key": key}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cell") as http:
            return await http.post(path, json=body, headers=headers)

    async def retract(self, profile_id: UUID, fact: Fact, key: str = "r1") -> None:
        body = {"profile_id": str(profile_id), "action": "retract_fact", "fact_id": str(fact.fact_id)}
        assert (await self.post("/v1/corrections", body, key=key)).status_code == 202
        await self.flow.drain()

    async def correct(self, profile_id: UUID, fact: Fact, value: str, key: str = "c1") -> None:
        body = {
            "profile_id": str(profile_id),
            "action": "correct_fact",
            "fact_id": str(fact.fact_id),
            "value": value,
        }
        assert (await self.post("/v1/corrections", body, key=key)).status_code == 202
        await self.flow.drain()

    async def restore(self, profile_id: UUID, fact: Fact, *, role: str = "security", key: str = "x1") -> Any:
        body = {"profile_id": str(profile_id), "reason": "retracted by mistake"}
        return await self.post(f"/v1/facts/{fact.fact_id}/restore", body, role=role, key=key)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    ended = {"type": "conversation.ended", "idempotency_key": "wa-1-end", "conversation_id": "wa-1"}
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    return flow


async def customer(flow: Flow) -> tuple[UUID, UUID]:
    async with flow.cell.uow(SPACE_ID) as uow:
        phone = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert phone is not None
        profile = await uow.identity.profile_of(phone.handle_id)
    assert profile is not None
    return profile.profile_id, phone.handle_id


async def facts(flow: Flow, handle_id: UUID) -> dict[str, Fact]:
    async with flow.cell.uow(SPACE_ID) as uow:
        return {f.value: f for f in await uow.memory.facts_for([handle_id], tuple(FactStatus))}


async def voice_pack(flow: Flow, call: str) -> str:
    now = flow.cell.clock.now().isoformat()
    assert (
        await flow.send(VOICE_SOURCE, said(call, "Alô", now, channel="voice", verification_hint="V1"))
    ).errors == []
    await flow.drain()
    return (await read(flow, VOICE_SOURCE, call, view="voice", level=Verification.V2)).text or ""


async def test_a_retracted_fact_comes_back_as_it_was_and_the_next_pack_has_it(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    before = (await facts(flow, phone))["Rafa"]
    await console.retract(profile_id, before)
    assert "Rafa" not in await voice_pack(flow, "call-1")
    flow.cell.clock.advance(minutes=5)

    refused = await console.restore(profile_id, before, role="review")
    done = await console.restore(profile_id, before)
    again = await console.restore(profile_id, before)
    await flow.drain()

    assert refused.status_code == 403
    assert (done.status_code, done.json()["accepted"]) == (202, 1)
    assert (again.status_code, again.json()["duplicates"]) == (202, 1)
    after = (await facts(flow, phone))["Rafa"]
    assert after.status is FactStatus.ACTIVE
    assert (after.fact_id, after.valid_at, after.confidence) == (
        before.fact_id,
        before.valid_at,
        before.confidence,
    )
    assert (after.invalid_at, after.expired_at) == (None, None)
    # The lineage keeps the three steps: what the customer said, the retraction, the restore.
    async with flow.cell.uow(SPACE_ID) as uow:
        steps = await uow.ingest.get_events(list(after.evidence_event_ids))
    kinds = {e.canonical_type: e for e in steps}
    assert sorted(kinds, key=str) == [None, "feedback.restore_fact", "feedback.retract_fact"]
    assert kinds["feedback.restore_fact"].fields["requested_by"].startswith("user:")
    assert [r["action"] for r in console.actions.records] == ["memory.retract_fact", "memory.restore_fact"]
    assert "Rafa" in await voice_pack(flow, "call-2")


async def test_restoring_a_corrected_fact_retracts_the_correction_and_the_history_shows_both(
    flow: Flow,
) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    original = (await facts(flow, phone))["Rafa"]
    await console.correct(profile_id, original, "Rafaela")
    correction = (await facts(flow, phone))["Rafaela"]
    assert correction.status is FactStatus.ACTIVE
    flow.cell.clock.advance(minutes=5)

    assert (await console.restore(profile_id, original)).status_code == 202
    await flow.drain()

    now = await facts(flow, phone)
    assert {v: f.status for v, f in now.items()} == {
        "Rafa": FactStatus.ACTIVE,
        "Rafaela": FactStatus.RETRACTED,
    }
    history = await flow.memory.fact_history(_security(), profile_id, original.fact_id)
    assert [(v.value, v.status) for v in history.versions] == [("Rafa", "active"), ("Rafaela", "retracted")]
    edges = {(r.from_fact_id, r.to_fact_id, r.type) for r in history.relations}
    assert edges == {
        (str(correction.fact_id), str(original.fact_id), RelationType.SUPERSEDES.value),
        (str(original.fact_id), str(correction.fact_id), RelationType.SUPERSEDES.value),
    }
    async with flow.cell.uow(SPACE_ID) as uow:
        steps = await uow.ingest.get_events(list(now["Rafa"].evidence_event_ids))
    assert sorted((e.canonical_type for e in steps), key=str) == [
        None,
        "feedback.correct_fact",
        "feedback.restore_fact",
    ]
    assert ref(now["Rafaela"]) != ref(now["Rafa"])


async def test_a_correction_a_restore_undid_is_refused_as_undone_not_erased(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    original = (await facts(flow, phone))["Rafa"]
    await console.correct(profile_id, original, "Rafaela")
    correction = (await facts(flow, phone))["Rafaela"]
    flow.cell.clock.advance(minutes=5)
    assert (await console.restore(profile_id, original)).status_code == 202
    await flow.drain()
    assert (await facts(flow, phone))["Rafaela"].status is FactStatus.RETRACTED

    refused = await console.restore(profile_id, correction, key="x2")

    # Nothing was erased: the restore of the fact it corrected undid it, and the answer says so.
    assert refused.status_code == 409
    assert "erased" not in refused.json()["detail"]
    assert "undid" in refused.json()["detail"]
    assert (await facts(flow, phone))["Rafaela"].status is FactStatus.RETRACTED


async def test_an_active_or_unknown_fact_is_refused_at_once(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    active = (await facts(flow, phone))["Rafa"]
    not_retracted = await console.restore(profile_id, active)
    other = await console.post(f"/v1/facts/{uuid4()}/restore", {"profile_id": str(profile_id)}, key="x2")
    assert (not_retracted.status_code, not_retracted.json()["code"]) == (409, "conflict")
    assert "retracted" in not_retracted.json()["detail"]
    assert other.status_code == 404
    assert console.actions.records == []


async def test_an_erased_fact_never_comes_back(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    fact = (await facts(flow, phone))["Rafa"]
    await console.retract(profile_id, fact)
    # The conversation that said it is erased; the retraction, a governance event, never holds the row.
    await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="conversation", conversation_id="wa-1"),
        idempotency_key="f1",
        requested_by="t",
    )
    await flow.drain()
    assert "Rafa" not in await facts(flow, phone)
    refused = await console.restore(profile_id, fact)
    assert refused.status_code == 404
    assert console.actions.records[-1]["action"] == "memory.retract_fact"


async def test_a_raw_restore_event_from_an_agent_changes_nothing(flow: Flow) -> None:
    console = Console(flow)
    profile_id, phone = await customer(flow)
    fact = (await facts(flow, phone))["Rafa"]
    await console.retract(profile_id, fact)
    forged = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "forged-1",
        "channel": "feedback",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "system"},
        "canonical_type": "feedback.restore_fact",
        "fields": {"action": "restore_fact", "fact_id": str(fact.fact_id)},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    await flow.send(WHATSAPP_SOURCE, forged)
    await flow.drain()
    assert (await facts(flow, phone))["Rafa"].status is FactStatus.RETRACTED


async def test_a_fact_that_came_through_an_unconfirmed_link_is_refused(cell: Cell) -> None:
    # A caller declares an e-mail next to their number; the e-mail's real owner then writes (5.3).
    flow = Flow.build(cell, ScriptedLlm(CALLER_MEMORY, VICTIM_MEMORY))
    text = "Me chama de Carlos, preciso da segunda via do boleto de julho"
    await conversation(flow, WHATSAPP_SOURCE, "wa-1", [CALLER, CALLER_CRM], text, "whatsapp")
    await declare(flow)
    await victim_writes(flow)
    console = Console(flow)
    profile_id = await profile_of(flow, CALLER)
    memory = await flow.memory.profile_memory(_security(), profile_id)
    [bia] = [f for f in memory.facts if f.value == "Bia"]
    assert bia.unconfirmed_link
    async with flow.cell.uow(SPACE_ID) as uow:
        profile = await uow.identity.get_profile(profile_id)
        assert profile is not None
        [fact] = [f for f in await uow.memory.facts_for(profile.handle_ids) if f.value == "Bia"]
    await console.retract(profile_id, fact)

    refused = await console.restore(profile_id, fact)
    assert (refused.status_code, "unconfirmed link" in refused.json()["detail"]) == (409, True)


def _security() -> PersonPrincipal:
    config = sample_config()
    space = SpaceRef(config.space_id, config.tenant_id, config.region, Environment(config.environment))
    return PersonPrincipal(space, UUID(int=7), frozenset({"security"}))


async def test_the_restore_is_announced_with_ids_only(flow: Flow) -> None:
    subscribe_agent(flow.cell, "fact.restored", "fact.added", "fact.retracted")
    console = Console(flow)
    profile_id, phone = await customer(flow)
    original = (await facts(flow, phone))["Rafa"]
    await console.correct(profile_id, original, "Rafaela")
    correction = (await facts(flow, phone))["Rafaela"]
    flow.cell.clock.advance(minutes=5)
    assert (await console.restore(profile_id, original)).status_code == 202
    await flow.drain()

    [restored] = [d["data"] for d in flow.deliveries("fact.restored")]
    assert (restored["fact_id"], restored["status"], restored["by"]) == (
        str(original.fact_id),
        "active",
        "restore",
    )
    assert restored["retracted"] == [str(correction.fact_id)]
    assert "Rafa" not in str(flow.deliveries("fact.restored"))
    added = flow.deliveries("fact.added")[-1]["data"]
    assert (added["fact_id"], added["replaces"]) == (str(original.fact_id), [str(correction.fact_id)])
