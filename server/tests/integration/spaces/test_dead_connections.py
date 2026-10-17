"""The space engines skip the pool's ping (a round trip on every transaction). A pooled connection the server
or the pooler closed is found by the transaction's first statement, which starts again on a fresh
connection once, and by the intake log's single statement, which is idempotent by intake id: an idle
timeout of the server, and a restart of PgBouncer, both cost a retry and never an error or a second row."""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text

from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.intake import PgIntakeLog
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.servers import Endpoint, Servers
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.intake import NewIntake
from tests.integration.conftest import CELL_DB, PgServer, clone_space, libpq

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Bouncer:
    """A PgBouncer of this test's own, in transaction mode, that the test stops and starts again."""

    def __init__(self, directory: Path) -> None:
        self.port = _free_port()
        target = libpq()
        users = directory / "users.txt"
        users.write_text(
            "".join(f'"niadra_{r}" ""\n' for r in ("app", "system", "owner")) + '"postgres" ""\n'
        )
        self.ini = directory / "pgbouncer.ini"
        self.ini.write_text(
            "[databases]\n"
            f"* = host={target['host']} port={target['port']}\n"
            "[pgbouncer]\n"
            f"listen_addr = 127.0.0.1\nlisten_port = {self.port}\nunix_socket_dir =\n"
            f"auth_type = trust\nauth_file = {users}\npool_mode = transaction\n"
            "default_pool_size = 4\nmax_db_connections = 6\nignore_startup_parameters = extra_float_digits\n"
            f"logfile = {directory / 'pgbouncer.log'}\n"
        )
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(["pgbouncer", str(self.ini)])  # noqa: S607
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            subprocess.run(["sleep", "0.05"], check=True)  # noqa: S607
        raise RuntimeError("pgbouncer did not start")

    def stop(self) -> None:
        if self.process is not None:
            self.process.send_signal(signal.SIGINT)  # its immediate shutdown: every connection goes
            self.process.wait(timeout=10)
            self.process = None


@pytest.fixture
def bouncer() -> Iterator[Bouncer]:
    if shutil.which("pgbouncer") is None:
        pytest.skip("needs the pgbouncer binary")
    with tempfile.TemporaryDirectory() as directory:
        running = Bouncer(Path(directory))
        running.start()
        try:
            yield running
        finally:
            running.stop()


class Cell:
    def __init__(self, servers: Servers) -> None:
        self.cell_engine = make_engine(servers.cell_url("system"), pool_size=1)
        self.registry = EngineRegistry(servers, self.cell_engine)
        keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(self.cell_engine, "dead"))
        self.uow = PgUnitOfWorkFactory(keys, self.registry, self.cell_engine)
        self.invalidated = 0

    async def watch(self, space_id: UUID) -> None:
        async with self.registry.lease(space_id, "app") as engine:
            event.listen(engine.sync_engine.pool, "invalidate", self._count)

    def _count(self, *_: object) -> None:
        self.invalidated += 1

    async def close(self) -> None:
        await self.registry.close()
        await self.cell_engine.dispose()


@pytest.fixture
async def space(server: PgServer) -> UUID:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    return space_id


@pytest.fixture
async def direct(server: PgServer, space: UUID) -> AsyncIterator[Cell]:
    endpoint = server.servers.direct
    cell = Cell(Servers(pooled=endpoint, direct=endpoint, passwords={}, cell_database=CELL_DB))
    await cell.registry.refresh()
    yield cell
    await cell.close()


@pytest.fixture
async def pooled(server: PgServer, bouncer: Bouncer, space: UUID) -> AsyncIterator[Cell]:
    cell = Cell(
        Servers(
            pooled=Endpoint("127.0.0.1", bouncer.port),
            direct=server.servers.direct,
            passwords={},
            cell_database=CELL_DB,
        )
    )
    await cell.registry.refresh()
    yield cell
    await cell.close()


async def _rows(cell: Cell, space_id: UUID) -> int:
    async with cell.uow(space_id) as uow:
        return int((await uow.session.execute(text("SELECT count(*) FROM ingest_intake"))).scalar_one())


def _intake(space_id: UUID) -> NewIntake:
    return NewIntake(uuid7(), space_id, NOW, uuid4(), (("k1", uuid7()),), b"\x00sealed", 1)


async def test_a_connection_the_server_closed_when_idle_costs_one_retry(direct: Cell, space: UUID) -> None:
    await direct.watch(space)
    async with direct.uow(space) as uow:
        # For this pooled connection only: the server closes it after a short idle time.
        await uow.session.execute(text("SET idle_session_timeout = '100ms'"))
    await asyncio.sleep(0.5)

    assert await _rows(direct, space) == 0
    assert direct.invalidated == 1


async def test_a_restart_of_the_pooler_costs_one_retry_in_a_transaction_and_in_the_intake_log(
    pooled: Cell, bouncer: Bouncer, space: UUID
) -> None:
    await pooled.watch(space)
    log = PgIntakeLog(pooled.registry)
    assert await _rows(pooled, space) == 0
    await log.accept(_intake(space))

    bouncer.stop()
    bouncer.start()
    assert await _rows(pooled, space) == 1
    assert pooled.invalidated == 1

    bouncer.stop()
    bouncer.start()
    await log.accept(_intake(space))
    assert await _rows(pooled, space) == 2
    assert pooled.invalidated == 2


async def test_the_intake_log_answers_a_repeated_call_as_the_first_and_stores_nothing_more(
    direct: Cell, space: UUID
) -> None:
    log = PgIntakeLog(direct.registry)
    intake = NewIntake(uuid7(), space, NOW, uuid4(), (("k1", uuid7()), ("k2", uuid7())), b"\x00sealed", 1)
    async with direct.uow(space) as uow:
        await uow.ingest.claim_keys(space, [("k1", uuid7())], NOW)

    assert await log.accept(intake) == {"k1"}
    assert await log.accept(intake) == {"k1"}

    assert await _rows(direct, space) == 1
    async with direct.uow(space) as uow:
        copies = (await uow.session.execute(text("SELECT count(*) FROM outbox"))).scalar_one()
        # k2 is claimed once, by the first call: a third batch naming it is a duplicate.
        assert (await uow.ingest.claim_keys(space, [("k2", uuid7())], NOW))[1] == {"k2"}
    assert copies == 1


def test_the_pooler_binary_is_what_ci_installs() -> None:
    # CI installs PgBouncer from apt (.github/workflows/ci.yml); without it these tests skip locally only.
    assert os.environ.get("CI") != "true" or shutil.which("pgbouncer") is not None
