"""The measured conversations of a period and an agent, to open without typing an id."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from niadra.app.measure import MeasureService
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.measure.model import MeasuredSession
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)


def person(sources: frozenset[UUID] | None = None) -> PersonPrincipal:
    return PersonPrincipal(
        SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX),
        uuid7(),
        frozenset({"analysis"}),
        sources,
    )


def session(key: str, source: UUID, at: datetime) -> MeasuredSession:
    return MeasuredSession(
        uuid7(), "m1", key, source, "whatsapp", None, "", at.date(), at, at, no_context=False
    )


async def test_lists_newest_first_by_agent_and_period_with_a_cursor(cell: Cell) -> None:
    service = MeasureService(cell.uow, cell.clock, cell.config)
    async with cell.uow(SPACE_ID) as uow:
        for i in range(5):
            await uow.measure.put_session(session(f"wa-{i}", WHATSAPP_SOURCE, T0 + timedelta(hours=i)))
        await uow.measure.put_session(session("voice-1", VOICE_SOURCE, T0))
        await uow.measure.put_session(session("wa-old", WHATSAPP_SOURCE, T0 - timedelta(days=30)))
    first = await service.measured_conversations(
        person(),
        source_id=WHATSAPP_SOURCE,
        since=date(2026, 9, 19),
        until=date(2026, 9, 21),
        cursor=None,
        limit=3,
    )
    assert [c.conversation_id for c in first.items] == ["wa-4", "wa-3", "wa-2"]
    rest = await service.measured_conversations(
        person(),
        source_id=WHATSAPP_SOURCE,
        since=date(2026, 9, 19),
        until=date(2026, 9, 21),
        cursor=first.next_cursor,
        limit=3,
    )
    assert [c.conversation_id for c in rest.items] == ["wa-1", "wa-0"]
    assert rest.next_cursor is None
    everyone = await service.measured_conversations(
        person(), source_id=None, since=date(2026, 9, 20), until=date(2026, 9, 20), cursor=None, limit=50
    )
    assert "voice-1" in [c.conversation_id for c in everyone.items]
    # A vendor person sees only the sources of the binding, whatever the filter asks.
    vendor = person(frozenset({VOICE_SOURCE}))
    mine = await service.measured_conversations(
        vendor, source_id=None, since=date(2026, 9, 1), until=date(2026, 9, 30), cursor=None, limit=50
    )
    assert [c.conversation_id for c in mine.items] == ["voice-1"]
    other = await service.measured_conversations(
        vendor,
        source_id=WHATSAPP_SOURCE,
        since=date(2026, 9, 1),
        until=date(2026, 9, 30),
        cursor=None,
        limit=50,
    )
    assert other.items == []
    with pytest.raises(InvalidInput):
        await service.measured_conversations(
            person(), source_id=None, since=date(2026, 9, 1), until=date(2026, 9, 30), cursor="junk", limit=5
        )
