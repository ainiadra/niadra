"""History search vectors in PostgreSQL: sealed in their rows, filled by the backfill, never stale (16.2)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels, FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService, unembedded
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.records import AgentAction, BusinessObject, Episode, Fact, ObjectKey, OpenItem, Trait
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.vocabulary import ActionStatus, FactStatus, OpenItemStatus, Sensitivity, Speaker
from niadra.testing.cell import sample_config

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


class NewEncoder(DeterministicModels):
    async def embed(self, texts):  # type: ignore[no-untyped-def]
        vectors, _ = await super().embed(texts)
        return vectors, "hash-64-v2"


def fact(origin: UUID, value: str) -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=origin,
        predicate="note",
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.75,
        valid_at=T0,
        created_at=T0,
        status=FactStatus.ACTIVE,
        last_confirmed_at=T0,
    )


async def history(pg_uow: PgUnitOfWorkFactory, space: UUID, origin: UUID) -> dict[str, Any]:
    """One row of every navigable kind, written as before items carried vectors."""
    rows: dict[str, Any] = {
        "fact": fact(origin, "prefere ser atendida pela manhã"),
        "structured": fact(origin, "Bia"),
        "episode": Episode(
            uuid7(),
            1,
            origin,
            uuid7(),
            "whatsapp",
            SOURCE,
            "Pediu a segunda via da fatura",
            "support",
            "invoice",
            "billing",
            "resolved",
            T0,
            T0,
            resolution="segunda via enviada",
        ),
        "item": OpenItem(uuid7(), origin, "Enviar a segunda via", "company", OpenItemStatus.OPEN, T0, {}),
        "object": BusinessObject(
            uuid7(), ObjectKey("invoice", "erp", "0823"), {"status": "open"}, T0, SOURCE, origin
        ),
    }
    rows["action"] = AgentAction(
        uuid7(),
        uuid7(),
        SOURCE,
        "credit",
        ActionStatus.CONFIRMED,
        T0,
        rows["object"].object_id,
        result="crédito de R$ 40",
    )
    rows["trait"] = Trait(
        uuid7(),
        uuid7(),
        1,
        "recurring_complaint",
        "billing",
        "volatile",
        "r1",
        T0,
        T0,
        T0 + timedelta(days=3650),
        0.9,
    )
    async with pg_uow(space) as uow:
        for key in ("fact", "structured"):
            await uow.memory.insert_fact(rows[key], normalize_value(rows[key].value))
        await uow.memory.add_episode_version(rows["episode"])
        await uow.memory.insert_open_item(rows["item"])
        await uow.objects.add_object(rows["object"])
        await uow.objects.add_action(rows["action"])
        await uow.traits.save_trait(
            rows["trait"], [EvidenceRef("episode", rows["episode"].episode_id, origin)]
        )
    return rows


def service(pg_uow: PgUnitOfWorkFactory, space: UUID, models: Any = None) -> MemoryService:
    models = models or DeterministicModels()
    clock = FixedClock()
    config = StaticConfig(sample_config(space))
    return MemoryService(
        pg_uow, models, DecideService(pg_uow, models, clock, decider=DeterministicDecider()), clock, config
    )


async def vectors(
    pg_uow: PgUnitOfWorkFactory, space: UUID, origin: UUID, rows: dict[str, Any]
) -> dict[str, Any]:
    async with pg_uow(space) as uow:
        found = [
            *await uow.memory.vectors_for([origin]),
            *await uow.objects.vectors_of([rows["object"].object_id], [rows["action"].action_id]),
            *await uow.traits.vectors_of([rows["trait"].trait_id]),
        ]
    return {v.doc_id: v for v in found}


async def test_the_backfill_seals_a_vector_into_every_navigable_row(pg_uow: PgUnitOfWorkFactory) -> None:
    space, origin = uuid7(), uuid7()
    rows = await history(pg_uow, space, origin)
    async with pg_uow(space) as uow:
        before = (
            await uow.session.execute(
                text("SELECT summary_ct, dek_version FROM episodes WHERE episode_id = :e"),
                {"e": rows["episode"].episode_id},
            )
        ).one()
    backfill = service(pg_uow, space)
    assert await backfill.backfill_vectors(space) == 7
    assert await backfill.backfill_vectors(space) == 0
    found = await vectors(pg_uow, space, origin, rows)
    assert set(found) == {
        f"fact:{rows['fact'].fact_id}",
        f"episode:{rows['episode'].episode_id}",
        f"open_item:{rows['item'].item_id}",
        f"object:{rows['object'].object_id}",
        f"action:{rows['action'].action_id}",
        f"trait:{rows['trait'].trait_id}",
    }
    assert {(v.model, len(v.vector)) for v in found.values()} == {("hash-64", 64)}
    # Insights read the same stored vector by episode, across customers.
    async with pg_uow(space) as uow:
        [by_episode] = await uow.memory.episode_vectors([rows["episode"].episode_id, uuid7()])
    assert by_episode == found[f"episode:{rows['episode'].episode_id}"]
    async with pg_uow(space) as uow:
        after = (
            await uow.session.execute(
                text(
                    "SELECT summary_ct, dek_version, embedding_ct, embedding_model, embedding_dim, "
                    "embedding_dek_version FROM episodes WHERE episode_id = :e"
                ),
                {"e": rows["episode"].episode_id},
            )
        ).one()
        structured = (
            await uow.session.execute(
                text("SELECT embedding_ct, embedding_dim FROM facts WHERE fact_id = :f"),
                {"f": rows["structured"].fact_id},
            )
        ).one()
    # The vector is sealed on its own: the summary's ciphertext is the one written with the episode.
    assert (after.summary_ct, after.dek_version) == (before.summary_ct, before.dek_version)
    assert (after.embedding_model, after.embedding_dim, after.embedding_dek_version) == ("hash-64", 64, 1)
    assert len(after.embedding_ct) > 2 * 64
    # A structured value never gets a vector (15.7), and the mark keeps the backfill from reading it again.
    assert (structured.embedding_ct, structured.embedding_dim) == (None, 0)


async def test_a_new_encoder_is_backfilled(pg_uow: PgUnitOfWorkFactory) -> None:
    space, origin = uuid7(), uuid7()
    rows = await history(pg_uow, space, origin)
    assert await service(pg_uow, space).backfill_vectors(space) == 7
    upgraded = service(pg_uow, space, NewEncoder())
    assert await upgraded.backfill_vectors(space) == 6
    assert await upgraded.backfill_vectors(space) == 0
    assert {v.model for v in (await vectors(pg_uow, space, origin, rows)).values()} == {"hash-64-v2"}


async def test_a_vector_lands_only_on_the_state_it_was_computed_from(pg_uow: PgUnitOfWorkFactory) -> None:
    space, origin = uuid7(), uuid7()
    rows = await history(pg_uow, space, origin)
    async with pg_uow(space) as uow:
        pending = {p.item_id: p for p in await unembedded(uow, None, 50, None)}
    obj, trait = rows["object"], rows["trait"]
    async with pg_uow(space) as uow:
        await uow.objects.set_state(
            obj.object_id,
            state={"status": "paid"},
            fields={},
            as_of=T0,
            source_id=SOURCE,
            state_version=None,
            record_ref=None,
        )
        await uow.traits.save_trait(replace(trait, value="invoice"), [])
    async with pg_uow(space) as uow:
        assert not await uow.objects.set_vector(pending[obj.object_id], [1.0] * 64, "hash-64")
        assert not await uow.traits.set_vector(pending[trait.trait_id], [1.0] * 64, "hash-64")
        # Unfenced rows never change their text in place.
        assert await uow.objects.set_vector(pending[rows["action"].action_id], [1.0] * 64, "hash-64")
    await service(pg_uow, space).backfill_vectors(space)
    async with pg_uow(space) as uow:
        assert await unembedded(uow, None, 50, None) == []
        # A new state or a new derivation drops the vector the old text had.
        await uow.objects.set_state(
            obj.object_id,
            state={},
            fields={},
            as_of=T0,
            source_id=SOURCE,
            state_version=None,
            record_ref=None,
        )
        await uow.traits.save_trait(replace(trait, value="billing"), [])
        waiting = {p.item_id for p in await unembedded(uow, None, 50, None)}
    assert waiting == {obj.object_id, trait.trait_id}


async def test_a_stored_vector_does_not_make_an_object_look_recent(pg_uow: PgUnitOfWorkFactory) -> None:
    space, owner = uuid7(), uuid7()
    old = BusinessObject(
        uuid7(), ObjectKey("invoice", "erp", "0001"), {}, T0 - timedelta(days=30), SOURCE, owner
    )
    new = BusinessObject(uuid7(), ObjectKey("invoice", "erp", "0002"), {}, T0, SOURCE, owner)
    async with pg_uow(space) as uow:
        await uow.objects.add_object(old)
        await uow.objects.add_object(new)
        [pending] = [p for p in await unembedded(uow, None, 50, None) if p.item_id == old.object_id]
        # The vector bumps the old invoice's `change_seq` past the new one's.
        assert await uow.objects.set_vector(pending, [1.0] * 64, "hash-64")
    async with pg_uow(space) as uow:
        assert [o.object_id for o in await uow.objects.objects_for([owner])] == [new.object_id, old.object_id]
