"""Alembic environment for one space database (`s_<space id hex>`), run with the async psycopg driver.

The provisioner and the setup runner apply this history to every space database; the URL comes, in
order, from `config.attributes["url"]`, the `sqlalchemy.url` option, then NIADRA_SPACE_DATABASE_URL
(the command line). `config.attributes["hash_modulus"]` sets the hash partitions of a new database.
"""

import asyncio
import os
import re
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from niadra.adapters.outbound.postgres.schema import SpaceBase

target_metadata = SpaceBase.metadata

# Partition children are created by the migrations, not declared as models.
_PARTITION = re.compile(r"_(p\d+|default|w\d+)$")


def _include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    table = name if type_ == "table" else parent_names.get("table_name") if type_ == "index" else None
    return not (table and _PARTITION.search(table))


def _url() -> Any:
    given = context.config.attributes.get("url") or context.config.get_main_option("sqlalchemy.url")
    if not given:
        given = os.environ["NIADRA_SPACE_DATABASE_URL"]
    url = make_url(given)
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=_include_name,
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
