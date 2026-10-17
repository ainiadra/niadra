"""The authenticator enrollment (5.22): a person whose second factor was never confirmed gets the
secret back after the password, and the first valid code at login confirms it."""

import pytest

from niadra.domain.common.errors import Conflict, NotFound, Unauthenticated
from niadra.domain.control.credentials import totp_code

from .conftest import PASSWORD, World


async def test_a_new_admin_logs_in_through_the_enrollment(world: World) -> None:
    created = await world.service.add_admin_beside("founder@acme.com", "Owner@Acme.com", PASSWORD)
    assert created.user.totp_confirmed_at is None

    with pytest.raises(Unauthenticated, match="enrollment required"):
        await world.service.login("owner@acme.com", PASSWORD, None, None)
    secret = await world.service.second_factor_setup("owner@acme.com", PASSWORD)
    assert secret == created.totp_secret

    token = await world.service.login(
        "owner@acme.com", PASSWORD, totp_code(secret, world.clock.now().timestamp()), None
    )
    assert world.service.authenticate(token.token).user_id == created.user.user_id
    with pytest.raises(Conflict, match="already set up"):
        await world.service.second_factor_setup("owner@acme.com", PASSWORD)
    # Confirmed: a login without a code is a wrong code, never a new enrollment.
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login("owner@acme.com", PASSWORD, None, None)


async def test_the_enrollment_needs_the_password(world: World) -> None:
    await world.service.add_admin_beside("founder@acme.com", "owner@acme.com", PASSWORD)
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.second_factor_setup("owner@acme.com", "not the password")
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.second_factor_setup("nobody@acme.com", PASSWORD)


async def test_a_wrong_first_code_confirms_nothing(world: World) -> None:
    await world.service.add_admin_beside("founder@acme.com", "owner@acme.com", PASSWORD)
    with pytest.raises(Unauthenticated, match="invalid second factor"):
        await world.service.login("owner@acme.com", PASSWORD, "000000", None)
    assert await world.service.second_factor_setup("owner@acme.com", PASSWORD)


async def test_the_admin_goes_into_an_existing_tenant_only(world: World) -> None:
    with pytest.raises(NotFound):
        await world.service.add_admin_beside("nobody@acme.com", "owner@acme.com", PASSWORD)
