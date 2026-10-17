"""`PgMemoryRepository` for purposes per item (13, 6.6): untagged rows with the attributes that decide
their purposes, tags that go on every version of a conversation, expiry by the last purpose, removal with
everything a row holds, and objections that leave with the customer on erasure."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import Relation, normalize_value
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.purposes import ItemKey, ItemKind, PurposeTag, Tagged
from niadra.domain.records import Episode, OpenItem
from niadra.domain.vocabulary import OpenItemStatus, RelationType
from tests.integration.memory.test_pg_memory import fact
from tests.integration.memory.test_pg_values import value

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")
BILLING = PurposeTag("billing", "contract")
MARKETING = PurposeTag("marketing", "consent")


def episode(origin: UUID, version: int = 1, episode_id: UUID | None = None) -> Episode:
    return Episode(
        episode_id=episode_id or uuid7(),
        version=version,
        origin_handle_id=origin,
        session_id=uuid7(),
        channel="whatsapp",
        source_id=SOURCE,
        summary="Pediu a segunda via",
        intent="request",
        category="invoice",
        normalized_category="billing",
        outcome="resolved",
        started_at=T0,
        ended_at=T0,
    )


def item(origin: UUID, session_id: UUID, event_id: UUID) -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=origin,
        description="Enviar a segunda via",
        owner="company",
        status=OpenItemStatus.RESOLVED,
        created_at=T0,
        origin={"session_id": str(session_id), "source_id": str(SOURCE), "event_ids": [str(event_id)]},
        closed_at=T0,
    )


async def test_untagged_rows_carry_what_decides_their_purposes(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    name, said, feedback = fact(marina, "Rafa"), uuid7(), uuid7()
    first = episode(marina)
    promise = item(marina, first.session_id, said)
    cep = value(marina, "45778-204", evidence=(said,))
    async with pg_uow(space) as uow:
        await uow.memory.insert_fact(name, normalize_value(name.value))
        await uow.memory.add_mentions(MemoryRef(marina, name.fact_id), [said])
        await uow.memory.add_mentions(MemoryRef(marina, name.fact_id), [feedback], feedback=True)
        await uow.memory.add_episode_version(first)
        await uow.memory.insert_open_item(promise)
        await uow.memory.insert_values([cep])
    async with pg_uow(space) as uow:
        found = {u.key.kind: u for u in await uow.memory.untagged(handle_ids=[marina], limit=10)}
        assert await uow.memory.untagged(handle_ids=[uuid7()], limit=10) == []
        assert len(await uow.memory.untagged(handle_ids=None, limit=10)) == 4
    # The company's feedback is never evidence: it names no purpose either.
    assert (found[ItemKind.FACT].category, found[ItemKind.FACT].event_ids) == ("contact", (said,))
    assert found[ItemKind.EPISODE].category == "billing"
    assert (found[ItemKind.EPISODE].session_id, found[ItemKind.EPISODE].source_id) == (
        first.session_id,
        SOURCE,
    )
    assert (found[ItemKind.OPEN_ITEM].session_id, found[ItemKind.OPEN_ITEM].event_ids) == (
        first.session_id,
        (said,),
    )
    assert (found[ItemKind.VALUE].episode_id, found[ItemKind.VALUE].event_ids) == (cep.episode_id, (said,))


async def test_tags_go_on_every_version_and_come_back_on_reads(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    first = episode(marina)
    second = episode(marina, 2, first.episode_id)
    name = fact(marina, "Rafa")
    async with pg_uow(space) as uow:
        await uow.memory.add_episode_version(first)
        await uow.memory.add_episode_version(second, replaces=first)
        await uow.memory.insert_fact(name, normalize_value(name.value))
    async with pg_uow(space) as uow:
        await uow.memory.set_purposes(
            [
                Tagged(ItemKey(ItemKind.EPISODE, marina, first.episode_id), (BILLING,)),
                Tagged(ItemKey(ItemKind.FACT, marina, name.fact_id), ()),
            ]
        )
    async with pg_uow(space) as uow:
        versions = await uow.memory.episode_versions(marina, first.episode_id)
        [stored] = await uow.memory.facts_for([marina])
        assert await uow.memory.untagged(handle_ids=[marina], limit=10) == []
        tagged = await uow.memory.tagged_with([marina], "billing")
    assert [v.purposes for v in versions] == [(BILLING,), (BILLING,)]
    assert stored.purposes == ()
    assert tagged == [Tagged(ItemKey(ItemKind.EPISODE, marina, first.episode_id), (BILLING,))]

    # A changed document tags again the rows no rule named.
    async with pg_uow(space) as uow:
        assert await uow.memory.untag_empty(limit=10) == 1
    async with pg_uow(space) as uow:
        [again] = await uow.memory.untagged(handle_ids=[marina], limit=10)
    assert again.key == ItemKey(ItemKind.FACT, marina, name.fact_id)


async def test_a_row_expires_when_its_last_purpose_does(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    both, only_marketing, untagged = (
        fact(marina, "Rafa"),
        fact(marina, "Ana", "nickname"),
        fact(marina, "x", "z"),
    )
    promise = item(marina, uuid7(), uuid7())
    async with pg_uow(space) as uow:
        for f in (both, only_marketing, untagged):
            await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.insert_open_item(promise)
        await uow.memory.set_purposes(
            [
                Tagged(ItemKey(ItemKind.FACT, marina, both.fact_id), (BILLING, MARKETING)),
                Tagged(ItemKey(ItemKind.FACT, marina, only_marketing.fact_id), (MARKETING,)),
                Tagged(ItemKey(ItemKind.OPEN_ITEM, marina, promise.item_id), (MARKETING,)),
            ]
        )
    marketing_ends = {"marketing": T0 + timedelta(days=1)}
    async with pg_uow(space) as uow:
        expired = await uow.memory.purpose_expired(marketing_ends, limit=10)
        # Billing has no cutoff: the row it also holds stays.
        assert set(expired) == {
            ItemKey(ItemKind.FACT, marina, only_marketing.fact_id),
            ItemKey(ItemKind.OPEN_ITEM, marina, promise.item_id),
        }
        both_end = {"marketing": T0 + timedelta(days=1), "billing": T0 + timedelta(days=1)}
        assert ItemKey(ItemKind.FACT, marina, both.fact_id) in await uow.memory.purpose_expired(
            both_end, limit=10
        )
        assert await uow.memory.purpose_expired({"billing": T0 + timedelta(days=1)}, limit=10) == []
        assert await uow.memory.purpose_expired({"marketing": T0}, limit=10) == []


async def test_removing_a_row_takes_its_mentions_relations_and_versions(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    a, b = fact(marina, "Rafa"), fact(marina, "Ana", "nickname")
    first = episode(marina)
    second = episode(marina, 2, first.episode_id)
    cep = value(marina, "45778-204")
    async with pg_uow(space) as uow:
        for f in (a, b):
            await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.add_mentions(MemoryRef(marina, a.fact_id), [uuid7()])
        await uow.memory.add_relations(
            [Relation(MemoryRef(marina, b.fact_id), MemoryRef(marina, a.fact_id), RelationType.SUPERSEDES)]
        )
        await uow.memory.add_episode_version(first)
        await uow.memory.add_episode_version(second, replaces=first)
        await uow.memory.insert_values([cep])
    async with pg_uow(space) as uow:
        erased = await uow.memory.remove_items(
            [
                ItemKey(ItemKind.FACT, marina, a.fact_id),
                ItemKey(ItemKind.EPISODE, marina, first.episode_id),
                ItemKey(ItemKind.VALUE, marina, cep.value_id),
            ]
        )
    assert sorted(r.table for r in erased) == [
        "episodes",
        "episodes",
        "fact_mentions",
        "facts",
        "settled_values",
    ]
    async with pg_uow(space) as uow:
        assert [f.fact_id for f in await uow.memory.facts_for([marina])] == [b.fact_id]
        assert await uow.memory.relations_for([marina]) == []
        assert await uow.memory.episode_versions(marina, first.episode_id) == []


async def test_objections_are_kept_per_handle_and_leave_with_the_customer(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, marina, other = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        await uow.memory.add_objection([marina, other], "marketing", T0)
        await uow.memory.add_objection([marina], "marketing", T0 + timedelta(days=1))
        await uow.memory.add_objection([marina], "billing", T0)
    async with pg_uow(space) as uow:
        assert await uow.memory.objections([marina]) == {marina: frozenset({"billing", "marketing"})}
        assert await uow.memory.objection_list([marina]) == [
            (marina, "billing", T0),
            (marina, "marketing", T0),
        ]
        first = await uow.memory.objected_handles(after=None, limit=2)
        rest = await uow.memory.objected_handles(after=first[-1], limit=2)
    assert len(first) == 2
    assert len(first + rest) == 3
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (marina,), None, None, T0)
    async with pg_uow(space) as uow:
        erased = [r for r in await uow.memory.erase_for(scope, limit=100) if r.table == "purpose_objections"]
    assert len(erased) == 2
    async with pg_uow(space) as uow:
        assert await uow.memory.objections([marina]) == {}
        assert await uow.memory.objections([other]) == {other: frozenset({"marketing"})}


async def test_the_pass_state_is_kept(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with pg_uow.system(space) as uow:
        assert await uow.memory.purpose_state("purposes_fingerprint") is None
        await uow.memory.set_purpose_state("purposes_fingerprint", 42)
        await uow.memory.set_purpose_state("purposes_fingerprint", 43)
    async with pg_uow.system(space) as uow:
        assert await uow.memory.purpose_state("purposes_fingerprint") == 43
