from uuid import uuid4

import pytest

from niadra.app import identity as identity_module
from niadra.app.identity import MERGE_SAFETY_NET
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.identity.model import AssertionState, MergeOp
from niadra.domain.identity.rules import choose_anchor
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.pipeline.lease import profile_unit
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID
from tests.unit.identity.conftest import Env, cpf, handle

T = HandleType
PHONE = handle(T.PHONE_E164, "+55 11 98765-4321")
EMAIL = handle(T.EMAIL, "ana.silva@acme.com.br")
EMAIL2 = handle(T.EMAIL, "bia.costa@acme.com.br")
ACTOR = "user:ops"


async def test_new_handles_arriving_together_share_one_profile(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL, handle(T.PHONE_E164, "(11) 98765-4321"))
    assert len(result.handle_ids) == 2
    assert result.handle_ids[0] == (await env.find(PHONE)).handle_id
    assert not result.pending_merge
    profile = await env.repo.get_profile(result.profile_id)
    assert profile is not None
    assert set(profile.handle_ids) == set(result.handle_ids)
    assert profile.pseudonym.startswith("p_")
    [assertion] = await env.repo.list_assertions(None, None, None, 10)
    assert (assertion.method, assertion.state) == (AssertionMethod.SAME_EVENT, AssertionState.APPLIED)


async def test_the_level_of_the_event_raises_the_handles(env: Env) -> None:
    await env.attach(PHONE, level=Verification.V2)
    await env.attach(PHONE, level=Verification.V1)
    assert (await env.find(PHONE)).level is Verification.V2


async def test_two_profiles_in_one_event_queue_a_merge_and_keep_the_event_on_the_anchor(env: Env) -> None:
    await env.attach(PHONE, level=Verification.V2)
    await env.attach(EMAIL)
    result = await env.attach(PHONE, EMAIL, handle(T.APP_USER_ID, "u-1", "app"))
    assert result.pending_merge
    assert result.profile_id == (await env.profile(PHONE)).profile_id
    assert await env.same(PHONE, handle(T.APP_USER_ID, "u-1", "app"))
    [task] = env.apply_tasks()
    assert task.name is TaskName.IDENTITY_APPLY
    assert set(task.payload) == {"op", "op_id", "assertion_ids", "profile_ids"}
    assert "+55" not in str(task.payload)
    assert "@" not in str(task.payload)
    [pending] = await env.repo.list_assertions(None, AssertionState.PENDING, None, 10)
    assert task.payload["assertion_ids"] == [str(pending.assertion_id)]


async def test_a_merge_run_after_the_commit_leaves_its_task_as_a_safety_net(env: Env) -> None:
    """An `identify` runs its merge before it answers; the task only covers a run that failed."""
    await env.attach(PHONE, level=Verification.V2)
    await env.attach(EMAIL)
    result = await env.attach(PHONE, EMAIL, merge_after_commit=True)
    assert result.merge is not None
    assert env.apply_tasks() == []
    [(task, due, reason)] = env.scheduled_applies()
    assert (reason, due) == (DelayReason.SAFETY_NET, env.cell.clock.now() + MERGE_SAFETY_NET)
    assert task.payload["op_id"] == str(result.merge.op_id)

    assert await env.service.merge_now(SPACE_ID, result.merge)

    assert await env.same(PHONE, EMAIL)
    assert env.outbox.leases == {}
    await env.service.identity_apply(task)
    assert len(env.repo.merge_log) == 1


async def test_a_merge_whose_profile_a_worker_holds_is_left_to_its_task(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_module, "MERGE_LEASE_WAIT_S", 0.0)
    await env.attach(PHONE, level=Verification.V2)
    await env.attach(EMAIL)
    busy = (await env.profile(EMAIL)).profile_id
    async with env.uow(SPACE_ID) as uow:
        grant = await uow.pipeline.acquire(SPACE_ID, [profile_unit(busy)], "worker", 60, env.cell.clock.now())
    assert grant is not None
    result = await env.attach(PHONE, EMAIL, merge_after_commit=True)
    assert result.merge is not None

    assert not await env.service.merge_now(SPACE_ID, result.merge)

    assert not await env.same(PHONE, EMAIL)
    [(task, _, _)] = env.scheduled_applies()
    await env.service.identity_apply(task)
    assert await env.same(PHONE, EMAIL)


