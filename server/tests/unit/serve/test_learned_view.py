"""The view each agent and model learned (13, 6.3) on the read path: a customer of the learned arm reads
a skeleton compiled with its agent's ceilings and gets its slot budget; one of the default arm reads the
view's own; a conversation's pinned body never changes when the view moves; a target model has its own."""

import json
from datetime import date
from typing import Any

import pytest

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.domain.common.ids import uuid7
from niadra.domain.compile import Variant
from niadra.domain.compile.views import SLOTS, slot_budget
from niadra.domain.measure import views as views_module
from niadra.domain.measure.views import ViewBudgets, reader_key
from niadra.domain.measure.weights import WeightOutcome
from niadra.domain.vocabulary import DeliveryPath, ReceiptSurface
from niadra.testing.cell import WHATSAPP_SOURCE, Cell
from tests.unit.compile.factories import T0
from tests.unit.serve.test_read_slots import AGENT, ask, harness

QUESTION = "Qual foi o número do registro da visita?"
SHRUNK = {"action": 0.1, "episode": 0.1}
"""Below the band on purpose: the read path compiles what the run says, and these lines must not fit."""


async def learned(cell: Cell, factors: dict[str, float], target: str = "neutral", version: int = 1) -> None:
    run = ViewBudgets(
        uuid7(),
        date(2026, 9, 21),
        T0,
        version,
        WeightOutcome.APPLIED,
        {reader_key(WHATSAPP_SOURCE, target, "chat"): factors},
    )
    await InMemoryMeasureRepository(cell.uow.store).put_view_budgets(run)


@pytest.fixture
def everyone_learns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views_module, "CONTROL_FRACTION", 0.0)


@pytest.fixture
def nobody_learns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views_module, "CONTROL_FRACTION", 1.0)


async def test_the_learned_arm_reads_its_agents_smaller_skeleton(cell: Cell, everyone_learns: None) -> None:
    today = harness(cell)
    await today.compile()
    before = await today.serve.context(AGENT, ask(conversation_id="c-0"))
    await learned(cell, SHRUNK)
    h = harness(cell)
    await h.compile()
    after = await h.serve.context(AGENT, ask(conversation_id="c-1"))
    assert after.path is DeliveryPath.T0  # compiled eagerly with the learned ceilings
    assert len(after.text or "") < len(before.text or "")
    assert after.manifest_hash != before.manifest_hash


async def test_the_default_arm_reads_the_views_own(cell: Cell, nobody_learns: None) -> None:
    today = harness(cell)
    await today.compile()
    before = await today.serve.context(AGENT, ask(conversation_id="c-0"))
    await learned(cell, SHRUNK)
    h = harness(cell)
    await h.compile()
    after = await h.serve.context(AGENT, ask(conversation_id="c-1"))
    assert after.text == before.text


async def test_a_pinned_conversation_keeps_its_bytes_when_the_view_moves(
    cell: Cell, everyone_learns: None
) -> None:
    h = harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask(conversation_id="call-9", query=QUESTION))
    await learned(cell, SHRUNK)
    cell.clock.advance(minutes=2)  # past the minute the read path keeps a space's learned views
    await h.compile()
    later = await h.serve.context(AGENT, ask(conversation_id="call-9", query=QUESTION))
    assert later.text == first.text
    fresh = await h.serve.context(AGENT, ask(conversation_id="call-10", query=QUESTION))
    assert fresh.text != first.text


async def test_the_slots_take_the_learned_slot_budget(cell: Cell, everyone_learns: None) -> None:
    await learned(cell, {SLOTS: 0.5})
    h = harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(query=QUESTION))
    slots = next(r for r in reversed(h.receipts.sent) if r.surface is ReceiptSurface.SLOTS)
    assert json.loads(slots.detail["slots"])["budget"] == slot_budget("chat") // 2


async def test_a_target_model_reads_what_was_learned_for_it(cell: Cell, everyone_learns: None) -> None:
    await learned(cell, {"action": 0.5}, target="openai/gpt-4o")
    h = harness(cell)
    await h.compile()
    seen: list[Variant] = []
    original = h.serve._pack

    async def spy(caller: Any, ask_: Any, target: Any, variant: Variant, config: Any) -> Any:
        seen.append(variant)
        return await original(caller, ask_, target, variant, config)

    h.serve._pack = spy  # type: ignore[method-assign]
    await h.serve.context(AGENT, ask(conversation_id="m-1", target={"provider": "openai", "model": "gpt-4o"}))
    await h.serve.context(AGENT, ask(conversation_id="m-2"))
    assert seen[0].budgets == (("actions", 50),)
    assert seen[1].budgets == ()  # nothing was learned for a read without a target model
