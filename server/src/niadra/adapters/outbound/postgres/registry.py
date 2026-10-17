"""Connection pools per space database, opened on first use and closed when idle (10).

Every process keeps an in-memory copy of the cell's directory (`space_databases`), read every 5 s and
again on a miss, and opens a small pool per (database, role) only when a request or a task needs it.
The first open checks the database's stamp: a directory row pointing at another space's database
raises and nothing is cached. With PgBouncer in front, a pool of one plus two overflow connections per
process and database keeps a few hundred databases inside a small instance's connection budget.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from niadra.adapters.outbound.postgres.directory import PgSpaceDirectoryRepository
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.schema.platform import SpaceIdentity
from niadra.adapters.outbound.postgres.servers import Role, Servers
from niadra.domain.common.errors import Unavailable
from niadra.domain.spaces.directory import RETRY_AFTER_S, DbStatus, SpaceDatabase
from niadra.domain.spaces.stamp import SpaceStamp, StampMismatch, check_stamp

log = logging.getLogger(__name__)

REFRESH_EVERY_S = 5.0
SWEEP_EVERY_S = 30.0
IDLE_AFTER_S = 120.0
# A request for a space the copy does not know reads the directory again, at most this often.
MISS_REFRESH_S = 1.0

_WAIT = {
    DbStatus.CREATING: "the space's database is being created",
    DbStatus.MIGRATING: "the space's database is being created",
    DbStatus.FAILED: "the space's database is being created",
    DbStatus.MAINTENANCE: "the space's database is being restored",
    DbStatus.DELETING: "the space is being deleted",
    DbStatus.DELETED: "the space was deleted",
}


class DirectoryReader(Protocol):
    async def __call__(self) -> Sequence[SpaceDatabase]: ...


@dataclass
class _Pool:
    engine: AsyncEngine
    space_id: UUID
    stamp: SpaceStamp
    last_used: float
    in_flight: int = 0


@dataclass
class PoolSettings:
    pool_size: int = 1
    max_overflow: int = 2
    pool_timeout_s: float = 5.0


@dataclass
class EngineRegistry:
    """`SpaceDatabases` for the application, and the engines behind the space units of work."""

    servers: Servers
    cell_engine: AsyncEngine
    pools: PoolSettings = field(default_factory=PoolSettings)
    reader: DirectoryReader | None = None
    monotonic: Callable[[], float] = time.monotonic
    idle_after_s: float = IDLE_AFTER_S

    def __post_init__(self) -> None:
        self._entries: dict[UUID, SpaceDatabase] = {}
        self._pools: dict[tuple[str, str, Role], _Pool] = {}
        self._opening: dict[tuple[str, str, Role], asyncio.Lock] = {}
        self._refresh_lock = asyncio.Lock()
        self._last_refresh = float("-inf")
        self._read = self.reader or self._read_directory

    # The SpaceDatabases port.

    async def require_serving(self, space_id: UUID) -> None:
        self._serving(await self._entry(space_id), space_id)

    async def status(self, space_id: UUID) -> DbStatus | None:
        entry = await self._entry(space_id)
        return None if entry is None else entry.status

    def active_spaces(self) -> list[UUID]:
        return sorted({pool.space_id for pool in self._pools.values()})

    def serving_spaces(self) -> list[UUID]:
        return sorted(space_id for space_id, entry in self._entries.items() if entry.serving)

    async def refresh(self) -> None:
        async with self._refresh_lock:
            rows = await self._read()
            self._last_refresh = self.monotonic()
            fresh = {row.space_id: row for row in rows}
            for space_id, before in list(self._entries.items()):
                after = fresh.get(space_id)
                if after is None or not after.serving or after.db_name != before.db_name:
                    await self._close(space_id, keep=after if after and after.serving else None)
            self._entries = fresh

    async def dispose(self, space_id: UUID) -> None:
        await self._close(space_id)

    # For the units of work.

    @contextlib.asynccontextmanager
    async def lease(self, space_id: UUID, role: Role) -> AsyncIterator[AsyncEngine]:
        """The pool of the space's database for `role`, marked in use until the block ends."""
        pool = await self._pool(space_id, role)
        pool.in_flight += 1
        pool.last_used = self.monotonic()
        try:
            yield pool.engine
        finally:
            pool.in_flight -= 1
            pool.last_used = self.monotonic()

    # Upkeep.

    async def sweep(self) -> int:
        """Closes pools nobody used for two minutes; a quiet sandbox holds no connection at all."""
        now = self.monotonic()
        idle = [
            key
            for key, pool in self._pools.items()
            if pool.in_flight == 0 and now - pool.last_used >= self.idle_after_s
        ]
        for key in idle:
            pool = self._pools.pop(key, None)
            if pool is not None:
                await pool.engine.dispose()
        return len(idle)

    async def run(self, stop: asyncio.Event) -> None:
        """Every process runs this: the directory copy every 5 s, idle pools every 30 s."""
        next_sweep = self.monotonic() + SWEEP_EVERY_S
        while not stop.is_set():
            try:
                await self.refresh()
                if self.monotonic() >= next_sweep:
                    await self.sweep()
                    next_sweep = self.monotonic() + SWEEP_EVERY_S
            except Exception:
                # The last good copy keeps serving; the cell database is back within a tick or two.
                log.exception("space directory refresh failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), REFRESH_EVERY_S)

    async def close(self) -> None:
        for key in list(self._pools):
            pool = self._pools.pop(key)
            await pool.engine.dispose()

    # Internals.

    async def _entry(self, space_id: UUID) -> SpaceDatabase | None:
        entry = self._entries.get(space_id)
        if entry is None and self.monotonic() - self._last_refresh >= MISS_REFRESH_S:
            await self.refresh()
            entry = self._entries.get(space_id)
        return entry

    @staticmethod
    def _serving(entry: SpaceDatabase | None, space_id: UUID) -> SpaceDatabase:
        if entry is None:
            raise Unavailable("the space's database is not ready yet", retry_after_s=RETRY_AFTER_S)
        if not entry.serving:
            raise Unavailable(_WAIT.get(entry.status, "the space's database is not ready"), RETRY_AFTER_S)
        assert entry.space_id == space_id
        return entry

    async def _pool(self, space_id: UUID, role: Role) -> _Pool:
        entry = self._serving(await self._entry(space_id), space_id)
        key = (entry.instance, entry.db_name, role)
        pool = self._pools.get(key)
        if pool is not None and pool.space_id == space_id:
            return pool
        async with self._opening.setdefault(key, asyncio.Lock()):
            pool = self._pools.get(key)
            if pool is not None and pool.space_id == space_id:
                return pool
            engine = make_engine(
                self.servers.url(role, entry.db_name, instance=entry.instance),
                pool_size=self.pools.pool_size,
                max_overflow=self.pools.max_overflow,
                pool_timeout=self.pools.pool_timeout_s,
                # One name per role, whatever the process: PgBouncer sends `SET application_name` to a server
                # connection each time a client with another name gets it, a statement per transaction
                # when every deployable shares the pool (9,000 in 80 s on the local cell, 28/09/2026).
                application_name=f"niadra-{role}",
                # The space's transactions and the intake log retry a first statement on a dead connection.
                pre_ping=False,
            )
            try:
                stamp = check_stamp(space_id, entry.db_name, await _read_stamp(engine))
            except BaseException:
                await engine.dispose()
                raise
            pool = _Pool(engine, space_id, stamp, self.monotonic())
            self._pools[key] = pool
            return pool

    async def _close(self, space_id: UUID, keep: SpaceDatabase | None = None) -> None:
        for key, pool in list(self._pools.items()):
            if pool.space_id == space_id and (keep is None or key[1] != keep.db_name):
                self._pools.pop(key, None)
                await pool.engine.dispose()

    async def _read_directory(self) -> Sequence[SpaceDatabase]:
        async with AsyncSession(self.cell_engine) as session, session.begin():
            return await PgSpaceDirectoryRepository(session).entries()


async def _read_stamp(engine: AsyncEngine) -> SpaceStamp | None:
    async with engine.connect() as connection:
        row = (await connection.execute(select(SpaceIdentity))).one_or_none()
    if row is None:
        return None
    return SpaceStamp(row.space_id, row.tenant_id, row.region, row.environment, row.cell_id, row.hash_modulus)


__all__ = ["EngineRegistry", "PoolSettings", "StampMismatch"]