async def test_a_merge_run_after_the_commit_keeps_every_protection(env: Env) -> None:
    """The same rules as the task: two verified profiles never merge on an event (5.3)."""
    await env.attach(PHONE, level=Verification.V3)
    await env.attach(EMAIL, level=Verification.V3)
    result = await env.attach(PHONE, EMAIL, merge_after_commit=True)
    assert result.merge is not None

    assert await env.service.merge_now(SPACE_ID, result.merge)

    assert not await env.same(PHONE, EMAIL)
    [pending] = await env.repo.list_assertions(None, AssertionState.REJECTED, None, 10)
    assert pending.reason == "both_profiles_verified"


async def test_identity_apply_merges_logs_bumps_and_requests_recompute(env: Env) -> None:
    await env.attach(PHONE, level=Verification.V2)
    await env.attach(EMAIL)
    survivor = (await env.profile(PHONE)).profile_id
    absorbed = (await env.profile(EMAIL)).profile_id
    await env.attach(PHONE, EMAIL)
    env.outbox.take(TaskName.COMPILE)
    await env.drain()
    merged = await env.profile(EMAIL)
    assert merged.profile_id == survivor
    assert (merged.membership_version, merged.privacy_version) == (2, 2)
    assert env.repo._profiles[absorbed].retired_at is not None
    [log] = env.repo.merge_log
    assert (log.op, log.from_profiles, log.to_profiles) == (MergeOp.MERGE, (absorbed,), (survivor,))
    assert {(row["kind"], row["profile_ids"][0]) for row in env.privacy.feed} == {
        ("merge", survivor),
        ("merge", absorbed),
    }
    recompute = [(t.name, t.profile_id) for t in env.outbox.take()]
    assert (TaskName.COMPILE, survivor) in recompute
    assert (TaskName.DERIVE_TRAITS, survivor) in recompute


async def test_retracting_the_merge_assertion_revives_the_absorbed_profile(env: Env) -> None:
    await env.attach(PHONE)
    await env.attach(EMAIL)
    before = {(await env.profile(h)).profile_id for h in (PHONE, EMAIL)}
    await env.attach(PHONE, EMAIL)
    await env.drain()
    [assertion] = await env.repo.list_assertions(None, AssertionState.APPLIED, None, 10)
    op = await env.service.retract(SPACE_ID, assertion.assertion_id, actor=ACTOR, reason="wrong import")
    assert op.status == "queued"
    await env.drain()
    assert {(await env.profile(h)).profile_id for h in (PHONE, EMAIL)} == before
    [merge] = [m for m in env.repo.merge_log if m.op is MergeOp.MERGE]
    [unmerge] = [m for m in env.repo.merge_log if m.op is MergeOp.UNMERGE]
    assert merge.reversed_at is not None
    assert (unmerge.op, unmerge.reason, unmerge.merge_id) == (MergeOp.UNMERGE, "wrong import", op.op_id)


async def test_retracting_a_pending_assertion_cancels_the_merge(env: Env) -> None:
    await env.attach(PHONE)
    await env.attach(EMAIL)
    await env.attach(PHONE, EMAIL)
    [pending] = await env.repo.list_assertions(None, AssertionState.PENDING, None, 10)
    await env.service.retract(SPACE_ID, pending.assertion_id, actor=ACTOR, reason=None)
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    assert env.privacy.feed == []
    [retracted] = await env.repo.get_assertions([pending.assertion_id])
    assert retracted.retracted_at is not None


@pytest.mark.parametrize("undo", ["unmerge", "retract"])
async def test_a_split_survives_the_record_that_joined_them(env: Env, undo: str) -> None:
    """The CRM record that wrongly joined two people comes again: they stay apart, and a person reviews."""
    await env.attach(PHONE)
    await env.attach(EMAIL)
    await env.attach(PHONE, EMAIL)
    await env.drain()
    joined = await env.profile(PHONE)
    if undo == "unmerge":
        moved = [(await env.find(PHONE)).handle_id]
        await env.service.unmerge(
            SPACE_ID, joined.profile_id, handle_ids=moved, actor=ACTOR, reason="two people"
        )
    else:
        [applied] = await env.repo.list_assertions(None, AssertionState.APPLIED, None, 10)
        await env.service.retract(SPACE_ID, applied.assertion_id, actor=ACTOR, reason="two people")
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    result = await env.attach(PHONE, EMAIL)
    assert result.merge is None
    assert not result.pending_merge
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    [suggestion] = env.repo.suggestions
    assert suggestion.features == {"agree": ["same_event"], "after_split": True}
    await env.service.accept_suggestion(SPACE_ID, suggestion.suggestion_id, actor=ACTOR, reason="same person")
    await env.drain()
    assert await env.same(PHONE, EMAIL)


