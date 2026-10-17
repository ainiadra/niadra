"""A batch of conversation turns is acknowledged once its intake row is written, and stored right after
(5.2): the same events, sessions and counters as the full transaction, in arrival order, with the hot
layer written before the answer whenever the handle is known."""

import logging
from typing import Any
from uuid import UUID, uuid4

import orjson
import pytest

from niadra.adapters.memory.intake import TransactionIntakeLog
from niadra.app.data_issues import DataIssuesService
from niadra.app.ingest import IngestService, normalized
from niadra.app.intake import Intake
from niadra.app.sessions import SessionService
from niadra.contracts.common import Handle
from niadra.contracts.events import BatchResponse
from niadra.contracts.ingest import validate_items
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.identity.handles import NewHandle
from niadra.domain.ingest.intake import MAX_INTAKE_ATTEMPTS, intake_aad
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import IssueQuery
from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind
from niadra.testing.cell import SPACE_ID, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.ingest.fakes import BLOCKED, FakeIdentity, handle_id, message, stop_storing

PHONE = Handle(type=HandleType.PHONE_E164, value="+5511988887777")
CARD = "4111 1111 1111 1111"


def deferred(cell: Cell, identity: FakeIdentity | None = None) -> tuple[IngestService, FakeIdentity]:
    """As production wires it: the answer hands the store to a worker."""
    identity = identity or FakeIdentity()
    sessions = SessionService(cell.uow, cell.clock, cell.config)
    ingest = IngestService(
        cell.uow,
        identity,
        sessions,
        cell.keys,
        cell.hot,
        cell.clock,
        cell.config,
        cell.objects,
        cell.secrets,
        intake=TransactionIntakeLog(cell.uow),
        cache=cell.cache,
        workers=cell.bus,
        issues=DataIssuesService(cell.uow, cell.clock, cell.config, RecordingAdminAudit(), cell.cache),  # type: ignore[arg-type]
    )
    return ingest, identity


async def send(
    ingest: IngestService, *raw: dict[str, Any], caller: SourcePrincipal | None = None
) -> BatchResponse:
    items, errors = validate_items(list(raw))
    return await ingest.ingest_items(caller or principal(), items, errors)


async def worker_stores(cell: Cell, ingest: IngestService) -> None:
    """What a worker does with the tasks the answers handed over."""
    await ingest.close()
    for task in cell.bus.take(TaskName.APPLY_INTAKE):
        await ingest.apply_intake(task)


def stored(cell: Cell) -> list[Any]:
    return list(cell.uow.store.data["ingest"]["events"].values())


def pending(cell: Cell) -> list[Any]:
    return list(cell.uow.store.data["ingest"].get("intake", {}).values())


def relay_copies(cell: Cell) -> list[Task]:
    return [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name is TaskName.APPLY_INTAKE]


async def hot_texts(cell: Cell, handle: UUID) -> list[str]:
    read = await cell.hot.read(SPACE_ID, [handle], 0)
    return [t.event_id.hex for t in read.turns]


def turn(key: str, text: str = "a fatura veio errada", **overrides: Any) -> dict[str, Any]:
    return message(key, text, **overrides)


async def test_a_batch_of_turns_is_answered_before_it_is_stored(cell: Cell) -> None:
    ingest, _ = deferred(cell)

    answer = await send(ingest, turn("m1"), turn("m2", "e veio em dobro"))

    assert answer == BatchResponse(accepted=2, duplicates=0, errors=[])
    assert stored(cell) == []
    assert len(pending(cell)) == 1
    [copy] = relay_copies(cell)
    assert copy.payload == {"intake_id": str(pending(cell)[0].intake_id)}

    await worker_stores(cell, ingest)

    assert [e.record.text for e in stored(cell)] == ["a fatura veio errada", "e veio em dobro"]
    assert pending(cell) == []
    assert relay_copies(cell) == []
    assert all(e.record.handle_ids == (handle_id(PHONE),) for e in stored(cell))
    sessions = list(cell.uow.store.data["sessions"]["sessions"].values())
    assert len(sessions) == 1
    beats = cell.uow.store.data["ingest"]["heartbeats"]
    assert [(b["received"], b["rejected"]) for b in beats.values()] == [(2, 0)]
    assert len((await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).turns) == 2


