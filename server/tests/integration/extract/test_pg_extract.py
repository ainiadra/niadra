from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import (
    EpisodeDraft,
    ExtractionResult,
    ExtractionRun,
    FactCommand,
    FactOp,
    RunStatus,
)

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)


def run(
    session_id, status: RunStatus, tokens: int, at: datetime, result: ExtractionResult | None = None
) -> ExtractionRun:  # type: ignore[no-untyped-def]
    return ExtractionRun(
        session_id,
        "x1.p1.s1",
        status,
        at,
        model="m",
        provider="p",
        input_tokens=tokens,
        output_tokens=tokens,
        cost_micros=tokens * 10,
        result=result,
    )


async def test_runs_accumulate_cost_and_keep_the_result_sealed(pg_uow: PgUnitOfWorkFactory) -> None:
    space, session = uuid7(), uuid7()
    result = ExtractionResult(
        episode=EpisodeDraft("Cliente Marina contestou", "complaint", "invoice", "pending"),
        facts=(FactCommand(FactOp.ADD, "preferred_name", (uuid7(),), value="Marina", valid_at=T0),),
    )
    async with pg_uow(space) as uow:
        await uow.extract.save_run(run(session, RunStatus.INVALID, 100, T0))
        await uow.extract.save_run(run(session, RunStatus.OK, 50, T0 + timedelta(seconds=1), result))
    async with pg_uow(space) as uow:
        found = await uow.extract.get_run(session, "x1.p1.s1")
        latest = await uow.extract.latest_run(session)
        stored = text("SELECT result_ct FROM extraction_runs WHERE session_id = :s")
        raw = (await uow.session.execute(stored, {"s": session})).scalar_one()
    assert found is not None
    assert (found.status, found.input_tokens, found.cost_micros) == (RunStatus.OK, 150, 1500)
    assert found.result == result
    assert latest == found
    assert b"Marina" not in raw


async def test_spend_adds_up_per_day_and_purpose(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    day = date(2026, 9, 22)
    async with pg_uow(space) as uow:
        await uow.extract.add_spend(day, "extraction", 300)
        await uow.extract.add_spend(day, "extraction", 250)
        await uow.extract.add_spend(day, "assistant", 5)
    async with pg_uow(space) as uow:
        assert await uow.extract.spent(day, "extraction") == 550
        assert await uow.extract.spent(day + timedelta(days=1), "extraction") == 0
        calls = (
            await uow.session.execute(text("SELECT calls FROM llm_spend WHERE purpose = 'extraction'"))
        ).scalar_one()
    assert calls == 2


async def test_the_budget_notice_is_marked_once_per_day_and_never_counts_as_spend(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid7()
    day = date(2026, 9, 22)
    async with pg_uow(space) as uow:
        assert await uow.extract.mark_budget_notice(day, "extraction")
        assert not await uow.extract.mark_budget_notice(day, "extraction")
        assert await uow.extract.mark_budget_notice(day + timedelta(days=1), "extraction")
        assert await uow.extract.spent(day, "extraction") == 0


async def test_a_run_records_when_memory_applied_it_and_saving_it_again_clears_that(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    """What ingest status compares with the profile's `compile` counter (7.1)."""
    space, session, profile = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        await uow.extract.save_run(run(session, RunStatus.OK, 10, T0))
        fresh = await uow.extract.latest_run(session)
        await uow.extract.mark_applied(session, "x1.p1.s1", profile, 7)
        applied = await uow.extract.latest_run(session)
    async with pg_uow(space) as uow:
        await uow.extract.save_run(run(session, RunStatus.OK, 10, T0 + timedelta(seconds=1)))
        again = await uow.extract.latest_run(session)
    marks = [(r.applied_profile_id, r.applied_work) if r else r for r in (fresh, applied, again)]
    assert marks == [(None, None), (profile, 7), (None, None)]
