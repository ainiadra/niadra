"""`PgIdentityRepository` as a privacy participant and export source, plus the Console's lookups."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.contracts.common import Handle
from niadra.domain.identity.export import EXPORTED
from niadra.domain.identity.model import SuggestionRecord
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleType, SubjectKind
from tests.integration.identity.test_pg_identity import NOW, World, email, make_world, phone

T = HandleType
ORG = Handle(type=T.SYSTEM_ID, value="acct-1", scope="crm", subject_kind=SubjectKind.ACCOUNT)


@pytest.fixture
def world(pg_uow: PgUnitOfWorkFactory) -> World:
    return make_world(pg_uow)


def _scope(kind: TargetKind, handles: tuple[UUID, ...], profile_id: UUID | None) -> ErasureScope:
    return ErasureScope(uuid4(), kind, handles, profile_id, None, NOW)


async def _merged_person(world: World) -> UUID:
    """Two profiles merged into one by an assertion later retracted, a link and a suggestion."""
    await world.attach(phone(1))
    await world.attach(email(1))
    await world.attach(phone(1), email(1))
    for task in world.recorder.take(TaskName.IDENTITY_APPLY):
        await world.run(task)
    await world.service.create_link(world.space_id, email(1), ORG, role="buyer")
    phone_id, email_id = await world.handle_id(phone(1)), await world.handle_id(email(1))
    async with world.raw(world.space_id) as uow:
        await uow.identity.add_suggestion(SuggestionRecord(uuid4(), phone_id, email_id, 0.7, NOW))
        pending = await uow.identity.list_assertions([phone_id], None, None, 10)
        await uow.identity.retract_assertions([pending[0].assertion_id], NOW)
    return await world.profile_id(email(1))


async def _erase_all(world: World, target: ErasureScope, limit: int) -> dict[str, int]:
    tables: dict[str, int] = {}
    while True:
        async with world.raw(world.space_id) as uow:
            rows = await uow.identity.erase_for(target, limit=limit)
        for row in rows:
            tables[row.table] = tables.get(row.table, 0) + 1
        if len(rows) < limit:
            return tables


async def _count(world: World, table: str) -> int:
    async with world.raw(world.space_id) as uow:
        stmt = text(f"SELECT count(*) FROM {table} WHERE space_id = :space")
        return int((await uow.session.execute(stmt, {"space": world.space_id})).scalar_one())


async def test_forgetting_a_profile_leaves_only_its_tombstone(world: World) -> None:
    profile_id = await _merged_person(world)
    async with world.raw(world.space_id) as uow:
        profile = await uow.identity.get_profile(profile_id)
        assert profile is not None
        version = await uow.identity.bump_privacy_version(profile_id)
    target = _scope(TargetKind.PROFILE, profile.handle_ids, profile_id)

    tables = await _erase_all(world, target, limit=2)

    assert tables == {
        "assertions": 1,
        "profile_links": 1,
        "identity_suggestions": 1,
        "merge_log": 1,
        "profile_membership": 2,
        "handles": 2,
        "profiles": 1,
    }
    for table in ("assertions", "merge_log", "identity_suggestions", "profile_links"):
        assert await _count(world, table) == 0
    # Only the account the person was linked to keeps its identity: it is another subject.
    assert (await _count(world, "handles"), await _count(world, "profile_membership")) == (1, 1)
    async with world.raw(world.space_id) as uow:
        assert await uow.identity.get_profile(profile_id) is None
        assert await uow.identity.revive_profile(profile_id) is None
        assert await uow.identity.bump_privacy_version(profile_id) == version + 1
        assert await uow.identity.bump_privacy_version(uuid4()) == 0
        assert await uow.identity.erase_for(target, limit=10) == []
        tombstone = (
            await uow.session.execute(
                text("SELECT erased_at, retired_at, pseudonym FROM profiles WHERE profile_id = :p"),
                {"p": profile_id},
            )
        ).one()
    assert tombstone.erased_at is not None
    assert tombstone.retired_at is not None
    assert tombstone.pseudonym.startswith("p_")


async def test_forgetting_one_handle_keeps_the_profile_and_its_history(world: World) -> None:
    profile_id = await _merged_person(world)
    phone_id = await world.handle_id(phone(1))

    async with world.raw(world.space_id) as uow:
        rows = await uow.identity.erase_for(_scope(TargetKind.HANDLE, (phone_id,), profile_id), limit=50)
        remaining = await uow.identity.get_profile(profile_id)

    assert {r.table for r in rows} == {"assertions", "identity_suggestions", "profile_membership", "handles"}
    assert remaining is not None
    assert remaining.handle_ids == (await world.handle_id(email(1)),)
    assert await _count(world, "merge_log") == 1


async def test_export_rows_carry_values_hmacs_and_the_erased_flag(world: World) -> None:
    profile_id = await _merged_person(world)
    async with world.raw(world.space_id) as uow:
        exported = {
            t: await uow.identity.export_rows(t, after_seq=0, upto_seq=10**12, limit=100) for t in EXPORTED
        }

    handles = {h["value"]: h for h in exported["handles"]}
    assert set(handles) >= {"+5511900010001", "person1@acme.com.br"}
    assert all(len(h["value_hmac"]) == 64 for h in handles.values())
    assert {p["profile_id"] for p in exported["profiles"]} >= {profile_id}
    assert exported["merge_log"][0]["op"] == "merge"
    assert exported["profile_links"][0]["method"] == "declared"
    for rows in exported.values():
        seqs = [r["change_seq"] for r in rows]
        assert seqs == sorted(seqs)

    async with world.raw(world.space_id) as uow:
        profile = await uow.identity.get_profile(profile_id)
    assert profile is not None
    last = max(r["change_seq"] for rows in exported.values() for r in rows)
    await _erase_all(world, _scope(TargetKind.PROFILE, profile.handle_ids, profile_id), limit=50)
    async with world.raw(world.space_id) as uow:
        after = await uow.identity.export_rows("profiles", after_seq=last, upto_seq=10**12, limit=10)
    assert after == [{"profile_id": profile_id, "change_seq": after[0]["change_seq"], "erased": True}]


async def test_search_lookups_and_suggestion_review(world: World) -> None:
    await world.attach(phone(5), email(5))
    await world.attach(Handle(type=T.EMAIL, value="Ana.Silva+x@gmail.com"))
    await world.attach(Handle(type=T.EMAIL, value="anasilva@gmail.com"))
    await world.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, world.space_id))
    profile_id = await world.profile_id(email(5))

    async with world.raw(world.space_id) as uow:
        profile = await uow.identity.get_profile(profile_id)
        assert profile is not None
        found = await uow.identity.find_profile_by_pseudonym(profile.pseudonym)
        missing = await uow.identity.find_profile_by_pseudonym("p_aaaaaaaaaaaaaaaa")
        [suggestion] = await uow.identity.open_suggestions(after=None, limit=10)
        await uow.identity.set_suggestion_status(suggestion.suggestion_id, "rejected")
        reviewed = await uow.identity.get_suggestion(suggestion.suggestion_id)
        still_open = await uow.identity.open_suggestions(after=None, limit=10)

    assert found == profile
    assert missing is None
    assert reviewed is not None
    assert reviewed.status == "rejected"
    assert still_open == []
    summaries = await world.service.search_profiles(world.space_id, "11 90005-0005")
    assert [s.profile.profile_id for s in summaries] == [profile_id]
    assert summaries[0].last_seen == NOW


async def test_accepted_suggestion_merges_through_identity_apply_and_dismissed_pairs_stay_closed(
    world: World,
) -> None:
    await world.attach(Handle(type=T.EMAIL, value="Bia.Souza+x@gmail.com"))
    await world.attach(Handle(type=T.EMAIL, value="biasouza@gmail.com"))
    await world.attach(Handle(type=T.EMAIL, value="Caio.Lima+x@gmail.com"))
    await world.attach(Handle(type=T.EMAIL, value="caiolima@gmail.com"))
    job = Task(TaskName.IDENTITY_SUGGEST, world.space_id)
    await world.service.identity_suggest(job)
    async with world.raw(world.space_id) as uow:
        first, second = await uow.identity.open_suggestions(after=None, limit=10)

    op = await world.service.accept_suggestion(
        world.space_id, first.suggestion_id, actor="user:1", reason=None
    )
    await world.service.dismiss_suggestion(world.space_id, second.suggestion_id)
    for task in world.recorder.take(TaskName.IDENTITY_APPLY):
        await world.run(task)
    await world.service.identity_suggest(job)

    assert op.status == "queued"
    async with world.raw(world.space_id) as uow:
        members = await uow.identity.memberships([first.handle_a, first.handle_b])
        reopened = await uow.identity.open_suggestions(after=None, limit=10)
    assert len(set(members.values())) == 1
    assert reopened == []
