"""A database server in memory, for the provisioner's and the fleet runner's rules."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from niadra.domain.spaces.directory import PRIMARY_INSTANCE, SpaceDatabase
from niadra.domain.spaces.stamp import SpaceStamp


class FakeHistory:
    def __init__(self, *revisions: str) -> None:
        self._order = list(revisions)

    def head(self) -> str:
        return self._order[-1]

    def reached(self, current: str | None, revision: str) -> bool:
        return current is not None and self._order.index(current) >= self._order.index(revision)


@dataclass
class FakeAdmin:
    history: FakeHistory
    databases_: dict[str, str | None] = field(default_factory=dict)  # name -> revision
    stamps: dict[str, SpaceStamp] = field(default_factory=dict)
    failing: dict[str, Exception] = field(default_factory=dict)  # name -> error of its next migration
    busy: bool = False
    stalls: int = 0  # passes whose connection for the lock stopped answering: they wait for good
    stalled: set[str] = field(default_factory=set)  # databases whose migration never returns
    undroppable: set[str] = field(default_factory=set)  # databases whose drop fails
    migrated: list[tuple[str, str]] = field(default_factory=list)
    partitions: set[tuple[str, str, int]] = field(default_factory=set)  # (database, parent, week start)
    dropped: list[str] = field(default_factory=list)

    @property
    def instances(self) -> Sequence[str]:
        return [PRIMARY_INSTANCE]

    @contextlib.asynccontextmanager
    async def leader(self) -> AsyncIterator[bool]:
        if self.stalls:
            self.stalls -= 1
            await asyncio.Event().wait()
        yield not self.busy

    async def create_database(self, entry: SpaceDatabase) -> bool:
        return await self.create_empty_database(entry.instance, entry.db_name)

    async def create_empty_database(self, instance: str, db_name: str) -> bool:
        created = db_name not in self.databases_
        self.databases_.setdefault(db_name, None)
        return created

    async def migrate(self, entry: SpaceDatabase, target: str | None = None) -> str:
        if entry.db_name in self.failing:
            raise self.failing[entry.db_name]
        if entry.db_name in self.stalled:
            await asyncio.Event().wait()
        goal = target or self.history.head()
        self.databases_[entry.db_name] = goal
        self.migrated.append((entry.db_name, goal))
        return goal

    async def ensure_week_partitions(
        self, entry: SpaceDatabase, parents: Sequence[str], week_starts_ms: Sequence[int]
    ) -> None:
        self.partitions.update(
            (entry.db_name, parent, start) for parent in parents for start in week_starts_ms
        )

    async def schema_version(self, entry: SpaceDatabase) -> str | None:
        return self.databases_.get(entry.db_name)

    async def write_stamp(self, entry: SpaceDatabase, stamp: SpaceStamp) -> None:
        self.stamps.setdefault(entry.db_name, stamp)

    async def read_stamp(self, instance: str, db_name: str) -> SpaceStamp | None:
        return self.stamps.get(db_name)

    async def drop_database(self, instance: str, db_name: str) -> None:
        if db_name in self.undroppable:
            raise RuntimeError(f"database {db_name} is being accessed by other users")
        self.databases_.pop(db_name, None)
        self.dropped.append(db_name)

    async def databases(self, instance: str) -> list[str]:
        return sorted(name for name in self.databases_ if name.startswith("s_"))

    async def rename_database(self, instance: str, old: str, new: str) -> None:
        self.databases_[new] = self.databases_.pop(old)

    async def resume(self, db_name: str) -> None:
        return None

    async def dump(self, instance: str, db_name: str) -> AsyncIterator[bytes]:
        yield b"dump"

    async def restore(self, instance: str, db_name: str, dump: Path) -> None:
        return None

    async def size_bytes(self, instance: str, db_name: str) -> int:
        return 1

    async def count_rows(self, instance: str, db_name: str, tables: Sequence[str]) -> dict[str, int]:
        return dict.fromkeys(tables, 0)
