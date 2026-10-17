"""A number that may have changed hands: after a long silence, or when the tenant's systems tie it
to another customer, the old handle stays suspended in its profile with that person's memory, and the
number starts over as a new handle whose events build a provisional profile. Only a record id of the
previous profile brings it back, never an OTP to the number or a typed e-mail."""

import pytest

from niadra.contracts.common import Handle
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.records import HandleRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, Verification
from tests.unit.identity.conftest import Env, cpf, handle

T = HandleType
M = AssertionMethod
PHONE = handle(T.PHONE_E164, "+55 11 98765-4321")
EMAIL = handle(T.EMAIL, "ana.silva@acme.com.br")
CRM_ID = handle(T.SYSTEM_ID, "48213", scope="crm")


async def silent_for_seven_months(env: Env) -> None:
    await env.attach(PHONE, EMAIL, CRM_ID)
    env.cell.clock.advance(days=210)


async def retired(env: Env, h: Handle) -> list[HandleRecord]:
    key = normalize_handle(h.type, h.value, h.scope, h.subject_kind).key
    return await env.repo.retired_handles([key])


async def test_a_number_back_after_a_long_silence_starts_a_provisional_profile(env: Env) -> None:
    await silent_for_seven_months(env)
    old_profile = await env.profile(EMAIL)
    result = await env.attach(PHONE, level=Verification.V1)
    # The old handle stays suspended in the old profile, with its memory, for the reconfirmation.
    [old] = await retired(env, PHONE)
    assert old.status is HandleStatus.SUSPECTED_RECYCLED
    assert (await env.repo.profile_of(old.handle_id)) == old_profile
    # The number is a new handle now, in an empty profile of its own: its events never reach Ana's.
    current = await env.find(PHONE)
    assert current.handle_id != old.handle_id
    assert current.status is HandleStatus.ACTIVE
    assert result.profile_id is not None
    assert result.profile_id != old_profile.profile_id
    assert (await env.profile(PHONE)).handle_ids == (current.handle_id,)


async def test_the_new_holder_keeps_building_the_provisional_profile(env: Env) -> None:
    await silent_for_seven_months(env)
    await env.attach(PHONE)
    newcomer = handle(T.EMAIL, "novo.dono@acme.com.br")
    await env.attach(PHONE, newcomer)
    assert await env.same(PHONE, newcomer)
    assert not await env.same(PHONE, EMAIL)
    assert await env.drain() == 0


async def test_a_record_id_in_the_same_event_vouches_for_the_number(env: Env) -> None:
    # The ERP's event about the same customer carries its CRM id: the number is still theirs.
    await silent_for_seven_months(env)
    await env.attach(PHONE, CRM_ID)
    assert (await env.find(PHONE)).status is HandleStatus.ACTIVE
    assert await env.same(PHONE, CRM_ID)
    assert await retired(env, PHONE) == []


async def test_an_email_in_the_same_event_does_not_vouch(env: Env) -> None:
    # Whoever holds the phone can type an e-mail; it is not a record id, and never joins the number back.
    await silent_for_seven_months(env)
    await env.attach(PHONE, EMAIL)
    [old] = await retired(env, PHONE)
    assert old.status is HandleStatus.SUSPECTED_RECYCLED
    assert not await env.same(PHONE, EMAIL)
    await env.attach(PHONE, EMAIL)
    assert await env.drain() == 0
    assert not await env.same(PHONE, EMAIL)


async def test_a_recent_number_is_never_suspended(env: Env) -> None:
    await env.attach(PHONE, EMAIL, CRM_ID)
    env.cell.clock.advance(days=30)
    await env.attach(PHONE)
    assert (await env.find(PHONE)).status is HandleStatus.ACTIVE
    assert await env.same(PHONE, EMAIL)


@pytest.mark.parametrize(
    ("method", "partner", "restored"),
    [
        (M.EXPLICIT_IDENTIFY, CRM_ID, True),
        (M.SYSTEM_IMPORT, CRM_ID, True),
        (M.LOGIN, CRM_ID, True),
        (M.SAME_EVENT, CRM_ID, True),
        (M.OTP, CRM_ID, False),  # an OTP to the number proves only who holds it now
        (M.EXPLICIT_IDENTIFY, EMAIL, False),
        (M.SAME_EVENT, EMAIL, False),
    ],
)
async def test_only_a_record_id_of_the_same_profile_brings_the_number_back(
    env: Env, method: AssertionMethod, partner: Handle, restored: bool
) -> None:
    await silent_for_seven_months(env)
    await env.attach(PHONE)
    await env.attach(PHONE, partner, method=method)
    await env.drain()
    assert await env.same(PHONE, EMAIL) is restored


async def test_another_profiles_record_id_never_brings_it_to_the_old_owner(env: Env) -> None:
    await silent_for_seven_months(env)
    await env.attach(PHONE)
    other = handle(T.SYSTEM_ID, "99999", scope="crm")
    await env.attach(other, handle(T.EMAIL, "novo.dono@acme.com.br"))
    await env.attach(PHONE, other, method=M.EXPLICIT_IDENTIFY)
    await env.drain()
    assert await env.same(PHONE, other)
    assert not await env.same(PHONE, EMAIL)


async def test_the_tenants_systems_tell_a_recent_number_changed_hands(env: Env) -> None:
    # Ana's number is recent, but the CRM now says it belongs to Bia, another customer of the same CRM.
    await env.attach(PHONE, EMAIL, CRM_ID)
    bia = handle(T.SYSTEM_ID, "77120", scope="crm")
    bia_email = handle(T.EMAIL, "bia.costa@acme.com.br")
    await env.attach(bia, bia_email)
    env.cell.clock.advance(days=20)
    await env.attach(PHONE, bia, method=M.SYSTEM_IMPORT)
    [old] = await retired(env, PHONE)
    assert old.status is HandleStatus.SUSPECTED_RECYCLED
    assert await env.repo.profile_of(old.handle_id) == await env.profile(EMAIL)
    assert await env.same(PHONE, bia_email)
    assert not await env.same(PHONE, EMAIL)


async def test_an_ordinary_event_never_takes_a_recent_number_from_its_customer(env: Env) -> None:
    # Whoever holds the phone can type someone else's document into a conversation.
    ana_doc, bia_doc = handle(T.GOV_ID_HMAC, cpf("529982247")), handle(T.GOV_ID_HMAC, cpf("111444777"))
    await env.attach(PHONE, EMAIL, ana_doc)
    await env.attach(bia_doc, handle(T.EMAIL, "bia.costa@acme.com.br"))
    await env.attach(PHONE, bia_doc)
    await env.drain()
    assert await retired(env, PHONE) == []
    assert await env.same(PHONE, EMAIL)
    assert not await env.same(PHONE, bia_doc)


async def test_a_phone_over_the_profile_limit_is_demoted_but_stays_its_customers(env: Env) -> None:
    phones = [handle(T.PHONE_E164, f"+55 11 9{n}000-000{n}") for n in range(1, 5)]
    await env.attach(CRM_ID, *phones)
    demoted = [p for p in phones if (await env.find(p)).status is HandleStatus.SUSPECTED_RECYCLED]
    assert len(demoted) == 1
    await env.attach(demoted[0])
    assert await retired(env, demoted[0]) == []
    assert await env.same(demoted[0], CRM_ID)
