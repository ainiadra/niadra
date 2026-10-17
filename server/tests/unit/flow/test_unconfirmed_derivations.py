"""What a profile keeps as a whole is derived from its confirmed rows only (5.3), end to end.

The attack of `test_unconfirmed_provenance`: a caller whose number is known declares an e-mail the space
has never seen, the e-mail joins the caller's profile provisionally, and the e-mail's real owner then
writes from it. Her conversation is kept apart from every read; here it must also stay out of what the
profile derives: a pattern, a warning and its webhook, the stored tail summaries and an agent note
distilled from her conversation. A confirmation derives everything again with her rows; a split takes them
away with the e-mail.
"""

from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.compile import load_snapshot
from niadra.config.models import TraitRuleConfig, TriggerRuleConfig, WebhookEndpoint
from niadra.contracts.agent_memory import DistillRequest
from niadra.domain.compile import Variant
from niadra.domain.identity.provenance import reader_view
from niadra.domain.records import Trait
from niadra.domain.tasks import Task, TaskName
from niadra.domain.triggers.firing import Firing
from niadra.domain.vocabulary import AssertionMethod
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_unconfirmed_provenance import (
    CALLER,
    CALLER_CRM,
    CALLER_MEMORY,
    VICTIM_EMAIL,
    VICTIM_MEMORY,
    conversation,
    declare,
    handle,
    now,
    profile_of,
    victim_writes,
)

PATTERN = TraitRuleConfig(rule_id="rc", catalog="recurring_complaint", version="1", params={"min_count": 2})
RECONTACT = TriggerRuleConfig(
    rule_id="came_back", condition="recontact", version="v1", endpoint_id="crm", params={"hours": 72}
)
CRM_REF = f"spaces/{SPACE_ID}/webhook/crm"
CRM = WebhookEndpoint(
    endpoint_id="crm",
    url="https://crm.example/hooks",
    source_id=BILLING_SOURCE,
    event_types=["*"],
    secret_refs=[CRM_REF],
)
NOTE = {
    "kind": "procedure",
    "title": "Ask for the invoice month first",
    "body": "Ask which month the invoice is from before opening a dispute.",
    "tags": ["invoice"],
}


def configured(cell: Cell) -> None:
    cell.secrets.values[CRM_REF] = "whsec_" + "c2VjcmV0LWtleS1mb3ItdGVzdHM="
    cell.config.put(
        sample_config().model_copy(
            update={"trait_rules": [PATTERN], "trigger_rules": [RECONTACT], "webhook_endpoints": [CRM]}
        )
    )


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    """The caller complained once from their own number; the declared e-mail's owner complains next, about
    the same kind of invoice, two hours later: together, a recurring complaint and a recontact."""
    configured(cell)
    flow = Flow.build(cell, ScriptedLlm(CALLER_MEMORY, VICTIM_MEMORY, NOTE, NOTE))
    await conversation(
        flow,
        WHATSAPP_SOURCE,
        "wa-1",
        [CALLER, CALLER_CRM],
        "Me chama de Carlos, preciso da segunda via do boleto de julho",
        "whatsapp",
    )
    await declare(flow)
    await victim_writes(flow)
    return flow


async def caller_profile(flow: Flow) -> UUID:
    return await profile_of(flow, CALLER)


async def patterns(flow: Flow, profile_id: UUID) -> list[Trait]:
    async with flow.cell.uow(SPACE_ID) as uow:
        return [t for t in await uow.traits.traits_of(profile_id) if t.retracted_at is None]


async def firings(flow: Flow) -> list[Firing]:
    async with flow.cell.uow(SPACE_ID) as uow:
        return [f for f in await uow.triggers.list_firings(limit=100) if f.retracted_at is None]


async def summaries(flow: Flow, profile_id: UUID) -> str:
    async with flow.cell.uow(SPACE_ID) as uow:
        return " ".join(
            [await uow.memory.tail_summary(profile_id, level) or "" for level in ("short", "long")]
        )


async def consolidate(flow: Flow, profile_id: UUID) -> None:
    await flow.memory.consolidate(Task(TaskName.CONSOLIDATE, SPACE_ID, profile_id=profile_id))
    await flow.drain()


async def confirm(flow: Flow) -> None:
    """The CRM says the e-mail is on the caller's record: the link is confirmed."""
    item = {
        "type": "identify",
        "idempotency_key": "crm-confirm",
        "handles": [VICTIM_EMAIL, CALLER_CRM],
        "method": "system_import",
        "occurred_at": now(flow),
    }
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain()


async def split(flow: Flow) -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        [claim] = [
            a
            for a in await uow.identity.list_assertions(None, None, None, 100)
            if a.method is AssertionMethod.DECLARED
        ]
    await flow.identity.retract(SPACE_ID, claim.assertion_id, actor="user:ops", reason="not the same person")
    await flow.drain()


async def victim_episode_id(flow: Flow) -> UUID:
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert found is not None
        [episode] = await uow.memory.episodes_for([found.handle_id])
    return episode.episode_id


# 1. Patterns.


