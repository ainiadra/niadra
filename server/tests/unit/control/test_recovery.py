"""Second-factor recovery (5.22): single-use recovery codes, a person's own change of authenticator app
and the admin reset for a person who lost both."""

from uuid import uuid4

import pytest

from niadra.app.control import BindingSpec, SecondFactorStatus
from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, NotFound, Unauthenticated
from niadra.domain.control.records import Caller, Role

from .conftest import PASSWORD, World

FOUNDER = "founder@acme.com"


async def _enrolled(world: World, email: str, secret: str | None) -> tuple[Caller, list[str]]:
    """The first login with a valid code confirms the authenticator and brings the recovery codes."""
    token = await world.service.login(email, PASSWORD, world.totp(secret), None)
    assert token.recovery_codes is not None
    return world.service.authenticate(token.token), token.recovery_codes


async def _with_code(world: World, code: str, email: str = FOUNDER) -> int | None:
    token = await world.service.login(email, PASSWORD, None, None, recovery_code=code)
    return token.recovery_codes_left


async def _owner_without_second_factor(world: World) -> Caller:
    """The admin of a second tenant that does not require the second factor."""
    await world.service.bootstrap(
        tenant_name="Beta",
        tenant_slug="beta",
        email="owner@beta.com",
        region="sa-east-1",
        cell_id="sa-east-1-a",
        cell_address="https://cell-a.sa-east-1.niadra.test",
        password=PASSWORD,
        with_totp=False,
    )
    return world.service.authenticate(
        (await world.service.login("owner@beta.com", PASSWORD, None, None)).token
    )


async def test_the_login_that_confirms_the_authenticator_brings_ten_codes_once(world: World) -> None:
    first = await world.service.login(FOUNDER, PASSWORD, world.totp(), None)
    assert first.recovery_codes is not None
    assert len(set(first.recovery_codes)) == 10
    assert first.recovery_codes_left is None

    again = await world.service.login(FOUNDER, PASSWORD, world.totp(), None)
    assert (again.recovery_codes, again.recovery_codes_left) == (None, None)
    status = await world.service.second_factor_status(world.service.authenticate(first.token))
    assert status == SecondFactorStatus(world.clock.now(), 10)
    # Only keyed hashes are kept, never a code in any spelling.
    stored = repr(world.uow.store.data)
    assert not any(c in stored or c.replace("-", "") in stored for c in first.recovery_codes)


async def test_a_recovery_code_signs_in_once(world: World) -> None:
    _, codes = await _enrolled(world, FOUNDER, None)

    token = await world.service.login(
        FOUNDER, PASSWORD, None, None, recovery_code=codes[3].lower().replace("-", " ")
    )
    assert token.recovery_codes_left == 9
    assert world.service.authenticate(token.token).user_id == world.boot.admin.user.user_id
    for spent_or_unknown in (codes[3], "ZZZZZ-ZZZZZ", "not a code at all"):
        with pytest.raises(Unauthenticated, match="invalid second factor"):
            await _with_code(world, spent_or_unknown)
    assert await _with_code(world, codes[4]) == 8


async def test_a_refused_login_spends_no_code(world: World) -> None:
    _, codes = await _enrolled(world, FOUNDER, None)
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.login(FOUNDER, "not the password", None, None, recovery_code=codes[0])
    with pytest.raises(Forbidden):
        await world.service.login(FOUNDER, PASSWORD, None, uuid4(), recovery_code=codes[0])
    assert await _with_code(world, codes[0]) == 9


async def test_the_code_and_a_recovery_code_together_are_refused(world: World) -> None:
    _, codes = await _enrolled(world, FOUNDER, None)
    with pytest.raises(InvalidInput, match="not both"):
        await world.service.login(FOUNDER, PASSWORD, world.totp(), None, recovery_code=codes[0])
    assert await _with_code(world, codes[0]) == 9


