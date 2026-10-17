"""A failed statement's error never carries its parameters."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from niadra.adapters.outbound.postgres.engines import make_engine
from tests.integration.conftest import PgServer

CANARY = "canario7731"


async def test_a_failed_statement_does_not_quote_its_parameters(server: PgServer) -> None:
    engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as failed:
                await conn.execute(text("INSERT INTO no_such_table VALUES (:v)"), {"v": CANARY})
    finally:
        await engine.dispose()
    assert CANARY not in str(failed.value)
