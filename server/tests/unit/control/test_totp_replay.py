"""An authenticator code works once (RFC 6238, 5.2): the control plane keeps the 30-second step of the last
code it accepted for each person and refuses a code of that step or an earlier one, at the login, the SSO
second factor (the same path), the change of authenticator and the presence proof of the account page."""

import pytest

from niadra.app.control import BindingSpec
from niadra.domain.common.errors import Forbidden, Unauthenticated
from niadra.domain.control.credentials import totp_code, totp_step
from niadra.domain.control.records import Role

from .conftest import PASSWORD, World

FOUNDER = "founder@acme.com"


def _code(world: World, secret: str, steps_from_now: int = 0) -> str:
    return totp_code(secret, world.clock.now().timestamp() + 30 * steps_from_now)


def test_the_step_of_a_code_is_found_within_one_step_of_drift() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    at = 1_790_000_000.0
    step = int(at // 30)
    assert totp_step(secret, totp_code(secret, at), at) == step
    assert totp_step(secret, totp_code(secret, at - 30), at) == step - 1
    assert totp_step(secret, totp_code(secret, at + 30), at) == step + 1
    assert totp_step(secret, totp_code(secret, at + 60), at) is None


async def test_the_same_code_signs_in_once(world: World) -> None:
    secret = world.boot.admin.totp_secret
    assert secret
    code = _code(world, secret)
    assert await world.service.login(FOUNDER, PASSWORD, code, None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, code, None)
    # Still refused at the end of its own step, and the next step's code works.
    world.clock.advance(seconds=29)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, code, None)
    world.clock.advance(seconds=1)
    assert await world.service.login(FOUNDER, PASSWORD, _code(world, secret), None)


async def test_a_code_older_than_the_last_one_accepted_is_refused(world: World) -> None:
    secret = world.boot.admin.totp_secret
    assert secret
    # A clock running ahead gives the next step's code: accepted, and the current one is then too old.
    assert await world.service.login(FOUNDER, PASSWORD, _code(world, secret, +1), None)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, _code(world, secret), None)


async def test_each_person_has_its_own_last_step(world: World) -> None:
    admin = await world.admin()
    other = await world.service.create_user(admin, "ana@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)])
    assert other.totp_secret
    code_ana = _code(world, other.totp_secret, +1)
    assert await world.service.login("ana@acme.com", PASSWORD, code_ana, None)
    # The founder's step is not Ana's: a code of the same step works for the founder.
    secret = world.boot.admin.totp_secret
    assert secret
    assert await world.service.login(FOUNDER, PASSWORD, _code(world, secret, +1), None)


async def test_the_code_that_confirms_a_new_authenticator_never_signs_in(world: World) -> None:
    caller = await world.admin()
    new = await world.service.start_second_factor_change(caller, PASSWORD)
    code = _code(world, new.secret, +1)
    assert len(await world.service.confirm_second_factor_change(caller, code)) == 10
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login(FOUNDER, PASSWORD, code, None)
    world.clock.advance(seconds=60)
    assert await world.service.login(FOUNDER, PASSWORD, _code(world, new.secret), None)


async def test_a_code_seen_at_the_login_does_not_confirm_a_change_either(world: World) -> None:
    secret = world.boot.admin.totp_secret
    assert secret
    token = await world.service.login(FOUNDER, PASSWORD, _code(world, secret, +1), None)
    caller = world.service.authenticate(token.token)
    new = await world.service.start_second_factor_change(caller, PASSWORD)
    # Same step as the login, other secret: the step is spent for the person, whatever the app.
    with pytest.raises(Forbidden, match="invalid second factor"):
        await world.service.confirm_second_factor_change(caller, _code(world, new.secret, +1))
    world.clock.advance(seconds=30)
    assert len(await world.service.confirm_second_factor_change(caller, _code(world, new.secret, +1))) == 10