async def test_a_recovery_code_opens_only_its_own_account(world: World) -> None:
    admin, founder_codes = await _enrolled(world, FOUNDER, None)
    rui = await world.service.create_user(admin, "rui@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)])
    await _enrolled(world, "rui@acme.com", rui.totp_secret)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await _with_code(world, founder_codes[0], "rui@acme.com")
    assert await _with_code(world, founder_codes[0]) == 9


async def test_new_codes_take_the_password_and_retire_the_old_ones(world: World) -> None:
    caller, old = await _enrolled(world, FOUNDER, None)
    with pytest.raises(Forbidden, match="invalid password"):
        await world.service.regenerate_recovery_codes(caller, "not the password")
    assert await _with_code(world, old[0]) == 9

    new = await world.service.regenerate_recovery_codes(caller, PASSWORD)
    assert len(new) == 10
    assert not set(new) & set(old)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await _with_code(world, old[1])
    assert await _with_code(world, new[0]) == 9
    assert (await world.service.second_factor_status(caller)).recovery_codes_left == 9


async def test_changing_the_app_keeps_the_old_one_until_the_new_one_is_confirmed(world: World) -> None:
    old_secret = world.boot.admin.totp_secret
    caller, old_codes = await _enrolled(world, FOUNDER, old_secret)
    with pytest.raises(Forbidden, match="invalid password"):
        await world.service.start_second_factor_change(caller, "not the password")
    with pytest.raises(Conflict, match="no authenticator change"):
        await world.service.confirm_second_factor_change(caller, world.totp(old_secret))

    new = await world.service.start_second_factor_change(caller, PASSWORD)
    assert new.secret != old_secret
    assert new.account == FOUNDER
    # Pending: the old app still signs in, the new one not yet.
    assert await world.service.login(FOUNDER, PASSWORD, world.totp(old_secret), None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, world.totp(new.secret), None)
    with pytest.raises(Forbidden, match="invalid second factor"):
        await world.service.confirm_second_factor_change(caller, world.totp(old_secret))

    world.clock.advance(minutes=5)
    codes = await world.service.confirm_second_factor_change(caller, world.totp(new.secret))
    assert len(codes) == 10
    assert await world.service.second_factor_status(caller) == SecondFactorStatus(world.clock.now(), 10)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, world.totp(old_secret), None)
    assert await world.service.login(FOUNDER, PASSWORD, world.totp(new.secret), None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await _with_code(world, old_codes[0])
    assert await _with_code(world, codes[0]) == 9
    with pytest.raises(Conflict, match="no authenticator change"):
        await world.service.confirm_second_factor_change(caller, world.totp(new.secret))


async def test_starting_the_change_again_drops_the_unconfirmed_secret(world: World) -> None:
    caller, _ = await _enrolled(world, FOUNDER, None)
    first = await world.service.start_second_factor_change(caller, PASSWORD)
    second = await world.service.start_second_factor_change(caller, PASSWORD)
    with pytest.raises(Forbidden, match="invalid second factor"):
        await world.service.confirm_second_factor_change(caller, world.totp(first.secret))
    assert await world.service.confirm_second_factor_change(caller, world.totp(second.secret))


async def test_a_person_without_a_second_factor_sets_one_up_from_the_account(world: World) -> None:
    owner = await _owner_without_second_factor(world)
    assert await world.service.second_factor_status(owner) == SecondFactorStatus(None, 0)
    with pytest.raises(Conflict, match="not set up"):
        await world.service.regenerate_recovery_codes(owner, PASSWORD)

    new = await world.service.start_second_factor_change(owner, PASSWORD)
    assert len(await world.service.confirm_second_factor_change(owner, world.totp(new.secret))) == 10
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login("owner@beta.com", PASSWORD, None, None)
    assert await world.service.login("owner@beta.com", PASSWORD, world.totp(new.secret), None)


async def test_an_admin_reset_sends_the_person_back_to_the_enrollment(world: World) -> None:
    admin, _ = await _enrolled(world, FOUNDER, None)
    rui = await world.service.create_user(admin, "rui@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)])
    rui_caller, rui_codes = await _enrolled(world, "rui@acme.com", rui.totp_secret)
    await world.service.start_second_factor_change(rui_caller, PASSWORD)
    world.clock.advance(minutes=1)

    reset = await world.service.reset_second_factor(
        admin, rui.user.user_id, "  lost the phone and the codes "
    )
    assert (reset.tenant_id, reset.user_id, reset.reset_by, reset.reason, reset.at) == (
        admin.tenant_id,
        rui.user.user_id,
        admin.user_id,
        "lost the phone and the codes",
        world.clock.now(),
    )
    assert list(world.uow.store.data["control"]["second_factor_resets"].values()) == [reset]
    assert await world.service.second_factor_status(rui_caller) == SecondFactorStatus(None, 0)
    assert world.uow.store.data["control"]["users"][rui.user.user_id].totp_pending_secret is None

    with pytest.raises(Unauthenticated, match="enrollment required"):
        await world.service.login("rui@acme.com", PASSWORD, None, None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await _with_code(world, rui_codes[0], "rui@acme.com")
    secret = await world.service.second_factor_setup("rui@acme.com", PASSWORD)
    assert secret != rui.totp_secret
    again = await world.service.login("rui@acme.com", PASSWORD, world.totp(secret), None)
    assert again.recovery_codes is not None
    assert len(again.recovery_codes) == 10


async def test_only_an_admin_of_the_same_tenant_resets(world: World) -> None:
    admin, _ = await _enrolled(world, FOUNDER, None)
    rui = await world.service.create_user(admin, "rui@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)])
    rui_caller, _ = await _enrolled(world, "rui@acme.com", rui.totp_secret)
    stranger = await _owner_without_second_factor(world)

    with pytest.raises(Forbidden):
        await world.service.reset_second_factor(rui_caller, admin.user_id, "trying my luck")
    with pytest.raises(NotFound, match="unknown user"):
        await world.service.reset_second_factor(stranger, rui.user.user_id, "not my tenant")
    with pytest.raises(NotFound, match="unknown user"):
        await world.service.reset_second_factor(admin, uuid4(), "nobody by that id")
    with pytest.raises(InvalidInput, match="reason"):
        await world.service.reset_second_factor(admin, rui.user.user_id, " x ")
    assert await world.service.second_factor_status(rui_caller) == SecondFactorStatus(world.clock.now(), 10)
    assert world.uow.store.data["control"]["second_factor_resets"] == {}
