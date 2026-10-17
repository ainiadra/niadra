"""Integration tests run against a real PostgreSQL 17: `scripts/test-db.sh start` prints the DSN.

The session builds a cell as production does, one database per space: the roles of `db-init.sql`
(`niadra_owner`, `niadra_app`, `niadra_system`), a cell database `niadra_test_cell` on the cell history,
and two space databases, A (sandbox) and B (production), provisioned through `Provisioner.create()`.
`pg_uow` is the real factory over a real `EngineRegistry`, connected as `niadra_app` (row-level security
on) and `niadra_system`. A test that asks for a space nobody provisioned gets a fresh database cloned
from a migrated template, so each test keeps its own space and its own data, as before the split.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.memory.fakes import (
    DictSecrets,
    FixedClock,
    InMemoryBus,
    InMemoryCache,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.outbound.keys.local import LocalKeyVault, WrappedKeyStore
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.provisioning import (
    DATABASE_GRANTS,
    ROLES,
    AlembicHistory,
    PgSpaceDatabaseAdmin,
    statements,
)
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.servers import Endpoint, Role, Servers
from niadra.adapters.outbound.postgres.uow import PgUnitOfWork, PgUnitOfWorkFactory
from niadra.app.pipeline import PipelineService
from niadra.app.provisioning import Provisioner, ProvisioningSettings
from niadra.domain.common.errors import Unavailable
from niadra.domain.spaces.directory import DbStatus, SpaceDatabase, WantedSpace, db_name_for
from niadra.testing.cell import TENANT_ID, sample_config

DSN = os.environ.get("NIADRA_TEST_DSN")
ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
CELL_DB = "niadra_test_cell"
CELL_ID = "test-cell"
TEMPLATE_DB = "s_template"
SPACE_A = UUID("0192f5a0-0000-7000-8000-0000000000aa")
SPACE_B = UUID("0192f5a0-0000-7000-8000-0000000000bb")
# Databases a production cluster has and a test cluster must never have: the fixtures drop databases.
PRODUCTION_MARKERS = ("niadra_cell", "niadra_control")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not DSN:
                item.add_marker(pytest.mark.skip(reason="NIADRA_TEST_DSN not set"))


def dsn_for(role: str, database: str) -> str:
    """The test server's DSN for a role and a database, as a string the command line tools accept."""
    assert DSN
    url = make_url(DSN).set(drivername="postgresql", username=f"niadra_{role}", database=database)
    return url.render_as_string(hide_password=False)


def libpq(role: str | None = None, database: str = "niadra_test") -> dict[str, Any]:
    assert DSN
    url = make_url(DSN)
    return {
        "host": url.host or url.query.get("host"),
        "port": url.port or url.query.get("port"),
        "user": f"niadra_{role}" if role else (url.username or "postgres"),
        "dbname": database,
        "autocommit": True,
    }


def server_endpoints() -> Servers:
    assert DSN
    url = make_url(DSN)
    endpoint = Endpoint(
        str(url.host or url.query.get("host") or "localhost"), int(url.port or url.query.get("port") or 5432)
    )
    # The test server trusts local connections: the roles need no password.
    return Servers(pooled=endpoint, direct=endpoint, passwords={}, cell_database=CELL_DB)


@dataclass
class PgServer:
    servers: Servers
    config: StaticConfig = field(default_factory=StaticConfig)

    def engine(self, role: Role, database: str) -> AsyncEngine:
        return make_engine(self.servers.url(role, database), pool_size=1, max_overflow=1)

    def admin(self) -> PgSpaceDatabaseAdmin:
        return PgSpaceDatabaseAdmin(self.servers, AlembicHistory(ALEMBIC_INI))


def reset_cluster() -> None:
    with psycopg.connect(**libpq()) as conn:
        names = [row[0] for row in conn.execute("SELECT datname FROM pg_database")]
        found = sorted(set(names) & set(PRODUCTION_MARKERS))
        assert not found, f"the test server holds {found}: refusing to drop databases there"
        for name in names:
            if name.startswith(("s_", CELL_DB)):
                conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        conn.execute(ROLES)  # one script: the DO block holds semicolons of its own
    create_cell_database(CELL_DB)


