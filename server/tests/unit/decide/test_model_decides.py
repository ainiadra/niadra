"""The decision model decides the model tasks of the port: on masked texts only, its answer acted on at the
task's threshold, the conservative label for a text it did not answer or answered without confidence, and a
spent budget left to the caller."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels
from niadra.app.decide import DecideService, answered
from niadra.domain.decide import (
    DECISION_MODEL,
    FALLBACK_MODEL,
    MODEL_TASKS,
    RULES,
    DecisionRecord,
    DecisionTask,
    pair,
)
from niadra.domain.extract.errors import BudgetExceeded
from niadra.ports.infra import Label, PiiSpan
from niadra.testing.cell import SPACE_ID, Cell

SESSION = "customer: my order 4471 never arrived, write to ana@example.com\nai_agent: I opened ticket 5521"


class NoPii(DeterministicModels):
    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        raise ConnectionError("models down")


class BrokenDecider(DeterministicDecider):
    async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]:
        raise ConnectionError("down")


class SpentDecider(DeterministicDecider):
    async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]:
        raise BudgetExceeded(3600, "daily extraction budget spent", cap_micros=500_000)


def record(uow: Any, subject_ref: str) -> DecisionRecord:
    [found] = [d for d in uow.decide.decisions.values() if d.subject_ref == subject_ref]
    return found


def service(cell: Cell, decider: Any = None, models: Any = None) -> DecideService:
    return DecideService(cell.uow, models or cell.models, cell.clock, decider=decider)


async def test_the_decision_model_s_confident_answer_is_the_decision(cell: Cell) -> None:
    decider = DeterministicDecider({"sensitive_category": Label("none", 0.95, "decision-model-1")})
    label = await service(cell, decider).decide(
        SPACE_ID, DecisionTask.SENSITIVE_CATEGORY, SESSION, "session:1"
    )
    assert label.label == "none"
    assert answered(label)
    async with cell.uow(SPACE_ID) as uow:
        found = record(uow, "session:1")
    assert (found.model, found.model_version, found.label, found.probability, found.threshold) == (
        DECISION_MODEL,
        "decision-model-1",
        "none",
        0.95,
        0.8,
    )


async def test_below_the_threshold_the_conservative_label_decides(cell: Cell) -> None:
    decider = DeterministicDecider({"sensitive_category": Label("none", 0.6, "decision-model-1")})
    label = await service(cell, decider).decide(
        SPACE_ID, DecisionTask.SENSITIVE_CATEGORY, SESSION, "session:1"
    )
    assert label.label == "unclassified"
    assert answered(label)  # the decision model answered; it was not sure enough
    async with cell.uow(SPACE_ID) as uow:
        assert (record(uow, "session:1").label, record(uow, "session:1").probability) == ("unclassified", 0.6)


@pytest.mark.parametrize(
    ("p_injection", "expected"), [(0.95, "injection"), (0.81, "injection"), (0.79, "safe"), (0.1, "safe")]
)
async def test_injection_is_flagged_above_the_calibrated_probability(
    cell: Cell, p_injection: float, expected: str
) -> None:
    # The adapter reports "safe" with its own probability; 0.8 was calibrated on 27/09.
    decider = DeterministicDecider({"injection": Label("safe", 1 - p_injection, "decision-model-1")})
    label = await service(cell, decider).decide(SPACE_ID, DecisionTask.INJECTION, "any turn", "event:1")
    assert label.label == expected


async def test_the_decision_model_reads_masked_text_only(cell: Cell) -> None:
    decider = DeterministicDecider()
    await service(cell, decider).decide(SPACE_ID, DecisionTask.INJECTION, SESSION, "event:1")
    [(task, [sent])] = decider.calls
    assert task == "injection"
    assert "ana@example.com" not in sent
    assert "[email_1]" in sent


async def test_without_pii_spans_the_decision_model_is_never_asked(cell: Cell) -> None:
    decider = DeterministicDecider()
    label = await service(cell, decider, NoPii()).decide(
        SPACE_ID, DecisionTask.INJECTION, "hello there", "event:1"
    )
    assert decider.calls == []
    assert (label.label, answered(label)) == ("injection", False)
    async with cell.uow(SPACE_ID) as uow:
        assert (record(uow, "event:1").model, record(uow, "event:1").label) == (FALLBACK_MODEL, "injection")


@pytest.mark.parametrize("decider", [BrokenDecider(), DeterministicDecider({"injection": None}), None])
async def test_a_decision_model_that_fails_or_is_missing_gives_the_conservative_label(
    cell: Cell, decider: Any
) -> None:
    labels = await service(cell, decider).decide_many(
        SPACE_ID, DecisionTask.INJECTION, ["hello", "hi"], ["event:a", "event:b"]
    )
    assert [(lbl.label, answered(lbl)) for lbl in labels] == [("injection", False)] * 2
    async with cell.uow(SPACE_ID) as uow:
        assert (record(uow, "event:b").model, record(uow, "event:b").probability) == (FALLBACK_MODEL, 0.0)


async def test_a_text_the_decision_model_did_not_answer_does_not_take_the_others_down(cell: Cell) -> None:
    class Partly(DeterministicDecider):
        async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]:
            return [None, Label("safe", 0.97, "decision-model-1")]

    turns = ["a turn", "another"]
    labels = await service(cell, Partly()).decide_many(SPACE_ID, DecisionTask.INJECTION, turns)
    assert [(lbl.label, answered(lbl)) for lbl in labels] == [("injection", False), ("safe", True)]


async def test_a_spent_budget_reaches_the_caller(cell: Cell) -> None:
    with pytest.raises(BudgetExceeded):
        await service(cell, SpentDecider()).decide(SPACE_ID, DecisionTask.INJECTION, "hello", "event:1")


async def test_a_contradiction_pair_is_the_decision_model_s_to_decide(cell: Cell) -> None:
    decider = DeterministicDecider({"contradiction": Label("compatible", 0.9, "decision-model-1")})
    text = pair("prefers email", "prefers WhatsApp")
    label = await service(cell, decider).decide(SPACE_ID, DecisionTask.CONTRADICTION, text, "fact:a|fact:b")
    assert label.label == "compatible"
    assert decider.calls == [("contradiction", [text])]


async def test_whether_an_action_does_an_open_item_is_the_decision_model_s(cell: Cell) -> None:
    # The models pod's hashing classifier read "informar que a sentença foi julgada" as not doing "comunicar a
    # sentença ao cliente" at 0.805 (juridico-sandbox, 05/10/2026); the decision model answered 0.91 for it
    # and 0.28 for "avisar o cliente sobre a verificação da sentença", which does not do it.
    decider = DeterministicDecider({"action_fulfils_item": Label("fulfils", 0.91, "decision-model-1")})
    text = pair("Comunicar a sentença ao cliente", "Informar que a sentença foi julgada improcedente")
    label = await service(cell, decider).decide(SPACE_ID, DecisionTask.ACTION_FULFILS_ITEM, text, "item:1")
    assert label.label == "fulfils"
    assert decider.calls == [("action_fulfils_item", [text])]
    unsure = DeterministicDecider({"action_fulfils_item": Label("fulfils", 0.72, "decision-model-1")})
    label = await service(cell, unsure).decide(SPACE_ID, DecisionTask.ACTION_FULFILS_ITEM, text, "item:2")
    assert label.label == "does_not_fulfil"


async def test_the_pairs_the_model_answered_are_known_and_a_default_is_not_an_answer(cell: Cell) -> None:
    decide = service(cell, DeterministicDecider())
    await decide.decide(SPACE_ID, DecisionTask.CONTRADICTION, pair("a", "b"), "fact:a|fact:b")
    await service(cell, None).decide(SPACE_ID, DecisionTask.CONTRADICTION, pair("c", "d"), "fact:c|fact:d")
    refs = ["fact:a|fact:b", "fact:c|fact:d", "fact:e|fact:f"]
    assert await decide.answered_before(SPACE_ID, DecisionTask.CONTRADICTION, refs) == {"fact:a|fact:b"}


def test_the_model_tasks_are_the_ones_measured_and_exact_rules_stay_out() -> None:
    assert {
        DecisionTask.INJECTION,
        DecisionTask.SENSITIVE_CATEGORY,
        DecisionTask.QUESTION_TYPE,
        DecisionTask.CONTRADICTION,
        DecisionTask.ACTION_FULFILS_ITEM,
        DecisionTask.ITEM_RESTATES_ITEM,
        DecisionTask.TURN_DELIVERS_ITEM,
        DecisionTask.TURN_ASKS_ITEM,
    } == MODEL_TASKS
    # The gate's question is asked per session by the triage, never one text at a time.
    assert DecisionTask.WORTH_EXTRACTING not in MODEL_TASKS
    assert DecisionTask.PII not in MODEL_TASKS
    assert all(RULES[t].conservative in RULES[t].labels for t in MODEL_TASKS)
