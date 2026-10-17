"""`PgMeasureRepository` against PostgreSQL: UNIQUE idempotency, session state and daily counters."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import (
    ContextUseRow,
    DailyCounters,
    DailyKey,
    MeasuredSession,
    RecontactScope,
    TransferState,
)
from niadra.domain.measure.trim import ContextTrim, TrimUnit, UnitCounters, UnitKey
from niadra.domain.privacy.erasure import ErasureScope, TargetKind

T0 = datetime(2026, 9, 22, 14, 7, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c002")


def row(session_id: UUID, profile: UUID, manifest: str = "m1", version: str = "m1/x1", **kw) -> ContextUseRow:
    return ContextUseRow(
        conversation_key="call-1",
        manifest_hash=manifest,
        source_id=SOURCE,
        measure_version=version,
        session_id=session_id,
        profile_id=profile,
        channel="voice",
        view="voice",
        experiment_group="",
        items=(
            {
                "id": "f1",
                "kind": "fact",
                "used": True,
                "repeated": False,
                "contradicted": False,
                "evidence": [],
            },
        ),
        questions=2,
        repeated=1,
        used=1,
        contradicted=0,
        late=False,
        measured_at=kw.pop("at", T0),
        **kw,
    )


def measured(session_id: UUID, profile: UUID, ended: datetime = T0) -> MeasuredSession:
    return MeasuredSession(
        session_id=session_id,
        measure_version="m1/x1",
        conversation_key="call-1",
        source_id=SOURCE,
        channel="voice",
        profile_id=profile,
        experiment_group="",
        day=ended.date(),
        ended_at=ended,
        measured_at=ended,
        no_context=False,
        normalized_category="technical_visit",
        initiated_by="customer",
    )


async def test_rows_are_idempotent_and_readable(pg_uow: PgUnitOfWorkFactory):
    space, session, profile = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        first = await uow.measure.insert_rows(
            [row(session, profile), row(session, profile, "m2", at=T0 + timedelta(seconds=1))]
        )
        again = await uow.measure.insert_rows([row(session, profile)])
    assert (len(first), again) == (2, [])
    async with pg_uow(space) as uow:
        rows = await uow.measure.rows_for_session(session)
        assert [r.manifest_hash for r in rows] == ["m1", "m2"]
        assert rows[0].items[0]["used"] is True
        await uow.measure.mark_transfer(session, SOURCE, unread=True)
        await uow.measure.delete_rows([rows[0]])
    async with pg_uow(space) as uow:
        [left] = await uow.measure.rows_for_conversation("call-1")
        assert (left.manifest_hash, left.transfer_unread) == ("m2", True)
    async with pg_uow(uuid7()) as other:
        assert await other.measure.rows_for_conversation("call-1") == []


async def test_session_state_moves_once(pg_uow: PgUnitOfWorkFactory):
    space, session, profile = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        await uow.measure.put_session(measured(session, profile))
        assert await uow.measure.set_transfer_state(session, TransferState.UNREAD)
        assert not await uow.measure.set_transfer_state(session, TransferState.READ)
        assert await uow.measure.mark_recontact(session, RecontactScope.GENERAL)
        assert await uow.measure.mark_recontact(session, RecontactScope.CATEGORY)
        assert not await uow.measure.mark_recontact(session, RecontactScope.GENERAL)
        await uow.measure.put_session(replace(measured(session, profile), measure_version="m1/x2"))
    async with pg_uow(space) as uow:
        stored = await uow.measure.get_session(session)
        assert stored is not None
        assert (stored.measure_version, stored.transfer_state, stored.recontact_scope) == (
            "m1/x2",
            TransferState.UNREAD,
            RecontactScope.CATEGORY,
        )
        found = await uow.measure.sessions_ended_between(profile, T0 - timedelta(hours=1), T0)
        assert [s.session_id for s in found] == [session]
        assert (
            await uow.measure.sessions_ended_between(
                profile, T0 + timedelta(seconds=1), T0 + timedelta(hours=1)
            )
            == []
        )


async def test_daily_counters_add_and_subtract(pg_uow: PgUnitOfWorkFactory):
    space, other_source = uuid7(), uuid7()
    key = DailyKey(date(2026, 9, 22), SOURCE, "voice", "voice", "")
    async with pg_uow(space) as uow:
        await uow.measure.add_daily(key, DailyCounters(deliveries=2, repeated=3, late_deliveries=1))
        await uow.measure.add_daily(key, DailyCounters(deliveries=1, repeated=-1))
        await uow.measure.add_daily(replace(key, source_id=other_source), DailyCounters(sessions=1))
    async with pg_uow(space) as uow:
        [mine] = await uow.measure.daily(date(2026, 9, 1), date(2026, 9, 30), frozenset({SOURCE}))
        assert (mine.counters.deliveries, mine.counters.repeated, mine.counters.late_deliveries) == (3, 2, 1)
        assert len(await uow.measure.daily(date(2026, 9, 1), date(2026, 9, 30))) == 2
        assert await uow.measure.daily(date(2026, 10, 1), date(2026, 10, 30)) == []


async def test_erasure_follows_the_profile(pg_uow: PgUnitOfWorkFactory):
    space, profile, keep = uuid7(), uuid7(), uuid7()
    scope = ErasureScope(uuid7(), TargetKind.PROFILE, (uuid7(),), profile, None, T0)
    async with pg_uow(space) as uow:
        session = uuid7()
        await uow.measure.insert_rows([row(session, profile), row(uuid7(), keep, "m9")])
        await uow.measure.put_session(measured(session, profile))
        erased = await uow.measure.erase_for(scope, limit=10)
        assert sorted(r.table for r in erased) == ["context_use", "measured_sessions"]
        assert all(r.change_seq for r in erased if r.table == "context_use")
    async with pg_uow(space) as uow:
        assert await uow.measure.get_session(session) is None
        assert [r.profile_id for r in await uow.measure.rows_for_conversation("call-1")] == [keep]
        assert await uow.measure.erase_for(scope, limit=10) == []


async def test_export_reads_rows_by_change_seq(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    async with pg_uow(space) as uow:
        first = row(uuid7(), uuid7())
        await uow.measure.insert_rows([first])
        [exported] = await uow.measure.export_rows("context_use", after_seq=0, upto_seq=2**62, limit=10)
        assert (exported["conversation_key"], exported["manifest_hash"]) == (
            first.conversation_key,
            first.manifest_hash,
        )
        seq = exported["change_seq"]
        assert await uow.measure.export_rows("context_use", after_seq=seq, upto_seq=2**62, limit=10) == []


async def test_units_trims_and_tokens_saved_round_trip(pg_uow: PgUnitOfWorkFactory):
    # What a source leaves unused, counted per unit and day, and its trim.
    space, session, profile = uuid7(), uuid7(), uuid7()
    day = T0.date()
    async with pg_uow(space) as uow:
        await uow.measure.insert_rows([row(session, profile, tokens_saved=42)])
        await uow.measure.add_daily(
            DailyKey(day, SOURCE, "voice", "voice", ""), DailyCounters(trimmed_deliveries=1, tokens_saved=42)
        )
        for offset in range(3):
            key = UnitKey(day - timedelta(days=offset), SOURCE, "voice", "fact:contract")
            await uow.measure.add_units(key, UnitCounters(deliveries=2, used=0))
        await uow.measure.add_units(UnitKey(day, SOURCE, "voice", "episode:visit"), UnitCounters(1, 1, 0))
        await uow.measure.add_units(UnitKey(day, SOURCE, "voice", "episode:visit"), UnitCounters(-1, -1, 0))
        await uow.measure.add_units(UnitKey(day, SOURCE, "chat", "fact:contract"), UnitCounters(9, 0, 0))
    trim = ContextTrim(SOURCE, "voice", (TrimUnit("fact:contract", 4),), T0, 28, 4)
    async with pg_uow(space) as uow:
        totals = await uow.measure.unit_totals(SOURCE, "voice", day - timedelta(days=1), day)
        await uow.measure.put_trim(trim)
        await uow.measure.put_trim(replace(trim, units=(TrimUnit("fact:contract", 6),)))
        [stored] = await uow.measure.rows_for_session(session)
        [daily] = await uow.measure.daily(day, day)
    assert totals == [("episode:visit", UnitCounters(0, 0, 0)), ("fact:contract", UnitCounters(4, 0, 0))]
    assert stored.tokens_saved == 42
    assert (daily.counters.trimmed_deliveries, daily.counters.tokens_saved) == (1, 42)
    async with pg_uow(space) as uow:
        assert await uow.measure.trims() == [replace(trim, units=(TrimUnit("fact:contract", 6),))]
