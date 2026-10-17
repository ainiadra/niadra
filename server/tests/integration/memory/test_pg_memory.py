from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import Relation, normalize_value
from niadra.domain.memory.summaries import TailState
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.records import Episode, Fact, ObjectKey, OpenItem, ProfileAttr
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, RelationType, Sensitivity, Speaker

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


def fact(origin: UUID, value: str, predicate: str = "preferred_name") -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=origin,
        predicate=predicate,
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.75,
        valid_at=T0,
        created_at=T0,
        status=FactStatus.ACTIVE,
        last_confirmed_at=T0,
        origin_conversation_id="c1",
        object_ref=ObjectKey("invoice", "erp", "0823"),
        mention_handle_ids=(uuid7(),),
    )


@pytest.fixture
def space() -> UUID:
    return uuid7()


async def test_facts_dedupe_by_hmac_live_in_partitions_and_are_sealed(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    origin = uuid7()
    marina = fact(origin, "Marina Souza")
    events = [uuid7(), uuid7()]
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_fact(marina, normalize_value(marina.value))
        assert not await uow.memory.insert_fact(
            fact(origin, "marina  souza"), normalize_value("marina  souza")
        )
        assert await uow.memory.insert_fact(fact(uuid7(), "Marina Souza"), normalize_value("Marina Souza"))
        assert await uow.memory.add_mentions(MemoryRef(origin, marina.fact_id), events) == 2
        assert await uow.memory.add_mentions(MemoryRef(origin, marina.fact_id), events) == 0
    async with pg_uow(space) as uow:
        found = await uow.memory.find_fact(origin, "preferred_name", normalize_value("MARINA SOUZA"))
        [listed] = await uow.memory.facts_for([origin])
        raw = await uow.session.execute(
            text("SELECT tableoid::regclass::text, value_ct FROM facts WHERE fact_id = :f"),
            {"f": marina.fact_id},
        )
        partition, ciphertext = raw.one()
    assert found == listed
    assert found == replace(marina, evidence_event_ids=tuple(sorted(events)))
    assert partition.startswith("facts_")
    assert b"Marina" not in ciphertext


async def test_fact_updates_relations_and_embeddings(pg_uow: PgUnitOfWorkFactory, space: UUID) -> None:
    origin = uuid7()
    a, b = fact(origin, "Ana"), fact(origin, "Bia")
    async with pg_uow(space) as uow:
        for f in (a, b):
            await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.update_fact(
            replace(
                a, status=FactStatus.EXPIRED, invalid_at=T0 + timedelta(days=1), expired_at=T0, times_seen=3
            )
        )
        edge = Relation(MemoryRef(origin, b.fact_id), MemoryRef(origin, a.fact_id), RelationType.CONTRADICTS)
        await uow.memory.add_relations([edge, edge])
        assert await uow.memory.set_vector(PendingVector(b), [0.5, -0.25, 1.0], "hash-3")
    async with pg_uow(space) as uow:
        expired = await uow.memory.facts_for([origin], (FactStatus.EXPIRED,))
        missing = await uow.memory.unembedded(model=None, handle_ids=[origin], limit=10)
        [vector] = await uow.memory.vectors_for([origin])
        relations = await uow.memory.relations_for([origin])
        [still] = await uow.memory.facts_for([origin])
        dim = (
            await uow.session.execute(
                text("SELECT embedding_dim FROM facts WHERE fact_id = :f"), {"f": b.fact_id}
            )
        ).scalar_one()
    assert [(f.value, f.times_seen, f.invalid_at) for f in expired] == [("Ana", 3, T0 + timedelta(days=1))]
    assert missing == []
    assert (vector.vector, vector.model, dim) == ([0.5, -0.25, 1.0], "hash-3", 3)
    assert relations == [edge]
    assert still.value == "Bia"


async def test_episode_versions_keep_one_current(pg_uow: PgUnitOfWorkFactory, space: UUID) -> None:
    first_origin, second_origin, session = uuid7(), uuid7(), uuid7()
    v1 = Episode(
        uuid7(),
        1,
        first_origin,
        session,
        "whatsapp",
        SOURCE,
        "Cliente Marina ligou",
        "complaint",
        "invoice",
        "billing",
        "pending",
        T0,
        T0 + timedelta(minutes=5),
        resolution=None,
        sentiment=-0.5,
    )
    v2 = replace(
        v1, version=2, origin_handle_id=second_origin, outcome="resolved", resolution="Crédito lançado"
    )
    async with pg_uow(space) as uow:
        await uow.memory.add_episode_version(v1)
        await uow.memory.add_episode_version(v2, v1)
    async with pg_uow(space) as uow:
        current = await uow.memory.current_episode([first_origin, second_origin], session)
        listed = await uow.memory.episodes_for([first_origin, second_origin])
        by_id = await uow.memory.get_episode(v1.episode_id)
        current_rows = text("SELECT count(*) FROM episodes WHERE is_current AND space_id = :s")
        raw = (await uow.session.execute(current_rows, {"s": space})).scalar_one()
    assert current == by_id
    assert listed == [v2]
    assert current is not None
    assert current.resolution == "Crédito lançado"
    assert raw == 1


async def test_open_items_attrs_and_tail_summaries(pg_uow: PgUnitOfWorkFactory, space: UUID) -> None:
    origin, profile = uuid7(), uuid7()
    item = OpenItem(
        uuid7(),
        origin,
        "Creditar a fatura 0823",
        "company",
        OpenItemStatus.OPEN,
        T0,
        {"session_id": "s1", "noted_at": T0.isoformat()},
        due_at=T0 + timedelta(days=2),
        object=ObjectKey("invoice", "erp", "0823"),
        expected_operation="invoice.credited",
    )
    attr = ProfileAttr(origin, "city", "Recife", SOURCE, T0)
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_open_item(item)
        assert not await uow.memory.insert_open_item(item)
        await uow.memory.put_attr(attr)
        await uow.memory.put_attr(replace(attr, value="Natal", observed_at=T0 + timedelta(hours=1)))
        await uow.memory.put_tail_summary(profile, "short", TailState("2026-09-22 billing", 1, T0, 3))
    closed_by = uuid7()
    async with pg_uow(space) as uow:
        [found] = await uow.memory.open_items_for([origin])
        await uow.memory.update_open_item(
            replace(found, status=OpenItemStatus.RESOLVED, closed_by_action_id=closed_by, closed_at=T0)
        )
        [city] = await uow.memory.attrs_for([origin])
        summary = await uow.memory.tail_summary(profile, "short")
        state = await uow.memory.tail_state(profile, "short")
    async with pg_uow(space) as uow:
        [resolved] = await uow.memory.open_items_for([origin], (OpenItemStatus.RESOLVED,))
        assert await uow.memory.open_items_for([origin]) == []
    assert found == item
    assert resolved.closed_by_action_id == closed_by
    assert (city.value, city.observed_at) == ("Natal", T0 + timedelta(hours=1))
    assert summary == "2026-09-22 billing"
    assert state == TailState("2026-09-22 billing", 1, T0, 3)


async def test_a_closed_item_keeps_what_closed_it_in_its_origin_until_it_reopens(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    origin = uuid7()
    item = OpenItem(
        uuid7(),
        origin,
        "Creditar a fatura 0823",
        "company",
        OpenItemStatus.OPEN,
        T0,
        {"session_id": "s1", "event_ids": ["e1"], "noted_at": T0.isoformat()},
    )
    by_erp = {"kind": "system_event", "action_id": None, "event_id": str(uuid7()), "operation": "credit"}
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_open_item(item)
    async with pg_uow(space) as uow:
        # The writer's item is the one it read: what closed it joins the origin the row holds.
        closed = replace(
            item, status=OpenItemStatus.RESOLVED, closed_at=T0, origin={**item.origin, "closed_by": by_erp}
        )
        await uow.memory.update_open_item(closed)
    async with pg_uow(space) as uow:
        [resolved] = await uow.memory.open_items_for([origin], (OpenItemStatus.RESOLVED,))
    assert resolved.origin == {**item.origin, "closed_by": by_erp}
    async with pg_uow(space) as uow:
        # An overdue sweep or a write that does not know what closed it keeps the origin as it is.
        await uow.memory.update_open_item(replace(resolved, origin=item.origin))
        [same] = await uow.memory.open_items_for([origin], (OpenItemStatus.RESOLVED,))
        assert same.origin["closed_by"] == by_erp
        reopened = replace(
            same,
            status=OpenItemStatus.OPEN,
            closed_at=None,
            origin={k: v for k, v in same.origin.items() if k != "closed_by"},
        )
        await uow.memory.update_open_item(reopened)
    async with pg_uow(space) as uow:
        [back] = await uow.memory.open_items_for([origin])
    assert back.origin == item.origin


async def test_a_restated_item_takes_the_new_structure_and_keeps_its_text_and_status(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    origin, about = uuid7(), uuid7()
    item = OpenItem(
        uuid7(),
        origin,
        "Protocolar a réplica até sexta-feira",
        "customer",
        OpenItemStatus.OPEN,
        T0,
        {"session_id": "s1"},
    )
    case = ObjectKey("legal_case", "firm", "case-1")
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_open_item(item)
        await uow.memory.restate_item(
            replace(
                item,
                description="ignored",
                status=OpenItemStatus.RESOLVED,
                due_at=T0 + timedelta(days=6),
                object=case,
                expected_operation="file",
                about_handle_id=about,
            )
        )
    async with pg_uow(space) as uow:
        [found] = await uow.memory.open_items_for([origin])
    assert found == replace(
        item, due_at=T0 + timedelta(days=6), object=case, expected_operation="file", about_handle_id=about
    )


async def test_a_reworded_item_is_sealed_again_and_loses_its_vector(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    origin = uuid7()
    item = OpenItem(
        uuid7(),
        origin,
        "Prazo pedido pelo cliente",
        "customer",
        OpenItemStatus.OPEN,
        T0,
        {"session_id": "s1"},
    )
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_open_item(item)
        [pending] = await uow.memory.unembedded(model="enc", handle_ids=[origin], limit=5)
        await uow.memory.restate_item(replace(item, description="Deixar pronta a minuta"), reworded=True)
        # A vector made of the old words never lands on the new ones.
        assert not await uow.memory.set_vector(pending, [0.3, 0.4], "enc")
    async with pg_uow(space) as uow:
        [found] = await uow.memory.open_items_for([origin])
        [again] = await uow.memory.unembedded(model="enc", handle_ids=[origin], limit=5)
    assert found.description == "Deixar pronta a minuta"
    assert again.record.item_id == item.item_id  # type: ignore[union-attr]


async def test_open_items_due_crosses_partitions_latest_first(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    def due(hours: int, status: OpenItemStatus = OpenItemStatus.OPEN) -> OpenItem:
        return OpenItem(
            uuid7(), uuid7(), f"item {hours}", "company", status, T0, {}, due_at=T0 + timedelta(hours=hours)
        )

    items = [due(5), due(1), due(48), due(2, OpenItemStatus.RESOLVED)]
    async with pg_uow(space) as uow:
        for item in items:
            await uow.memory.insert_open_item(item)
    async with pg_uow(space) as uow:
        found = await uow.memory.open_items_due(T0 + timedelta(hours=24), limit=10)
        first = await uow.memory.open_items_due(T0 + timedelta(hours=24), limit=1)
    # Latest first: an item that just fell due never waits behind old ones for the limit.
    assert [i.description for i in found] == ["item 5", "item 1"]
    assert [i.description for i in first] == ["item 5"]


async def test_episode_summaries_are_bound_to_the_episode_id_for_other_readers(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    # The insights module opens summaries itself with `episodes:{episode_id}:summary_ct`.
    episode = Episode(
        uuid7(),
        3,
        uuid7(),
        uuid7(),
        "chat",
        SOURCE,
        "Resumo",
        "request",
        "invoice",
        "billing",
        "resolved",
        T0,
        T0,
    )
    async with pg_uow(space) as uow:
        await uow.memory.add_episode_version(episode)
        ct, version = (
            await uow.session.execute(
                text("SELECT summary_ct, dek_version FROM episodes WHERE episode_id = :e"),
                {"e": episode.episode_id},
            )
        ).one()
    # The fixture's own vault: data keys live in its memory.
    sealer = Sealer(pg_uow._keys, space)
    opened = await sealer.open_text(
        ct, version, table="episodes", row=episode.episode_id, column="summary_ct"
    )
    assert opened == "Resumo"


async def test_sessions_seen_and_the_origin_of_each_edge_round_trip(
    pg_uow: PgUnitOfWorkFactory, space: UUID
) -> None:
    # Each edge says who drew it, and the primary key leaves the origin out, so
    # the first edge of a pair and type stays.
    origin = uuid7()
    counted, legacy = (
        replace(fact(origin, "Ana"), sessions_seen=3),
        replace(fact(origin, "Bia"), sessions_seen=1),
    )
    band = Relation(
        MemoryRef(origin, legacy.fact_id),
        MemoryRef(origin, counted.fact_id),
        RelationType.CONTRADICTS,
        "band",
    )
    rule = Relation(
        MemoryRef(origin, counted.fact_id), MemoryRef(origin, legacy.fact_id), RelationType.SUPERSEDES, "rule"
    )
    async with pg_uow(space) as uow:
        for f in (counted, legacy):
            await uow.memory.insert_fact(f, normalize_value(f.value))
        await uow.memory.add_relations([band, rule, replace(band, origin="model")])
        await uow.memory.update_fact(replace(counted, sessions_seen=4))
    async with pg_uow(space) as uow:
        found = {f.value: f.sessions_seen for f in await uow.memory.facts_for([origin])}
        everything = await uow.memory.relations_for([origin])
        only_band = await uow.memory.relations_for([origin], origin="band")
    assert found == {"Ana": 4, "Bia": 1}
    assert sorted(everything, key=lambda r: r.type.value) == [band, rule]
    assert only_band == [band]
