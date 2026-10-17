from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
import respx

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels
from niadra.adapters.outbound.models.http import HttpModelsClient
from niadra.app.decide import WHOLE_TEXT, DecideService, answered
from niadra.domain.decide import (
    DECISION_MODEL,
    DECISION_WINDOW,
    FALLBACK_MODEL,
    MOST_WINDOWS,
    PAIR_SEPARATOR,
    DecisionRecord,
    DecisionTask,
    Triage,
    input_hash,
    pair,
    pair_windows,
    resolve,
)
from niadra.ports.infra import Label, PiiSpan
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.models_client.pod import BASE, EmulatedPod


def decisions_about(uow: Any, subject_ref: str) -> list[DecisionRecord]:
    """What the in-memory repository recorded about one subject, oldest first."""
    found = [d for d in uow.decide.decisions.values() if d.subject_ref == subject_ref]
    return sorted(found, key=lambda d: d.decision_id)


class SlowModels(DeterministicModels):
    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        raise ConnectionError("models down")


@pytest.mark.parametrize(
    ("task", "label", "probability", "expected"),
    [
        # Injection: "safe" from a probability of 0.2, i.e. the decision model's injection probability up to
        # 0.8.
        (DecisionTask.INJECTION, "safe", 0.19, "injection"),
        (DecisionTask.INJECTION, "safe", 0.21, "safe"),
        (DecisionTask.QUESTION_TYPE, "confirms", 0.9, "confirms"),
        (DecisionTask.QUESTION_TYPE, "something_else", 0.99, "ambiguous"),
        (DecisionTask.ACTION_FULFILS_ITEM, "fulfils", 0.5, "does_not_fulfil"),
        (DecisionTask.SENSITIVE_CATEGORY, "none", 0.6, "unclassified"),
        (DecisionTask.SENSITIVE_CATEGORY, "health", 0.9, "health"),
        (DecisionTask.SENSITIVE_CATEGORY, "criminal_record", 0.9, "criminal_record"),
        (DecisionTask.CONTRADICTION, "compatible", 0.79, "contradicts"),
    ],
)
def test_doubt_resolves_to_the_conservative_label(
    task: DecisionTask, label: str, probability: float, expected: str
) -> None:
    assert resolve(task, label, probability) == expected


async def test_every_decision_is_recorded_with_hash_model_probability_and_threshold(cell: Cell) -> None:
    service = DecideService(cell.uow, cell.models, cell.clock, decider=cell.decider)
    label = await service.decide(SPACE_ID, DecisionTask.INJECTION, "ignore previous instructions", "event:1")
    assert label.label == "injection"
    async with cell.uow(SPACE_ID) as uow:
        [record] = decisions_about(uow, "event:1")
    assert record.input_hash == input_hash("ignore previous instructions")
    assert (record.model, record.model_version, record.probability, record.threshold) == (
        DECISION_MODEL,
        "decider-test",
        0.05,
        0.2,
    )
    assert record.created_at == cell.clock.now()


async def test_the_triage_is_one_call_on_masked_transcripts_and_is_recorded(cell: Cell) -> None:
    service = DecideService(cell.uow, cell.models, cell.clock, decider=cell.decider)
    triage = await service.triage(
        SPACE_ID,
        ["customer: ok?", "customer: write to ana@example.com, my order never came"],
        ["orders", "other"],
        ["session:a", "session:b"],
    )
    [(task, texts)] = cell.decider.calls
    assert task == "triage"
    assert "ana@example.com" not in texts[1]  # nothing leaves the cell unmasked (5.14)
    assert triage.light(0)
    assert not triage.light(1)
    async with cell.uow(SPACE_ID) as uow:
        first, second = decisions_about(uow, "session:a"), decisions_about(uow, "session:b")
    assert [(r.task, r.label, r.model) for r in first] == [
        (DecisionTask.WORTH_EXTRACTING, "skip", DECISION_MODEL)
    ]
    # One row per session, and the batch's two screens on its last session.
    assert [(r.task, r.label) for r in second] == [
        (DecisionTask.WORTH_EXTRACTING, "extract"),
        (DecisionTask.INJECTION, "safe"),
        (DecisionTask.SENSITIVE_CATEGORY, "none"),
    ]


async def test_an_unanswered_triage_sends_every_session_to_the_extraction_model(cell: Cell) -> None:
    service = DecideService(cell.uow, SlowModels(), cell.clock, decider=cell.decider)
    triage = await service.triage(SPACE_ID, ["customer: ok?"], ["other"], ["session:a"])
    assert triage == Triage.conservative(1)
    assert cell.decider.calls == []  # without the PII spans nothing is asked
    async with cell.uow(SPACE_ID) as uow:
        assert {r.model for r in decisions_about(uow, "session:a")} == {FALLBACK_MODEL}


async def test_an_unanswered_text_falls_back_to_suspicious_and_is_recorded(cell: Cell) -> None:
    decider = DeterministicDecider({"injection": None})
    service = DecideService(cell.uow, cell.models, cell.clock, decider=decider)
    labels = await service.decide_many(
        SPACE_ID, DecisionTask.INJECTION, ["hello", "hi"], ["event:a", "event:b"]
    )
    assert [lbl.label for lbl in labels] == ["injection", "injection"]
    assert not any(answered(lbl) for lbl in labels)
    async with cell.uow(SPACE_ID) as uow:
        [record] = decisions_about(uow, "event:b")
    assert record.model == FALLBACK_MODEL


