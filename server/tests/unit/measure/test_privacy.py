"""Measurement rows follow the erasure of their profile or conversation (19.6, 8.4) and feed the export."""

from datetime import UTC, datetime
from uuid import UUID

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import ContextUseRow, DailyCounters, DailyKey, MeasuredSession
from niadra.domain.privacy.erasure import ErasureScope, TargetKind

T0 = datetime(2026, 9, 22, 14, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c002")


def row(conversation: str, profile: UUID) -> ContextUseRow:
    return ContextUseRow(
        conversation, "m1", SOURCE, "m2/x1", uuid7(), profile, "voice", "voice", "", (), 1, 0, 1, 0, False, T0
    )


def session(conversation: str, profile: UUID) -> MeasuredSession:
    return MeasuredSession(
        uuid7(), "m2/x1", conversation, SOURCE, "voice", profile, "", T0.date(), T0, T0, False
    )


async def test_a_profile_erasure_takes_its_rows_and_sessions_but_not_the_aggregates() -> None:
    repo = InMemoryMeasureRepository(MemoryStore())
    marina, other = uuid7(), uuid7()
    await repo.insert_rows([row("call-1", marina), row("call-2", other)])
    await repo.put_session(session("call-1", marina))
    key = DailyKey(T0.date(), SOURCE, "voice", "voice", "")
    await repo.add_daily(key, DailyCounters(deliveries=1))
    target = ErasureScope(uuid7(), TargetKind.PROFILE, (uuid7(),), marina, None, T0)
    assert sorted(r.table for r in await repo.erase_for(target, limit=10)) == [
        "context_use",
        "measured_sessions",
    ]
    assert await repo.rows_for_conversation("call-1") == []
    assert len(await repo.rows_for_conversation("call-2")) == 1
    [daily] = await repo.daily(T0.date(), T0.date())
    assert daily.counters.deliveries == 1


async def test_a_conversation_erasure_takes_that_conversation_only() -> None:
    repo = InMemoryMeasureRepository(MemoryStore())
    marina = uuid7()
    await repo.insert_rows([row("call-1", marina), row("call-2", marina)])
    target = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), marina, "call-1", T0)
    assert [r.key["conversation_key"] for r in await repo.erase_for(target, limit=10)] == ["call-1"]
    assert await repo.erase_for(target.with_events((uuid7(),)), limit=10) == []


async def test_export_rows_follow_transfer_marks() -> None:
    repo = InMemoryMeasureRepository(MemoryStore())
    first = row("call-1", uuid7())
    await repo.insert_rows([first])
    await repo.mark_transfer(first.session_id, SOURCE, unread=True)
    [exported] = await repo.export_rows("context_use", after_seq=0, upto_seq=10**6, limit=10)
    assert (exported["conversation_key"], exported["transfer_unread"]) == ("call-1", True)
