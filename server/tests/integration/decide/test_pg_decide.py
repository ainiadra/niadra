from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import text

from niadra.domain.common.ids import uuid7
from niadra.domain.decide import DecisionRecord, DecisionTask, input_hash
from tests.integration.conftest import CloningUnitOfWork


async def test_decisions_round_trip_and_replays_are_ignored(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    record = DecisionRecord(
        decision_id=uuid7(),
        task=DecisionTask.INJECTION,
        input_hash=input_hash("x"),
        model="decision-model",
        model_version="decision-model-1",
        label="safe",
        probability=0.97,
        threshold=0.5,
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
        subject_ref="event:1",
    )
    async with pg_uow(space) as uow:
        await uow.decide.record([record])
        await uow.decide.record([record])
    stored = text(
        "SELECT decision_id, task, label, input_hash, probability FROM decisions WHERE subject_ref = :s"
    )
    async with pg_uow.system(space) as uow:
        [found] = (await uow.session.execute(stored, {"s": "event:1"})).all()
    assert (found.decision_id, found.task, found.label) == (record.decision_id, record.task.value, "safe")
    assert found.input_hash == record.input_hash
    assert abs(found.probability - 0.97) < 1e-6


async def test_the_pairs_the_decision_model_answered_are_found_by_subject(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    asked = DecisionRecord(
        decision_id=uuid7(),
        task=DecisionTask.CONTRADICTION,
        input_hash=input_hash("a [SEP] b"),
        model="decision-model",
        model_version="decision-model-1",
        label="compatible",
        probability=0.93,
        threshold=0.8,
        created_at=datetime(2026, 9, 27, tzinfo=UTC),
        subject_ref="fact:a|fact:b",
    )
    # A default for a model that did not answer is not an answer, and another task's row is not either.
    unanswered = replace(
        asked, decision_id=uuid7(), model="conservative-default", subject_ref="fact:c|fact:d"
    )
    other_task = replace(asked, decision_id=uuid7(), task=DecisionTask.INJECTION, subject_ref="fact:e|fact:f")
    async with pg_uow(space) as uow:
        await uow.decide.record([asked, unanswered, other_task])
    refs = ["fact:a|fact:b", "fact:c|fact:d", "fact:e|fact:f", "fact:g|fact:h"]
    async with pg_uow(space) as uow:
        assert await uow.decide.answered(DecisionTask.CONTRADICTION, "decision-model", refs) == {
            "fact:a|fact:b"
        }
        assert await uow.decide.answered(DecisionTask.CONTRADICTION, "decision-model", []) == set()
