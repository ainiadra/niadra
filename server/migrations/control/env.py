"""Alembic environment for the control database, run with the async psycopg driver.

The URL comes, in order, from `config.attributes["url"]` (the setup runner), the `sqlalchemy.url`
option, then NIADRA_CONTROL_DATABASE_URL (the command line).
"""

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from niadra.adapters.outbound.postgres.control_schema import Base

target_metadata = Base.metadata


# Its own version table, so the control and cell histories never collide in one database.
VERSION_TABLE = "alembic_version_control"


def _url() -> Any:
    given = context.config.attributes.get("url") or context.config.get_main_option("sqlalchemy.url")
    url = make_url(given or os.environ["NIADRA_CONTROL_DATABASE_URL"])
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
        version_table=VERSION_TABLE,
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
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, version_table=VERSION_TABLE
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
