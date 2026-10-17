"""On PostgreSQL an insert that names one row twice fails ("ON CONFLICT DO UPDATE command cannot affect row a
second time"). The first erasure of a space reads the rows written before the index in pages, and a page of
episodes holds every version of an episode under one key: the erasure failed there and stayed pending (08/10).
Every writer of the index takes each row once."""

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

from niadra.app.named_rows import NamedRowsService
from niadra.app.raw_index import sealed_tokens
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import MentionKey, MentionStore
from niadra.domain.privacy.raw_index import ERASED, handle_tokens
from niadra.domain.vocabulary import HandleType
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.memory.test_pg_purposes import episode

EMAIL = "clara@exemplo.com"
PLAIN = handle_tokens([(HandleType.EMAIL, EMAIL)])
STORES = list(MentionStore)
NOW = datetime(2026, 10, 8, 22, tzinfo=UTC)


async def test_the_first_erasure_reads_every_version_of_an_episode_written_before_the_index(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, owner = uuid7(), uuid7()
    first = dataclasses.replace(episode(owner), summary=f"Mandou o boleto para {EMAIL}")
    second = dataclasses.replace(first, version=2, summary=f"Reenviou o boleto para {EMAIL}")
    async with pg_uow(space) as uow:
        await uow.memory.add_episode_version(first)
        await uow.memory.add_episode_version(second, replaces=first)
    tokens = await sealed_tokens(pg_uow._keys, space, PLAIN)
    async with pg_uow(space) as uow:
        # As if written before the index existed: the first erasure reads them into it.
        await uow.mentions.drop(
            await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
        )

    scope = ErasureScope(uuid4(), TargetKind.HANDLE, (uuid4(),), uuid4(), None, NOW)
    done = await NamedRowsService(pg_uow, pg_uow._keys).redact_named(space, scope, PLAIN, tokens, Held())

    assert done.rows >= 1
    async with pg_uow(space) as uow:
        versions = await uow.memory.episode_versions(owner, first.episode_id)
        left = await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
    assert {v.summary for v in versions} == {
        f"Mandou o boleto para {ERASED}",
        f"Reenviou o boleto para {ERASED}",
    }
    assert left == []


async def test_each_writer_takes_a_row_named_twice_in_one_write(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    row = MentionKey(MentionStore.EPISODE, str(uuid7()))
    event = uuid7()
    async with pg_uow(space) as uow:
        await uow.mentions.index([(row, frozenset({b"t-a"})), (row, frozenset({b"t-b"}))])
        await uow.mentions.index_objects(
            [("turns/short/x/a.bin", frozenset({b"t-a"})), ("turns/short/x/a.bin", frozenset({b"t-b"}))]
        )
        await uow.ingest.index_mentions([(event, frozenset({b"t-a"})), (event, frozenset({b"t-b"}))])
    async with pg_uow(space) as uow:
        await uow.mentions.index([(row, frozenset({b"t-c"})), (row, frozenset({b"t-d"}))], replace=True)
    async with pg_uow(space) as uow:
        by_a = await uow.mentions.naming([b"t-a"], frozenset(), stores=STORES, after=None, limit=10)
        by_d = await uow.mentions.naming([b"t-d"], frozenset(), stores=STORES, after=None, limit=10)
        objects = await uow.mentions.objects_naming([b"t-b"], prefix="turns/", after=None, limit=10)
    assert by_a == [], "a replace leaves only what the row names now"
    assert by_d == [row]
    assert objects == ["turns/short/x/a.bin"]