async def test_the_intake_row_holds_the_batch_masked_and_sealed(cell: Cell) -> None:
    ingest, _ = deferred(cell)

    answer = await send(ingest, turn("m1", f"meu cartao e {CARD}"))

    assert answer.masked == {"card": 1}
    [row] = pending(cell)
    assert CARD.replace(" ", "").encode() not in row.body_ct
    assert b"meu cartao" not in row.body_ct
    body = await cell.keys.decrypt(SPACE_ID, row.body_ct, intake_aad(row.intake_id), row.dek_version)
    [event] = Intake.from_json(orjson.loads(body)).events
    assert event.text is not None
    assert "1111 1111" not in event.text
    assert event.content_meta == {"type": "text", "masked": {"card": 1}}
    await worker_stores(cell, ingest)
    [stored_event] = stored(cell)
    assert stored_event.record.text == event.text
    assert stored_event.record.content_meta == event.content_meta


async def test_a_repeated_turn_is_a_duplicate_across_batches(cell: Cell) -> None:
    ingest, _ = deferred(cell)

    first = await send(ingest, turn("m1"), turn("m1"))
    again = await send(ingest, turn("m1"), turn("m2"))
    await worker_stores(cell, ingest)

    assert (first.accepted, first.duplicates) == (1, 1)
    assert (again.accepted, again.duplicates) == (1, 1)
    assert sorted(e.idempotency_key for e in stored(cell)) == ["m1", "m2"]
    beats = cell.uow.store.data["ingest"]["heartbeats"]
    assert sum(b["received"] for b in beats.values()) == 4


async def test_anything_but_turns_is_stored_before_the_answer(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    system = turn(
        "s1",
        kind="system_event",
        canonical_type="invoice.disputed",
        content=None,
        speaker={"role": "system"},
        fields={"amount": 12},
    )

    await send(ingest, turn("m1"), system)

    assert pending(cell) == []
    assert sorted(e.idempotency_key for e in stored(cell)) == ["m1", "s1"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"conversation_aliases": ["trunk-9"]},
        {"handles": [{"type": "email_domain", "value": "acme.com.br"}]},
        {"backing": {"checked": 1, "guard_violations": ["0a1b2c3d"]}, "speaker": {"role": "ai_agent"}},
        {"content": {"type": "text", "text": "4111111111111111"}},
    ],
    ids=["aliases", "organization", "guard lines", "a bare number whose masking needs the turn before"],
)
async def test_turns_that_storing_could_refuse_or_that_need_the_log_take_the_full_transaction(
    cell: Cell, overrides: dict[str, Any]
) -> None:
    ingest, _ = deferred(cell)

    answer = await send(ingest, turn("m1", **overrides))

    assert answer.accepted == 1
    assert pending(cell) == []
    assert [e.idempotency_key for e in stored(cell)] == ["m1"]