async def test_a_merge_queued_before_a_split_commits_is_rejected(env: Env) -> None:
    """An attach that read the two profiles before the split committed queued their merge: the task, run
    after the split, rejects it instead of undoing the split."""
    await env.attach(PHONE)
    await env.attach(EMAIL)
    await env.attach(PHONE, EMAIL)
    await env.drain()
    joined = await env.profile(PHONE)
    moved = [(await env.find(PHONE)).handle_id]
    await env.service.unmerge(SPACE_ID, joined.profile_id, handle_ids=moved, actor=ACTOR, reason="two people")
    await env.drain()
    phone, email = await env.find(PHONE), await env.find(EMAIL)
    late = identity_module._assertion(
        phone, email, AssertionMethod.SAME_EVENT, AssertionState.PENDING, env.cell.clock.now()
    )
    members = {phone.handle_id: (await env.profile(PHONE)).profile_id}
    members[email.handle_id] = (await env.profile(EMAIL)).profile_id
    async with env.uow(SPACE_ID) as uow:
        await uow.identity.add_assertions([late])
        await env.service._enqueue_apply(uow, SPACE_ID, [late.assertion_id], members)
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    [rejected] = await env.repo.get_assertions([late.assertion_id])
    assert (rejected.state, rejected.reason) == (AssertionState.REJECTED, "separated_by_split")


async def test_an_operator_merges_a_split_pair_again(env: Env) -> None:
    await env.attach(PHONE)
    await env.attach(EMAIL)
    await env.attach(PHONE, EMAIL)
    await env.drain()
    joined = await env.profile(PHONE)
    moved = [(await env.find(PHONE)).handle_id]
    await env.service.unmerge(SPACE_ID, joined.profile_id, handle_ids=moved, actor=ACTOR, reason="two people")
    await env.drain()
    profiles = [(await env.profile(h)).profile_id for h in (PHONE, EMAIL)]
    await env.service.merge(
        SPACE_ID, profiles, force=False, actor=ACTOR, reason="it was one person after all"
    )
    await env.drain()
    assert await env.same(PHONE, EMAIL)


async def test_a_dismissed_pair_of_profiles_stays_dismissed_whatever_handles_agree_next(env: Env) -> None:
    first = [handle(T.EMAIL, "mariana.souza@acme.com.br"), handle(T.PHONE_E164, "+55 11 98765-4321")]
    second = [handle(T.EMAIL, "mariana.souza@outlook.com"), handle(T.PHONE_E164, "+55 21 98765-4321")]
    await env.attach(*first)
    await env.attach(*second)
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    [suggestion] = env.repo.suggestions
    assert suggestion.features == {"agree": ["email_local", "phone_subscriber"]}
    await env.service.dismiss_suggestion(SPACE_ID, suggestion.suggestion_id)
    # A Gmail alias on each side is now the strongest agreement, between two other handles.
    await env.attach(first[0], handle(T.EMAIL, "marianasouza@gmail.com"))
    await env.attach(second[0], handle(T.EMAIL, "mariana.souza+loja@gmail.com"))
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    assert [s.suggestion_id for s in env.repo.suggestions] == [suggestion.suggestion_id]


async def test_blocking_a_merged_handle_detaches_it(env: Env) -> None:
    await env.attach(PHONE, EMAIL)
    op = await env.service.block(SPACE_ID, handle(T.EMAIL, "Ana.Silva@acme.com.br"), actor=ACTOR, reason="qa")
    assert op.status == "queued"
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    assert (await env.find(EMAIL)).status is HandleStatus.BLOCKED
    fresh = await env.service.block(SPACE_ID, handle(T.EMAIL, "nobody@acme.com.br"), actor=ACTOR, reason=None)
    assert fresh.status == "blocked"
    await env.attach(handle(T.EMAIL, "nobody@acme.com.br"), EMAIL2)
    assert (await env.find(handle(T.EMAIL, "nobody@acme.com.br"))).status is HandleStatus.BLOCKED


async def test_create_assertion_inside_one_profile_is_applied_at_once(env: Env) -> None:
    await env.attach(PHONE, EMAIL)
    op = await env.service.create_assertion(
        SPACE_ID,
        [PHONE, EMAIL],
        method=AssertionMethod.OTP,
        subject_kind=SubjectKind.PERSON,
        actor=ACTOR,
        reason=None,
    )
    assert op.status == "applied"
    assert env.apply_tasks() == []


