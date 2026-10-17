"""The workers' login, `niadra_worker`: its own pool in PgBouncer, the application role in the database."""

import dataclasses

from sqlalchemy import text

from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.spaces.directory import db_name_for
from tests.integration.conftest import SPACE_A, SPACE_B, CloningUnitOfWork, PgServer
from tests.integration.memory.test_pg_memory import fact


async def test_the_worker_login_is_the_application_role_under_the_same_policies(
    server: PgServer, pg_uow: CloningUnitOfWork
) -> None:
    ana = fact(uuid7(), "Ana Lima")
    async with pg_uow(SPACE_A) as uow:
        await uow.memory.insert_fact(ana, normalize_value(ana.value))

    worker = dataclasses.replace(server.servers, app_login="niadra_worker")
    engine = make_engine(worker.url("app", db_name_for(SPACE_A)), pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as conn:
            who = (await conn.execute(text("SELECT session_user, current_user"))).one()
            assert tuple(who) == ("niadra_worker", "niadra_app")
            timeout = (await conn.execute(text("SELECT current_setting('transaction_timeout')"))).scalar_one()
            assert timeout == "1min"
            count = text("SELECT count(*) FROM facts WHERE fact_id = :f")
            await conn.execute(text("SELECT set_config('niadra.space_id', :s, true)"), {"s": str(SPACE_B)})
            assert (await conn.execute(count, {"f": ana.fact_id})).scalar_one() == 0
            await conn.execute(text("SELECT set_config('niadra.space_id', :s, true)"), {"s": str(SPACE_A)})
            assert (await conn.execute(count, {"f": ana.fact_id})).scalar_one() == 1
    finally:
        await engine.dispose()


def test_only_the_application_role_changes_its_login(server: PgServer) -> None:
    worker = dataclasses.replace(server.servers, app_login="niadra_worker")
    assert worker.url("app", "s_x").username == "niadra_worker"
    assert worker.url("system", "s_x").username == "niadra_system"
    assert worker.url("owner", "s_x", direct=True).username == "niadra_owner"
    assert server.servers.url("app", "s_x").username == "niadra_app"