async def test_the_full_transaction_stores_what_was_acknowledged_before_it(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    ended = {
        "type": "conversation.ended",
        "idempotency_key": "end",
        "conversation_id": "conv-1",
        "occurred_at": "2026-09-22T17:00:00Z",
    }

    await send(ingest, turn("m1"))
    assert stored(cell) == []
    await send(ingest, ended)

    # The end found the turn's session, so it closed it rather than waiting for turns that already came.
    [event] = stored(cell)
    assert event.idempotency_key == "m1"
    assert pending(cell) == []
    [(_, session)] = list(cell.uow.store.data["sessions"]["sessions"].values())
    assert session.ended_at is not None
    await worker_stores(cell, ingest)


async def test_a_known_handle_gets_its_hot_turn_with_the_answer(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    await send(ingest, turn("m1"))
    await worker_stores(cell, ingest)

    await send(ingest, turn("m2", "e veio em dobro"))

    [second] = [e for e in pending(cell)]
    body = await cell.keys.decrypt(SPACE_ID, second.body_ct, intake_aad(second.intake_id), second.dek_version)
    [event] = Intake.from_json(orjson.loads(body)).events
    assert event.event_id.hex in await hot_texts(cell, handle_id(PHONE))
    await worker_stores(cell, ingest)
    assert len(await hot_texts(cell, handle_id(PHONE))) == 2


async def test_a_first_contact_gets_its_hot_turn_once_stored(cell: Cell) -> None:
    ingest, _ = deferred(cell)

    await send(ingest, turn("m1"))

    assert await hot_texts(cell, handle_id(PHONE)) == []
    await worker_stores(cell, ingest)
    assert len(await hot_texts(cell, handle_id(PHONE))) == 1


async def test_a_turn_written_under_a_stale_handle_moves_when_stored(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    stale = uuid4()
    key = normalized(PHONE, SubjectKind.PERSON).key
    await cell.cache.set(await ingest._handle_cache_key(SPACE_ID, key), stale.bytes + b"\x00", 60)

    await send(ingest, turn("m1"))
    assert len(await hot_texts(cell, stale)) == 1
    await worker_stores(cell, ingest)

    assert await hot_texts(cell, stale) == []
    assert len(await hot_texts(cell, handle_id(PHONE))) == 1


async def test_a_repeated_batch_leaves_no_second_hot_turn(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    await send(ingest, turn("m1"))
    await worker_stores(cell, ingest)
    [original] = await hot_texts(cell, handle_id(PHONE))

    answer = await send(ingest, turn("m1"))
    await worker_stores(cell, ingest)

    assert answer.duplicates == 1
    assert await hot_texts(cell, handle_id(PHONE)) == [original]


async def test_the_relay_copy_stores_what_a_dead_process_acknowledged(cell: Cell) -> None:
    ingest, identity = deferred(cell)
    await send(ingest, turn("m1"))
    [copy] = relay_copies(cell)
    stop_storing(ingest)  # the process died before storing it

    worker, _ = deferred(cell, identity)
    await worker.apply_intake(copy)

    assert [e.idempotency_key for e in stored(cell)] == ["m1"]
    assert pending(cell) == []
    assert relay_copies(cell) == []


async def keeps_failing(cell: Cell, ingest: IngestService, *raw: dict[str, Any]) -> None:
    """Sends a batch whose storing fails every attempt: it ends set aside."""
    assert (await send(ingest, *raw)).accepted == len(raw)
    with pytest.raises(InvalidInput):  # the worker's task fails, and the task system retries it
        await worker_stores(cell, ingest)
    [copy] = relay_copies(cell)
    for _ in range(MAX_INTAKE_ATTEMPTS - 1):
        with pytest.raises(InvalidInput):
            await ingest.apply_intake(copy)


async def opened(cell: Cell, row: Any) -> Intake:
    body = await cell.keys.decrypt(SPACE_ID, row.body_ct, intake_aad(row.intake_id), row.dek_version)
    return Intake.from_json(orjson.loads(body))


async def test_a_batch_that_keeps_failing_is_kept_aside_so_later_ones_are_stored(
    cell: Cell, caplog: pytest.LogCaptureFixture
) -> None:
    ingest, _ = deferred(cell)
    await keeps_failing(cell, ingest, turn("bad", handles=[{"type": "phone_e164", "value": BLOCKED}]))

    # Never deleted: kept sealed, out of the order, and the relay stops bringing it back.
    [kept] = pending(cell)
    assert kept.failed_at is not None
    assert [e.idempotency_key for e in (await opened(cell, kept)).events] == ["bad"]
    assert relay_copies(cell) == []
    assert "kept aside for a retry" in caplog.text
    async with cell.uow(SPACE_ID) as uow:
        [issue] = await uow.turns.issues(IssueQuery(kind="row_failed"))
    assert issue.field == "acknowledged_batch"
    [listed] = await ingest.failed(SPACE_ID)
    assert (listed.intake_id, listed.kind, listed.attempts) == (kept.intake_id, "events", MAX_INTAKE_ATTEMPTS)

    await send(ingest, turn("m1"))
    await worker_stores(cell, ingest)
    assert [e.idempotency_key for e in stored(cell)] == ["m1"]
    assert len(pending(cell)) == 1


async def test_a_kept_batch_is_stored_once_retried(cell: Cell, monkeypatch: pytest.MonkeyPatch) -> None:
    ingest, _ = deferred(cell)
    await keeps_failing(cell, ingest, turn("bad", handles=[{"type": "phone_e164", "value": BLOCKED}]))
    monkeypatch.setattr("tests.unit.ingest.fakes.BLOCKED", "+5511000000009")  # what made it fail is fixed

    assert await ingest.retry(SPACE_ID, None) == 1
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.ingest.has_intake()
    await ingest.apply_intake(Task(TaskName.APPLY_INTAKE, SPACE_ID, {}))

    assert [e.idempotency_key for e in stored(cell)] == ["bad"]
    assert pending(cell) == []
    assert await ingest.failed(SPACE_ID) == []
    assert await ingest.retry(SPACE_ID, None) == 0


async def test_a_listing_of_kept_batches_counts_every_one_past_what_it_shows(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("niadra.app.ingest.MAX_FAILED_LISTED", 1)
    ingest, _ = deferred(cell)
    for key in ("bad-1", "bad-2"):
        await keeps_failing(cell, ingest, turn(key, handles=[{"type": "phone_e164", "value": BLOCKED}]))

    assert len(await ingest.failed(SPACE_ID)) == 1
    assert await ingest.kept(SPACE_ID) == 2


async def test_an_erasure_takes_the_targets_events_from_a_kept_batch_and_keeps_the_rest(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    blocked = turn("bad", handles=[{"type": "phone_e164", "value": BLOCKED}])
    await keeps_failing(cell, ingest, blocked, turn("gone", conversation_id="conv-gone"))
    [kept] = pending(cell)

    gone = ErasureScope(uuid4(), TargetKind.CONVERSATION, (), None, "conv-gone", cell.clock.now())
    await ingest.fence(SPACE_ID, gone)

    [left] = pending(cell)
    assert left.intake_id == kept.intake_id
    assert left.failed_at is not None
    assert [e.idempotency_key for e in (await opened(cell, left)).events] == ["bad"]

    # A kept batch left with none of its events goes with the erasure.
    await ingest.purge(
        SPACE_ID, ErasureScope(uuid4(), TargetKind.CONVERSATION, (), None, "conv-1", cell.clock.now())
    )
    assert pending(cell) == []


async def test_an_erasure_of_a_person_finds_their_events_in_a_kept_batch_by_their_handle(cell: Cell) -> None:
    ingest, _ = deferred(cell)
    other = Handle(type=HandleType.PHONE_E164, value="+5511977776666")
    blocked = turn("bad", handles=[{"type": "phone_e164", "value": BLOCKED}])
    await keeps_failing(cell, ingest, blocked, turn("theirs", handles=[other.model_dump(exclude_none=True)]))
    key = normalized(other, SubjectKind.PERSON).key
    async with cell.uow(SPACE_ID) as uow:
        [record] = await uow.identity.add_handles(
            [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], cell.clock.now()
        )

    scope = ErasureScope(uuid4(), TargetKind.HANDLE, (record.handle_id,), None, None, cell.clock.now())
    await ingest.fence(SPACE_ID, scope)

    [left] = pending(cell)
    assert [e.idempotency_key for e in (await opened(cell, left)).events] == ["bad"]


async def test_a_persons_access_reads_their_events_in_a_kept_batch_and_no_one_elses(cell: Cell) -> None:
    """Their events kept aside are theirs to access, unsealed, as an erasure would take them (5.14): a
    package that left them out cut what Niadra keeps about the person."""
    ingest, _ = deferred(cell)
    other = Handle(type=HandleType.PHONE_E164, value="+5511977776666")
    blocked = turn("bad", handles=[{"type": "phone_e164", "value": BLOCKED}])
    theirs = turn("theirs", "o protocolo é 734211", handles=[other.model_dump(exclude_none=True)])
    await keeps_failing(cell, ingest, blocked, theirs)
    key = normalized(other, SubjectKind.PERSON).key
    async with cell.uow(SPACE_ID) as uow:
        [record] = await uow.identity.add_handles(
            [NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], cell.clock.now()
        )
    [kept] = pending(cell)

    scope = ErasureScope(uuid4(), TargetKind.PROFILE, (record.handle_id,), None, None, cell.clock.now())
    [row] = await ingest.kept_for(SPACE_ID, scope)

    assert row["text"] == "o protocolo é 734211"
    assert (row["kept_aside"], row["intake_id"], row["failed_at"]) == (True, kept.intake_id, kept.failed_at)
    nobody = ErasureScope(uuid4(), TargetKind.PROFILE, (uuid4(),), None, None, cell.clock.now())
    assert await ingest.kept_for(SPACE_ID, nobody) == []
    assert len(pending(cell)) == 1, "reading it for the package takes nothing"


async def test_answers_stored_by_one_worker_keep_their_order(
    cell: Cell, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    ingest, _ = deferred(cell)

    for n in range(5):
        await send(ingest, turn(f"m{n}"))
    await worker_stores(cell, ingest)

    # Stored in the order they were answered.
    assert [e.idempotency_key for e in stored(cell)] == [f"m{n}" for n in range(5)]
    assert "failed" not in caplog.text
