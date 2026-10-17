"""CompileService (5.10, 5.11): packs and manifests stored, generation bumped, T0 written, hot layer."""

from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import InMemoryCache
from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.app.compile import PACK_TTL_S, T0_TTL_S, CompileService, default_variants, open_pack
from niadra.config.models import TaskView
from niadra.domain.compile import CompiledPack, Variant, profile_target
from niadra.domain.serve.cache_keys import CacheEpoch, gen_key, t0_key, wanted_key
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, SubjectKind, Verification
from niadra.ports.infra import HotTurn
from niadra.testing.cell import (
    SPACE_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    Cell,
    PatchedUnitOfWorkFactory,
    sample_config,
)
from tests.unit.compile.factories import PHONE, PROFILE, T0, marina
from tests.unit.compile.fakes import World


@pytest.fixture
def world() -> World:
    return World(snapshots=[marina()])


def service(
    cell: Cell, world: World, epoch: CacheEpoch | None = None
) -> tuple[CompileService, PatchedUnitOfWorkFactory]:
    uow = PatchedUnitOfWorkFactory(cell.uow, **world.repos())
    return CompileService(uow, cell.keys, cell.cache, cell.clock, cell.config, hot=cell.hot, epoch=epoch), uow


def task(name: TaskName = TaskName.COMPILE, **payload: object) -> Task:
    return Task(name, SPACE_ID, dict(payload), profile_id=PROFILE)


async def stored(uow: PatchedUnitOfWorkFactory) -> list:  # type: ignore[type-arg]
    async with uow(SPACE_ID) as u:
        return await u.compile.packs_for(profile_target(PROFILE))


async def test_compile_stores_every_default_variant_and_publishes_t0(cell: Cell, world: World) -> None:
    svc, uow = service(cell, world, CacheEpoch(3))
    await svc.compile(task())
    packs = await stored(uow)
    # Each source's usual view: whatsapp chat and voice at V0 and V1, the billing agent at no_customer.
    assert len(packs) == 5
    assert {p.generation for p in packs} == {1}
    variants = {Variant.parse(p.pack_key.split("/", 1)[1]) for p in packs}
    assert {(v.view, v.verification) for v in variants} == {
        ("chat", Verification.V0),
        ("chat", Verification.V1),
        ("voice", Verification.V0),
        ("voice", Verification.V1),
        ("chat", Verification.NO_CUSTOMER),
    }
    assert cell.cache.values[gen_key(3, SPACE_ID, PROFILE)] == b"1"
    for p in packs:
        variant = p.pack_key.split("/", 1)[1]
        key = t0_key(3, SPACE_ID, PROFILE, variant)
        cached = await open_pack(cell.keys, SPACE_ID, key, cell.cache.values[key])
        assert cached.pack_hash == p.pack_hash
        assert cached.generation == 1
        async with uow(SPACE_ID) as u:
            assert (await u.compile.get_manifest(p.manifest_hash))["pack_hash"] == p.pack_hash  # type: ignore[index]


async def test_each_run_bumps_the_generation_and_keeps_bytes_when_nothing_changed(
    cell: Cell, world: World
) -> None:
    svc, uow = service(cell, world)
    await svc.compile(task())
    first = {p.pack_key: p.pack_hash for p in await stored(uow)}
    await svc.compile(task())
    second = await stored(uow)
    assert {p.generation for p in second} == {2}
    assert {p.pack_key: p.pack_hash for p in second} == first


async def test_wanted_variants_become_eager(cell: Cell, world: World) -> None:
    svc, uow = service(cell, world)
    voice_v3 = Variant(view="voice", verification=Verification.V3, audience=Audience.CUSTOMER_AGENT)
    await cell.cache.set(wanted_key(0, SPACE_ID, PROFILE), f'["{voice_v3.key}", "not|a|variant"]'.encode())
    await svc.compile(task())
    keys = {p.pack_key.split("/", 1)[1] for p in await stored(uow)}
    assert voice_v3.key in keys


