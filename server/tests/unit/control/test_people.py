"""People of a tenant in the Console (5.22): invitations to choose a password, deactivation and
reactivation, role changes with a reason, and the access history that keeps who did what and why."""

from datetime import timedelta
from uuid import UUID

import pytest

from niadra.app.control import BindingSpec
from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, NotFound, Unauthenticated
from niadra.domain.control.credentials import totp_code
from niadra.domain.control.records import AccessChangeKind, Caller, Role

from .conftest import PASSWORD, World

NEW_PASSWORD = "another long passphrase"


async def _sign_in(world: World, email: str, space_id: UUID | None = None) -> tuple[Caller, str]:
    """Signs a person in with PASSWORD, setting up the authenticator first, as the tenant requires it."""
    secret = await world.service.second_factor_setup(email, PASSWORD)
    issued = await world.service.login(email, PASSWORD, world.totp(secret), space_id)
    return world.service.authenticate(issued.token), issued.token


async def _invite(world: World, admin: Caller, email: str, *roles: Role) -> str:
    created = await world.service.create_user(
        admin, email, None, [BindingSpec(r) for r in roles or (Role.REVIEW,)]
    )
    assert created.invitation is not None
    return created.invitation.token


async def test_an_invited_person_chooses_a_password_then_sets_up_the_authenticator(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(admin, "Nina@Acme.com", None, [BindingSpec(Role.REVIEW)])
    assert created.totp_secret is None
    assert created.invitation is not None
    assert created.invitation.invitation.expires_at == world.clock.now() + timedelta(days=7)
    token = created.invitation.token
    # Only a keyed hash of the token is kept.
    assert token not in repr(world.uow.store.data)

    # No password yet: nothing signs in.
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.login("nina@acme.com", "!", None, None)

    preview = await world.service.preview_invitation(token)
    assert (preview.email, preview.tenant_name) == ("nina@acme.com", "Acme")
    assert await world.service.accept_invitation(token, NEW_PASSWORD) == "nina@acme.com"

    # The tenant requires the second factor: the first sign-in asks for the enrollment.
    with pytest.raises(Unauthenticated, match="enrollment required"):
        await world.service.login("nina@acme.com", NEW_PASSWORD, None, None)
    secret = await world.service.second_factor_setup("nina@acme.com", NEW_PASSWORD)
    first = await world.service.login(
        "nina@acme.com", NEW_PASSWORD, totp_code(secret, world.clock.now().timestamp()), None
    )
    assert first.recovery_codes is not None
    assert world.service.authenticate(first.token).roles == frozenset({Role.REVIEW})


async def test_an_invitation_works_once_and_only_until_it_expires(world: World) -> None:
    admin = await world.admin()
    token = await _invite(world, admin, "rui@acme.com")
    await world.service.accept_invitation(token, NEW_PASSWORD)
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await world.service.accept_invitation(token, NEW_PASSWORD)
    with pytest.raises(Unauthenticated):
        await world.service.preview_invitation(token)

    late = await _invite(world, admin, "lia@acme.com")
    world.clock.advance(days=7, seconds=1)
    with pytest.raises(Unauthenticated, match="invalid or expired"):
        await world.service.accept_invitation(late, NEW_PASSWORD)
    with pytest.raises(Unauthenticated):
        await world.service.accept_invitation("not-a-real-invitation-token-at-all", NEW_PASSWORD)


async def test_a_new_link_ends_the_earlier_one_and_serves_a_forgotten_password(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(admin, "ivo@acme.com", None, [BindingSpec(Role.ANALYSIS)])
    assert created.invitation is not None
    again = await world.service.reissue_invitation(admin, created.user.user_id)
    with pytest.raises(Unauthenticated):
        await world.service.accept_invitation(created.invitation.token, NEW_PASSWORD)
    await world.service.accept_invitation(again.token, NEW_PASSWORD)

    # Forgot the password: a new link sets another one, and the old password stops working.
    forgot = await world.service.reissue_invitation(admin, created.user.user_id)
    await world.service.accept_invitation(forgot.token, "a third long passphrase")
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.login("ivo@acme.com", NEW_PASSWORD, None, None)


async def test_a_refused_password_does_not_spend_the_link(world: World) -> None:
    admin = await world.admin()
    token = await _invite(world, admin, "bea@acme.com")
    with pytest.raises(InvalidInput):
        await world.service.accept_invitation(token, "short")
    await world.service.accept_invitation(token, NEW_PASSWORD)


async def test_the_list_says_how_each_person_signs_in_and_the_state_of_the_second_factor(
    world: World,
) -> None:
    admin = await world.admin()
    await _invite(world, admin, "nina@acme.com", Role.REVIEW, Role.ANALYSIS)
    people = {p.user.email: p for p in await world.service.list_users(admin)}
    founder, nina = people["founder@acme.com"], people["nina@acme.com"]
    assert founder.user.totp_confirmed_at is not None
    assert founder.recovery_codes_left == 10
    assert founder.invitation is None
    assert nina.invitation is not None
    assert nina.invitation.expires_at > world.clock.now()
    assert nina.user.totp_confirmed_at is None
    assert {b.role for b in nina.bindings} == {Role.REVIEW, Role.ANALYSIS}
    assert not nina.sso


async def test_a_deactivated_person_is_cut_at_once_on_the_control_plane(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(
        admin, "ana@acme.com", PASSWORD, [BindingSpec(Role.SECURITY)], with_totp=False
    )
    ana, token = await _sign_in(world, "ana@acme.com")
    assert (await world.service.authenticate_active(token)).user_id == ana.user_id

    await world.service.deactivate_user(admin, created.user.user_id, "left the company")
    # The token still decodes (a cell accepts it until it expires), but the control plane refuses it.
    assert world.service.authenticate(token).user_id == ana.user_id
    with pytest.raises(Unauthenticated):
        await world.service.authenticate_active(token)
    with pytest.raises(Unauthenticated):
        await world.service.reissue(ana)
    with pytest.raises(Unauthenticated, match="invalid credentials"):
        await world.service.login("ana@acme.com", PASSWORD, None, None)

    view = await world.service.person(admin, created.user.user_id)
    assert not view.user.active
    assert view.user.deactivated_at == world.clock.now()

    await world.service.reactivate_user(admin, created.user.user_id, "came back")
    again = await world.service.login("ana@acme.com", PASSWORD, world.totp(view.user.totp_secret), None)
    assert (await world.service.authenticate_active(again.token)).roles == frozenset({Role.SECURITY})
    assert (await world.service.person(admin, created.user.user_id)).user.deactivated_at is None


async def test_deactivation_ends_open_invitations(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(admin, "leo@acme.com", None, [BindingSpec(Role.REVIEW)])
    assert created.invitation is not None
    await world.service.deactivate_user(admin, created.user.user_id, "wrong person")
    with pytest.raises(Unauthenticated):
        await world.service.accept_invitation(created.invitation.token, NEW_PASSWORD)
    with pytest.raises(Conflict):
        await world.service.reissue_invitation(admin, created.user.user_id)
    with pytest.raises(Conflict, match="already deactivated"):
        await world.service.deactivate_user(admin, created.user.user_id, "again")


async def test_nobody_deactivates_themselves_or_the_last_tenant_admin(world: World) -> None:
    admin = await world.admin()
    with pytest.raises(Conflict, match="themselves"):
        await world.service.deactivate_user(admin, admin.user_id, "testing")

    # An admin of one space passes the role check but is not a tenant-wide admin: cutting the founder,
    # the only tenant-wide admin, would leave the tenant without one.
    sandbox = world.boot.space.space_id
    await world.service.create_user(
        admin, "space@acme.com", PASSWORD, [BindingSpec(Role.ADMIN, sandbox)], with_totp=False
    )
    space_admin, _ = await _sign_in(world, "space@acme.com", sandbox)
    assert Role.ADMIN in space_admin.roles
    with pytest.raises(Conflict, match="at least one admin"):
        await world.service.deactivate_user(space_admin, admin.user_id, "take over")
    assert (await world.service.person(admin, admin.user_id)).user.active


async def test_the_history_keeps_who_changed_what_and_why(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(admin, "eva@acme.com", None, [BindingSpec(Role.REVIEW)])
    user_id = created.user.user_id
    await world.service.set_roles(
        admin, user_id, [BindingSpec(Role.REVIEW), BindingSpec(Role.ANALYSIS)], "measurement too"
    )
    await world.service.deactivate_user(admin, user_id, "vacation")
    await world.service.reactivate_user(admin, user_id, "back from vacation")
    await world.service.reset_second_factor(admin, user_id, "lost the phone")

    history = await world.service.access_history(admin, user_id)
    assert [c.kind for c in history] == [
        AccessChangeKind.SECOND_FACTOR_RESET,
        AccessChangeKind.REACTIVATED,
        AccessChangeKind.DEACTIVATED,
        AccessChangeKind.ROLES_CHANGED,
        AccessChangeKind.INVITED,
    ]
    assert all(c.actor_id == admin.user_id for c in history)
    kinds = {c.kind: c for c in history}
    assert kinds[AccessChangeKind.DEACTIVATED].reason == "vacation"
    roles = kinds[AccessChangeKind.ROLES_CHANGED]
    assert roles.reason == "measurement too"
    assert [r["role"] for r in roles.detail["before"]] == ["review"]
    assert sorted(r["role"] for r in roles.detail["after"]) == ["analysis", "review"]
    assert kinds[AccessChangeKind.SECOND_FACTOR_RESET].reason == "lost the phone"
    assert kinds[AccessChangeKind.INVITED].detail["password"] is False

    # The person's acceptance is theirs.
    token = (await world.service.reissue_invitation(admin, user_id)).token
    await world.service.accept_invitation(token, NEW_PASSWORD)
    latest = (await world.service.access_history(admin, user_id))[0]
    assert (latest.kind, latest.actor_id) == (AccessChangeKind.INVITATION_ACCEPTED, user_id)


async def test_people_of_another_tenant_look_missing_and_only_admins_manage_people(world: World) -> None:
    admin = await world.admin()
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
    owner = world.service.authenticate(
        (await world.service.login("owner@beta.com", PASSWORD, None, None)).token
    )
    for call in (
        world.service.deactivate_user(owner, admin.user_id, "not yours"),
        world.service.reactivate_user(owner, admin.user_id, "not yours"),
        world.service.reissue_invitation(owner, admin.user_id),
        world.service.access_history(owner, admin.user_id),
        world.service.person(owner, admin.user_id),
        world.service.set_roles(owner, admin.user_id, [BindingSpec(Role.REVIEW)]),
    ):
        with pytest.raises(NotFound):
            await call

    reviewer = await world.service.create_user(
        admin, "rev@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)], with_totp=False
    )
    rev, _ = await _sign_in(world, "rev@acme.com")
    for call in (
        world.service.list_users(rev),
        world.service.create_user(rev, "x@acme.com", None, [BindingSpec(Role.REVIEW)]),
        world.service.deactivate_user(rev, admin.user_id, "nope"),
        world.service.reissue_invitation(rev, reviewer.user.user_id),
    ):
        with pytest.raises(Forbidden):
            await call


async def test_a_person_created_with_a_password_keeps_working_as_before(world: World) -> None:
    admin = await world.admin()
    created = await world.service.create_user(admin, "old@acme.com", PASSWORD, [BindingSpec(Role.REVIEW)])
    assert created.invitation is None
    assert created.totp_secret is not None
    token = await world.service.login("old@acme.com", PASSWORD, world.totp(created.totp_secret), None)
    assert token.recovery_codes is not None
