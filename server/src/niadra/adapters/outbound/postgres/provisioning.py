"""Creating, migrating, dumping, restoring and dropping space databases, as the schema owner.

Everything here uses the direct endpoint (the instance itself), never the pooler: CREATE and DROP
DATABASE cannot run inside a transaction, a migration holds a session advisory lock, and `pg_dump` and
`pg_restore` keep a session for their whole run. When PgBouncer's admin console is configured, a drop
first KILLs the database's pool and a rename PAUSEs it, so the pooler never keeps a server connection
to a database that is going away or changing name.

The provisioning lock and the list of databases go through one session per instance, kept open from one
pass to the next: a pass with nothing to create or drop opens no connection (one per pass would be about
17,000 a day on the 79 connections of the micro instance). Everything else opens a connection for its work
and closes it after.
"""

import asyncio
import contextlib
import os
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from psycopg.conninfo import make_conninfo
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from niadra.adapters.outbound.postgres.schema.platform import SpaceIdentity
from niadra.adapters.outbound.postgres.servers import DRIVER, Servers, libpq_environment
from niadra.domain.spaces.directory import DB_PREFIX, PRIMARY_INSTANCE, SpaceDatabase
from niadra.domain.spaces.stamp import SpaceStamp, StampMismatch, check_stamp
from niadra.ports.provisioning import LockLost

# Alembic keeps the running migration's context in module globals (`alembic.context`, `alembic.op`),
# so two upgrades at once in one process corrupt each other. On 24/09 the first space migration to run
# for real took two sandbox databases of the same wave together and both failed ("No context has been
# configured yet"). Every upgrade in the process takes this lock; the rest of a wave stays concurrent.
_ALEMBIC = threading.Lock()


def upgrade(config: AlembicConfig, target: str) -> None:
    """`alembic upgrade`, one at a time in this process. Blocking: call it through `asyncio.to_thread`."""
    with _ALEMBIC:
        alembic_command.upgrade(config, target)


# Extensions db-init creates as the master user for operations, which the owner restoring a dump may not
# create: RDS keeps pg_stat_statements for rds_superuser. Dumps leave them out; db-init creates them again on
# the next deploy.
OPERATOR_EXTENSIONS = ("pg_stat_statements",)


# The same block `deploy/k8s/charts/niadra/files/db-init.sql` runs for the cell database; a test in
# deploy/ keeps the two identical. The owner creates every table, so default privileges suffice.
DATABASE_GRANTS = """\
REVOKE ALL ON DATABASE {database} FROM PUBLIC;
GRANT CONNECT ON DATABASE {database} TO niadra_app, niadra_system;
GRANT USAGE ON SCHEMA public TO niadra_app, niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO niadra_app;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO niadra_app, niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO niadra_app, niadra_system;
"""

# The roles of a cell as the chart's db-init.sql leaves them, for a server of our own (the tests, local
# development): the owner creates databases and may end the other roles' sessions.
ROLES = """\
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['niadra_owner', 'niadra_app', 'niadra_system'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r);
    END IF;
  END LOOP;
END $$;
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_worker') THEN
    CREATE ROLE niadra_worker LOGIN;
  END IF;
END $$;
GRANT niadra_app TO niadra_worker;
ALTER ROLE niadra_owner CREATEDB;
ALTER ROLE niadra_system BYPASSRLS;
ALTER ROLE niadra_app SET transaction_timeout = '60s';
ALTER ROLE niadra_worker SET role = 'niadra_app';
ALTER ROLE niadra_worker SET transaction_timeout = '60s';
ALTER ROLE niadra_system SET transaction_timeout = '300s';
GRANT pg_signal_backend TO niadra_owner;
"""

# What the space history grants on its SECURITY DEFINER functions and takes from the application role on
# `events`. Dumps carry no privileges, so a restore applies this again; keep it in line with the
# latest revision that changes those grants. One statement per line: `statements` splits on ";\n".
DEFINER_GRANTS = """\
REVOKE EXECUTE ON FUNCTION ensure_week_partition(regclass, bigint), create_hash_partitions(regclass, int) FROM PUBLIC, niadra_app;
GRANT EXECUTE ON FUNCTION ensure_week_partition(regclass, bigint), create_hash_partitions(regclass, int) TO niadra_system;
DO $$ DECLARE part regclass; BEGIN REVOKE UPDATE, DELETE ON events FROM niadra_app; FOR part IN SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent = 'events'::regclass LOOP EXECUTE format('REVOKE UPDATE, DELETE ON %s FROM niadra_app', part); END LOOP; END $$;
"""

