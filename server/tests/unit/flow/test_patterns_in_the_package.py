"""Patterns in the access and portability package: every pattern with its evidence, retracted
ones included, and the record of an objection to profiling."""

import io
import json
import zipfile
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.adapters.memory.identity import InMemoryIdentityRepository
from niadra.adapters.memory.traits import InMemoryTraitRepository
from niadra.config.models import TraitRuleConfig
from niadra.contracts.privacy import ExportRequest
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.flow.harness import Flow

PHONE = "+5511977776666"


def complaint() -> dict[str, Any]:
    return {
        "episode": {
            "summary": "Reclamou da fatura",
            "intent": "complaint",
            "category": "invoice",
            "outcome": "pending",
            "resolution": None,
            "sentiment": -0.4,
        },
        "facts": [],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


async def package(flow: Flow, space: UUID, profile_id: UUID) -> dict[str, list[dict[str, Any]]]:
    made = await flow.privacy.export_package(
        space, ExportRequest(profile_id=profile_id), requested_by="user:u"
    )
    data = await flow.privacy.download_package(space, UUID(made.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {
            name.removesuffix(".jsonl"): [json.loads(line) for line in archive.read(name).splitlines()]
            for name in archive.namelist()
        }


async def test_the_package_holds_patterns_with_evidence_retractions_and_the_objection(cell: Cell) -> None:
    rule = TraitRuleConfig(rule_id="rc", catalog="recurring_complaint", version="1", params={"min_count": 3})
    cell.config.put(sample_config().model_copy(update={"trait_rules": [rule]}))
    space = cell.config.spaces()[0].space_id
    flow = Flow.build(cell, ScriptedLlm(complaint(), complaint(), complaint()))
    for n in range(3):
        cell.clock.advance(days=1)
        now = cell.clock.now().isoformat()
        name = f"wa-{n}"
        said = {
            "type": "event",
            "idempotency_key": f"{name}-1",
            "channel": "whatsapp",
            "conversation_id": name,
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": "A fatura veio errada de novo"},
            "occurred_at": now,
        }
        ended = {"type": "conversation.ended", "idempotency_key": f"{name}-end", "conversation_id": name}
        assert (await flow.send(WHATSAPP_SOURCE, said, {**ended, "occurred_at": now})).errors == []
        await flow.drain()
    key = normalize_handle(HandleType.PHONE_E164, PHONE, "", None).key
    [handle] = await InMemoryIdentityRepository(cell.uow.store).find_handles([key])
    profile = await InMemoryIdentityRepository(cell.uow.store).profile_of(handle.handle_id)
    assert profile is not None
    profile_id = profile.profile_id

    first = await package(flow, space, profile_id)
    [trait] = first["traits"]
    assert trait["name"] == "rc"
    assert len(trait["evidence"]) == 3
    assert first["profiling_objections"] == []

    # A pattern a person retracted stays in the package, with when it was retracted.
    traits = InMemoryTraitRepository(cell.uow.store)
    await traits.retract(UUID(trait["trait_id"]), cell.clock.now())
    [retracted] = (await package(flow, space, profile_id))["traits"]
    assert retracted["retracted_at"] is not None

    await flow.traits.opt_out(space, profile_id)
    await flow.drain()
    last = await package(flow, space, profile_id)
    [objection] = last["profiling_objections"]
    assert objection["handle_id"] == str(handle.handle_id)
    assert objection["opted_out_at"] == cell.clock.now().isoformat()
