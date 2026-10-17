"""The erasure cascade on PostgreSQL against the company's feedback (8.4): a mention by a feedback event
never holds a fact, the correction written for an erased fact goes with it, and the feedback event is
rewritten as the system role with ids and kinds only, its `change_seq` bumped so the export sends it
again. Both the evidence step and the handle's own step run as the system role, since the application
role may not change a stored event."""

import itertools
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import (
    DictSecrets,
    FixedClock,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.app.privacy import PrivacyService
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import MemoryRef
from niadra.domain.ingest.events import NewEvent
from niadra.domain.memory.facts import Relation, normalize_value
from niadra.domain.privacy.erasure import ErasureRequest, ErasureScope, ErasureStatus, TargetKind
from niadra.domain.records import EventRecord, Fact
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, FactStatus, RelationType, Sensitivity, Speaker
from niadra.testing.cell import sample_config
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
SOURCE = UUID(int=1)
REASON = "a cliente disse Rafa"


class _NoDestinations:
    async def open(self, *args: Any) -> Any:
        raise AssertionError("no export here")

    async def check_write_only(self, *args: Any) -> None: ...


def _service(pg_uow: CloningUnitOfWork, space: UUID) -> PrivacyService:
    return PrivacyService(
        pg_uow,  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryObjectStore(),
        InMemoryHotLayer(),
        FixedClock(NOW),
        StaticConfig(sample_config(space)),
        secrets=DictSecrets(),  # type: ignore[arg-type]
        destinations=_NoDestinations(),  # type: ignore[arg-type]
        batch_size=3,
    )


_MS = itertools.count(int(NOW.timestamp() * 1000))


def _received_id() -> UUID:
    """An event id minted at NOW, as one is minted at arrival: the archive takes a month by id and by
    `received_at` together. One millisecond apart each, so the ids keep the order the events were made in."""
    return uuid7(next(_MS))


def _said(space: UUID, handle: UUID, conversation: str) -> NewEvent:
    record = EventRecord(
        event_id=_received_id(),
        kind=EventKind.MESSAGE,
        source_id=SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=NOW,
        received_at=NOW,
        handle_ids=(handle,),
        text="Me chama de Rafa",
        conversation_id=conversation,
    )
    return NewEvent(space, record, f"key-{record.event_id}")


def _feedback(space: UUID, handle: UUID, fields: dict[str, Any]) -> NewEvent:
    record = EventRecord(
        event_id=_received_id(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=SOURCE,
        channel="feedback",
        speaker=Speaker.SYSTEM,
        occurred_at=NOW,
        received_at=NOW,
        handle_ids=(handle,),
        canonical_type=f"feedback.{fields['action']}",
        fields=fields,
    )
    return NewEvent(space, record, f"key-{record.event_id}")


def _fact(origin: UUID, value: str, status: FactStatus = FactStatus.ACTIVE) -> Fact:
    return Fact(
        uuid7(),
        origin,
        "preferred_name",
        value,
        "contact",
        Sensitivity.NORMAL,
        Speaker.CUSTOMER,
        0.8,
        NOW,
        NOW,
        status,
        NOW,
        origin_conversation_id="wa-1",
    )


async def _corrected(
    pg_uow: CloningUnitOfWork, space: UUID, fact_on: UUID, feedback_on: UUID
) -> tuple[Fact, Fact, EventRecord, EventRecord]:
    """What `correct_fact` leaves: the retracted row, the corrected one and the event on both."""
    said = _said(space, fact_on, "wa-1")
    original = _fact(fact_on, "Rafa", FactStatus.RETRACTED)
    fixed = _fact(fact_on, "Rafaela")
    fields = {
        "action": "correct_fact",
        "fact_id": str(original.fact_id),
        "value": "Rafaela",
        "reason": REASON,
    }
    correction = _feedback(space, feedback_on, fields)
    async with pg_uow(space) as uow:
        await uow.ingest.insert_events([said, correction])
        for row in (original, fixed):
            assert await uow.memory.insert_fact(row, normalize_value(row.value))
        await uow.memory.add_mentions(MemoryRef(fact_on, original.fact_id), [said.record.event_id])
        for row in (original, fixed):
            await uow.memory.add_mentions(
                MemoryRef(fact_on, row.fact_id), [correction.record.event_id], feedback=True
            )
        await uow.memory.add_relations(
            [
                Relation(
                    MemoryRef(fact_on, fixed.fact_id),
                    MemoryRef(fact_on, original.fact_id),
                    RelationType.SUPERSEDES,
                    origin="rule",
                )
            ]
        )
    return original, fixed, said.record, correction.record


async def _erase(pg_uow: CloningUnitOfWork, space: UUID, scope: ErasureScope) -> ErasureRequest:
    request = ErasureRequest(
        request_id=scope.request_id,
        target_kind=scope.kind,
        target_id=scope.conversation_id or str(scope.handle_ids[0]),
        status=ErasureStatus.PENDING,
        requested_by="user:dpo",
        requested_at=NOW,
        counts={"scope": scope.to_json()},
    )
    async with pg_uow(space) as uow:
        await uow.privacy.create_erasure(space, request)
    await _service(pg_uow, space).erase(Task(TaskName.ERASE, space, {"request_id": str(request.request_id)}))
    async with pg_uow(space) as uow:
        done = await uow.privacy.get_erasure(space, request.request_id)
    assert done is not None
    assert done.status is ErasureStatus.COMPLETED
    return done


async def _seq(pg_uow: CloningUnitOfWork, space: UUID, event_id: UUID) -> int:
    async with pg_uow(space) as uow:
        found = await uow.session.execute(
            text("SELECT change_seq FROM events WHERE event_id = :e"), {"e": event_id}
        )
        return int(found.scalar_one())


async def test_a_conversation_erasure_takes_the_corrected_fact_and_its_correction(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, rafa = uuid4(), uuid7()
    _, _, said, correction = await _corrected(pg_uow, space, rafa, rafa)
    before = await _seq(pg_uow, space, correction.event_id)

    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (rafa,), None, "wa-1", NOW)
    done = await _erase(pg_uow, space, scope)

    async with pg_uow(space) as uow:
        assert await uow.memory.facts_for([rafa], tuple(FactStatus)) == []
        assert await uow.memory.relations_for([rafa]) == []
        assert await uow.ingest.get_events([said.event_id]) == []
        [kept] = await uow.ingest.get_events([correction.event_id])
    assert kept.fields == {"action": "correct_fact", "fact_id": correction.fields["fact_id"]}
    assert await _seq(pg_uow, space, correction.event_id) > before
    assert (done.erased["facts"], done.erased["feedback_values"]) == (2, 1)


async def test_a_handle_erasure_clears_the_feedback_left_on_another_handle(pg_uow: CloningUnitOfWork) -> None:
    space, phone, email = uuid4(), uuid7(), uuid7()
    _, _, _, correction = await _corrected(pg_uow, space, phone, email)

    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (phone,), None, None, NOW)
    await _erase(pg_uow, space, scope)

    async with pg_uow(space) as uow:
        assert await uow.memory.facts_for([phone], tuple(FactStatus)) == []
        [kept] = await uow.ingest.get_events([correction.event_id])
    assert kept.fields == {"action": "correct_fact", "fact_id": correction.fields["fact_id"]}


async def test_a_mention_copied_from_feedback_stays_feedback(pg_uow: CloningUnitOfWork) -> None:
    space, rafa = uuid4(), uuid7()
    feedback, said, again = uuid7(), uuid7(), uuid7()
    retracted, duplicate = _fact(rafa, "Rafa", FactStatus.RETRACTED), _fact(rafa, "Rafinha")
    async with pg_uow(space) as uow:
        for row in (retracted, duplicate):
            assert await uow.memory.insert_fact(row, normalize_value(row.value))
        await uow.memory.add_mentions(MemoryRef(rafa, retracted.fact_id), [said])
        await uow.memory.add_mentions(MemoryRef(rafa, retracted.fact_id), [feedback], feedback=True)
        # The consolidation copies a row's evidence onto the one it keeps, without knowing its kind.
        await uow.memory.add_mentions(MemoryRef(rafa, duplicate.fact_id), [again, feedback])
        flags = dict(
            (
                await uow.session.execute(
                    text("SELECT event_id, bool_and(feedback) FROM fact_mentions GROUP BY event_id")
                )
            ).all()
        )
    assert flags == {feedback: True, said: False, again: False}