def create_cell_database(name: str) -> None:
    """An empty database with the grants `db-init.sql` gives the cell's; the cell history comes next."""
    assert name.startswith(CELL_DB), "reset_cluster drops only the cell databases it recognizes"
    with psycopg.connect(**libpq()) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}" OWNER niadra_owner')
    with psycopg.connect(**libpq(database=name)) as conn:
        for statement in statements(DATABASE_GRANTS.format(database=name)):
            conn.execute(statement)


def drop_database(name: str) -> None:
    with psycopg.connect(**libpq()) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def alembic(history: str, dsn: str, *commands: str) -> None:
    env = {**os.environ, f"NIADRA_{history.upper()}_DATABASE_URL": dsn}
    for command in commands:
        subprocess.run(
            [sys.executable, "-m", "alembic", "--name", history, *command.split()],
            cwd=ROOT,
            env=env,
            check=True,
        )


@pytest.fixture(scope="session")
def server() -> PgServer:
    assert DSN, "NIADRA_TEST_DSN is not set"
    assert "niadra_test" in DSN, "integration tests only run against a niadra_test database"
    reset_cluster()
    test = PgServer(server_endpoints())
    # The cell history, and `alembic check` on it: the models and the migrations agree.
    alembic("cell", dsn_for("owner", CELL_DB), "upgrade head", "check")
    asyncio.run(provision_fixed_spaces(test))
    return test


async def provision_fixed_spaces(test: PgServer) -> None:
    admin = test.admin()
    try:
        template = SpaceDatabase(UUID(int=0), TENANT_ID, "sandbox", TEMPLATE_DB, DbStatus.CREATING, 8)
        await admin.create_empty_database(template.instance, TEMPLATE_DB)
        await admin.migrate(template)
    finally:
        await admin.close()
    # The space history, and `alembic check` on it, on the template every cloned space starts from.
    await asyncio.to_thread(alembic, "space", dsn_for("owner", TEMPLATE_DB), "check")
    for space_id, environment in ((SPACE_A, "sandbox"), (SPACE_B, "production")):
        test.config.put(sample_config(space_id).model_copy(update={"environment": environment}))
    async with cell_stack(test) as stack:
        for space_id in (SPACE_A, SPACE_B):
            config = test.config.space(space_id)
            wanted = WantedSpace(space_id, config.tenant_id, config.environment)
            entry = await stack.provisioner.create(wanted)
            assert entry.status is DbStatus.READY, entry


class RecordingControl:
    """The control plane as the cell sees it: records the deletion receipts it receives."""

    def __init__(self) -> None:
        self.receipts: list[tuple[str, UUID, dict[str, Any]]] = []
        self.down = False

    async def snapshot(self, cell_id: str, after: int | None, wait_s: float) -> None:
        return None

    async def revocations(self, cell_id: str, after: int) -> dict[str, Any]:
        return {}

    async def post_deletion_receipt(self, cell_id: str, space_id: UUID, receipt: dict[str, Any]) -> None:
        if self.down:
            raise Unavailable("the control plane is away")
        self.receipts.append((cell_id, space_id, receipt))

    async def push_usage(self, cell_id: str, items: Sequence[Mapping[str, Any]]) -> int:
        return len(items)


@dataclass
class CellStack:
    """What a cell's `loops` holds, on the test server."""

    test: PgServer
    config: StaticConfig
    cell_engine: AsyncEngine
    registry: EngineRegistry
    uow: PgUnitOfWorkFactory
    admin: PgSpaceDatabaseAdmin
    vault: LocalKeyVault
    pipeline: PipelineService
    provisioner: Provisioner
    bus: InMemoryBus
    objects: InMemoryObjectStore
    cache: InMemoryCache
    secrets: DictSecrets
    clock: FixedClock
    control: RecordingControl


