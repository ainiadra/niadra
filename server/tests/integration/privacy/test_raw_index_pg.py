"""The index of raw content on PostgreSQL (5.14.0): a lookup by the target's tokens and a page of what expired
each run one statement, whatever the number of objects the space holds."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.domain.privacy.raw_index import RawObject
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


@contextmanager
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def note(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", note)


def payload(space: UUID, n: int, *tokens: bytes, days_ago: int = 0, owner: str = "_") -> RawObject:
    return RawObject(f"raw/{space}/{owner}/{n:05d}", owner, NOW - timedelta(days=days_ago), frozenset(tokens))


async def test_the_index_finds_by_token_expires_by_time_and_forgets_by_owner(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, held = uuid4(), str(uuid4())
    async with pg_uow(space) as uow:
        assert not await uow.privacy.raw_index_ready(space)
        await uow.privacy.index_raw(
            space,
            [
                payload(space, 1, b"t-ana", b"t-doc", days_ago=40),
                payload(space, 2, b"t-bia", days_ago=50),
                payload(space, 3, days_ago=60, owner=held),
                payload(space, 4, b"t-ana"),
            ],
        )
        # Written again by a retried task: the first row stays.
        await uow.privacy.index_raw(space, [payload(space, 1, days_ago=0)])
        await uow.privacy.mark_raw_index_ready(space)
    async with pg_uow(space) as uow:
        assert await uow.privacy.raw_index_ready(space)
        named = [r.key for r in await uow.privacy.raw_named(space, [b"t-doc", b"t-zzz"])]
        due = await uow.privacy.raw_written_before(space, NOW - timedelta(days=30), keep={held}, limit=1)
        everything = await uow.privacy.raw_written_before(space, NOW - timedelta(days=30), keep=(), limit=10)
    assert named == [f"raw/{space}/_/00001"]
    assert due == [f"raw/{space}/_/00002"], "oldest first, the held handle's kept"
    assert everything == [f"raw/{space}/{held}/00003", f"raw/{space}/_/00002", f"raw/{space}/_/00001"]
    async with pg_uow(space) as uow:
        await uow.privacy.unindex_raw(space, [f"raw/{space}/_/00001"])
        await uow.privacy.unindex_raw_owned(space, [held])
        left = await uow.privacy.raw_written_before(space, NOW + timedelta(days=1), keep=(), limit=10)
        ana = [r.key for r in await uow.privacy.raw_named(space, [b"t-ana"])]
    assert left == [f"raw/{space}/_/00002", f"raw/{space}/_/00004"]
    assert ana == [f"raw/{space}/_/00004"]


async def test_a_lookup_and_a_page_cost_one_statement_whatever_the_size_of_the_space(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        await uow.privacy.index_raw(space, [payload(space, 0, b"t-target", days_ago=40)])

    async def costs() -> tuple[int, int]:
        async with pg_uow(space) as uow:
            with statements() as lookup:
                rows = await uow.privacy.raw_named(space, [b"t-target"])
            assert [r.key for r in rows] == [f"raw/{space}/_/00000"]
            with statements() as page:
                await uow.privacy.raw_written_before(space, NOW - timedelta(days=30), keep=(), limit=500)
        return len(lookup), len(page)

    small = await costs()
    async with pg_uow(space) as uow:
        await uow.privacy.index_raw(
            space, [payload(space, n, f"t-{n}".encode(), days_ago=n % 3) for n in range(1, 2_001)]
        )
    assert await costs() == small == (1, 1)
