"""The space's learned retrieval weights reach the per-turn slots' fusion (11, 3.4), and the slots
receipt names which version fused them."""

from datetime import date
from typing import Any

import pytest

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.app import serve as serve_module
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.weights import RetrievalWeights, WeightOutcome
from niadra.domain.serve.slots import DEFAULT_WEIGHTS
from niadra.domain.vocabulary import ReceiptSurface
from niadra.testing.cell import Cell
from tests.unit.compile.factories import T0
from tests.unit.serve.test_read_slots import AGENT, ask, harness

QUESTION = "Qual foi o número do registro da visita?"


@pytest.fixture
def fused(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []
    plan_slots = serve_module.plan_slots

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("weights"))
        return plan_slots(*args, **kwargs)

    monkeypatch.setattr(serve_module, "plan_slots", spy)
    return seen


async def learned(cell: Cell, view: str, weights: dict[str, float], version: int = 3) -> None:
    run = RetrievalWeights(uuid7(), date(2026, 9, 21), T0, version, WeightOutcome.APPLIED, {view: weights})
    await InMemoryMeasureRepository(cell.uow.store).put_retrieval_weights(run)


async def test_the_fusion_uses_the_learned_weights_of_the_view(cell: Cell, fused: list[Any]) -> None:
    await learned(cell, "chat", {"lexical": 1.8, "semantic": 0.6})
    h = harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(query=QUESTION))
    assert fused == [{**DEFAULT_WEIGHTS, "lexical": 1.8, "semantic": 0.6}]
    slots = h.receipts.sent[-1]
    assert slots.surface is ReceiptSurface.SLOTS
    assert slots.detail["weights"] == "3"


async def test_until_the_space_learns_the_fusion_keeps_the_defaults(cell: Cell, fused: list[Any]) -> None:
    await learned(cell, "voice", {"lexical": 1.8})  # another view's weights leave chat alone
    h = harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(query=QUESTION))
    assert fused == [None]
    assert "weights" not in h.receipts.sent[-1].detail
