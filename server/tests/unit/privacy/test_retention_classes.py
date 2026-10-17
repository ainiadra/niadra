"""The memory a tenant sets a window for leaves for good when it expires."""

from dataclasses import replace
from datetime import timedelta

import pytest

from niadra.app.privacy import PrivacyService
from niadra.config.models import TenantSettings
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import ExtractionRun, RunStatus
from niadra.domain.measure.normalize import normalize_value
from niadra.domain.records import OpenItem, SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import OpenItemStatus
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.compile.factories import PHONE, episode, fact
from tests.unit.privacy.conftest import FakeDestinations


def service(cell: Cell, **days: int) -> PrivacyService:
    config = sample_config()
    settings = config.settings.model_copy(update={"retention_days": days})
    cell.config.put(config.model_copy(update={"settings": settings}))
    return PrivacyService(
        cell.uow,
        cell.keys,
        cell.objects,
        cell.hot,
        cell.clock,
        cell.config,
        secrets=cell.secrets,
        destinations=FakeDestinations(),
        batch_size=2,
    )


async def test_facts_and_episodes_past_their_window_leave(cell: Cell) -> None:
    now = cell.clock.now()
    old = [
        replace(fact(i, f"old_{i}", "x", "contract"), last_confirmed_at=now - timedelta(days=400))
        for i in range(3)
    ]
    kept = replace(fact(9, "plan", "fibra", "contract"), last_confirmed_at=now - timedelta(days=10))
    async with cell.uow(SPACE_ID) as uow:
        for f in [*old, kept]:
            await uow.memory.insert_fact(f, normalize_value(f.value))
        for days in (500, 20):
            await uow.memory.add_episode_version(
                replace(episode(days, "billing", days_ago=0), started_at=now - timedelta(days=days),
                        ended_at=now - timedelta(days=days) + timedelta(minutes=5))
            )  # fmt: skip
    await service(cell, facts=365, episodes=365).retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with cell.uow(SPACE_ID) as uow:
        facts = await uow.memory.facts_for([PHONE])
        episodes = await uow.memory.episodes_for([PHONE])
    assert [f.predicate for f in facts] == ["plan"]
    assert [e.ended_at.date() for e in episodes] == [(now - timedelta(days=20) + timedelta(minutes=5)).date()]


async def test_nothing_expires_until_the_tenant_sets_a_window(cell: Cell) -> None:
    now = cell.clock.now()
    ancient = replace(fact(1, "old", "x", "contract"), last_confirmed_at=now - timedelta(days=4000))
    async with cell.uow(SPACE_ID) as uow:
        await uow.memory.insert_fact(ancient, normalize_value(ancient.value))
    await service(cell).retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with cell.uow(SPACE_ID) as uow:
        assert len(await uow.memory.facts_for([PHONE])) == 1


async def test_sessions_past_their_window_leave(cell: Cell) -> None:
    now = cell.clock.now()
    sessions = []
    async with cell.uow(SPACE_ID) as uow:
        for days in (90, 60, 5):
            at = now - timedelta(days=days)
            session = SessionRecord(
                uuid7(),
                "conversation",
                WHATSAPP_SOURCE,
                "whatsapp",
                at,
                at,
                conversation_id=f"c{days}",
                closed_at=at,
            )
            await uow.sessions.create_session(SPACE_ID, session)
            await uow.extract.save_run(ExtractionRun(session.session_id, "x1", RunStatus.OK, at))
            sessions.append(session)
    await service(cell, sessions=45).retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with cell.uow(SPACE_ID) as uow:
        left = [s for s in sessions if await uow.sessions.get_session(s.session_id) is not None]
    assert left == sessions[2:]


async def test_closed_items_leave_by_their_window(cell: Cell) -> None:
    """A resolved item leaves when its window passes after it closed; an item still open never expires. Each
    leaves a tombstone for the continuous export."""
    now = cell.clock.now()

    def item(text: str, **fields: object) -> OpenItem:
        row = OpenItem(uuid7(), PHONE, text, "customer", OpenItemStatus.OPEN, now - timedelta(days=400), {})
        return replace(row, **fields)  # type: ignore[arg-type]

    closed = item("Enviar o boleto", status=OpenItemStatus.RESOLVED, closed_at=now - timedelta(days=40))
    recent = item("Ligar amanhã", status=OpenItemStatus.RESOLVED, closed_at=now - timedelta(days=5))
    pending = item("Mandar a planta")
    async with cell.uow(SPACE_ID) as uow:
        for row in (closed, recent, pending):
            await uow.memory.insert_open_item(row)
    await service(cell, open_items=30).retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    async with cell.uow(SPACE_ID) as uow:
        left = {i.item_id for i in await uow.memory.open_items_for([PHONE], tuple(OpenItemStatus))}
        tombstones = await uow.privacy.tombstones_between(SPACE_ID, 0, None)
    assert left == {recent.item_id, pending.item_id}
    assert [t.key["item_id"] for t in tombstones] == [str(closed.item_id)]


def test_the_memory_windows_are_the_tenants_with_floors() -> None:
    TenantSettings(
        retention_days={"facts": 365, "episodes": 730, "open_items": 30, "receipts": 30, "sessions": 90}
    )
    with pytest.raises(ValueError, match="receipts is at least 30 days"):
        TenantSettings(retention_days={"receipts": 7})