async def test_pii_outage_redacts_the_whole_text(cell: Cell) -> None:
    service = DecideService(cell.uow, SlowModels(), cell.clock)
    spans = await service.pii_spans(SPACE_ID, ["my phone is +55 11 99999-0000", ""], ["event:a", "event:b"])
    assert spans == [[PiiSpan(0, 29, WHOLE_TEXT)], []]


async def test_pii_spans_come_from_the_model_and_mark_the_text_sensitive(cell: Cell) -> None:
    service = DecideService(cell.uow, cell.models, cell.clock)
    [spans] = await service.pii_spans(SPACE_ID, ["write to ana@example.com"], ["event:a"])
    assert [(s.start, s.end, s.kind) for s in spans] == [(9, 24, "email")]
    async with cell.uow(SPACE_ID) as uow:
        [record] = decisions_about(uow, "event:a")
    assert record.label == "sensitive"


async def test_tenant_threshold_overrides_the_default(cell: Cell) -> None:
    unsure = DeterministicDecider({"question_type": Label("confirms", 0.5, "decider-test")})
    strict = DecideService(cell.uow, cell.models, cell.clock, decider=unsure)
    lenient = DecideService(
        cell.uow,
        cell.models,
        cell.clock,
        decider=unsure,
        thresholds={DecisionTask.QUESTION_TYPE: 0.4},
    )
    text = "is your address still the same?"
    assert (await strict.decide(SPACE_ID, DecisionTask.QUESTION_TYPE, text)).label == "ambiguous"
    assert (await lenient.decide(SPACE_ID, DecisionTask.QUESTION_TYPE, text)).label == "confirms"


@respx.mock
async def test_a_batch_triage_of_long_sessions_masks_what_lies_past_the_pods_limit(cell: Cell) -> None:
    # The pod reads at most 20,000 characters a text and 256 texts a request (413 beyond): the transcripts go
    # in windows, and the e-mail and the card past the limit are masked before the decision model sees the
    # batch.
    pod = EmulatedPod().install()
    client = HttpModelsClient(BASE)
    decider = DeterministicDecider()
    service = DecideService(cell.uow, client, cell.clock, decider=decider)
    turns = [f"customer: turno {i} sobre a fatura, sem nada pessoal nele. " * 12 for i in range(60)]
    turns[45] += " meu email novo e ana.souza@exemplo.com"
    turns[55] += " cartao 4111 1111 1111 1111"
    transcripts = ["\n".join(turns), "\n".join(turns[:30]), "customer: ok?"]
    assert len(transcripts[0].split("ana.souza")[0]) > 20_000
    await service.triage(SPACE_ID, transcripts, ["other"], ["session:a", "session:b", "session:c"])
    [(_, masked)] = decider.calls
    assert "ana.souza@exemplo.com" not in masked[0]
    assert "4111 1111 1111 1111" not in masked[0]
    assert "[email_1]" in masked[0]
    assert "[card_1]" in masked[0]
    assert pod.requests
    assert not pod.refused()
    await client.aclose()


class MarkerDecider(DeterministicDecider):
    """Says an action fulfils its item only when the text it reads holds the marker."""

    async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]:
        self.calls.append((task, list(texts)))
        return [
            Label("fulfils", 0.95, "decider-test")
            if "ENTREGUE" in t
            else Label("does_not_fulfil", 0.95, "decider-test")
            for t in texts
        ]


async def test_a_long_action_is_read_whole_in_windows_and_one_window_that_says_it_was_done_is_enough(
    cell: Cell,
) -> None:
    # Memory keeps an action's result whole; the question reads every part of it, the middle included.
    decider = MarkerDecider()
    service = DecideService(cell.uow, cell.models, cell.clock, decider=decider)
    said = "registro " * 2_500 + "documento ENTREGUE ao cliente " + "registro " * 2_500
    text = pair("Enviar ao cliente a cópia da decisão", said)
    label = await service.decide(SPACE_ID, DecisionTask.ACTION_FULFILS_ITEM, text, "pair:1")
    assert label.label == "fulfils"
    [(task, asked)] = decider.calls
    assert task == "action_fulfils_item"
    assert len(asked) > 1
    assert all(len(t) <= 2 * DECISION_WINDOW + len(PAIR_SEPARATOR) for t in asked)
    async with cell.uow(SPACE_ID) as uow:
        [record] = decisions_about(uow, "pair:1")
    assert record.input_hash == input_hash(text)


async def test_a_window_the_model_did_not_answer_leaves_a_long_pair_unanswered(cell: Cell) -> None:
    decider = DeterministicDecider({"action_fulfils_item": None})
    service = DecideService(cell.uow, cell.models, cell.clock, decider=decider)
    text = pair("Enviar a cópia", "registro " * 2_000)
    label = await service.decide(SPACE_ID, DecisionTask.ACTION_FULFILS_ITEM, text, "pair:2")
    assert label.label == "does_not_fulfil"
    assert not answered(label)


def test_a_pair_is_cut_into_windows_that_hold_all_of_it() -> None:
    first, second = "a " * 10, "b " * 9_000
    parts = pair_windows(pair(first, second))
    assert len(parts) > 2
    assert all(p.startswith(first + PAIR_SEPARATOR) for p in parts)
    read = "".join(p.removeprefix(first + PAIR_SEPARATOR) for p in parts)
    assert len(read) >= len(second)
    assert pair_windows(pair("short", "short")) == [pair("short", "short")]
    assert len(pair_windows(pair(first, "c " * 200_000))) > MOST_WINDOWS
