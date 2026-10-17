"""A new extractor, prompt or schema extracts the recent sessions again, in a bounded pass."""

from datetime import timedelta
from typing import Any

from niadra.app import extract as module
from niadra.app.extract import ExtractService, extractor_key, needs_reextraction
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import ExtractionRun, RunStatus
from niadra.domain.records import SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config


def outbox(cell: Cell, name: TaskName) -> list[Task]:
    return [t for t in cell.uow.store.module("pipeline").setdefault("outbox", []) if t.name is name]


def scheduled(cell: Cell) -> list[Task]:
    return [t for t, _, _ in cell.uow.store.module("pipeline").setdefault("scheduled", [])]


def service(cell: Cell) -> ExtractService:
    none: Any = None
    return ExtractService(cell.uow, none, none, cell.clock, cell.config)


def with_schema(version: str) -> Any:
    config = sample_config()
    return config.model_copy(update={"extraction": config.extraction.model_copy(update={"version": version})})


async def closed_session(cell: Cell, days_ago: float, versions: str, status: RunStatus) -> SessionRecord:
    opened = cell.clock.now() - timedelta(days=days_ago)
    session = SessionRecord(
        session_id=uuid7(int(opened.timestamp() * 1000)),
        kind="conversation",
        source_id=WHATSAPP_SOURCE,
        channel="whatsapp",
        opened_at=opened,
        last_event_at=opened + timedelta(minutes=5),
        conversation_id=f"c-{opened.timestamp()}",
        profile_id=uuid7(),
        closed_at=opened + timedelta(minutes=5),
    )
    async with cell.uow(SPACE_ID) as uow:
        await uow.sessions.create_session(SPACE_ID, session)
        await uow.extract.save_run(ExtractionRun(session.session_id, versions, status, opened))
    return session


async def test_a_changed_extractor_extracts_the_recent_sessions_again(cell: Cell) -> None:
    extract = service(cell)
    cell.config.put(with_schema("s1"))
    old_key = extractor_key(cell.config.space(SPACE_ID))
    assert await extract.watch_extractor(SPACE_ID) == 0  # the first pass only records the version
    recent = await closed_session(cell, 3, old_key, RunStatus.OK)
    reopened = await closed_session(cell, 4, f"{old_key}#r1", RunStatus.OK)
    await closed_session(cell, 2, old_key, RunStatus.MINIMAL)  # the gate skipped it: left alone
    await closed_session(cell, 45, old_key, RunStatus.OK)  # outside the window
    cell.config.put(with_schema("s2"))
    new_key = extractor_key(cell.config.space(SPACE_ID))
    done = await closed_session(cell, 1, new_key, RunStatus.OK)  # already on the new extractor

    assert await extract.watch_extractor(SPACE_ID) == 1
    assert await extract.watch_extractor(SPACE_ID) == 0
    [task] = outbox(cell, TaskName.REEXTRACT)
    await extract.reextract(task)

    asked = {t.payload["session_id"] for t in outbox(cell, TaskName.EXTRACT)}
    assert asked == {str(recent.session_id), str(reopened.session_id)}
    assert str(done.session_id) not in asked
    assert all(t.payload["reason"] == "reextract" for t in outbox(cell, TaskName.EXTRACT))
    async with cell.uow(SPACE_ID) as uow:
        _, changed_at = await uow.extract.extractor_state()
    assert changed_at == cell.clock.now()


async def test_the_pass_pages_and_goes_on_the_next_day_past_its_daily_share(cell: Cell, monkeypatch) -> None:
    # The pass stopped at 1,000 sessions and the older ones stayed on the old extraction with
    # no trace. It now reaches every session of the window, a daily share at a time.
    monkeypatch.setattr(module, "REEXTRACT_PAGE", 2)
    monkeypatch.setattr(module, "REEXTRACT_DAILY", 2)
    extract = service(cell)
    cell.config.put(with_schema("s1"))
    old_key = extractor_key(cell.config.space(SPACE_ID))
    await extract.watch_extractor(SPACE_ID)
    for day in range(5, 0, -1):
        await closed_session(cell, day, old_key, RunStatus.OK)
    cell.config.put(with_schema("s2"))
    await extract.watch_extractor(SPACE_ID)
    [first] = outbox(cell, TaskName.REEXTRACT)
    await extract.reextract(first)
    assert len(outbox(cell, TaskName.EXTRACT)) == 2
    [(second, at, _)] = cell.uow.store.module("pipeline")["scheduled"]
    assert at.date() > cell.clock.now().date(), "past the day's share, the pass waits for tomorrow"
    cell.clock.advance(days=1)
    await extract.reextract(second)
    third = scheduled(cell)[-1]
    await extract.reextract(third)
    assert len(outbox(cell, TaskName.EXTRACT)) == 5, "every session of the window, none left behind"

    # A later change makes the older pass stop where it is.
    before = len(outbox(cell, TaskName.EXTRACT))
    cell.config.put(with_schema("s3"))
    await extract.reextract(second)
    assert len(outbox(cell, TaskName.EXTRACT)) == before


def test_what_needs_a_new_extraction() -> None:
    assert needs_reextraction([("a", RunStatus.OK)], "b")
    assert needs_reextraction([("a#r1", RunStatus.OK), ("a", RunStatus.OK)], "b")
    assert not needs_reextraction([("a", RunStatus.OK), ("b", RunStatus.OK)], "b")
    assert not needs_reextraction([("b#r2", RunStatus.OK), ("a", RunStatus.OK)], "b")
    assert not needs_reextraction([("a", RunStatus.FAILED)], "b")
    assert not needs_reextraction([], "b")