async def test_a_pattern_never_counts_what_came_through_the_declared_e_mail(flow: Flow) -> None:
    profile_id = await caller_profile(flow)
    # One complaint of the caller's own: the rule asks for two, and hers must not be the second.
    assert await patterns(flow, profile_id) == []
    await confirm(flow)
    [pattern] = await patterns(flow, profile_id)
    assert pattern.name == "rc"
    assert ("episode", await victim_episode_id(flow)) in pattern.evidence


async def test_a_split_leaves_no_pattern_on_either_side(flow: Flow) -> None:
    await confirm(flow)
    profile_id = await caller_profile(flow)
    assert await patterns(flow, profile_id) != []
    # A person decides the link was wrong after all: her complaint leaves with the e-mail.
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert found is not None
    await flow.identity.unmerge(
        SPACE_ID, profile_id, handle_ids=[found.handle_id], actor="user:ops", reason="not the same person"
    )
    await flow.drain()
    assert await caller_profile(flow) != await profile_of(flow, VICTIM_EMAIL)
    assert await patterns(flow, await caller_profile(flow)) == []
    assert await patterns(flow, await profile_of(flow, VICTIM_EMAIL)) == []


# 2. Warnings and their webhooks.


async def test_a_warning_never_fires_on_what_came_through_the_declared_e_mail(flow: Flow) -> None:
    profile_id = await caller_profile(flow)
    await flow.triggers.evaluate(Task(TaskName.TRIGGER, SPACE_ID, {}, profile_id=profile_id))
    await flow.drain()
    # Her complaint two hours after his would be a recontact; the webhook would carry her episode's id.
    assert await firings(flow) == []
    assert flow.deliveries("trigger.fired") == []
    await confirm(flow)
    [fired] = await firings(flow)
    assert fired.rule_id == "came_back"
    assert await victim_episode_id(flow) in {e.id for e in fired.evidence}


# 3. The stored tail summaries.


async def test_the_stored_summaries_cover_the_confirmed_episodes_only(flow: Flow) -> None:
    profile_id = await caller_profile(flow)
    await consolidate(flow, profile_id)
    stored = await summaries(flow, profile_id)
    assert "boleto" in stored
    assert "81220" not in stored
    # Her own view rebuilds them with her episode; the confirmed view never holds it.
    async with flow.cell.uow(SPACE_ID) as uow:
        profile = await uow.identity.get_profile(profile_id)
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert profile is not None
        assert found is not None
        hers = await load_snapshot(uow, reader_view(profile, [found.handle_id]))
        confirmed = await load_snapshot(uow, reader_view(profile))
    assert any("81220" in text for _, text in hers.summaries)
    assert not any("81220" in text for _, text in confirmed.summaries)
    # A confirmation stores them again, now with her episode.
    await confirm(flow)
    assert "81220" in await summaries(flow, profile_id)


async def test_a_split_takes_the_stored_summaries_away_with_the_e_mail(flow: Flow) -> None:
    profile_id = await caller_profile(flow)
    await consolidate(flow, profile_id)
    await split(flow)
    # Recomputed at the split, not at night: the caller's profile keeps his episode alone, hers hers.
    stored = await summaries(flow, profile_id)
    assert "boleto" in stored
    assert "81220" not in stored
    theirs = await profile_of(flow, VICTIM_EMAIL)
    assert theirs != profile_id
    assert "81220" in await summaries(flow, theirs)


# 4. Agent notes distilled from a conversation.


async def distill(flow: Flow, conversation_id: str) -> Any:
    agent = principal(WHATSAPP_SOURCE)
    await flow.agent_memory.distill(
        agent, DistillRequest(source_id=WHATSAPP_SOURCE, conversation_id=conversation_id)
    )
    await flow.drain(only={TaskName.DISTILL_NOTE})
    page = await flow.agent_memory.list_proposals(agent, source_id=None, status=None, cursor=None, limit=10)
    return page.items[0]


async def test_an_agent_note_is_never_distilled_from_the_declared_e_mails_conversation(flow: Flow) -> None:
    calls = len(flow.llm.calls)
    failed = await distill(flow, "email-1")
    assert (failed.status, failed.problem) == ("failed", "unconfirmed_link")
    assert len(flow.llm.calls) == calls  # her turns never reached the model
    # The caller's own conversation is his, confirmed: it distills as always.
    assert (await distill(flow, "wa-1")).status == "pending"
    await confirm(flow)
    assert (await distill(flow, "email-1")).status == "pending"


# The pre-warmed pack of a reader through the e-mail.


async def test_the_e_mails_reader_finds_its_packs_compiled_at_the_join(cell: Cell) -> None:
    configured(cell)
    flow = Flow.build(cell, ScriptedLlm(CALLER_MEMORY))
    await conversation(
        flow, WHATSAPP_SOURCE, "wa-1", [CALLER, CALLER_CRM], "Me chama de Carlos, boleto de julho", "whatsapp"
    )
    await declare(flow)
    profile_id = await caller_profile(flow)
    async with flow.cell.uow(SPACE_ID) as uow:
        found = await flow.serve._reads.find_handle(uow, handle(VICTIM_EMAIL))
        assert found is not None
        stored = [
            Variant.parse(p.pack_key.split("/", 1)[1])
            for p in await uow.compile.packs_for(f"profile:{profile_id}")
        ]
    readers = {v.reader_handle_id for v in stored}
    assert found.handle_id in readers
