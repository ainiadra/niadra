"""History search vectors: written with the items, and a backfill for what the write side missed (16.2)."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

from niadra.adapters.memory.fakes import DeterministicModels
from niadra.app.compile import compile_settings
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService, MemorySettings, unembedded
from niadra.domain.compile import MemorySnapshot
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.serve.history import vector_text
from niadra.domain.traits.evidence import EvidenceRef
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.compile.factories import PHONE, T0, marina, uid


class NewEncoder(DeterministicModels):
    """The same vectors under another model name, as after an encoder upgrade."""

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        vectors, _ = await super().embed(texts)
        return vectors, "hash-64-v2"


def service(cell: Cell, models: Any = None, batch: int = 16) -> MemoryService:
    models = models or cell.models
    decide = DecideService(cell.uow, models, cell.clock, decider=cell.decider)
    return MemoryService(
        cell.uow, models, decide, cell.clock, cell.config, MemorySettings(backfill_batch=batch)
    )


async def written_before_vectors(cell: Cell) -> MemorySnapshot:
    """Marina's memory as rows written before items carried vectors: none has one."""
    snapshot = marina()
    async with cell.uow(SPACE_ID) as uow:
        for fact in snapshot.facts:
            await uow.memory.insert_fact(fact, normalize_value(fact.value))
        for episode in snapshot.episodes:
            await uow.memory.add_episode_version(episode)
        for item in snapshot.open_items:
            await uow.memory.insert_open_item(item)
        for obj in snapshot.objects:
            await uow.objects.add_object(obj)
        for action in snapshot.actions:
            await uow.objects.add_action(action)
        for trait in snapshot.traits:
            lasting = replace(trait, expires_at=T0 + timedelta(days=3650))
            await uow.traits.save_trait(lasting, [EvidenceRef("episode", uid(0x201), PHONE)])
    return snapshot


async def stored_vectors(cell: Cell, snapshot: MemorySnapshot) -> dict[str, ItemVector]:
    async with cell.uow(SPACE_ID) as uow:
        found = [
            *await uow.memory.vectors_for(snapshot.profile.handle_ids),
            *await uow.objects.vectors_of(
                [o.object_id for o in snapshot.objects], [a.action_id for a in snapshot.actions]
            ),
            *await uow.traits.vectors_of([t.trait_id for t in snapshot.traits]),
        ]
    return {v.doc_id: v for v in found}


async def test_the_backfill_fills_every_navigable_item_in_small_batches_idempotently(cell: Cell) -> None:
    snapshot = await written_before_vectors(cell)
    backfill = service(cell, batch=4)
    # Eleven rows: three facts, four episodes, an open item, an object, an action and a pattern.
    assert [await backfill.backfill_vectors(SPACE_ID) for _ in range(4)] == [4, 4, 3, 0]
    vectors = await stored_vectors(cell, snapshot)
    # "gestante" is a structured value: it never gets a vector (15.7), and no pass picks it up again.
    assert set(vectors) == {
        f"fact:{uid(0x101)}",
        f"fact:{uid(0x102)}",
        *(f"episode:{uid(0x200 + n)}" for n in (1, 2, 3, 4)),
        f"open_item:{uid(0x401)}",
        f"object:{uid(0x700)}",
        f"action:{uid(0x500)}",
        f"trait:{uid(0x600)}",
    }
    settings = compile_settings(cell.config.space(SPACE_ID))
    fmt = Formatter(settings.timezone, settings.locale)
    models = DeterministicModels()
    for record in (snapshot.episodes[3], snapshot.objects[0], snapshot.actions[0], snapshot.traits[0]):
        [vector] = [v for v in vectors.values() if v.item_id in _ids(record)]
        assert vector.vector == models._embed(vector_text(record, fmt))
        assert vector.model == "hash-64"
    assert await backfill.backfill_vectors(SPACE_ID) == 0
    assert await stored_vectors(cell, snapshot) == vectors


async def test_a_new_encoder_replaces_every_vector_through_the_backfill(cell: Cell) -> None:
    snapshot = await written_before_vectors(cell)
    assert await service(cell).backfill_vectors(SPACE_ID) == 11
    upgraded = service(cell, NewEncoder())
    # The structured fact stays without a vector; the ten others are encoded again.
    assert await upgraded.backfill_vectors(SPACE_ID) == 10
    assert await upgraded.backfill_vectors(SPACE_ID) == 0
    assert {v.model for v in (await stored_vectors(cell, snapshot)).values()} == {"hash-64-v2"}


async def test_a_vector_of_an_older_state_never_lands(cell: Cell) -> None:
    snapshot = await written_before_vectors(cell)
    [invoice] = snapshot.objects
    async with cell.uow(SPACE_ID) as uow:
        pending = [p for p in await unembedded(uow, None, 50, None) if p.item_id == invoice.object_id]
        await uow.objects.set_state(
            invoice.object_id,
            state={"status": "paid"},
            fields={},
            as_of=T0,
            source_id=invoice.source_id,
            state_version=None,
            record_ref=None,
        )
        # The backfill read the open invoice; the paid one arrived before its vector did.
        assert not await uow.objects.set_vector(pending[0], [1.0] * 64, "hash-64")
    await service(cell).backfill_vectors(SPACE_ID)
    [vector] = [v for v in (await stored_vectors(cell, snapshot)).values() if v.item_id == invoice.object_id]
    paid = replace(invoice, state={"status": "paid"})
    settings = compile_settings(cell.config.space(SPACE_ID))
    assert vector.vector == DeterministicModels()._embed(
        vector_text(paid, Formatter(settings.timezone, settings.locale))
    )


async def test_a_new_state_or_a_new_derivation_drops_the_old_vector(cell: Cell) -> None:
    snapshot = await written_before_vectors(cell)
    await service(cell).backfill_vectors(SPACE_ID)
    [invoice], [trait] = snapshot.objects, snapshot.traits
    async with cell.uow(SPACE_ID) as uow:
        await uow.objects.set_state(
            invoice.object_id,
            state={},
            fields={},
            as_of=T0,
            source_id=invoice.source_id,
            state_version=None,
            record_ref=None,
        )
        again = replace(trait, value="billing", expires_at=T0 + timedelta(days=3650))
        await uow.traits.save_trait(again, [EvidenceRef("episode", uid(0x204), PHONE)])
        waiting = {p.item_id for p in await unembedded(uow, None, 50, snapshot.profile)}
    assert waiting == {invoice.object_id, trait.trait_id}


def _ids(record: Any) -> set[Any]:
    return {
        getattr(record, name)
        for name in ("episode_id", "object_id", "action_id", "trait_id")
        if hasattr(record, name)
    }