async def test_list_assertions_pages_and_filters_by_profile(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL, handle(T.APP_USER_ID, "u-1", "app"))
    await env.attach(EMAIL2, handle(T.APP_USER_ID, "u-2", "app"))
    page, cursor = await env.service.list_assertions(
        SPACE_ID, profile_id=None, state=None, after=None, limit=2
    )
    assert len(page) == 2
    assert cursor == page[-1].assertion_id
    rest, end = await env.service.list_assertions(
        SPACE_ID, profile_id=None, state=None, after=cursor, limit=2
    )
    assert len(rest) == 1
    assert end is None
    mine, _ = await env.service.list_assertions(
        SPACE_ID, profile_id=result.profile_id, state=AssertionState.APPLIED, after=None, limit=10
    )
    assert len(mine) == 2


@pytest.mark.parametrize(
    ("profiles", "force", "reason"),
    [("same", False, None), ("two", True, None), ("kinds", False, None)],
)
async def test_merge_validation(env: Env, profiles: str, force: bool, reason: str | None) -> None:
    person = (await env.attach(PHONE)).profile_id
    other = (await env.attach(EMAIL)).profile_id
    account = (
        await env.attach(handle(T.ORG_REGISTRY_HMAC, "11222333000181"), kind=SubjectKind.ACCOUNT)
    ).profile_id
    pair = {"same": [person, person], "two": [person, other], "kinds": [person, account]}[profiles]
    with pytest.raises(InvalidInput):
        await env.service.merge(SPACE_ID, pair, force=force, actor=ACTOR, reason=reason)


async def test_unmerge_validation_and_unknown_ids(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL)
    other = await env.attach(EMAIL2)
    with pytest.raises(InvalidInput):
        await env.service.unmerge(SPACE_ID, result.profile_id, actor=ACTOR, reason=None)
    with pytest.raises(InvalidInput):
        await env.service.unmerge(
            SPACE_ID, result.profile_id, handle_ids=list(other.handle_ids), actor=ACTOR, reason=None
        )
    with pytest.raises(NotFound):
        await env.service.retract(SPACE_ID, uuid4(), actor=ACTOR, reason=None)
    with pytest.raises(NotFound):
        await env.service.profile_summary(SPACE_ID, uuid4())
    with pytest.raises(NotFound):
        await env.service.end_link(SPACE_ID, uuid4())


async def test_links_validate_kinds_and_methods(env: Env) -> None:
    org = handle(T.ORG_REGISTRY_HMAC, "11222333000181")
    with pytest.raises(InvalidInput):
        await env.service.create_link(
            SPACE_ID, PHONE, handle(T.EMAIL, "x@acme.com", kind=SubjectKind.PERSON), role="b"
        )
    with pytest.raises(InvalidInput):
        await env.service.create_link(SPACE_ID, PHONE, org, role="buyer", method=AssertionMethod.OTP)
    link = await env.service.create_link(SPACE_ID, PHONE, org, role="buyer", method=AssertionMethod.LOGIN)
    ended = await env.service.end_link(SPACE_ID, link.link_id)
    assert ended.valid_to == env.cell.clock.now()
    assert await env.service.end_link(SPACE_ID, link.link_id) == ended
    assert await env.service.active_links(SPACE_ID, [link.person_handle_id]) == []


async def test_profile_summary_lists_handles_and_links(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL, handle(T.GOV_ID_HMAC, cpf("529982247")))
    await env.service.create_link(
        SPACE_ID, EMAIL, handle(T.ORG_REGISTRY_HMAC, "11222333000181"), role="buyer"
    )
    summary = await env.service.profile_summary(SPACE_ID, result.profile_id)
    assert {h.type for h in summary.handles} == {T.PHONE_E164, T.EMAIL, T.GOV_ID_HMAC}
    assert [link.role for link in summary.links] == ["buyer"]
    assert choose_anchor(summary.handles).handle_id in result.handle_ids


async def test_a_failed_attach_leaves_nothing_behind(env: Env) -> None:
    with pytest.raises(InvalidInput):
        await env.attach(PHONE, handle(T.SYSTEM_ID, "x"))
    assert await env.repo.find_handle(T.PHONE_E164, "", "+5511987654321") is None