# One reconciler or setup runner at a time per cell, whatever the number of `loops` replicas.
PROVISIONING_LOCK = 0x4E49_4144_5052_4F56
# Per space database: one migration at a time (advisory locks are per database).
MIGRATION_LOCK = "hashtext('niadra_migrate')"
DUMP_CHUNK = 1024 * 1024
RENAME_ATTEMPTS = 50
APPLICATION_NAME = "niadra-provisioner"
LIBPQ_PARAMETERS = {
    "PGHOST": "host",
    "PGPORT": "port",
    "PGUSER": "user",
    "PGPASSWORD": "password",
    "PGDATABASE": "dbname",
    "PGSSLMODE": "sslmode",
}


@dataclass(frozen=True, slots=True)
class Lease:
    """How the provisioning lock stays a lease on its session. While the lock is held, the holder asks the
    server something every `beat_s`; the server ends the session once it has heard nothing for `idle_s`
    (`idle_session_timeout`), which lets the lock go; and a statement not answered within `answer_s` cuts
    the session, so the holder stops (`LockLost`) at most `beat_s + answer_s` after the last answer, before
    the server lets the lock go. Without it, a session that went silent kept the lock for as long as the
    server kept the session: the silence broke no TCP connection, so for good."""

    beat_s: float = 5.0
    answer_s: float = 5.0
    idle_s: float = 15.0

    def __post_init__(self) -> None:
        if self.beat_s + self.answer_s >= self.idle_s:
            raise ValueError("the holder must give up the lock before the server lets it go")


def statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";\n") if s.strip()]


class AlembicHistory:
    """`SchemaHistory` over the `space` section of alembic.ini."""

    SECTION = "space"

    def __init__(self, ini: Path) -> None:
        self.ini = ini
        self._script = ScriptDirectory.from_config(AlembicConfig(str(ini), ini_section=self.SECTION))

    def head(self) -> str:
        head = self._script.get_current_head()
        if head is None:
            raise RuntimeError("the space history has no revision")
        return head

    def reached(self, current: str | None, revision: str) -> bool:
        if current is None:
            return False
        return any(script.revision == revision for script in self._script.iterate_revisions(current, "base"))

    def config(self, url: URL, hash_modulus: int) -> AlembicConfig:
        # The URL goes through the attributes, never the process environment: two databases migrate at
        # once, and the password never lands in an ini value where `%` would need escaping.
        config = AlembicConfig(str(self.ini), ini_section=self.SECTION)
        config.attributes["url"] = url
        config.attributes["hash_modulus"] = hash_modulus
        return config