@asynccontextmanager
async def cell_stack(
    test: PgServer, *, config: StaticConfig | None = None, keys: WrappedKeyStore | None = None
) -> AsyncIterator[CellStack]:
    config = config if config is not None else test.config
    cell_engine = make_engine(test.servers.cell_url("system"), pool_size=2, max_overflow=2)
    registry = EngineRegistry(test.servers, cell_engine)
    vault = LocalKeyVault(b"k" * 32, keys if keys is not None else PgWrappedKeyStore(cell_engine, "test"))
    uow = PgUnitOfWorkFactory(vault, registry, cell_engine)
    admin = test.admin()
    clock = FixedClock()
    bus = InMemoryBus()
    pipeline = PipelineService(uow, bus, clock)
    objects, cache, secrets, control = (
        InMemoryObjectStore(),
        InMemoryCache(),
        DictSecrets(),
        RecordingControl(),
    )
    provisioner = Provisioner(
        uow, admin, registry, config, pipeline, vault, secrets, objects, cache, control, clock,
        ProvisioningSettings(CELL_ID),
    )  # fmt: skip
    try:
        await registry.refresh()
        yield CellStack(
            test, config, cell_engine, registry, uow, admin, vault, pipeline, provisioner, bus, objects,
            cache, secrets, clock, control,
        )  # fmt: skip
    finally:
        await registry.close()
        await admin.close()
        await cell_engine.dispose()


class ClonedSpaces:
    """Space databases for tests that pick their own space id: cloned from the migrated template,
    stamped and marked ready in the directory, the way the provisioner leaves a new space."""

    def __init__(self, registry: EngineRegistry) -> None:
        self._registry = registry
        self._known: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def ensure(self, space_id: UUID) -> None:
        if space_id in self._known:
            return
        async with self._lock:
            if space_id not in self._known and await self._registry.status(space_id) is None:
                await asyncio.to_thread(clone_space, space_id)
                await self._registry.refresh()
            self._known.add(space_id)


def clone_space(space_id: UUID, environment: str = "sandbox") -> None:
    name = db_name_for(space_id)
    with psycopg.connect(**libpq("owner", CELL_DB)) as conn:
        conn.execute(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE_DB} OWNER niadra_owner")
    with psycopg.connect(**libpq("owner", name)) as conn:
        for statement in statements(DATABASE_GRANTS.format(database=name)):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO space_identity (space_id, tenant_id, region, environment, cell_id, hash_modulus) "
            "VALUES (%s, %s, 'us-east-2', %s, %s, 8)",
            (space_id, TENANT_ID, environment, CELL_ID),
        )
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    with psycopg.connect(**libpq("owner", CELL_DB)) as conn:
        conn.execute(
            "INSERT INTO space_databases (space_id, tenant_id, environment, db_name, hash_modulus, status, "
            "schema_version, ready_at) VALUES (%s, %s, %s, %s, 8, 'ready', %s, now())",
            (space_id, TENANT_ID, environment, name, row[0] if row else None),
        )


class CloningUnitOfWork:
    """The production factory, with a database cloned on the first use of a space nobody provisioned."""

    def __init__(self, inner: PgUnitOfWorkFactory, spaces: ClonedSpaces) -> None:
        self.inner = inner
        self._spaces = spaces
        self._keys = inner._keys  # some tests seal a value by hand, with the factory's vault

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._open(space_id, system=False)

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._open(space_id, system=True)

    def cell(self) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self.inner.cell()

    async def ensure(self, space_id: UUID) -> None:
        await self._spaces.ensure(space_id)

    @asynccontextmanager
    async def _open(self, space_id: UUID, *, system: bool) -> AsyncIterator[PgUnitOfWork]:
        await self._spaces.ensure(space_id)
        async with self.inner.system(space_id) if system else self.inner(space_id) as uow:
            yield uow


@pytest.fixture
async def pg_stack(server: PgServer) -> AsyncIterator[CellStack]:
    async with cell_stack(server) as stack:
        yield stack


@pytest.fixture
async def pg_uow(pg_stack: CellStack) -> CloningUnitOfWork:
    return CloningUnitOfWork(pg_stack.uow, ClonedSpaces(pg_stack.registry))


async def owner_sql(test: PgServer, database: str, sql: str, **params: Any) -> list[Any]:
    """A statement as the owner, around the application roles (to plant rows that row-level security
    must hide, or to look at a database from outside)."""
    engine = test.engine("owner", database)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()
