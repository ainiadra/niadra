"""Claims (`declared`, `explicit_identify`) join handles only provisionally and never merge two profiles
without a record id (5.3): the protections against a takeover by typing someone else's e-mail."""

from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID
from tests.unit.identity.conftest import Env, handle

T = HandleType
M = AssertionMethod
PHONE = handle(T.PHONE_E164, "+55 11 98765-4321")
EMAIL = handle(T.EMAIL, "ana.silva@acme.com.br")
CRM = handle(T.SYSTEM_ID, "48213", "crm")
APP = handle(T.APP_USER_ID, "u-42", "app")
STRANGER = handle(T.PHONE_E164, "+55 21 97777-4444")
STRANGER_EMAIL = handle(T.EMAIL, "outra.pessoa@provedor.com.br")


async def victim(env: Env) -> None:
    await env.attach(PHONE, EMAIL, CRM, level=Verification.V1)


async def test_a_declared_number_joins_the_profile_but_stays_provisional(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    assert await env.same(STRANGER, EMAIL)
    assert (await env.find(STRANGER)).provisional
    assert not any([(await env.find(h)).provisional for h in (PHONE, EMAIL, CRM)])


async def test_a_claim_never_raises_the_level_of_the_handles_it_names(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED, level=Verification.V3)
    assert (await env.find(EMAIL)).level is Verification.V1
    assert (await env.find(STRANGER)).level is Verification.V0


async def test_explicit_between_two_existing_profiles_is_a_suggestion_even_when_one_is_verified(
    env: Env,
) -> None:
    await victim(env)
    await env.attach(STRANGER, level=Verification.V3)  # an OTP to the caller's own number
    result = await env.attach(STRANGER, EMAIL, method=M.EXPLICIT_IDENTIFY)
    assert not result.pending_merge
    assert await env.drain() == 0
    assert not await env.same(STRANGER, EMAIL)
    [suggestion] = await env.repo.open_suggestions(after=None, limit=10)
    assert suggestion.features == {"agree": ["explicit_identify"]}


async def test_a_declaration_of_values_nobody_knows_binds_nothing(env: Env) -> None:
    # Declared before the customer ever wrote: whoever uses the e-mail next must not write into the
    # declarer's profile.
    await env.attach(STRANGER, STRANGER_EMAIL, method=M.DECLARED)
    assert not await env.same(STRANGER, STRANGER_EMAIL)
    assert len(await env.repo.open_suggestions(after=None, limit=10)) == 1
    # A replay changes nothing.
    await env.attach(STRANGER, STRANGER_EMAIL, method=M.DECLARED)
    assert not await env.same(STRANGER, STRANGER_EMAIL)
    assert len(await env.repo.open_suggestions(after=None, limit=10)) == 1


async def test_what_joins_through_a_provisional_number_is_provisional_too(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    await env.attach(STRANGER, STRANGER_EMAIL)  # an ordinary event from the claimed number
    assert await env.same(STRANGER_EMAIL, EMAIL)
    assert (await env.find(STRANGER_EMAIL)).provisional
    # An id it brings along never vouches for the claimed number either.
    stranger_crm = handle(T.SYSTEM_ID, "99999", "erp")
    await env.attach(STRANGER, stranger_crm)
    await env.attach(STRANGER, stranger_crm, method=M.SYSTEM_IMPORT)
    assert (await env.find(STRANGER)).provisional


async def test_only_a_record_id_a_login_or_an_operator_confirms(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    for method in (M.OTP, M.DECLARED, M.SAME_EVENT):
        await env.attach(STRANGER, EMAIL, method=method)
        assert (await env.find(STRANGER)).provisional
    await env.attach(STRANGER, CRM, method=M.SYSTEM_IMPORT)
    assert not (await env.find(STRANGER)).provisional


async def test_a_login_with_the_profiles_app_id_confirms(env: Env) -> None:
    await env.attach(PHONE, EMAIL, APP)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    await env.attach(STRANGER, APP, method=M.LOGIN)
    assert not (await env.find(STRANGER)).provisional


async def test_an_operator_confirms_by_asserting_the_pair(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    op = await env.service.create_assertion(
        SPACE_ID,
        [STRANGER, EMAIL],
        method=M.EXPLICIT_IDENTIFY,
        subject_kind=SubjectKind.PERSON,
        actor="user:ops",
        reason=None,
    )
    assert op.status == "applied"
    assert not (await env.find(STRANGER)).provisional


async def test_undoing_the_claim_leaves_the_declarer_on_their_own_and_uncapped(env: Env) -> None:
    await victim(env)
    await env.attach(STRANGER, EMAIL, method=M.DECLARED)
    await env.attach(STRANGER, STRANGER_EMAIL)
    [claim] = [a for a in await env.repo.list_assertions(None, None, None, 100) if a.method is M.DECLARED]
    await env.service.retract(SPACE_ID, claim.assertion_id, actor="user:ops", reason="not the same person")
    await env.drain()
    assert not await env.same(STRANGER, EMAIL)
    assert await env.same(STRANGER, STRANGER_EMAIL)
    assert not (await env.find(STRANGER)).provisional
    assert not (await env.find(STRANGER_EMAIL)).provisional


async def test_claimed_numbers_never_push_out_the_customers_own(env: Env) -> None:
    # A profile keeps three numbers and demotes the weakest beyond them: a claimed number goes first,
    # never the customer's own, however old and unproven it is.
    await env.attach(PHONE, EMAIL, CRM)
    env.cell.clock.advance(days=1)
    for n, known in enumerate((EMAIL, CRM, PHONE)):
        await env.attach(handle(T.PHONE_E164, f"+55 31 9666{n}-2222"), known, method=M.DECLARED)
    assert (await env.find(PHONE)).status is HandleStatus.ACTIVE
