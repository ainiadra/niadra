"""PgServeRepository against PostgreSQL: the receipt writer's projections only move forward (5.11, rev. 11),
and the erasure cascade drops a profile's pins and watermarks (8.4)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.serve.records import PIN_TTL, AgentWatermark, ConversationPin

AT = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


async def test_pins_move_to_a_later_generation_only(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()

    async def project(uow, pack_hash: str, generation: int, minutes: int) -> None:  # type: ignore[no-untyped-def]
        await uow.serve.project_pins(
            [
                ConversationPin(
                    conversation_id="c1",
                    view="chat",
                    pack_key="p/chat",
                    pack_hash=pack_hash,
                    generation=generation,
                    pinned_at=AT + timedelta(minutes=minutes),
                    expires_at=AT + timedelta(minutes=minutes) + PIN_TTL,
                )
            ]
        )

    async with pg_uow(space) as uow:
        await project(uow, "h2", 2, 0)
        await project(uow, "h1", 1, 5)  # an older pack's receipt, projected after
    async with pg_uow(space) as uow:
        assert await uow.serve.get_pin("c1", "chat") == ConversationPin(
            "c1", "chat", "p/chat", "h2", 2, AT, AT + PIN_TTL
        )
        assert await uow.serve.get_pin("c1", "voice") is None
        await project(uow, "h3", 3, 2)
    async with pg_uow(space) as uow:
        newer = await uow.serve.get_pin("c1", "chat")
        assert newer is not None
        assert (newer.pack_hash, newer.generation, newer.expires_at) == (
            "h3",
            3,
            AT + timedelta(minutes=2) + PIN_TTL,
        )


async def test_watermarks_move_by_generation_then_time_and_per_thread(pg_uow: PgUnitOfWorkFactory) -> None:
    space, source, profile = uuid4(), uuid4(), uuid4()

    async def project(uow, manifest: str, generation: int, minutes: int, thread: str = "c1") -> None:  # type: ignore[no-untyped-def]
        await uow.serve.project_watermarks(
            [
                AgentWatermark(
                    source_id=source,
                    profile_id=profile,
                    manifest_hash=manifest,
                    seen_at=AT + timedelta(minutes=minutes),
                    thread=thread,
                    generation=generation,
                )
            ]
        )

    async with pg_uow(space) as uow:
        await project(uow, "m2", 2, 0)
        await project(uow, "m1", 1, 5)  # a later read of the older pinned pack
        await project(uow, "other", 1, 1, thread="c2")
    async with pg_uow(space) as uow:
        assert await uow.serve.get_watermark(source, profile, "c1") == AgentWatermark(
            source, profile, "m2", AT, "c1", 2
        )
        assert (await uow.serve.get_watermark(source, profile, "c2")) is not None
        assert await uow.serve.get_watermark(source, profile) is None
        await project(uow, "m3", 2, 1)
        mark = await uow.serve.get_watermark(source, profile, "c1")
        assert mark is not None
        assert mark.manifest_hash == "m3"
        # A conversation the source has not read in yet starts from its latest mark anywhere.
        latest = await uow.serve.latest_watermark(source, profile)
        assert latest is not None
        assert (latest.thread, latest.manifest_hash) == ("c1", "m3")
        assert await uow.serve.latest_watermark(uuid4(), profile) is None


async def test_erasure_drops_the_profile_pins_and_watermarks(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, source = uuid4(), uuid4(), uuid4()
    async with pg_uow(space) as uow:
        for conversation, key in (("c1", f"{profile}/chat"), ("c2", "someone-else/chat")):
            await uow.serve.project_pins(
                [
                    ConversationPin(
                        conversation_id=conversation,
                        view="chat",
                        pack_key=key,
                        pack_hash="h",
                        generation=1,
                        pinned_at=AT,
                        expires_at=AT + PIN_TTL,
                    )
                ]
            )
        await uow.serve.project_watermarks(
            [AgentWatermark(source_id=source, profile_id=profile, manifest_hash="m", seen_at=AT)]
        )
    target = ErasureScope(uuid4(), TargetKind.PROFILE, (uuid4(),), profile, None, AT)
    async with pg_uow(space) as uow:
        rows = await uow.serve.erase_for(target, limit=10)
        assert sorted(r.table for r in rows) == ["agent_watermarks", "conversation_pins"]
        assert await uow.serve.erase_for(target, limit=10) == []
        assert await uow.serve.get_pin("c2", "chat") is not None


async def test_the_first_group_of_a_profile_stays_and_leaves_with_its_erasure(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    # A later receipt never moves the group; the profile's erasure takes it along.
    space, profile, at = uuid4(), uuid4(), datetime(2026, 9, 25, 12, tzinfo=UTC)
    async with pg_uow(space) as uow:
        await uow.serve.project_assignment(
            experiment="pilot", profile_id=profile, group="control", assigned_at=at
        )
        await uow.serve.project_assignment(
            experiment="pilot", profile_id=profile, group="memory", assigned_at=at
        )
        # An element experiment's arm is kept the same way, with its kind (6.4).
        await uow.serve.project_assignment(
            experiment="size_bonus", profile_id=profile, group="on", assigned_at=at, kind="element"
        )
        assert await uow.serve.get_assignments(profile) == {"pilot": "control", "size_bonus": "on"}
        assert await uow.serve.get_assignments(uuid4()) == {}
        scope = ErasureScope(uuid4(), TargetKind.PROFILE, (), profile, None, at)
        erased = await uow.serve.erase_for(scope, limit=100)
        assert [r.table for r in erased] == ["experiment_assignments"] * 2
        assert await uow.serve.get_assignments(profile) == {}


async def test_a_batch_of_pins_and_marks_moves_each_row_forward_only(pg_uow: PgUnitOfWorkFactory) -> None:
    space, source, profile = uuid4(), uuid4(), uuid4()

    def pin(conversation: str, generation: int, minutes: int) -> ConversationPin:
        at = AT + timedelta(minutes=minutes)
        return ConversationPin(conversation, "chat", "p/chat", f"h{generation}", generation, at, at + PIN_TTL)

    def mark(thread: str, generation: int, minutes: int) -> AgentWatermark:
        return AgentWatermark(
            source, profile, f"m{generation}", AT + timedelta(minutes=minutes), thread, generation
        )

    async with pg_uow(space) as uow:
        await uow.serve.project_pins([pin("c1", 2, 0), pin("c2", 1, 0)])
        await uow.serve.project_watermarks([mark("c1", 2, 0), mark("c2", 1, 0)])
    async with pg_uow(space) as uow:
        await uow.serve.project_pins([pin("c1", 1, 9), pin("c2", 3, 1)])  # c1's is an older pack's receipt
        await uow.serve.project_watermarks([mark("c1", 1, 9), mark("c2", 3, 1)])
    async with pg_uow(space) as uow:
        assert await uow.serve.get_pin("c1", "chat") == pin("c1", 2, 0)
        assert await uow.serve.get_pin("c2", "chat") == pin("c2", 3, 1)
        assert await uow.serve.get_watermark(source, profile, "c1") == mark("c1", 2, 0)
        assert await uow.serve.get_watermark(source, profile, "c2") == mark("c2", 3, 1)
