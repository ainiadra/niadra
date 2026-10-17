"""Identity as a privacy participant: erasure, the profile tombstone and the continuous export (8.4, 22.3)."""

from uuid import UUID, uuid4

from niadra.adapters.memory.identity import InMemoryIdentityRepository
from niadra.domain.identity.export import EXPORTED
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import LinkRecord
from niadra.domain.vocabulary import AssertionMethod, HandleType, SubjectKind
from niadra.testing.cell import SPACE_ID
from tests.unit.identity.conftest import Env, handle

T = HandleType
PHONE = handle(T.PHONE_E164, "+5511988887777")
EMAIL = handle(T.EMAIL, "ana@acme.com.br")
OTHER = handle(T.EMAIL, "bruno@acme.com.br")
ORG = handle(T.SYSTEM_ID, "acct-9", "crm", SubjectKind.ACCOUNT)


def scope(
    env: Env, kind: TargetKind, handle_ids: tuple[UUID, ...], profile_id: UUID | None = None
) -> ErasureScope:
    return ErasureScope(uuid4(), kind, handle_ids, profile_id, None, env.cell.clock.now())


async def erase_all(repo: InMemoryIdentityRepository, target: ErasureScope, limit: int = 2) -> dict[str, int]:
    tables: dict[str, int] = {}
    while True:
        rows = await repo.erase_for(target, limit=limit)
        for row in rows:
            tables[row.table] = tables.get(row.table, 0) + 1
        if len(rows) < limit:
            return tables


async def test_forgetting_a_profile_erases_its_identity_and_leaves_a_tombstone(env: Env) -> None:
    person = await env.attach(PHONE, EMAIL)
    other = await env.attach(OTHER)
    await env.service.create_link(SPACE_ID, PHONE, ORG, role="buyer")
    profile = await env.repo.get_profile(person.profile_id)
    assert profile is not None
    before = profile.privacy_version
    target = scope(env, TargetKind.PROFILE, profile.handle_ids, profile.profile_id)

    erased = await erase_all(env.repo, target)

    assert erased == {
        "assertions": 1,
        "profile_links": 1,
        "profile_membership": 2,
        "handles": 2,
        "profiles": 1,
    }
    assert await env.repo.find_handle(T.EMAIL, "", "ana@acme.com.br") is None
    assert await env.repo.get_profile(person.profile_id) is None
    assert await env.repo.get_profile(other.profile_id) is not None
    assert await env.repo.bump_privacy_version(person.profile_id) == before + 1
    assert await env.repo.revive_profile(person.profile_id) is None
    assert await env.repo.erase_for(target, limit=10) == []


async def test_forgetting_one_handle_keeps_the_rest_of_the_profile(env: Env) -> None:
    person = await env.attach(PHONE, EMAIL)
    phone = await env.find(PHONE)
    target = scope(env, TargetKind.HANDLE, (phone.handle_id,), person.profile_id)

    erased = await erase_all(env.repo, target, limit=50)

    assert erased == {"assertions": 1, "profile_membership": 1, "handles": 1}
    remaining = await env.repo.get_profile(person.profile_id)
    assert remaining is not None
    assert remaining.handle_ids == ((await env.find(EMAIL)).handle_id,)


async def test_a_conversation_leaves_identity_alone(env: Env) -> None:
    person = await env.attach(PHONE, EMAIL)
    target = ErasureScope(
        uuid4(), TargetKind.CONVERSATION, person.handle_ids, person.profile_id, "c-1", env.cell.clock.now()
    )

    assert await env.repo.erase_for(target, limit=10) == []


async def test_unknown_profile_has_privacy_version_zero(env: Env) -> None:
    assert await env.repo.bump_privacy_version(uuid4()) == 0


async def test_export_rows_by_change_seq_with_the_erased_profile_flagged(env: Env) -> None:
    person = await env.attach(PHONE, EMAIL)
    await env.repo.add_link(
        LinkRecord(uuid4(), person.handle_ids[0], uuid4(), "buyer", False, env.cell.clock.now(), None),
        method=AssertionMethod.DECLARED,
        origin_event_id=None,
        confidence=0.9,
    )
    exported = {t: await env.repo.export_rows(t, after_seq=0, upto_seq=10_000, limit=100) for t in EXPORTED}

    assert {h["value"] for h in exported["handles"]} == {"+5511988887777", "ana@acme.com.br"}
    assert [p["profile_id"] for p in exported["profiles"]] == [person.profile_id]
    assert len(exported["profile_membership"]) == 2
    assert exported["profile_links"][0]["method"] == "declared"
    assert exported["assertions"][0]["method"] == "same_event"
    seqs = [r["change_seq"] for rows in exported.values() for r in rows]
    assert len(seqs) == len(set(seqs))

    profile = await env.repo.get_profile(person.profile_id)
    assert profile is not None
    last = max(seqs)
    await erase_all(env.repo, scope(env, TargetKind.PROFILE, profile.handle_ids, profile.profile_id))
    after = await env.repo.export_rows("profiles", after_seq=last, upto_seq=last + 1000, limit=10)
    assert after == [{"profile_id": person.profile_id, "change_seq": after[0]["change_seq"], "erased": True}]
    window = await env.repo.export_rows("handles", after_seq=0, upto_seq=last + 1000, limit=10)
    assert window == []
