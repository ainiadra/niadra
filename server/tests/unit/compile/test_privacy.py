"""Packs, manifests and pins in the erasure cascade (8.4), and the receipt writer's projections (5.11)."""

from datetime import UTC, datetime, timedelta

from niadra.adapters.memory.compile import InMemoryPackRepository
from niadra.adapters.memory.serve import InMemoryServeRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.compile import StoredPack, pack_key, profile_target
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.serve.records import PIN_TTL, AgentWatermark, ConversationPin

T0 = datetime(2026, 9, 22, 14, tzinfo=UTC)


def stored(profile, variant: str = "chat") -> StoredPack:
    return StoredPack(
        pack_key(profile, variant), profile_target(profile), 1, "compiler-1.0", 1, b"{}", "h", "mh", T0
    )


async def save(cell: "Cell", pack: StoredPack) -> None:
    await cell.packs.save_bundle(pack.target, pack.generation, [pack])


class Cell:
    def __init__(self) -> None:
        store = MemoryStore()
        self.packs = InMemoryPackRepository(store)
        self.pins = InMemoryServeRepository(store)


async def pin(cell: Cell, conversation: str, key: str, generation: int = 1, at: datetime = T0) -> None:
    await cell.pins.project_pins(
        [
            ConversationPin(
                conversation_id=conversation,
                view="chat",
                pack_key=key,
                pack_hash="h",
                generation=generation,
                pinned_at=at,
                expires_at=at + PIN_TTL,
            )
        ]
    )


async def test_a_profile_erasure_drops_packs_manifests_pins_and_watermarks() -> None:
    cell = Cell()
    marina, other, source = uuid7(), uuid7(), uuid7()
    await save(cell, stored(marina))
    await save(cell, stored(other))
    await cell.packs.save_manifest("mh-marina", {"profile_id": str(marina), "items": []})
    await cell.packs.save_manifest("mh-other", {"profile_id": str(other), "items": []})
    await pin(cell, "c1", pack_key(marina, "chat"))
    await pin(cell, "c2", pack_key(other, "chat"))
    await cell.pins.project_watermarks(
        [AgentWatermark(source_id=source, profile_id=marina, manifest_hash="mh-marina", seen_at=T0)]
    )
    target = ErasureScope(uuid7(), TargetKind.PROFILE, (uuid7(),), marina, None, T0)
    tables = sorted(r.table for r in await cell.packs.erase_for(target, limit=10))
    assert tables == ["agent_watermarks", "conversation_pins", "pack_bundles", "pack_manifests"]
    assert await cell.packs.packs_for(profile_target(marina)) == []
    assert await cell.packs.get_manifest("mh-marina") is None
    assert await cell.packs.get_manifest("mh-other") is not None
    assert await cell.pins.get_pin("c1", "chat") is None
    assert await cell.pins.get_pin("c2", "chat") is not None
    assert await cell.pins.get_watermark(source, marina) is None


async def test_a_conversation_erasure_keeps_manifests_and_watermarks() -> None:
    cell = Cell()
    marina, source = uuid7(), uuid7()
    await save(cell, stored(marina))
    await cell.packs.save_manifest("mh", {"profile_id": str(marina), "items": []})
    await pin(cell, "c1", "elsewhere/chat")
    await cell.pins.project_watermarks(
        [AgentWatermark(source_id=source, profile_id=marina, manifest_hash="mh", seen_at=T0)]
    )
    target = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), marina, "c1", T0)
    assert sorted(r.table for r in await cell.packs.erase_for(target, limit=10)) == [
        "conversation_pins",
        "pack_bundles",
    ]
    assert await cell.packs.get_manifest("mh") is not None
    assert await cell.pins.get_watermark(source, marina) is not None


async def test_projections_only_move_forward() -> None:
    cell = Cell()
    await pin(cell, "c1", "p/chat", generation=2, at=T0 + timedelta(minutes=5))
    await pin(cell, "c1", "p/older", generation=1, at=T0 + timedelta(minutes=9))  # a late, older receipt
    current = await cell.pins.get_pin("c1", "chat")
    assert current is not None
    assert (current.pack_key, current.generation) == ("p/chat", 2)
    await pin(cell, "c1", "p/chat", generation=2, at=T0 + timedelta(minutes=7))
    moved = await cell.pins.get_pin("c1", "chat")
    assert moved is not None
    assert moved.expires_at == T0 + timedelta(minutes=7) + PIN_TTL
    source, profile = uuid7(), uuid7()

    async def mark(manifest: str, generation: int, minutes: int, thread: str = "c1") -> None:
        await cell.pins.project_watermarks(
            [
                AgentWatermark(
                    source_id=source,
                    profile_id=profile,
                    manifest_hash=manifest,
                    seen_at=T0 + timedelta(minutes=minutes),
                    thread=thread,
                    generation=generation,
                )
            ]
        )

    await mark("g2", 2, 1)
    await mark("g1-pinned", 1, 5)  # a later read of the older pack the conversation is pinned to
    await mark("g2-late", 2, 0)  # an earlier receipt of the same generation, projected late
    current = await cell.pins.get_watermark(source, profile, "c1")
    assert current is not None
    assert (current.manifest_hash, current.generation) == ("g2", 2)
    await mark("other", 1, 9, thread="c2")
    assert (await cell.pins.get_watermark(source, profile, "c1")) == current
    assert await cell.pins.get_watermark(source, profile) is None