async def test_a_request_that_arrives_mid_run_schedules_another_run(cell: Cell, world: World) -> None:
    class RacingPipeline(InMemoryTaskStore):
        async def begin_work(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
            seen = await super().begin_work(space_id, profile_id, name)
            await self.request_work(task())  # memory changed again while the compiler was running
            return seen

    racing = RacingPipeline(cell.uow.store)
    svc = CompileService(
        PatchedUnitOfWorkFactory(cell.uow, pipeline=racing, **world.repos()),
        cell.keys,
        cell.cache,
        cell.clock,
        cell.config,
    )
    await racing.request_work(task())
    racing.take()
    await svc.compile(task())
    assert [t.name for t in racing.take()] == [TaskName.COMPILE]
    quiet = CompileService(
        PatchedUnitOfWorkFactory(cell.uow, **world.repos()), cell.keys, cell.cache, cell.clock, cell.config
    )
    await quiet.compile(task())
    assert racing.take() == []


async def test_the_request_is_done_only_once_the_generation_is_published(cell: Cell, world: World) -> None:
    # Ingest status reads `done` as "a read now sees it" (7.1): at the cache write, it has not moved yet.
    svc, uow = service(cell, world)
    at_publish: list[int] = []

    class Watching(InMemoryCache):
        async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None:
            async with uow(SPACE_ID) as u:
                at_publish.append(await u.pipeline.work_done(SPACE_ID, PROFILE, TaskName.COMPILE))
            await super().bump_generation(gen_key, generation, ttl_s)

    svc._cache = Watching()
    async with uow(SPACE_ID) as u:
        await u.pipeline.request_work(task())
    await svc.compile(task())
    async with uow(SPACE_ID) as u:
        assert (at_publish, await u.pipeline.work_done(SPACE_ID, PROFILE, TaskName.COMPILE)) == ([0], 1)


async def test_packs_expire_after_a_day_and_the_generation_after_three(cell: Cell, world: World) -> None:
    svc, _ = service(cell, world)
    ttls: dict[str, int] = {}

    class Recording(InMemoryCache):
        async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None:
            ttls["generation"] = ttl_s
            await super().bump_generation(gen_key, generation, ttl_s)

        async def put_if_generation(
            self, pack_key: str, gen_key: str, value: bytes, generation: int, ttl_s: int
        ) -> bool:
            ttls["pack"] = ttl_s
            return await super().put_if_generation(pack_key, gen_key, value, generation, ttl_s)

    svc._cache = Recording()
    await svc.compile(task())
    assert ttls == {"generation": T0_TTL_S, "pack": PACK_TTL_S}
    assert (PACK_TTL_S, T0_TTL_S) == (24 * 3600, 72 * 3600)


async def test_a_vanished_profile_compiles_nothing(cell: Cell) -> None:
    svc, uow = service(cell, World())
    await svc.compile(task())
    assert await stored(uow) == []


async def test_the_hot_layer_is_shortened_after_the_covered_turns(cell: Cell, world: World) -> None:
    svc, _ = service(cell, world)
    turn = HotTurn(UUID(int=1), int(T0.timestamp() * 1000) - 2 * 3600 * 1000, b"x")
    await cell.hot.add(SPACE_ID, PHONE, [turn], cap_ms=72 * 3600 * 1000)
    await svc.compile(task())
    assert cell.hot.ttl_ms[(SPACE_ID, PHONE)] == 24 * 3600 * 1000


async def test_a_cache_outage_does_not_fail_the_compilation(cell: Cell, world: World) -> None:
    class DownCache(InMemoryCache):
        async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None:
            raise ConnectionError("down")

    cell.cache = DownCache()
    svc, uow = service(cell, world)
    await svc.compile(task())
    assert len(await stored(uow)) == 5


async def test_seed_gen_rewrites_generation_and_t0_from_stored_packs(cell: Cell, world: World) -> None:
    svc, uow = service(cell, world)
    await svc.compile(task())
    await svc.compile(task())
    cell.cache.values.clear()  # a failover lost everything
    await svc.seed_generation(task(TaskName.SEED_GEN))
    assert cell.cache.values[gen_key(0, SPACE_ID, PROFILE)] == b"2"
    packs = await stored(uow)
    for p in packs:
        key = t0_key(0, SPACE_ID, PROFILE, p.pack_key.split("/", 1)[1])
        assert (await open_pack(cell.keys, SPACE_ID, key, cell.cache.values[key])).pack_hash == p.pack_hash


async def test_compile_on_demand_uses_the_stored_generation(cell: Cell, world: World) -> None:
    svc, uow = service(cell, world)
    await svc.compile(task())
    variant = Variant(view="brief", verification=Verification.V2, purposes=frozenset({"support"}))
    async with uow(SPACE_ID) as u:
        profile = world.profile(PROFILE)
        assert profile is not None
        pack = await svc.compile_on_demand(u, profile, variant)
    assert isinstance(pack, CompiledPack)
    assert pack.generation == 1
    assert pack.variant == variant.key


def test_the_eager_set_covers_v0_task_views_and_sources_the_policy_singles_out() -> None:
    base = sample_config()
    config = base.model_copy(
        update={
            "views": [
                TaskView(name="task:billing", purposes=frozenset({"billing"})),
                TaskView(name="task:orders", purposes=frozenset({"orders"})),
            ],
            "policy": base.policy.model_copy(
                update={"competes_with": {str(VOICE_SOURCE): [str(WHATSAPP_SOURCE)]}}
            ),
        }
    )
    found = {(v.view, v.verification, v.source_id) for v in default_variants(config)}
    assert found == {
        ("chat", Verification.V0, None),
        ("chat", Verification.V1, None),
        ("voice", Verification.V0, VOICE_SOURCE),
        ("voice", Verification.V1, VOICE_SOURCE),
        ("chat", Verification.NO_CUSTOMER, None),
        # The billing agent serves the billing task view only; no source serves orders.
        ("task:billing", Verification.NO_CUSTOMER, None),
    }
    accounts = {v.view for v in default_variants(config, kind=SubjectKind.ACCOUNT)}
    assert accounts == {"account", "voice", "task:billing"}
