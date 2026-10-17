"""Alembic environment for the cell database (`niadra_cell`), run with the async psycopg driver.

The URL comes, in order, from `config.attributes["url"]` (the setup runner), the `sqlalchemy.url`
option, then NIADRA_CELL_DATABASE_URL or NIADRA_DATABASE_URL (the command line).
"""

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from niadra.adapters.outbound.postgres.schema import CellBase

target_metadata = CellBase.metadata


def _url() -> Any:
    given = context.config.attributes.get("url") or context.config.get_main_option("sqlalchemy.url")
    if not given:
        given = os.environ.get("NIADRA_CELL_DATABASE_URL") or os.environ["NIADRA_DATABASE_URL"]
    url = make_url(given)
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
