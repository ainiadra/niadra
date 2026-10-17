"""Spec 12, tests 1 and 2: a space's rows live in its own database, and a mis-wired directory fails."""

from collections.abc import Sequence
from uuid import UUID

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, db_name_for
from niadra.domain.spaces.stamp import StampMismatch
from tests.integration.conftest import (
    CELL_DB,
    SPACE_A,
    SPACE_B,
    CellStack,
    CloningUnitOfWork,
    PgServer,
    owner_sql,
)
from tests.integration.memory.test_pg_memory import fact


async def test_a_space_s_rows_live_in_its_own_database_only(
    server: PgServer, pg_uow: CloningUnitOfWork
) -> None:
    marina = fact(uuid7(), "Marina Souza")
    async with pg_uow(SPACE_A) as uow:
        await uow.memory.insert_fact(marina, normalize_value(marina.value))
        ciphertext, version = (
            await uow.session.execute(
                text("SELECT value_ct, dek_version FROM facts WHERE fact_id = :f"), {"f": marina.fact_id}
            )
        ).one()

    databases = {name for (name,) in await owner_sql(server, CELL_DB, "SELECT datname FROM pg_database")}
    assert {db_name_for(SPACE_A), db_name_for(SPACE_B)} <= databases
    count = "SELECT count(*) FROM facts WHERE fact_id = :f"
    assert await owner_sql(server, db_name_for(SPACE_A), count, f=marina.fact_id) == [(1,)]
    assert await owner_sql(server, db_name_for(SPACE_B), count, f=marina.fact_id) == [(0,)]

    # A session of B that forges A's id finds nothing: A's rows are not in B's database at all.
    async with pg_uow(SPACE_B) as uow:
        await uow.session.execute(text("SELECT set_config('niadra.space_id', :a, true)"), {"a": str(SPACE_A)})
        assert (await uow.session.execute(text("SELECT count(*) FROM facts"))).scalar_one() == 0

    # The ciphertext opens only under A's key: B's key refuses it, whatever the place it is read from.
    aad = {"table": "facts", "row": marina.fact_id, "column": "value_ct"}
    assert await Sealer(pg_uow._keys, SPACE_A).open_text(ciphertext, version, **aad) == "Marina Souza"
    await Sealer(pg_uow._keys, SPACE_B).seal(
        "B's own key exists", table="facts", row=uuid7(), column="value_ct"
    )
    with pytest.raises(InvalidTag):
        await Sealer(pg_uow._keys, SPACE_B).open_text(ciphertext, version, **aad)


async def test_row_level_security_still_hides_a_stray_row_inside_a_space_database(
    server: PgServer, pg_uow: CloningUnitOfWork
) -> None:
    """The belt under the database boundary: a row of A that ended up in B's database stays invisible to
    B's sessions, which connect as `niadra_app` with B pinned."""
    planted = "INSERT INTO blocked_values (space_id, type, value_hmac) VALUES (:a, 'stray-test', '\\x01')"
    await owner_sql(server, db_name_for(SPACE_B), planted, a=SPACE_A)
    try:
        async with pg_uow(SPACE_B) as uow:
            seen = await uow.session.execute(
                text("SELECT count(*) FROM blocked_values WHERE type = 'stray-test'")
            )
            assert seen.scalar_one() == 0
            assert (await uow.session.execute(text("SELECT current_user"))).scalar_one() == "niadra_app"
        async with pg_uow.system(SPACE_B) as uow:  # the maintenance role bypasses it, and sees the row
            seen = await uow.session.execute(
                text("SELECT count(*) FROM blocked_values WHERE type = 'stray-test'")
            )
            assert seen.scalar_one() == 1
    finally:
        await owner_sql(server, db_name_for(SPACE_B), "DELETE FROM blocked_values WHERE type = 'stray-test'")


async def test_a_directory_row_pointing_at_another_space_s_database_fails_and_caches_nothing(
    server: PgServer, pg_stack: CellStack
) -> None:
    rows = {r.space_id: r for r in await _rows(pg_stack)}
    wrong = rows[SPACE_A].moved(DbStatus.READY, db_name=rows[SPACE_B].db_name)

    async def mis_wired() -> Sequence[SpaceDatabase]:
        return [wrong]

    registry = EngineRegistry(server.servers, pg_stack.cell_engine, reader=mis_wired)
    try:
        await registry.refresh()
        for _ in range(2):  # a second attempt checks again rather than trusting a cached pool
            with pytest.raises(StampMismatch, match="belongs to another space"):
                async with registry.lease(SPACE_A, "app"):
                    pass
        assert registry.active_spaces() == []
    finally:
        await registry.close()


async def test_idle_pools_are_closed_and_reopened_on_demand(server: PgServer, pg_stack: CellStack) -> None:
    clock = [0.0]
    registry = EngineRegistry(server.servers, pg_stack.cell_engine, monotonic=lambda: clock[0])
    try:
        async with registry.lease(SPACE_A, "app") as engine:
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT current_database()"))).scalar_one() == db_name_for(
                    SPACE_A
                )
            clock[0] = 500.0
            assert await registry.sweep() == 0  # in use: never closed under a transaction
        assert registry.active_spaces() == [SPACE_A]
        clock[0] = 1000.0
        assert await registry.sweep() == 1
        assert registry.active_spaces() == []
        async with registry.lease(SPACE_A, "system"):
            pass
        assert registry.active_spaces() == [SPACE_A]
    finally:
        await registry.close()


async def _rows(stack: CellStack) -> list[SpaceDatabase]:
    async with stack.uow.cell() as uow:
        return await uow.directory.entries()


def test_the_fixed_spaces_are_distinct() -> None:
    assert len({SPACE_A, SPACE_B, UUID(int=0)}) == 3
