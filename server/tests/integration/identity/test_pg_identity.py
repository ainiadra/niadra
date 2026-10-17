"""`PgIdentityRepository` and `IdentityService` against a real PostgreSQL 17."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.identity import PgIdentityRepository
from niadra.adapters.outbound.postgres.leases import PgLeaseStore
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.collaborators import AttachResult
from niadra.app.identity import IdentityService
from niadra.contracts.common import Handle
from niadra.domain.common.errors import Conflict
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.identity.model import AssertionState, MergeOp
from niadra.domain.records import LinkRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.testing.cell import PatchedUnitOfWorkFactory, sample_config
from tests.integration.conftest import CloningUnitOfWork

T = HandleType
NOW = datetime(2026, 9, 22, 17, tzinfo=UTC)


@dataclass
class Recorder:
    """Fakes for the privacy and pipeline ports, which other modules own."""

    feed: list[tuple[str, list[UUID], int]] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    async def record_feed(
        self, *, kind: str, profile_ids: Sequence[UUID], handle_ids: Sequence[UUID], privacy_version: int
    ) -> None:
        self.feed.append((kind, list(profile_ids), privacy_version))

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        return []

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        self.tasks.extend(tasks)

    async def request_work(self, task: Task) -> None:
        self.tasks.append(task)

    def take(self, name: TaskName) -> list[Task]:
        taken = [t for t in self.tasks if t.name is name]
        self.tasks = [t for t in self.tasks if t.name is not name]
        return taken


@dataclass
class World:
    space_id: UUID
    uow: PatchedUnitOfWorkFactory
    service: IdentityService
    recorder: Recorder
    raw: PgUnitOfWorkFactory

    async def attach(self, *handles: Handle, level: Verification = Verification.V0) -> UUID:
        async with self.uow(self.space_id) as uow:
            result = await self.service.attach(
                uow,
                list(handles),
                subject_kind=SubjectKind.PERSON,
                method=AssertionMethod.SAME_EVENT,
                origin_event_id=uuid4(),
                level=level,
                at=NOW,
            )
        return result.profile_id

    async def profile_id(self, h: Handle) -> UUID:
        async with self.raw(self.space_id) as uow:
            record = await uow.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            profile = await uow.identity.profile_of(record.handle_id)
            assert profile is not None
            return profile.profile_id

    async def handle_id(self, h: Handle) -> UUID:
        async with self.raw(self.space_id) as uow:
            record = await uow.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            return record.handle_id

    async def run(self, task: Task) -> None:
        for _ in range(10):
            try:
                await self.service.identity_apply(task)
                return
            except Conflict:
                await asyncio.sleep(0.01)  # the worker retries a task whose profiles moved
        raise AssertionError("identity_apply kept conflicting")


def phone(n: int) -> Handle:
    return Handle(type=T.PHONE_E164, value=f"+55119{n:04d}{n:04d}"[:14])


def email(n: int) -> Handle:
    return Handle(type=T.EMAIL, value=f"person{n}@acme.com.br")


def make_world(pg_uow: PgUnitOfWorkFactory, space_id: UUID | None = None) -> World:
    space_id = space_id or uuid4()
    recorder = Recorder()
    uow = PatchedUnitOfWorkFactory(pg_uow, privacy=recorder, pipeline=recorder)  # type: ignore[arg-type]
    config = StaticConfig(sample_config(space_id))
    return World(space_id, uow, IdentityService(uow, FixedClock(NOW), config), recorder, pg_uow)


@pytest.fixture
def world(pg_uow: PgUnitOfWorkFactory) -> World:
    return make_world(pg_uow)


async def test_handles_are_stored_as_hmac_and_ciphertext(world: World) -> None:
    await world.attach(Handle(type=T.GOV_ID_HMAC, value="529.982.247-25"), email(1))
    async with world.raw(world.space_id) as uow:
        rows = (await uow.session.execute(text("SELECT value_hmac, value_ct FROM handles"))).all()
        record = await uow.identity.find_handle(T.GOV_ID_HMAC, "BR", "52998224725")
    assert record is not None
    assert record.value == "52998224725"
    assert all(b"52998224725" not in bytes(r.value_ct) for r in rows)
    assert all(len(r.value_hmac) == 32 for r in rows)


async def test_merge_and_unmerge_round_trip(world: World) -> None:
    first = await world.attach(phone(1))
    second = await world.attach(email(1))
    await world.attach(phone(1), email(1))
    [task] = world.recorder.take(TaskName.IDENTITY_APPLY)
    await world.run(task)
    await world.run(task)
    assert await world.profile_id(email(1)) == await world.profile_id(phone(1))
    async with world.raw(world.space_id) as uow:
        [applied] = await uow.identity.list_assertions(None, AssertionState.APPLIED, None, 10)
        logs = (
            (
                await uow.session.execute(
                    text("SELECT op FROM merge_log WHERE space_id = :s"), {"s": world.space_id}
                )
            )
            .scalars()
            .all()
        )
    assert logs == [MergeOp.MERGE.value]
    op = await world.service.retract(world.space_id, applied.assertion_id, actor="user:ops", reason="wrong")
    [retract] = world.recorder.take(TaskName.IDENTITY_APPLY)
    assert retract.payload["op_id"] == str(op.op_id)
    await world.run(retract)
    assert {await world.profile_id(phone(1)), await world.profile_id(email(1))} == {first, second}
    kinds = [kind for kind, _, _ in world.recorder.feed]
    assert kinds.count("merge") == 2
    assert kinds.count("unmerge") == 2


async def test_same_document_in_two_spaces_has_different_hmacs(pg_uow: PgUnitOfWorkFactory) -> None:
    one, two = make_world(pg_uow), make_world(pg_uow)
    document = Handle(type=T.GOV_ID_HMAC, value="529.982.247-25")
    assert await one.attach(document) != await two.attach(document)
    hmacs = set()
    for world in (one, two):
        async with pg_uow(world.space_id) as uow:
            found = await uow.session.execute(
                text("SELECT value_hmac FROM handles WHERE type = 'gov_id_hmac'")
            )
            hmacs.update(found.scalars())
    assert len(hmacs) == 2


@pytest.mark.parametrize("shape", ["chain", "cross"])
async def test_concurrent_merges_converge(world: World, shape: str) -> None:
    handles = [phone(10), email(10), Handle(type=T.APP_USER_ID, value="u-10", scope="app")]
    for h in handles:
        await world.attach(h)
    ids = [await world.handle_id(h) for h in handles]
    pairs = [(ids[0], ids[1]), (ids[1], ids[2])] if shape == "chain" else [(ids[0], ids[1]), (ids[1], ids[0])]
    tasks = []
    for a, b in pairs:
        op = await world.service.create_assertion(
            world.space_id,
            [handles[ids.index(a)], handles[ids.index(b)]],
            method=AssertionMethod.EXPLICIT_IDENTIFY,
            subject_kind=SubjectKind.PERSON,
            actor="test",
            reason=None,
        )
        assert op.status == "queued"
        tasks += world.recorder.take(TaskName.IDENTITY_APPLY)
    await asyncio.gather(*(world.run(t) for t in tasks))

    involved = handles if shape == "chain" else handles[:2]
    assert len({await world.profile_id(h) for h in involved}) == 1
    async with world.raw(world.space_id) as uow:
        orphans = (
            await uow.session.execute(
                text(
                    "SELECT count(*) FROM profile_membership m JOIN profiles p USING (space_id, profile_id)"
                    " WHERE m.space_id = :s AND p.retired_at IS NOT NULL"
                ),
                {"s": world.space_id},
            )
        ).scalar_one()
        merges = (
            await uow.session.execute(
                text("SELECT count(*) FROM merge_log WHERE space_id = :s"), {"s": world.space_id}
            )
        ).scalar_one()
    assert orphans == 0
    assert merges == (2 if shape == "chain" else 1)


async def test_optimistic_version_and_locks(world: World) -> None:
    profile_id = await world.attach(phone(20))
    async with world.raw(world.space_id) as uow:
        [locked] = await uow.identity.lock_profiles([profile_id, uuid4()])
        bumped = await uow.identity.bump_profile(profile_id, locked.membership_version, privacy=True)
        assert (bumped.membership_version, bumped.privacy_version) == (2, 2)
        with pytest.raises(Conflict):
            await uow.identity.bump_profile(profile_id, locked.membership_version, privacy=False)


async def test_repository_details(world: World) -> None:
    await world.attach(phone(30), email(30))
    at = NOW + timedelta(hours=1)
    async with world.raw(world.space_id) as uow:
        repo: PgIdentityRepository = uow.identity
        mail = await repo.find_handle(T.EMAIL, "", "person30@acme.com.br")
        assert mail is not None
        again_key = HandleKey(T.EMAIL, "", mail.value)
        assert (
            await repo.add_handles([NewHandle(again_key, SubjectKind.PERSON, HandleStatus.ACTIVE)], at) == []
        )
        await repo.touch_handles([mail.handle_id], NOW - timedelta(days=1))
        await repo.raise_level([mail.handle_id], Verification.V2)
        await repo.raise_level([mail.handle_id], Verification.V1)
        [again] = await repo.get_handles([mail.handle_id])
        assert (again.first_seen, again.last_seen, again.level) == (
            NOW - timedelta(days=1),
            NOW,
            Verification.V2,
        )
        await repo.add_blocked(T.EMAIL, "qa@acme.com.br", "qa")
        await repo.add_blocked(T.EMAIL, "qa@acme.com.br", "qa")
        qa = HandleKey(T.EMAIL, "", "qa@acme.com.br")
        assert await repo.blocked_keys([qa, HandleKey(T.PHONE_E164, "", "qa@acme.com.br")]) == {qa}
        org = await repo.find_handle(T.PHONE_E164, "", phone(30).value)
        assert org is not None
        link = LinkRecord(uuid4(), mail.handle_id, org.handle_id, "buyer", True, NOW, None)
        await repo.add_link(link, method=AssertionMethod.DECLARED, origin_event_id=None, confidence=0.5)
        assert await repo.active_links([org.handle_id]) == [link]
        await repo.end_link(link.link_id, NOW)
        assert await repo.active_links([org.handle_id]) == []
        counterparts = await repo.recent_counterparts(mail.handle_id, NOW - timedelta(hours=1))
        assert [h.handle_id for h in counterparts] == [org.handle_id]
        assert await repo.pair_asserted(org.handle_id, mail.handle_id)
        page = await repo.list_handles([T.EMAIL, T.PHONE_E164], None, 1)
        assert len(page) == 1
        assert len(await repo.list_handles([T.EMAIL, T.PHONE_E164], page[0].handle_id, 5)) == 1


async def test_suggestions_are_written_once(world: World) -> None:
    await world.attach(Handle(type=T.EMAIL, value="Ana.Silva+x@gmail.com"))
    await world.attach(Handle(type=T.EMAIL, value="anasilva@gmail.com"))
    for _ in range(2):
        await world.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, world.space_id))
    async with world.raw(world.space_id) as uow:
        suggestions = await uow.identity.open_suggestions(after=None, limit=10)
    assert len(suggestions) == 1
    assert suggestions[0].features == {"agree": ["gmail_canonical"]}


@pytest.fixture
def rls_uow(pg_uow: PgUnitOfWorkFactory) -> PgUnitOfWorkFactory:
    # The factory of the tests connects as `niadra_app`, which row-level security restricts.
    return pg_uow


async def test_row_level_security_isolates_spaces(rls_uow: PgUnitOfWorkFactory) -> None:
    mine, theirs = make_world(rls_uow), make_world(rls_uow)
    await mine.attach(phone(40), email(40))
    async with rls_uow(theirs.space_id) as uow:
        visible = (await uow.session.execute(text("SELECT count(*) FROM handles"))).scalar_one()
        profiles = (await uow.session.execute(text("SELECT count(*) FROM profiles"))).scalar_one()
    async with rls_uow(mine.space_id) as uow:
        own = (await uow.session.execute(text("SELECT count(*) FROM handles"))).scalar_one()
    assert (visible, profiles) == (0, 0)
    assert own == 2


async def test_a_number_that_changed_hands_starts_over_and_its_old_handle_stays(world: World) -> None:
    # Seven months of silence, then the number comes back alone.
    crm = Handle(type=T.SYSTEM_ID, value="48213", scope="crm")
    await world.attach(phone(7), email(7), crm)
    old_profile = await world.profile_id(email(7))
    old_handle = await world.handle_id(phone(7))
    async with world.uow(world.space_id) as uow:
        result = await world.service.attach(
            uow,
            [phone(7)],
            subject_kind=SubjectKind.PERSON,
            method=AssertionMethod.SAME_EVENT,
            origin_event_id=uuid4(),
            level=Verification.V1,
            at=NOW + timedelta(days=210),
        )
    key = HandleKey(T.PHONE_E164, "", phone(7).value)
    async with world.raw(world.space_id) as uow:
        [retired] = await uow.identity.retired_handles([key])
        current = await uow.identity.find_handle(T.PHONE_E164, "", phone(7).value)
        owner = await uow.identity.profile_of(retired.handle_id)
        hashes = (
            await uow.session.execute(
                text("SELECT length(value_hmac), length(former_hmac) FROM handles WHERE handle_id = :h"),
                {"h": old_handle},
            )
        ).one()
    assert retired.handle_id == old_handle
    assert retired.status is HandleStatus.SUSPECTED_RECYCLED
    assert owner is not None
    assert owner.profile_id == old_profile
    assert current is not None
    assert current.handle_id != old_handle
    assert result.profile_id not in (None, old_profile)
    assert tuple(hashes) == (24, 32)


async def test_a_declared_handle_is_stored_provisional_until_a_record_id_confirms_it(world: World) -> None:
    crm = Handle(type=T.SYSTEM_ID, value="48213", scope="crm")
    await world.attach(phone(20), email(20), crm)

    async def claim(method: AssertionMethod, *handles: Handle) -> None:
        async with world.uow(world.space_id) as uow:
            await world.service.attach(
                uow,
                list(handles),
                subject_kind=SubjectKind.PERSON,
                method=method,
                origin_event_id=uuid4(),
                level=Verification.V3,
                at=NOW,
            )

    async def provisional(h: Handle) -> bool:
        async with world.raw(world.space_id) as uow:
            record = await uow.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            return record.provisional

    await claim(AssertionMethod.DECLARED, phone(21), email(20))
    assert await world.profile_id(phone(21)) == await world.profile_id(email(20))
    assert await provisional(phone(21))
    assert not await provisional(email(20))
    async with world.raw(world.space_id) as uow:
        [level] = (
            await uow.session.execute(
                text("SELECT level FROM handles WHERE handle_id = :h"),
                {"h": await world.handle_id(email(20))},
            )
        ).scalars()
    assert level == Verification.V0.value  # the claim's level never reaches the handles it names

    async def unconfirmed_members() -> tuple[UUID, ...]:
        # Every read loads them with the profile, in the same statement (5.3).
        async with world.raw(world.space_id) as uow:
            by_handle = await uow.identity.profile_of(await world.handle_id(email(20)))
            by_id = await uow.identity.get_profile(await world.profile_id(email(20)))
        assert by_handle is not None
        assert by_id is not None
        assert by_handle.provisional_handle_ids == by_id.provisional_handle_ids
        assert set(by_handle.provisional_handle_ids) <= set(by_handle.handle_ids)
        return by_handle.provisional_handle_ids

    assert await unconfirmed_members() == (await world.handle_id(phone(21)),)
    await claim(AssertionMethod.SYSTEM_IMPORT, phone(21), crm)
    assert not await provisional(phone(21))
    assert await unconfirmed_members() == ()


async def test_an_identify_merges_before_it_answers_under_the_profiles_lease(
    pg_uow: CloningUnitOfWork,
) -> None:
    """The merge an identify asks for, on PostgreSQL: the attach schedules the task as a safety net,
    `merge_now` takes the lease in one statement, merges, and gives it back; the scheduled task then finds
    nothing to do."""
    space_id = uuid4()
    await pg_uow.ensure(space_id)
    recorder = Recorder()
    uow = PatchedUnitOfWorkFactory(pg_uow, privacy=recorder)  # type: ignore[arg-type]
    leases = PgLeaseStore(pg_uow.inner._registry)
    config = StaticConfig(sample_config(space_id))
    service = IdentityService(uow, FixedClock(NOW), config, leases=leases)

    async def attach(*handles: Handle, level: Verification, after: bool = False) -> AttachResult:
        async with uow(space_id) as tx:
            return await service.attach(
                tx,
                list(handles),
                subject_kind=SubjectKind.PERSON,
                method=AssertionMethod.SAME_EVENT,
                origin_event_id=uuid4(),
                level=level,
                at=NOW,
                merge_after_commit=after,
            )

    first = (await attach(phone(1), level=Verification.V2)).profile_id
    await attach(email(1), level=Verification.V0)
    result = await attach(phone(1), email(1), level=Verification.V0, after=True)
    assert result.merge is not None
    async with pg_uow.system(space_id) as tx:
        scheduled = (
            await tx.session.execute(text("SELECT task FROM scheduled_tasks WHERE task = 'identity_apply'"))
        ).all()
    assert len(scheduled) == 1

    assert await service.merge_now(space_id, result.merge)

    async with pg_uow(space_id) as tx:
        for h in (phone(1), email(1)):
            record = await tx.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            profile = await tx.identity.profile_of(record.handle_id)
            assert profile is not None
            assert profile.profile_id == first
    async with pg_uow.system(space_id) as tx:
        left = (await tx.session.execute(text("SELECT count(*) FROM queue_leases"))).scalar_one()
    assert left == 0
