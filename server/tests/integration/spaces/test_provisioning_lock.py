"""The provisioning lock and the list of databases on one session kept across passes (8.1), and the lease
that lets the lock go when that session stops answering.

A pass used to open a direct connection every 5 s (about 17,000 a day on the 79 of the micro instance).
And a session that held the lock and went silent kept it for as long as the server kept the session: the
silence broke no TCP connection, so every later pass found the lock taken, and no removed space's database
was dropped until the process restarted. The proxy here goes silent the same way: it keeps both sockets
open and forwards nothing."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

import pytest

from niadra.adapters.outbound.postgres.provisioning import (
    APPLICATION_NAME,
    AlembicHistory,
    Lease,
    PgSpaceDatabaseAdmin,
)
from niadra.adapters.outbound.postgres.servers import Endpoint, Servers
from niadra.domain.spaces.directory import PRIMARY_INSTANCE
from niadra.ports.provisioning import LockLost
from tests.integration.conftest import ALEMBIC_INI, CELL_DB, PgServer, owner_sql

LEASE = Lease(beat_s=0.2, answer_s=0.3, idle_s=1.0)


class SilentProxy:
    """A TCP proxy to the test server whose connections can all go silent for good at once."""

    def __init__(self, target: Endpoint) -> None:
        self._target = target
        self._server: asyncio.Server | None = None
        self.silent = False
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        assert self._server is not None
        self._server.close()

    async def _handle(self, client_in: asyncio.StreamReader, client_out: asyncio.StreamWriter) -> None:
        if self._target.host.startswith("/"):  # the directory of the server's unix socket
            socket = f"{self._target.host}/.s.PGSQL.{self._target.port}"
            server_in, server_out = await asyncio.open_unix_connection(socket)
        else:
            server_in, server_out = await asyncio.open_connection(self._target.host, self._target.port)
        await asyncio.gather(self._pipe(client_in, server_out), self._pipe(server_in, client_out))

    async def _pipe(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            while data := await source.read(65536):
                if not self.silent:
                    sink.write(data)
                    await sink.drain()
        if not self.silent:
            sink.close()


@pytest.fixture
async def proxy(server: PgServer) -> AsyncIterator[SilentProxy]:
    running = SilentProxy(server.servers.direct)
    await running.start()
    yield running
    await running.close()


def admin_through(server: PgServer, port: int | None = None) -> PgSpaceDatabaseAdmin:
    direct = server.servers.direct if port is None else Endpoint("127.0.0.1", port)
    servers = Servers(pooled=server.servers.pooled, direct=direct, passwords={}, cell_database=CELL_DB)
    return PgSpaceDatabaseAdmin(servers, AlembicHistory(ALEMBIC_INI), LEASE)


async def provisioner_backends(server: PgServer) -> set[int]:
    rows = await owner_sql(
        server,
        CELL_DB,
        "SELECT pid FROM pg_stat_activity WHERE application_name = :name AND pid <> pg_backend_pid()",
        name=APPLICATION_NAME,
    )
    return {pid for (pid,) in rows}


async def test_passes_with_nothing_to_do_share_one_session(server: PgServer) -> None:
    before = await provisioner_backends(server)
    admin = admin_through(server)
    seen: set[int] = set()
    try:
        for _ in range(5):
            async with admin.leader() as leading:
                assert leading
                await admin.databases(PRIMARY_INSTANCE)
            seen |= await provisioner_backends(server) - before
    finally:
        await admin.close()
    assert len(seen) == 1


async def test_a_session_the_server_ended_while_idle_is_replaced(server: PgServer) -> None:
    before = await provisioner_backends(server)
    admin = admin_through(server)
    try:
        async with admin.leader() as leading:
            assert leading
        (first,) = await provisioner_backends(server) - before
        await asyncio.sleep(LEASE.idle_s + 0.5)  # the server ends the session: `idle_session_timeout`
        assert first not in await provisioner_backends(server)
        async with admin.leader() as leading:
            assert leading
        assert await provisioner_backends(server) - before - {first}
    finally:
        await admin.close()


async def test_a_silent_holder_gives_the_lock_up_before_the_server_lets_it_go(
    server: PgServer, proxy: SilentProxy
) -> None:
    silent, other = admin_through(server, proxy.port), admin_through(server)
    holding = asyncio.Event()

    async def pass_() -> None:
        async with silent.leader() as leading:
            assert leading
            holding.set()
            await asyncio.Event().wait()  # a pass that is still working when its session goes silent

    running = asyncio.create_task(pass_())
    try:
        await holding.wait()
        proxy.silent = True
        started = time.monotonic()
        taken_at = None
        while taken_at is None and time.monotonic() - started < 10 * LEASE.idle_s:
            async with other.leader() as leading:
                if leading:
                    taken_at = time.monotonic() - started
                    # Never two holders: the silent one stopped before the server let the lock go.
                    assert running.done()
            await asyncio.sleep(0.05)
        with pytest.raises(LockLost):
            await running
        assert taken_at is not None, "the lock stayed with the silent session"
        assert taken_at <= LEASE.idle_s + LEASE.beat_s + 1.0
    finally:
        running.cancel()
        with contextlib.suppress(BaseException):
            await running
        await silent.close()
        await other.close()


async def test_a_pass_abandoned_at_its_deadline_lets_the_lock_go(server: PgServer) -> None:
    admin, other = admin_through(server), admin_through(server)

    async def stuck() -> None:
        async with admin.leader() as leading:
            assert leading
            await asyncio.Event().wait()

    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stuck(), 0.5)
        async with other.leader() as leading:
            assert leading
        # The session stays, without the lock.
        async with admin.leader() as leading:
            assert leading
    finally:
        await admin.close()
        await other.close()
