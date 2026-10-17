"""`PgAssistRepository` against PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.configure.model import AssistRun, AssistStatus, AssistTask

T0 = datetime(2026, 9, 22, 10, tzinfo=UTC)


async def test_runs_are_stored_updated_and_counted(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    run = AssistRun(
        uuid7(),
        AssistTask.WEBHOOK_MAPPING,
        AssistStatus.QUEUED,
        uuid7(),
        T0,
        proposal={"request": {"source_id": None, "description": "<name> quer mapear"}},
    )
    async with pg_uow(space) as uow:
        await uow.configure.add_run(run)
        assert await uow.configure.count_since(T0) == 1
    proposed = replace(
        run,
        status=AssistStatus.PROPOSED,
        sample_ids=("e1", "e2"),
        diff_hash="ab",
        proposal={**run.proposal, "document": [{"version": "v1"}]},
    )
    async with pg_uow(space) as uow:
        await uow.configure.save_run(proposed)
    async with pg_uow(space) as uow:
        assert await uow.configure.get_run(run.run_id) == proposed
        assert await uow.configure.count_since(datetime(2026, 10, 1, tzinfo=UTC)) == 0
    async with pg_uow(uuid7()) as other:
        assert await other.configure.get_run(run.run_id) is None


async def test_runs_are_listed_newest_first_with_a_keyset(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    runs = [
        AssistRun(uuid7(), AssistTask.WEBHOOK_MAPPING, AssistStatus.QUEUED, uuid7(), T0) for _ in range(3)
    ]
    async with pg_uow(space) as uow:
        for run in runs:
            await uow.configure.add_run(run)
    async with pg_uow(space) as uow:
        first = await uow.configure.list_runs(before_id=None, limit=2)
        rest = await uow.configure.list_runs(before_id=first[-1].run_id, limit=10)
    assert [r.run_id for r in first + rest] == [r.run_id for r in reversed(runs)]
    async with pg_uow(uuid7()) as other:
        assert await other.configure.list_runs(before_id=None, limit=10) == []
