"""The control database for the integration tests: its own database, migrated once per session and emptied
before each test."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.ed25519 import Ed25519Signer
from niadra.adapters.outbound.postgres.control import PgControlUnitOfWorkFactory
from niadra.adapters.outbound.postgres.uow import make_engine
from niadra.app.control import ControlService
from tests.integration.conftest import DSN, ROOT

TABLES = (
    "tenants, projects, cells, spaces, sources, api_keys, revocations, users, role_bindings, config_diffs,"
    " config_versions, snapshots, signing_keys, secret_refs, quotas, usage_aggregates, placement_moves,"
    " recovery_codes, second_factor_resets, invitations, access_changes, sso_connections, sso_domains,"
    " sso_attempts"
)


@pytest.fixture(scope="session")
def control_dsn() -> Iterator[str]:
    assert DSN
    assert "niadra_test" in DSN
    parts = urlsplit(DSN)
    dsn = urlunsplit(parts._replace(path="/niadra_test_control"))
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS niadra_test_control WITH (FORCE)")
        conn.execute("CREATE DATABASE niadra_test_control")
    env = {**os.environ, "NIADRA_CONTROL_DATABASE_URL": dsn}
    subprocess.run(
        [sys.executable, "-m", "alembic", "--name", "control", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "--name", "control", "check"], cwd=ROOT, env=env, check=True
    )
    yield dsn
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS niadra_test_control WITH (FORCE)")


@pytest.fixture
async def control_uow(control_dsn: str) -> AsyncIterator[PgControlUnitOfWorkFactory]:
    engine = make_engine(control_dsn, pool_size=2)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {TABLES} CASCADE")
    try:
        yield PgControlUnitOfWorkFactory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def service(control_uow: PgControlUnitOfWorkFactory) -> tuple[ControlService, FixedClock]:
    clock = FixedClock()
    return ControlService(control_uow, Ed25519Signer(Ed25519PrivateKey.generate()), clock, b"p" * 32), clock
