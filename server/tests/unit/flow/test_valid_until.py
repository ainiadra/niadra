"""An event may say until when what it states holds (10, D.1): the facts it gives keep that moment, the
compiler is asked to run again at it, and neither the context nor the search shows them after it."""

from datetime import timedelta
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.config.models import CategoryRule, PredicateSpec, SpaceConfig
from niadra.contracts.context import ContextRequest, HistoryFilters, SearchRequest
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Verification
from niadra.testing.cell import WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_system_text import extraction

PHONE = {"type": "phone_e164", "value": "+5511944442222"}


def offer() -> dict[str, Any]:
    answer = extraction()
    answer["facts"][0] |= {"predicate": "offer", "value": "oferta fibra 1 giga por 99", "evidence": ["e1"]}
    return answer


def offers_space() -> SpaceConfig:
    """Offers are a category any agent reads without proof, so the test is about validity alone."""
    config = sample_config()
    policy = config.policy.model_copy(
        update={
            "categories": [
                *config.policy.categories,
                CategoryRule(name="offer", min_verification=Verification.V0),
            ]
        }
    )
    extraction = config.extraction.model_copy(
        update={
            "categories": [*config.extraction.categories, CategoryRule(name="offer")],
            "predicates": [*config.extraction.predicates, PredicateSpec(name="offer", category="offer")],
        }
    )
    return config.model_copy(update={"policy": policy, "extraction": extraction})


async def test_a_declared_validity_reaches_the_fact_and_ends_it(cell: Cell) -> None:
    cell.config.put(offers_space())
    flow = Flow.build(cell, ScriptedLlm(offer()))
    clock = cell.clock
    until = clock.now() + timedelta(days=2)
    item = {
        "type": "event",
        "idempotency_key": "o-1",
        "channel": "whatsapp",
        "conversation_id": "offer-1",
        "handles": [PHONE],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Quero a oferta de fibra 1 giga por 99 que vocês mandaram"},
        "occurred_at": clock.now().isoformat(),
        "valid_until": until.isoformat(),
    }
    ended = {"type": "conversation.ended", "idempotency_key": "o-end", "conversation_id": "offer-1",
             "occurred_at": clock.now().isoformat()}  # fmt: skip
    assert (await flow.send(WHATSAPP_SOURCE, item, ended)).errors == []
    await flow.drain()
    [fact] = cell.uow.store.module("memory")["facts"].values()
    assert fact.valid_until == until
    scheduled = cell.uow.store.module("pipeline").get("scheduled", [])
    assert any(t.name is TaskName.COMPILE and due == until for t, due, _ in scheduled)

    agent = principal(WHATSAPP_SOURCE, cell.config.space(cell.config.spaces()[0].space_id))
    from niadra.contracts.common import Handle

    subject = Handle.model_validate(PHONE)
    ask = SearchRequest(subject=subject, query="oferta fibra giga")
    assert any(i.kind == "fact" for i in (await flow.serve.search(agent, ask)).items)
    clock.advance(days=3)
    assert not any(i.kind == "fact" for i in (await flow.serve.search(agent, ask)).items)
    expired = ask.model_copy(update={"filters": HistoryFilters(show_expired=True)})
    assert any(i.valid_until == until for i in (await flow.serve.search(agent, expired)).items)
    # The compile scheduled for that moment rebuilds the stored packs without the fact.
    async with cell.uow.system(cell.config.spaces()[0].space_id) as uow:
        due = await uow.pipeline.move_due_scheduled(clock.now(), 10)
    assert TaskName.COMPILE in [t.name for t in due]
    for task in due:
        if task.name is TaskName.COMPILE:
            await flow.compile.compile(task)
    context = await flow.serve.context(agent, ContextRequest(subject=subject))
    assert "1 giga" not in (context.text or "")
