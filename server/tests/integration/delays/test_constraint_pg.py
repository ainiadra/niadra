"""The live constraint on `scheduled_tasks.reason` against `DelayReason`, on migrated space databases."""

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from niadra.domain.common.ids import uuid7
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.spaces.directory import db_name_for
from niadra.domain.tasks import Task, TaskName
from tests.integration.conftest import SPACE_B, TEMPLATE_DB, CloningUnitOfWork, PgServer, owner_sql

DUE = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)


@pytest.mark.parametrize("database", [TEMPLATE_DB, db_name_for(SPACE_B)])
async def test_the_constraint_allows_exactly_the_reasons_the_code_writes(
    server: PgServer, database: str
) -> None:
    # The template is what every new space starts from; space B went through the provisioner.
    [(definition, validated)] = await owner_sql(
        server,
        database,
        "SELECT pg_get_constraintdef(oid), convalidated FROM pg_constraint "
        "WHERE conname = 'ck_scheduled_tasks_reason'",
    )
    assert validated
    assert set(re.findall(r"'([^']+)'::text", definition)) == {r.value for r in DelayReason}


async def test_every_reason_is_stored_and_any_other_is_refused(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    tasks = {reason: Task(TaskName.EXTRACT, space, {"session_id": reason.value}) for reason in DelayReason}
    async with pg_uow(space) as uow:
        for reason, task in tasks.items():
            await uow.pipeline.schedule(task, DUE, reason)
    async with pg_uow.system(space) as uow:
        rows = await uow.session.execute(text("SELECT id, reason FROM scheduled_tasks"))
        assert dict(rows.tuples().all()) == {task.id: reason.value for reason, task in tasks.items()}
    with pytest.raises(ValueError, match="busy_space"):
        async with pg_uow(space) as uow:
            await uow.pipeline.schedule(Task(TaskName.EXTRACT, space), DUE, "busy_space")  # type: ignore[arg-type]
    # The database refuses it on its own too, whoever writes the row.
    with pytest.raises(IntegrityError, match="ck_scheduled_tasks_reason"):
        async with pg_uow.system(space) as uow:
            await uow.session.execute(
                text(
                    "INSERT INTO scheduled_tasks (id, space_id, task, queue, payload, due_at, reason) "
                    "VALUES (:id, :space, 'extract', 'extraction', '{}', :due, 'busy_space')"
                ),
                {"id": uuid7(), "space": space, "due": DUE},
            )