class PgSpaceDatabaseAdmin:
    """`SpaceDatabaseAdmin` on PostgreSQL, as `niadra_owner` over the direct endpoint."""

    def __init__(self, servers: Servers, history: AlembicHistory, lease: Lease | None = None) -> None:
        self._servers = servers
        self._history = history
        self._lease = lease or Lease()
        self._sessions: dict[str, psycopg.AsyncConnection[Any]] = {}
        self._leading = asyncio.Lock()

    @property
    def history(self) -> AlembicHistory:
        return self._history

    @property
    def instances(self) -> Sequence[str]:
        return [PRIMARY_INSTANCE, *sorted(self._servers.instances)]

    async def close(self) -> None:
        for instance in list(self._sessions):
            session = self._sessions.pop(instance)
            with contextlib.suppress(Exception):
                async with asyncio.timeout(self._lease.answer_s):
                    await session.close()

    @contextlib.asynccontextmanager
    async def leader(self) -> AsyncIterator[bool]:
        if self._leading.locked():
            # An advisory lock is re-entrant on its session: a second holder in this process would get it too.
            yield False
            return
        async with self._leading:
            session, rows = await self._ask(
                PRIMARY_INSTANCE, "SELECT pg_try_advisory_lock(%s)", (PROVISIONING_LOCK,)
            )
            if not rows[0][0]:
                yield False
                return
            holder = asyncio.current_task()
            assert holder is not None
            done, lost = asyncio.Event(), asyncio.Event()
            beat = asyncio.create_task(self._beat(session, holder, done, lost))
            failure: BaseException | None = None
            try:
                yield True
            except BaseException as exc:
                failure = exc
            done.set()
            # The beat cancels the holder when the session stops answering; that cancellation becomes LockLost.
            mine = lost.is_set() and holder.uncancel() == 0
            await asyncio.wait({beat})
            if not lost.is_set():
                # `unlock_all`: the lock is re-entrant, and nothing else takes an advisory lock on this session.
                with contextlib.suppress(Exception):
                    await self._answer(PRIMARY_INSTANCE, session, "SELECT pg_advisory_unlock_all()")
            if mine:
                raise LockLost("the session holding the provisioning lock stopped answering") from failure
            if failure is not None:
                raise failure

    async def _beat(
        self,
        session: psycopg.AsyncConnection[Any],
        holder: asyncio.Task[Any],
        done: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), self._lease.beat_s)
            if done.is_set():
                return
            try:
                await self._answer(PRIMARY_INSTANCE, session, "SELECT 1")
            except Exception:
                lost.set()
                if not done.is_set():
                    holder.cancel()
                return

    async def create_database(self, entry: SpaceDatabase) -> bool:
        return await self.create_empty_database(entry.instance, entry.db_name)

    async def create_empty_database(self, instance: str, db_name: str) -> bool:
        name = _identifier(db_name)
        async with self._inside(instance, self._home(instance)) as conn:
            exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
            if not exists:
                await conn.execute(
                    text(f"CREATE DATABASE {name} OWNER niadra_owner TEMPLATE template0 ENCODING 'UTF8'")
                )
        async with self._inside(instance, name) as conn:
            for statement in statements(DATABASE_GRANTS.format(database=name)):
                await conn.execute(text(statement))
        return not exists

    async def migrate(self, entry: SpaceDatabase, target: str | None = None) -> str:
        name = _identifier(entry.db_name)
        url = self._url(entry.instance, name)
        async with self._inside(entry.instance, name) as lock:
            await lock.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK})"))
            try:
                config = self._history.config(url, entry.hash_modulus)
                await asyncio.to_thread(upgrade, config, target or "head")
                version = await _version(lock)
            finally:
                await lock.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})"))
        if version is None:
            raise RuntimeError(f"{name} has no schema version after the migration")
        return version

    async def ensure_week_partitions(
        self, entry: SpaceDatabase, parents: Sequence[str], week_starts_ms: Sequence[int]
    ) -> None:
        async with self._inside(entry.instance, _identifier(entry.db_name)) as conn:
            for parent in parents:
                for start in week_starts_ms:
                    await conn.execute(
                        text("SELECT ensure_week_partition(CAST(:parent AS regclass), :start)"),
                        {"parent": _identifier(parent), "start": start},
                    )

    async def schema_version(self, entry: SpaceDatabase) -> str | None:
        async with self._inside(entry.instance, _identifier(entry.db_name)) as conn:
            return await _version(conn)

    async def write_stamp(self, entry: SpaceDatabase, stamp: SpaceStamp) -> None:
        name = _identifier(entry.db_name)
        async with self._inside(entry.instance, name) as conn:
            await conn.execute(
                text(
                    "INSERT INTO space_identity (one, space_id, tenant_id, region, environment, cell_id, "
                    "hash_modulus) VALUES (true, :space_id, :tenant_id, :region, :environment, :cell_id, "
                    ":hash_modulus) ON CONFLICT (one) DO NOTHING"
                ),
                {
                    "space_id": stamp.space_id,
                    "tenant_id": stamp.tenant_id,
                    "region": stamp.region,
                    "environment": stamp.environment,
                    "cell_id": stamp.cell_id,
                    "hash_modulus": stamp.hash_modulus,
                },
            )
            written = await _stamp(conn)
        check_stamp(stamp.space_id, name, written)

    async def read_stamp(self, instance: str, db_name: str) -> SpaceStamp | None:
        async with self._inside(instance, _identifier(db_name)) as conn:
            return await _stamp(conn)

    async def drop_database(self, instance: str, db_name: str) -> None:
        name = _identifier(db_name)
        await self._pooler(f"KILL {name}", missing_ok=True)
        async with self._inside(instance, self._home(instance)) as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))

    async def databases(self, instance: str) -> list[str]:
        _, rows = await self._ask(
            instance,
            "SELECT datname FROM pg_database WHERE datname LIKE %s ORDER BY datname",
            (DB_PREFIX.replace("_", r"\_") + "%",),
        )
        return [str(name) for (name,) in rows]

    async def rename_database(self, instance: str, old: str, new: str) -> None:
        old, new = _identifier(old), _identifier(new)
        await self._pooler(f"PAUSE {old}", missing_ok=True)
        async with self._inside(instance, self._home(instance)) as conn:
            # A terminated backend takes a moment to exit, and the rename refuses while any is still there.
            for _ in range(RENAME_ATTEMPTS):
                left = await conn.scalar(
                    text(
                        "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                        "WHERE datname = :n AND pid <> pg_backend_pid()"
                    ),
                    {"n": old},
                )
                if not left:
                    break
                await asyncio.sleep(0.2)
            await conn.execute(text(f"ALTER DATABASE {old} RENAME TO {new}"))

    async def resume(self, db_name: str) -> None:
        await self._pooler(f"RESUME {_identifier(db_name)}", missing_ok=True)

    async def dump(self, instance: str, db_name: str) -> AsyncIterator[bytes]:
        env = libpq_environment(self._url(instance, _identifier(db_name)).set(drivername="postgresql"))
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            *(f"--exclude-extension={name}" for name in OPERATOR_EXTENSIONS),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_process_env(env),
        )
        assert process.stdout is not None
        assert process.stderr is not None
        errors = asyncio.ensure_future(process.stderr.read())
        try:
            while chunk := await process.stdout.read(DUMP_CHUNK):
                yield chunk
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise
        finally:
            code = await process.wait()
            stderr = (await errors).decode(errors="replace")
        if code != 0:
            raise RuntimeError(f"pg_dump of {db_name} failed ({code}): {stderr[-500:]}")

    async def restore(self, instance: str, db_name: str, dump: Path) -> None:
        env = libpq_environment(self._url(instance, _identifier(db_name)).set(drivername="postgresql"))
        process = await asyncio.create_subprocess_exec(
            "pg_restore",
            "--jobs=2",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            f"--dbname={env['PGDATABASE']}",
            str(dump),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=_process_env(env),
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"pg_restore into {db_name} failed ({process.returncode}): "
                f"{stderr.decode(errors='replace')[-500:]}"
            )
        async with self._inside(instance, _identifier(db_name)) as conn:
            # A space database: the dump carried no privileges, so its definer functions get theirs again.
            if await conn.scalar(text("SELECT to_regprocedure('ensure_week_partition(regclass,bigint)')")):
                for statement in statements(DEFINER_GRANTS):
                    await conn.execute(text(statement))

    async def size_bytes(self, instance: str, db_name: str) -> int:
        async with self._inside(instance, self._home(instance)) as conn:
            size = await conn.scalar(text("SELECT pg_database_size(:n)"), {"n": _identifier(db_name)})
            return int(size or 0)

    async def count_rows(self, instance: str, db_name: str, tables: Sequence[str]) -> dict[str, int]:
        async with self._inside(instance, _identifier(db_name)) as conn:
            return {
                table: int(await conn.scalar(text(f"SELECT count(*) FROM {_identifier(table)}")) or 0)
                for table in tables
            }

    # Internals.

    def _url(self, instance: str, database: str) -> URL:
        return self._servers.url("owner", database, direct=True, instance=instance)

    def _home(self, instance: str) -> str:
        """A database that always exists: the cell database on the cell's own instance, `postgres` on the
        others. CREATE, DROP and ALTER DATABASE run from there."""
        return self._servers.cell_database if instance == PRIMARY_INSTANCE else "postgres"

    async def _ask(
        self, instance: str, statement: str, params: Sequence[object] = ()
    ) -> tuple[psycopg.AsyncConnection[Any], list[tuple[Any, ...]]]:
        """One statement on the instance's session. A session kept from an earlier pass that fails (the
        server ended it after `idle_s` without a statement, or it stopped answering) is replaced once."""
        for attempt in (1, 2):
            session = self._sessions.get(instance)
            kept = session is not None and not session.closed
            if session is None or session.closed:
                session = await self._open_session(instance)
            try:
                return session, await self._answer(instance, session, statement, params)
            except psycopg.OperationalError:
                if not kept or attempt == 2:
                    raise
        raise AssertionError("unreachable")

    async def _open_session(self, instance: str) -> psycopg.AsyncConnection[Any]:
        env = libpq_environment(self._url(instance, self._home(instance)))
        session = await psycopg.AsyncConnection.connect(
            make_conninfo(**{LIBPQ_PARAMETERS[name]: value for name, value in env.items()}),
            autocommit=True,
            application_name=APPLICATION_NAME,
            connect_timeout=max(2, round(self._lease.answer_s)),
            options=f"-c idle_session_timeout={round(self._lease.idle_s * 1000)}",
        )
        self._sessions[instance] = session
        return session

    async def _answer(
        self,
        instance: str,
        session: psycopg.AsyncConnection[Any],
        statement: str,
        params: Sequence[object] = (),
    ) -> list[tuple[Any, ...]]:
        """A statement answered within `answer_s`, or the session is cut: its socket closed, so neither end
        waits on it again. (psycopg's own cancellation of a statement waits up to 10 s more.)"""

        async def run() -> list[tuple[Any, ...]]:
            cursor = await session.execute(statement, params)
            return await cursor.fetchall()

        asked = asyncio.ensure_future(run())
        try:
            done, _ = await asyncio.wait({asked}, timeout=self._lease.answer_s)
        finally:
            if not asked.done():
                self._cut(instance, session)
                asked.cancel()
                await asyncio.wait({asked})
                if not asked.cancelled():
                    asked.exception()  # retrieved: the cut is what failed it
        if not done:
            raise psycopg.OperationalError(f"no answer from the database within {self._lease.answer_s} s")
        if asked.exception() is not None:
            self._cut(instance, session)
        return asked.result()

    def _cut(self, instance: str, session: psycopg.AsyncConnection[Any]) -> None:
        if self._sessions.get(instance) is session:
            del self._sessions[instance]
        session.pgconn.finish()

    @contextlib.asynccontextmanager
    async def _inside(self, instance: str, database: str) -> AsyncIterator[AsyncConnection]:
        """A one-off autocommit connection to one database, closed on exit."""
        engine = create_async_engine(
            self._url(instance, database),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={"prepare_threshold": None, "application_name": APPLICATION_NAME},
        )
        try:
            async with engine.connect() as conn:
                yield conn
        finally:
            await engine.dispose()

    async def _pooler(self, command: str, *, missing_ok: bool) -> None:
        admin = self._servers.pooler_admin
        if admin is None:
            return
        try:
            async with await psycopg.AsyncConnection.connect(
                host=admin.host,
                port=admin.port,
                user="niadra_owner",
                password=self._servers.passwords.get("owner") or "",
                dbname="pgbouncer",
                autocommit=True,
                cursor_factory=psycopg.AsyncClientCursor,
            ) as conn:
                await conn.execute(command)
        except psycopg.Error as exc:
            # KILL or PAUSE of a database the pooler never opened answers "no such database".
            if missing_ok and "no such database" in str(exc).lower():
                return
            raise


async def _version(conn: AsyncConnection) -> str | None:
    if not await conn.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")):
        return None
    version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return None if version is None else str(version)


async def _stamp(conn: AsyncConnection) -> SpaceStamp | None:
    if not await conn.scalar(text("SELECT to_regclass('public.space_identity') IS NOT NULL")):
        return None
    row = (await conn.execute(select(SpaceIdentity))).one_or_none()
    if row is None:
        return None
    return SpaceStamp(row.space_id, row.tenant_id, row.region, row.environment, row.cell_id, row.hash_modulus)


def _identifier(name: str) -> str:
    """Database and table names reach SQL as identifiers, so only plain lowercase names pass."""
    plain = all(c.isascii() and (c.islower() or c.isdigit() or c == "_") for c in name)
    if not name or len(name) > 63 or not plain:
        raise ValueError(f"not a plain identifier: {name!r}")
    return name


def _process_env(env: dict[str, str]) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", **env}


__all__ = [
    "APPLICATION_NAME",
    "DATABASE_GRANTS",
    "DEFINER_GRANTS",
    "DRIVER",
    "ROLES",
    "AlembicHistory",
    "Lease",
    "PgSpaceDatabaseAdmin",
    "StampMismatch",
    "statements",
]
