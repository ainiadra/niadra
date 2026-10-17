"""What derived memory and objects name a person by, kept beside them on PostgreSQL (5.14.0): a row is indexed
in the transaction that seals it, an erasure finds the rows and objects of others that name the target in one
statement a page, whatever the size of the space, and a redacted row's entry is replaced."""

import dataclasses
from datetime import UTC, datetime

from niadra.app.raw_index import sealed_tokens
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.privacy.mentions import MentionKey, MentionStore, signals_key
from niadra.domain.privacy.raw_index import body_tokens, handle_tokens
from niadra.domain.signals.records import SELF, SignalRow, SignalWrite
from niadra.domain.turns.records import AppliedTurns
from niadra.domain.vocabulary import HandleType
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.memory.test_pg_privacy import fact
from tests.integration.privacy.test_raw_index_pg import statements

CLARA = "+5511987654321"
STORES = list(MentionStore)


async def test_a_sealed_row_is_indexed_with_it_and_replaced_when_redacted(pg_uow: CloningUnitOfWork) -> None:
    space, marcos = uuid7(), uuid7()
    named = fact(marcos, "irmã Clara (11) 98765-4321")
    plain = handle_tokens([(HandleType.PHONE_E164, CLARA)])
    async with pg_uow(space) as uow:
        tokens = await sealed_tokens(pg_uow._keys, space, plain)
        await uow.memory.insert_fact(named, normalize_value(named.value))
        await uow.memory.insert_fact(other := fact(marcos, "Rua A"), normalize_value(other.value))
    key = MentionKey(MentionStore.FACT, str(named.fact_id))

    async with pg_uow(space) as uow:
        assert await uow.mentions.naming(sorted(tokens), plain, stores=STORES, after=None, limit=500) == [key]
        redacted = dataclasses.replace(named, value="irmã Clara [erased]")
        assert await uow.memory.rewrite_fact(redacted, normalize_value(redacted.value))
        now = await sealed_tokens(pg_uow._keys, space, body_tokens(redacted.value.encode()))
        await uow.mentions.index([(key, now)], replace=True)
    async with pg_uow(space) as uow:
        assert await uow.mentions.naming(sorted(tokens), plain, stores=STORES, after=None, limit=500) == []


async def test_a_lookup_costs_one_statement_whatever_the_size_of_the_space(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    hers = MentionKey(MentionStore.EPISODE, str(uuid7()))
    async with pg_uow(space) as uow:
        await uow.mentions.index([(hers, frozenset({b"t-clara", b"t-other"}))])
        await uow.mentions.index_objects([("turns/short/x/a.bin", frozenset({b"t-clara"}))])

    async def lookup() -> tuple[list[object], list[str], int]:
        async with pg_uow(space) as uow:
            with statements() as seen:
                rows = await uow.mentions.naming(
                    [b"t-clara"], frozenset(), stores=STORES, after=None, limit=500
                )
                objects = await uow.mentions.objects_naming(
                    [b"t-clara"], prefix="turns/", after=None, limit=500
                )
        return rows, objects, len(seen)

    small = await lookup()
    async with pg_uow(space) as uow:
        await uow.mentions.index(
            [
                (MentionKey(MentionStore.FACT, str(uuid7())), frozenset({f"t-{n}".encode()}))
                for n in range(2_000)
            ]
        )
        await uow.mentions.index_objects(
            [(f"turns/short/x/{n}.bin", frozenset({f"t-{n}".encode()})) for n in range(2_000)]
        )
    large = await lookup()

    assert small == large == ([hers], ["turns/short/x/a.bin"], 2)


async def test_an_objects_entry_adds_up_and_is_replaced_by_a_redaction(pg_uow: CloningUnitOfWork) -> None:
    space, key = uuid7(), "turns/short/x/a.bin"
    async with pg_uow(space) as uow:
        await uow.mentions.index_objects([(key, frozenset({b"a"}))], replace=False)
        await uow.mentions.index_objects([(key, frozenset({b"b"}))], replace=False)
        assert await uow.mentions.objects_naming([b"a"], prefix="turns/", after=None, limit=10) == [key]
        assert await uow.mentions.objects_naming([b"b"], prefix="turns/", after=None, limit=10) == [key]
        await uow.mentions.index_objects([(key, frozenset({b"b"}))])
        assert await uow.mentions.objects_naming([b"a"], prefix="turns/", after=None, limit=10) == []
        await uow.mentions.index_objects([(key, frozenset())])
        assert await uow.mentions.objects_naming([b"b"], prefix="turns/", after=None, limit=10) == []
        assert not await uow.mentions.ready("text_mentions_ready")
        await uow.mentions.mark_ready("text_mentions_ready")
        assert await uow.mentions.ready("text_mentions_ready")


async def test_a_turn_group_and_a_signals_write_index_what_they_name_in_their_own_statement(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, handle, now = uuid7(), uuid7(), datetime(2026, 10, 9, tzinfo=UTC)
    row = SignalRow(handle, SELF, b"sealed", 1, 1, 6, now, now)
    async with pg_uow(space) as uow:
        await uow.turns.apply(
            AppliedTurns(intake_ids=(), mentions=(("turns/short/x/t.bin", frozenset({b"t1"})),))
        )
        await uow.signals.store_many(
            [SignalWrite(handle, now, (row,), (), (), named=((SELF, frozenset({b"t2"})),))]
        )
    async with pg_uow(space) as uow:
        assert await uow.mentions.objects_naming([b"t1"], prefix="turns/", after=None, limit=10) == [
            "turns/short/x/t.bin"
        ]
        found = await uow.mentions.naming([b"t2"], frozenset(), stores=STORES, after=None, limit=10)
        assert found == [signals_key(handle, SELF)]
