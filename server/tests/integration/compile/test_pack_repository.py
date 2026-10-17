"""PgPackRepository against PostgreSQL: one encrypted bundle per profile, forward-only generations,
immutable manifests (5.10)."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from niadra.adapters.outbound.postgres.schema.read_path import PackBundle
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.compile import StoredPack
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.serve.records import PIN_TTL, ConversationPin

AS_OF = datetime(2026, 9, 22, 16, 6, tzinfo=UTC)


def pack(key: str, target: str, generation: int, body: bytes = b'{"text": "Marina"}') -> StoredPack:
    return StoredPack(
        key, target, generation, "compiler-1.0", 1, body, f"hash-{generation}", f"m-{generation}", AS_OF
    )


async def test_bundles_round_trip_encrypted_and_never_go_back_a_generation(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, profile = uuid4(), uuid4()
    target = f"profile:{profile}"
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(
            target, 2, [pack(f"{profile}/chat", target, 2), pack(f"{profile}/voice", target, 2)]
        )
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(target, 1, [pack(f"{profile}/chat", target, 1, b"stale")])
    async with pg_uow(space) as uow:
        chat = await uow.compile.get_pack(f"{profile}/chat")
        assert chat is not None
        assert chat.generation == 2
        assert chat.body == b'{"text": "Marina"}'
        assert chat.as_of == AS_OF
        assert [p.pack_key for p in await uow.compile.packs_for(target)] == [
            f"{profile}/chat",
            f"{profile}/voice",
        ]
        assert await uow.compile.current_generation(target) == 2
        assert await uow.compile.current_generation("profile:none") == 0
        [raw] = (
            await uow.session.execute(select(PackBundle.body_ct).where(PackBundle.target == target))
        ).all()
        assert b"Marina" not in raw[0]


async def test_a_bundle_replaces_every_variant_at_once(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    target = f"profile:{profile}"
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(
            target, 1, [pack(f"{profile}/{v}", target, 1) for v in ("chat", "voice")]
        )
        await uow.compile.save_bundle(target, 2, [pack(f"{profile}/chat", target, 2, b"new")])
        assert await uow.compile.get_pack(f"{profile}/voice") is None
    async with pg_uow(space) as uow:
        assert [(p.pack_key, p.body) for p in await uow.compile.packs_for(target)] == [
            (f"{profile}/chat", b"new")
        ]
        assert await uow.compile.current_generation(target) == 2


async def test_manifests_are_content_addressed_and_saved_together(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        await uow.compile.save_manifest("abc", {"items": [{"id": "fact:1"}], "pack_hash": "p"})
        await uow.compile.save_manifests({"abc": {"items": []}, "def": {"items": [], "pack_hash": "q"}})
    async with pg_uow(space) as uow:
        assert await uow.compile.get_manifest("abc") == {"items": [{"id": "fact:1"}], "pack_hash": "p"}
        assert await uow.compile.get_manifest("def") == {"items": [], "pack_hash": "q"}
        assert await uow.compile.get_manifest("missing") is None


async def test_erasure_drops_bundles_manifests_and_pins_of_the_profile(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, other = uuid4(), uuid4(), uuid4()
    target = f"profile:{profile}"
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(target, 1, [pack(f"{profile}/chat", target, 1)])
        await uow.compile.save_bundle(f"profile:{other}", 1, [pack(f"{other}/chat", f"profile:{other}", 1)])
        await uow.compile.save_manifest("mine", {"profile_id": str(profile), "items": []})
        await uow.compile.save_manifest("theirs", {"profile_id": str(other), "items": []})
        await uow.serve.project_pins(
            [
                ConversationPin(
                    conversation_id="c1",
                    view="chat",
                    pack_key=f"{profile}/chat",
                    pack_hash="h",
                    generation=1,
                    pinned_at=AS_OF,
                    expires_at=AS_OF + PIN_TTL,
                )
            ]
        )
    scope = ErasureScope(uuid4(), TargetKind.PROFILE, (uuid4(),), profile, None, AS_OF)
    async with pg_uow(space) as uow:
        erased = []
        while len(batch := await uow.compile.erase_for(scope, limit=2)) == 2:
            erased += batch
        erased += batch
    assert sorted(r.table for r in erased) == ["conversation_pins", "pack_bundles", "pack_manifests"]
    async with pg_uow(space) as uow:
        assert await uow.compile.packs_for(target) == []
        assert await uow.compile.get_manifest("mine") is None
        assert await uow.compile.get_manifest("theirs") is not None
        assert await uow.compile.packs_for(f"profile:{other}") != []
        assert await uow.serve.get_pin("c1", "chat") is None


async def test_a_conversation_erasure_keeps_the_manifests(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    target = f"profile:{profile}"
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(target, 1, [pack(f"{profile}/chat", target, 1)])
        await uow.compile.save_manifest("mine", {"profile_id": str(profile), "items": []})
    scope = ErasureScope(uuid4(), TargetKind.CONVERSATION, (), profile, "c1", AS_OF)
    async with pg_uow(space) as uow:
        assert [r.table for r in await uow.compile.erase_for(scope, limit=10)] == ["pack_bundles"]
        assert await uow.compile.get_manifest("mine") is not None


async def test_reads_only_move_forward_and_survive_the_next_generation(pg_uow: PgUnitOfWorkFactory) -> None:
    # The receipt writer records the last read of each variant; a variant the next generation
    # keeps keeps its read, one it cuts leaves with it.
    space, profile = uuid4(), uuid4()
    target = f"profile:{profile}"
    later = AS_OF.replace(hour=18)
    async with pg_uow(space) as uow:
        await uow.compile.save_bundle(
            target, 1, [pack(f"{profile}/{v}", target, 1) for v in ("chat", "voice")]
        )
        await uow.compile.mark_read({f"{profile}/chat": later, f"{profile}/voice": AS_OF})
        await uow.compile.mark_read({f"{profile}/chat": AS_OF, f"{profile}/gone": AS_OF})
    async with pg_uow(space) as uow:
        reads = {p.pack_key: p.last_read_at for p in await uow.compile.packs_for(target)}
        assert reads == {f"{profile}/chat": later, f"{profile}/voice": AS_OF}
        await uow.compile.save_bundle(target, 2, [pack(f"{profile}/chat", target, 2)])
    async with pg_uow(space) as uow:
        reads = {p.pack_key: p.last_read_at for p in await uow.compile.packs_for(target)}
        assert reads == {f"{profile}/chat": later}
        marks = await uow.session.scalar(select(PackBundle.last_read).where(PackBundle.target == target))
        assert set(marks) == {"chat"}
