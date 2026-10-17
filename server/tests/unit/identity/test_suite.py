"""The mandatory identity suite of 5.19: 33 edge cases, one scenario each."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import uuid4

import pytest

from niadra.config.models import TenantSettings
from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.handles import HandleKey, is_partial
from niadra.domain.identity.model import AssertionState, MergeOp
from niadra.domain.identity.rules import Rejection, choose_anchor
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.identity.conftest import Env, cpf, handle, make_env

T = HandleType
M = AssertionMethod
PHONE = handle(T.PHONE_E164, "+55 11 98765-4321")
PHONES = [handle(T.PHONE_E164, v) for v in ("+55 21 97777-1234", "+55 31 96666-2222", "+55 41 95555-3333")]
EMAIL = handle(T.EMAIL, "ana.silva@acme.com.br")
EMAIL2 = handle(T.EMAIL, "bia.costa@acme.com.br")
EMAIL3 = handle(T.EMAIL, "caio.lima@acme.com.br")
CPF1 = handle(T.GOV_ID_HMAC, cpf("529982247"))
CPF2 = handle(T.GOV_ID_HMAC, cpf("111444777"))
CNPJ1 = handle(T.ORG_REGISTRY_HMAC, "11.222.333/0001-81")
CNPJ2 = handle(T.ORG_REGISTRY_HMAC, "12.ABC.345/01DE-35")
ACTOR = "user:ops"

Scenario = Callable[[Env], Awaitable[None]]
CASES: dict[int, Scenario] = {}


def case(number: int) -> Callable[[Scenario], Scenario]:
    def register(scenario: Scenario) -> Scenario:
        CASES[number] = scenario
        return scenario

    return register


@pytest.mark.parametrize("number", range(1, 34))
async def test_identity_suite(number: int, env: Env) -> None:
    await CASES[number](env)


@case(1)
async def cross_identifies_at_the_same_time(env: Env) -> None:
    await env.attach(PHONE)
    await env.attach(EMAIL)
    await env.service.create_assertion(
        SPACE_ID,
        [PHONE, EMAIL],
        method=M.EXPLICIT_IDENTIFY,
        subject_kind=SubjectKind.PERSON,
        actor=ACTOR,
        reason=None,
    )
    await env.service.create_assertion(
        SPACE_ID,
        [EMAIL, PHONE],
        method=M.EXPLICIT_IDENTIFY,
        subject_kind=SubjectKind.PERSON,
        actor=ACTOR,
        reason=None,
    )
    assert await env.drain() == 2
    assert await env.same(PHONE, EMAIL)
    assert [m.op for m in env.repo.merge_log] == [MergeOp.MERGE]
    states = {a.state for a in await env.repo.list_assertions(None, None, None, 10)}
    assert states == {AssertionState.APPLIED}


@case(2)
async def burst_of_identifies_to_one_target(env: Env) -> None:
    others = [PHONE, handle(T.APP_USER_ID, "u-42", "app"), handle(T.WA_BSUID, "BR.x1", "waba-1")]
    for h in [EMAIL, *others]:
        await env.attach(h)
    # From the tenant's systems: an explicit identify without a record id would only leave suggestions.
    for h in others:
        assert (await env.attach(h, EMAIL, method=M.SYSTEM_IMPORT)).pending_merge
    burst = [a for t in env.apply_tasks() for a in t.payload["assertion_ids"]]
    await env.service.identity_apply(
        Task(
            TaskName.IDENTITY_APPLY, SPACE_ID, {"op": "apply", "op_id": str(uuid4()), "assertion_ids": burst}
        )
    )
    assert await env.same(EMAIL, *others)
    assert len(env.repo.merge_log) == 3


@case(3)
async def two_verified_profiles_are_never_merged_automatically(env: Env) -> None:
    await env.attach(PHONE, level=Verification.V3)
    await env.attach(EMAIL, level=Verification.V3)
    assert (await env.attach(PHONE, EMAIL)).pending_merge
    await env.drain()
    assert not await env.same(PHONE, EMAIL)
    rejected = await env.repo.list_assertions(None, AssertionState.REJECTED, None, 10)
    assert [a.reason for a in rejected] == [Rejection.VERIFIED_PROFILES.value]
    first, second = (await env.profile(PHONE)).profile_id, (await env.profile(EMAIL)).profile_id
    await env.service.merge(
        SPACE_ID, [first, second], force=True, actor=ACTOR, reason="same customer, checked"
    )
    await env.drain()
    assert await env.same(PHONE, EMAIL)
    assert env.repo.merge_log[-1].actor == ACTOR
    assert env.repo.merge_log[-1].reason == "same customer, checked"


@case(4)
async def blocked_handles_never_join(env: Env) -> None:
    placeholder = handle(T.PHONE_E164, "+55 11 99999-9999")
    await env.attach(placeholder, EMAIL)
    await env.attach(placeholder, EMAIL2)
    assert (await env.find(placeholder)).status is HandleStatus.BLOCKED
    assert not await env.same(EMAIL, EMAIL2)
    assert not await env.same(placeholder, EMAIL)
    settings = TenantSettings(blocked_values={"email": ["QA@acme.com.br"]})
    env.cell.config.put(sample_config().model_copy(update={"settings": settings}))
    await env.attach(handle(T.EMAIL, "qa@acme.com.br"), PHONE)
    assert (await env.find(handle(T.EMAIL, "qa@acme.com.br"))).status is HandleStatus.BLOCKED


@case(5)
async def per_type_limits_demote_and_never_reject(env: Env) -> None:
    for phone in [PHONE, *PHONES]:
        await env.attach(phone, EMAIL)
        env.cell.clock.advance(days=2)
    assert await env.same(PHONE, *PHONES, EMAIL)
    assert (await env.find(PHONE)).status is HandleStatus.SUSPECTED_RECYCLED
    assert {(await env.find(p)).status for p in PHONES} == {HandleStatus.ACTIVE}
    await env.attach(EMAIL, CPF1)
    result = await env.attach(EMAIL, CPF2)
    assert result.profile_id == (await env.profile(EMAIL)).profile_id
    assert not await env.same(EMAIL, CPF2)
    rejected = await env.repo.list_assertions(None, AssertionState.REJECTED, None, 10)
    assert [a.reason for a in rejected] == [Rejection.ONE_PER_SCOPE.value]


@case(6)
async def recycled_number_is_not_a_bridge(env: Env) -> None:
    await env.attach(PHONE, EMAIL)
    env.cell.clock.advance(days=200)
    await env.attach(EMAIL2)
    result = await env.attach(PHONE, EMAIL2)
    assert not result.pending_merge
    assert result.profile_id == (await env.profile(EMAIL2)).profile_id
    # The old handle stays suspended with the first owner; the number starts over with the new one.
    key = (await env.find(PHONE)).type, "", (await env.find(PHONE)).value
    [old] = await env.repo.retired_handles([HandleKey(*key)])
    assert old.status is HandleStatus.SUSPECTED_RECYCLED
    assert await env.repo.profile_of(old.handle_id) == await env.profile(EMAIL)
    assert await env.same(PHONE, EMAIL2)
    assert env.apply_tasks() == []


@case(7)
async def shared_handle_becomes_ambiguous(env: Env) -> None:
    tablet = handle(T.PHONE_E164, "+55 11 3456-7890")
    await env.attach(tablet, EMAIL)
    await env.attach(tablet, EMAIL2)
    assert await env.same(EMAIL, EMAIL2)  # the tablet joined the first two before it looked shared
    result = await env.attach(tablet, EMAIL3)
    assert (await env.find(tablet)).status is HandleStatus.AMBIGUOUS
    assert result.profile_id == (await env.profile(EMAIL3)).profile_id
    assert not await env.same(tablet, EMAIL3)
    # Marking it undoes what it joined, so the first two people are apart again.
    assert await env.drain() == 1
    assert not await env.same(EMAIL, EMAIL2)
    assert not await env.same(tablet, EMAIL)
    assert not await env.same(tablet, EMAIL2)


@case(8)
async def gmail_alias_is_only_a_suggestion(env: Env) -> None:
    first, second = handle(T.EMAIL, "Ana.Silva+promo@gmail.com"), handle(T.EMAIL, "anasilva@gmail.com")
    await env.attach(first)
    await env.attach(second)
    assert not await env.same(first, second)
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    [suggestion] = env.repo.suggestions
    assert {suggestion.handle_a, suggestion.handle_b} == {
        (await env.find(first)).handle_id,
        (await env.find(second)).handle_id,
    }
    assert suggestion.features == {"agree": ["gmail_canonical"]}
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    assert len(env.repo.suggestions) == 1
    assert not await env.same(first, second)


@case(9)
async def legacy_phone_without_the_ninth_digit(env: Env) -> None:
    legacy = await env.attach(handle(T.WA_ID, "551187654321"))
    assert (await env.attach(PHONE)).profile_id == legacy.profile_id
    landline = await env.attach(handle(T.WA_ID, "551133334444"))
    assert (await env.attach(handle(T.PHONE_E164, "+55 11 93333-4444"))).profile_id != landline.profile_id


@case(10)
async def invalid_national_document(env: Env) -> None:
    with pytest.raises(InvalidInput):
        await env.attach(PHONE, handle(T.GOV_ID_HMAC, "529.982.247-24"))
    assert await env.repo.find_handle(T.PHONE_E164, "", "+5511987654321") is None


@case(11)
async def same_document_in_two_tenants(env: Env) -> None:
    other = make_env(Cell())
    first, second = await env.attach(CPF1, EMAIL), await other.attach(CPF1)
    assert first.profile_id != second.profile_id
    assert (await env.profile(CPF1)).pseudonym != (await other.profile(CPF1)).pseudonym
    assert await other.repo.find_handle(T.EMAIL, "", "ana.silva@acme.com.br") is None


@case(12)
async def homonyms_stay_apart(env: Env) -> None:
    first, second = handle(T.EMAIL, "maria.souza@acme.com"), handle(T.EMAIL, "maria.souza@outra.com")
    await env.attach(first, PHONE)
    await env.attach(second, PHONES[0])
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    assert env.repo.suggestions == []
    assert not await env.same(first, second)


@case(13)
async def partial_unmerge(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL, CPF1)
    email = await env.find(EMAIL)
    await env.service.unmerge(
        SPACE_ID, result.profile_id, handle_ids=[email.handle_id], actor=ACTOR, reason="wrong"
    )
    await env.drain()
    assert (await env.profile(PHONE)).profile_id == result.profile_id
    assert await env.same(PHONE, CPF1)
    assert not await env.same(PHONE, EMAIL)
    log = env.repo.merge_log[-1]
    assert (log.op, log.handles_moved, log.actor) == (MergeOp.UNMERGE, (email.handle_id,), ACTOR)
    assert {row["kind"] for row in env.privacy.feed} == {"unmerge"}


@case(14)
async def unmerge_of_a_bridge_splits_three_ways(env: Env) -> None:
    await env.attach(PHONE, EMAIL)
    result = await env.attach(EMAIL, CPF1)
    assert await env.same(PHONE, EMAIL, CPF1)
    bridge = await env.find(EMAIL)
    await env.service.unmerge(
        SPACE_ID, result.profile_id, handle_ids=[bridge.handle_id], actor=ACTOR, reason=None
    )
    await env.drain()
    profiles = {(await env.profile(h)).profile_id for h in (PHONE, EMAIL, CPF1)}
    assert len(profiles) == 3


@case(15)
async def partial_forgetting_detaches_one_handle(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL)
    before = (await env.profile(PHONE)).privacy_version
    phone = await env.find(PHONE)
    await env.service.unmerge(
        SPACE_ID, result.profile_id, handle_ids=[phone.handle_id], actor="privacy", reason=None
    )
    await env.drain()
    assert (await env.profile(EMAIL)).privacy_version > before
    feed_profiles = {p for row in env.privacy.feed for p in row["profile_ids"]}
    assert feed_profiles == {result.profile_id, (await env.profile(PHONE)).profile_id}


@case(16)
async def out_of_order_replay_never_regresses_times(env: Env) -> None:
    now = env.cell.clock.now()
    await env.attach(PHONE, at=now)
    await env.attach(PHONE, at=now - timedelta(hours=3))
    record = await env.find(PHONE)
    assert (record.first_seen, record.last_seen) == (now - timedelta(hours=3), now)


@case(17)
async def idempotent_retries(env: Env) -> None:
    event_id = uuid4()
    await env.attach(EMAIL)
    first = await env.attach(PHONE, EMAIL, event_id=event_id)
    assertions = await env.repo.list_assertions(None, None, None, 10)
    assert await env.attach(PHONE, EMAIL, event_id=event_id) == first
    assert await env.repo.list_assertions(None, None, None, 10) == assertions
    await env.attach(PHONE)
    await env.attach(EMAIL2)
    await env.attach(PHONE, EMAIL2)
    [task] = env.apply_tasks()
    await env.service.identity_apply(task)
    await env.service.identity_apply(task)
    assert len(env.repo.merge_log) == 1


@case(18)
async def whatsapp_id_without_phone_is_partial(env: Env) -> None:
    bsuid = handle(T.WA_BSUID, "BR.1a2b3c", "waba-77")
    result = await env.attach(bsuid)
    profile = await env.repo.get_profile(result.profile_id)
    assert profile is not None
    assert is_partial({h.type for h in await env.repo.get_handles(profile.handle_ids)})
    with pytest.raises(InvalidInput):
        await env.attach(handle(T.WA_BSUID, "BR.1a2b3c"))


@case(19)
async def channel_rotation_creates_an_alias_never_a_merge(env: Env) -> None:
    old, new = handle(T.WA_BSUID, "BR.old", "waba-1"), handle(T.WA_BSUID, "BR.new", "waba-1")
    await env.attach(old, PHONE)
    await env.attach(old, new, method=M.CHANNEL_ROTATION)
    assert await env.same(old, new, PHONE)
    await env.attach(EMAIL)
    result = await env.attach(new, EMAIL, method=M.CHANNEL_ROTATION)
    assert not result.pending_merge
    assert not await env.same(new, EMAIL)
    assert env.apply_tasks() == []


@case(20)
async def same_system_id_in_two_verified_profiles(env: Env) -> None:
    crm = handle(T.SYSTEM_ID, "C-1001", "crm")
    await env.attach(crm, PHONE, level=Verification.V3)
    await env.attach(EMAIL, level=Verification.V4)
    assert (await env.attach(crm, EMAIL)).pending_merge
    await env.drain()
    assert not await env.same(crm, EMAIL)


@case(21)
async def business_object_follows_its_owner_on_unmerge(env: Env) -> None:
    erp = handle(T.SYSTEM_ID, "INV-CUST-9", "erp")
    result = await env.attach(PHONE, erp)
    owner = await env.find(erp)
    await env.service.unmerge(
        SPACE_ID, result.profile_id, handle_ids=[owner.handle_id], actor=ACTOR, reason=None
    )
    await env.drain()
    # Objects hang on the origin handle (L6): resolving it again yields the owner's new profile.
    moved = await env.repo.profile_of(owner.handle_id)
    assert moved is not None
    assert moved.profile_id != result.profile_id


@case(22)
async def person_organization_assertion_becomes_a_link(env: Env) -> None:
    op = await env.service.create_assertion(
        SPACE_ID,
        [CPF1, CNPJ1],
        method=M.EXPLICIT_IDENTIFY,
        subject_kind=SubjectKind.PERSON,
        actor=ACTOR,
        reason=None,
    )
    assert op.status == "linked"
    assert await env.repo.list_assertions(None, None, None, 10) == []
    [link] = await env.repo.active_links([(await env.find(CPF1)).handle_id])
    assert link.org_handle_id == (await env.find(CNPJ1)).handle_id
    assert (await env.profile(CNPJ1)).kind is SubjectKind.ACCOUNT


@case(23)
async def company_pbx_phone_is_ambiguous(env: Env) -> None:
    pbx = handle(T.PHONE_E164, "+55 11 3000-1000", kind=SubjectKind.ACCOUNT)
    await env.attach(pbx, CNPJ1, kind=SubjectKind.ACCOUNT)
    await env.attach(pbx, CNPJ2, kind=SubjectKind.ACCOUNT)
    assert (await env.find(pbx)).status is HandleStatus.AMBIGUOUS
    assert not await env.same(CNPJ1, CNPJ2)


@case(24)
async def driver_linked_to_three_carriers(env: Env) -> None:
    driver = handle(T.PHONE_E164, "+55 11 98888-7777")
    carriers = [handle(T.SYSTEM_ID, f"carrier-{i}", "tms", SubjectKind.PARTNER) for i in range(3)]
    links = [await env.service.create_link(SPACE_ID, driver, c, role="driver") for c in carriers]
    driver_id = (await env.find(driver)).handle_id
    assert len(await env.service.active_links(SPACE_ID, [driver_id])) == 3
    await env.service.end_link(SPACE_ID, links[0].link_id)
    assert len(await env.service.active_links(SPACE_ID, [driver_id])) == 2
    assert (await env.profile(carriers[0])).kind is SubjectKind.PARTNER


@case(25)
async def webmail_domain_never_unites_organizations(env: Env) -> None:
    webmail, domain = handle(T.EMAIL_DOMAIN, "gmail.com"), handle(T.EMAIL_DOMAIN, "acme.com.br")
    await env.attach(CNPJ1, webmail, domain, kind=SubjectKind.ACCOUNT)
    await env.attach(CNPJ2, webmail, domain, kind=SubjectKind.ACCOUNT)
    assert (await env.find(webmail)).status is HandleStatus.BLOCKED
    assert not await env.same(CNPJ1, CNPJ2)
    assert not await env.same(CNPJ1, domain)


@case(26)
async def two_organizations_same_name_different_registries(env: Env) -> None:
    await env.attach(CNPJ1, handle(T.SYSTEM_ID, "acme", "crm", SubjectKind.ACCOUNT), kind=SubjectKind.ACCOUNT)
    await env.attach(
        CNPJ2, handle(T.SYSTEM_ID, "acme-2", "crm", SubjectKind.ACCOUNT), kind=SubjectKind.ACCOUNT
    )
    await env.service.identity_suggest(Task(TaskName.IDENTITY_SUGGEST, SPACE_ID))
    assert not await env.same(CNPJ1, CNPJ2)
    assert env.repo.suggestions == []


@case(27)
async def event_with_two_subjects_links_and_never_merges(env: Env) -> None:
    result = await env.attach(PHONE, CNPJ1)
    assert result.profile_id == (await env.profile(PHONE)).profile_id
    assert not await env.same(PHONE, CNPJ1)
    [link] = await env.repo.active_links([(await env.find(PHONE)).handle_id])
    assert link.org_handle_id == (await env.find(CNPJ1)).handle_id
    assert await env.repo.list_assertions(None, None, None, 10) == []


@case(28)
async def sole_proprietor_is_a_person_and_an_account(env: Env) -> None:
    link = await env.service.create_link(SPACE_ID, CPF1, CNPJ1, role="owner", can_see_contacts=True)
    assert (await env.profile(CPF1)).kind is SubjectKind.PERSON
    assert (await env.profile(CNPJ1)).kind is SubjectKind.ACCOUNT
    assert not await env.same(CPF1, CNPJ1)
    assert (link.role, link.can_see_contacts) == ("owner", True)


@case(29)
async def same_system_id_in_different_namespaces(env: Env) -> None:
    crm, erp = handle(T.SYSTEM_ID, "1001", "crm"), handle(T.SYSTEM_ID, "1001", "erp")
    await env.attach(crm)
    await env.attach(erp)
    assert (await env.find(crm)).handle_id != (await env.find(erp)).handle_id
    assert not await env.same(crm, erp)


@case(30)
async def unmerge_takes_each_link_with_its_half(env: Env) -> None:
    result = await env.attach(PHONE, EMAIL)
    await env.service.create_link(SPACE_ID, PHONE, CNPJ1, role="buyer")
    await env.service.create_link(SPACE_ID, EMAIL, CNPJ2, role="technical_contact")
    email = await env.find(EMAIL)
    await env.service.unmerge(
        SPACE_ID, result.profile_id, handle_ids=[email.handle_id], actor=ACTOR, reason=None
    )
    await env.drain()
    phone_side = await env.service.profile_summary(SPACE_ID, (await env.profile(PHONE)).profile_id)
    email_side = await env.service.profile_summary(SPACE_ID, (await env.profile(EMAIL)).profile_id)
    assert [link.role for link in phone_side.links] == ["buyer"]
    assert [link.role for link in email_side.links] == ["technical_contact"]


@case(31)
async def fact_goes_to_the_speaker_handle_with_the_highest_level(env: Env) -> None:
    app = handle(T.APP_USER_ID, "u-9", "app")
    await env.attach(app, PHONE)
    await env.verify(app, Verification.V3)
    await env.verify(PHONE, Verification.V1)
    assert choose_anchor([await env.find(PHONE), await env.find(app)]).type is T.APP_USER_ID


@case(32)
async def system_event_fact_anchors_on_the_system_id(env: Env) -> None:
    erp = handle(T.SYSTEM_ID, "CUST-7", "erp")
    await env.attach(EMAIL, erp, level=Verification.V2)
    await env.verify(EMAIL, Verification.V4)
    records = [await env.find(EMAIL), await env.find(erp)]
    assert choose_anchor(records, system_event=True).type is T.SYSTEM_ID


@case(33)
async def anchor_tie_goes_to_the_smallest_handle_id(env: Env) -> None:
    await env.attach(PHONE, EMAIL, level=Verification.V2)
    records = [await env.find(EMAIL), await env.find(PHONE)]
    assert choose_anchor(records).handle_id == min(r.handle_id for r in records)
