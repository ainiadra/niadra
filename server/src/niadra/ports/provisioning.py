"""What the provisioner, the setup runner and the backup tooling need from the database servers.

All of it runs as the schema owner on a direct connection, never through the pooler: CREATE and DROP
DATABASE cannot run inside a transaction, and migrations, dumps and restores hold session state.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from niadra.domain.spaces.directory import SpaceDatabase
from niadra.domain.spaces.stamp import SpaceStamp


class LockLost(ConnectionError):
    """The session that holds the provisioning lock stopped answering. The server lets the lock go shortly
    after, so the holder stops acting at once and leaves the work to the next holder."""


class SchemaHistory(Protocol):
    """The Alembic history every space database follows."""

    def head(self) -> str: ...

    def reached(self, current: str | None, revision: str) -> bool:
        """True when a database at `current` has applied `revision` already."""
        ...


class SpaceDatabaseAdmin(Protocol):
    @property
    def history(self) -> SchemaHistory: ...

    @property
    def instances(self) -> Sequence[str]: ...

    def leader(self) -> AbstractAsyncContextManager[bool]:
        """Holds the cell's provisioning lock (a session advisory lock on the cell database) while the
        block runs; False when another holder has it. One reconciler or runner acts at a time. The lock is
        a lease: when its session stops answering, the block is cancelled and `LockLost` raised before the
        server can hand the lock to anyone else."""
        ...

    async def create_database(self, entry: SpaceDatabase) -> bool:
        """Creates the database with the grants every space database gets; False when it existed
        already (a creation that failed half-way and is retried)."""
        ...

    async def create_empty_database(self, instance: str, db_name: str) -> bool:
        """The same, under any name: where a dump is restored or verified."""
        ...

    async def migrate(self, entry: SpaceDatabase, target: str | None = None) -> str:
        """Applies the space history up to `target` (the head by default) under the database's own
        migration lock, with the row's hash modulus; returns the revision now applied."""
        ...

    async def ensure_week_partitions(
        self, entry: SpaceDatabase, parents: Sequence[str], week_starts_ms: Sequence[int]
    ) -> None:
        """The weekly partitions of `parents` for those weeks, on a direct connection that needs no
        directory row: a new database gets them before anything may write to it."""
        ...

    async def schema_version(self, entry: SpaceDatabase) -> str | None: ...

    async def write_stamp(self, entry: SpaceDatabase, stamp: SpaceStamp) -> None: ...

    async def read_stamp(self, instance: str, db_name: str) -> SpaceStamp | None: ...

    async def drop_database(self, instance: str, db_name: str) -> None:
        """Closes the pooler's connections to it, then drops it even if a connection remains."""
        ...

    async def databases(self, instance: str) -> list[str]:
        """Every database of the instance whose name starts with `s_`."""
        ...

    async def rename_database(self, instance: str, old: str, new: str) -> None:
        """Pauses the pooler for `old`, ends the remaining connections and renames."""
        ...

    async def resume(self, db_name: str) -> None:
        """Lets the pooler reach `db_name` again after a rename."""
        ...

    def dump(self, instance: str, db_name: str) -> AsyncIterator[bytes]:
        """`pg_dump -Fc --no-owner --no-privileges` of the database, as it comes out."""
        ...

    async def restore(self, instance: str, db_name: str, dump: Path) -> None:
        """`pg_restore --no-owner` of a custom-format dump into an existing, empty database."""
        ...

    async def size_bytes(self, instance: str, db_name: str) -> int: ...

    async def count_rows(self, instance: str, db_name: str, tables: Sequence[str]) -> dict[str, int]: ...
