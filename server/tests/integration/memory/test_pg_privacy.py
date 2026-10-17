"""`PgMemoryRepository` in the erasure cascade (8.4, L17) and the continuous export (22.3)."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import Relation, normalize_value
from niadra.domain.memory.summaries import TailState
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import Episode, Fact, OpenItem, ProfileAttr
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, RelationType, Sensitivity, Speaker

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


def fact(origin: UUID, value: str) -> Fact:
    return Fact(
        uuid7(),
        origin,
        f"p_{value}",
        value,
        "contact",
        Sensitivity.NORMAL,
        Speaker.CUSTOMER,
        0.8,
        T0,
        T0,
        FactStatus.ACTIVE,
        T0,
        origin_conversation_id="c1",
    )


def episode(origin: UUID, conversation: str) -> Episode:
    return Episode(
        uuid7(),
        1,
        origin,
        uuid7(),
        "whatsapp",
        SOURCE,
        "Pediu segunda via",
        "support",
        "invoice",
        "billing",
        "resolved",
        T0,
        T0,
        conversation_id=conversation,
    )


def item(origin: UUID, events: list[UUID]) -> OpenItem:
    return OpenItem(
        uuid7(),
        origin,
        "Enviar segunda via",
        "company",
        OpenItemStatus.OPEN,
        T0,
        {"session_id": str(uuid7()), "event_ids": [str(e) for e in events]},
    )


async def erase_all(
    pg_uow: PgUnitOfWorkFactory, space: UUID, scope: ErasureScope, limit: int = 2
) -> list[ErasedRow]:
    rows: list[ErasedRow] = []
    while True:
        async with pg_uow(space) as uow:
            batch = await uow.memory.erase_for(scope, limit=limit)
        rows += batch
        if len(batch) < limit:
            return rows


async def test_erased_events_take_mentions_orphaned_facts_and_their_items(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, marina = uuid7(), uuid7()
    e1, e2 = uuid7(), uuid7()
    only, kept = fact(marina, "Marina"), fact(marina, "Rua A")
    doomed_item, kept_item = item(marina, [e1]), item(marina, [e2])
    async with pg_uow(space) as uow:
        for f, events in ((only, [e1]), (kept, [e1, e2])):
            await uow.memory.insert_fact(f, normalize_value(f.value))
            await uow.memory.add_mentions(MemoryRef(marina, f.fact_id), events)
        await uow.memory.add_relations(
            [
                Relation(
                    MemoryRef(marina, only.fact_id), MemoryRef(marina, kept.fact_id), RelationType.SUPERSEDES
                )
            ]
        )
        await uow.memory.insert_open_item(doomed_item)
        await uow.memory.insert_open_item(kept_item)
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c1", T0, (e1,))
    rows = await erase_all(pg_uow, space, scope)
    assert sorted(r.table for r in rows) == ["fact_mentions", "fact_mentions", "facts", "open_items"]
    assert all(r.change_seq for r in rows)
    assert {"origin_handle_id": str(marina), "fact_id": str(only.fact_id)} in [r.key for r in rows]
    async with pg_uow(space) as uow:
        [left] = await uow.memory.facts_for([marina])
        assert (left.fact_id, left.evidence_event_ids) == (kept.fact_id, (e2,))
        assert await uow.memory.relations_for([marina]) == []
        assert [i.item_id for i in await uow.memory.open_items_for([marina])] == [kept_item.item_id]


async def test_a_handle_erasure_takes_everything_anchored_on_it(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina, other, profile = uuid7(), uuid7(), uuid7(), uuid7()
    kept = fact(other, "Bia")
    async with pg_uow(space) as uow:
        for f in (fact(marina, "Marina"), fact(marina, "Rua B"), kept):
            await uow.memory.insert_fact(f, normalize_value(f.value))
            await uow.memory.add_mentions(MemoryRef(f.origin_handle_id, f.fact_id), [uuid7()])
        first = (await uow.memory.facts_for([marina]))[0]
        assert await uow.memory.set_vector(PendingVector(first), [0.1] * 4, "e")
        await uow.memory.add_episode_version(episode(marina, "c1"))
        await uow.memory.insert_open_item(item(marina, [uuid7()]))
        await uow.memory.put_attr(ProfileAttr(marina, "name", "Marina", SOURCE, T0))
        await uow.memory.put_tail_summary(profile, "short", TailState("resumo", 1, T0, 1))
    rows = await erase_all(
        pg_uow, space, ErasureScope(uuid7(), TargetKind.HANDLE, (marina,), profile, None, T0)
    )
    assert sorted({r.table for r in rows}) == [
        "episodes",
        "fact_mentions",
        "facts",
        "open_items",
        "profile_attrs",
        "tail_summaries",
    ]
    async with pg_uow(space) as uow:
        assert [f.fact_id for f in await uow.memory.facts_for([marina, other])] == [kept.fact_id]
        assert await uow.memory.episodes_for([marina]) == []
        assert await uow.memory.attrs_for([marina]) == []
        assert await uow.memory.tail_summary(profile, "short") is None
        left = (
            await uow.session.execute(
                text("SELECT count(*) FROM fact_mentions WHERE origin_handle_id = :h"), {"h": marina}
            )
        ).scalar_one()
        assert left == 0


async def test_a_conversation_erasure_takes_its_episodes_only(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    kept = episode(marina, "c2")
    async with pg_uow(space) as uow:
        await uow.memory.add_episode_version(episode(marina, "c1"))
        await uow.memory.add_episode_version(kept)
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c1", T0)
    assert [r.table for r in await erase_all(pg_uow, space, scope, limit=10)] == ["episodes"]
    async with pg_uow(space) as uow:
        assert [e.episode_id for e in await uow.memory.episodes_for([marina])] == [kept.episode_id]


async def test_export_reads_decrypted_rows_by_change_seq(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    f = fact(marina, "Marina")
    event = uuid7()
    async with pg_uow(space) as uow:
        await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.add_mentions(MemoryRef(marina, f.fact_id), [event])
        await uow.memory.add_episode_version(episode(marina, "c1"))
        await uow.memory.insert_open_item(item(marina, [event]))
    async with pg_uow(space) as uow:
        tables = {}
        for table in ("facts", "fact_mentions", "episodes", "open_items"):
            tables[table] = await uow.memory.export_rows(table, after_seq=0, upto_seq=2**62, limit=10)
    [exported] = tables["facts"]
    assert (exported["fact_id"], exported["value"], "value_ct" in exported) == (f.fact_id, "Marina", False)
    assert tables["fact_mentions"][0]["event_id"] == event
    assert tables["episodes"][0]["summary"] == "Pediu segunda via"
    assert tables["episodes"][0]["is_current"] is True
    assert tables["open_items"][0]["description"] == "Enviar segunda via"
    seqs = [rows[0]["change_seq"] for rows in tables.values()]
    assert seqs == sorted(seqs)
    async with pg_uow(space) as uow:
        assert await uow.memory.export_rows("facts", after_seq=seqs[0], upto_seq=2**62, limit=10) == []
