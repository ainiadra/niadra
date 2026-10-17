"""The control plane API (7.4).

Three kinds of caller: Console people with a control-issued JWT, verified here with the control's own
key; cells, with the `x-niadra-cell-token` header (mTLS is the deployment's job, 4.7); and Niadra
operators, with `x-niadra-operator-token`, for tenants, cells, quotas and placement moves.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from niadra.app.control import (
    BindingSpec,
    ControlService,
    IssuedInvitation,
    IssuedKey,
    PersonView,
    SourceSpec,
)
from niadra.app.sso import SsoService
from niadra.contracts.control import (
    AccessChangeOut,
    BindingIn,
    BindingOut,
    CellCreate,
    CellOut,
    ConfigTypeOut,
    DeletionReceiptIn,
    DeletionReceiptOut,
    DiffCreate,
    DiffOut,
    DocumentOut,
    InvitationAcceptOut,
    InvitationAcceptRequest,
    InvitationOut,
    InvitationPreviewOut,
    InvitationTokenRequest,
    IssuedKeyOut,
    KeyCreate,
    KeyOut,
    KeyRotate,
    LoginRequest,
    MeOut,
    MoveOut,
    MoveRequest,
    PasswordRequest,
    PlacementOut,
    PreferenceIn,
    PreferencesOut,
    ProjectCreate,
    ProjectOut,
    ProposalCreate,
    QuotaIn,
    RecoveryCodesOut,
    RevocationFeedOut,
    RevocationOut,
    RevokeRequest,
    RoleOut,
    RolesSet,
    RollbackRequest,
    SecondFactorConfirmRequest,
    SecondFactorOut,
    SecondFactorRequest,
    SecondFactorResetOut,
    SecondFactorResetRequest,
    SecondFactorStatusOut,
    SecretRefIn,
    SecretRefOut,
    SnapshotOut,
    SourceCreate,
    SourceOut,
    SpaceDeletionOut,
    SpaceKeyIn,
    SpaceKeyOut,
    SpaceOut,
    SpaceRoles,
    TenantCreate,
    TenantOut,
    TokenOut,
    TokenRequest,
    UsageItem,
    UsageOut,
    UsagePush,
    UsageStatementLine,
    UsageStatementOut,
    UserCreate,
    UserOut,
    UserStateRequest,
    VersionOut,
)
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.control.credentials import has_password, totp_uri
from niadra.domain.control.records import (
    ApiKey,
    Caller,
    Cell,
    ConfigDiff,
    DeletionReceipt,
    DiffStatus,
    PlacementMove,
    Revocation,
    Role,
    RoleBinding,
    Source,
    Space,
    UsageAggregate,
)
from niadra.domain.control.usage import to_csv

ROLE_DESCRIPTIONS = {
    Role.ADMIN: "Sources, keys, people, roles and SSO",
    Role.SECURITY: "Reads and receipts, policy and simulation, access cuts, erasure, export",
    Role.INTEGRATION: (
        "System mappings, task views, pattern and trigger rules, webhooks, configuration assistant"
    ),
    Role.REVIEW: "The sampled review queue, masked by each session's purpose",
    Role.ANALYSIS: "Measurement, patterns and usage, aggregated or by pseudonym",
    Role.VENDOR: "Only the vendor's own sources and their measurement",
}


@dataclass
class ControlContainer:
    control: ControlService
    cell_token: str
    operator_token: str
    sso: SsoService | None = None
    public_url: str | None = None
    """The control plane's public address, for what the identity provider must call back; read from each
    request when the deployment does not set it."""


router = APIRouter()


def _container(request: Request) -> ControlContainer:
    container: ControlContainer = request.app.state.container
    return container


def _service(request: Request) -> ControlService:
    return _container(request).control


def _same(given: str | None, expected: str) -> bool:
    return bool(given) and bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


async def person(request: Request) -> Caller:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("missing bearer credential")
    # A deactivated person is refused here at once, not when the token expires.
    return await _service(request).authenticate_active(token.strip())


@dataclass(frozen=True)
class CellAuth:
    """A cell's credential, checked against the cell it acts for: a cell with a credential of its own (a
    dedicated cell) is reached only with that one, which acts for no other cell; the others
    share `cell_token`."""

    given: str
    container: ControlContainer

    async def check(self, cell_id: str) -> None:
        own = await self.container.control.cell_credential(cell_id)
        if own is None:
            allowed = _same(self.given, self.container.cell_token)
        else:
            allowed = hmac.compare_digest(hashlib.sha256(self.given.encode()).digest(), own)
        if not allowed:
            raise Unauthenticated("invalid cell credential")


async def cell(request: Request) -> CellAuth:
    given = request.headers.get("x-niadra-cell-token")
    if not given:
        raise Unauthenticated("invalid cell credential")
    return CellAuth(given, _container(request))


async def operator(request: Request) -> None:
    if not _same(request.headers.get("x-niadra-operator-token"), _container(request).operator_token):
        raise Unauthenticated("invalid operator credential")


async def cell_or_operator(request: Request) -> None:
    headers = request.headers
    container = _container(request)
    if not (
        _same(headers.get("x-niadra-cell-token"), container.cell_token)
        or _same(headers.get("x-niadra-operator-token"), container.operator_token)
    ):
        raise Unauthenticated("invalid cell or operator credential")


Person = Annotated[Caller, Depends(person)]
Service = Annotated[ControlService, Depends(_service)]
CellCaller = Annotated[CellAuth, Depends(cell)]


# Login and keys for offline verification


@router.post("/v1/auth/login", response_model=TokenOut)
async def login(body: LoginRequest, service: Service) -> TokenOut:
    issued = await service.login(
        body.email, body.password, body.totp, body.space_id, recovery_code=body.recovery_code
    )
    return TokenOut(
        access_token=issued.token,
        expires_in=issued.expires_in,
        space_id=issued.space_id,
        recovery_codes=issued.recovery_codes,
        recovery_codes_left=issued.recovery_codes_left,
    )


@router.post("/v1/auth/second-factor", response_model=SecondFactorOut)
async def second_factor(body: SecondFactorRequest, service: Service) -> SecondFactorOut:
    """The authenticator enrollment, for a person whose login answered "second factor enrollment
    required": the password again, and the secret comes back. The first valid code at login confirms
    it, and that login answers with the person's recovery codes; once confirmed, this answers 409."""
    secret = await service.second_factor_setup(body.email, body.password)
    return SecondFactorOut(secret=secret, totp_uri=totp_uri(secret, body.email.strip().lower()))


@router.post("/v1/auth/token", response_model=TokenOut)
async def reissue(body: TokenRequest, caller: Person, service: Service) -> TokenOut:
    issued = await service.reissue(caller, body.space_id)
    return TokenOut(access_token=issued.token, expires_in=issued.expires_in, space_id=issued.space_id)


@router.get("/.well-known/jwks.json")
async def jwks(service: Service, response: Response) -> dict[str, object]:
    response.headers["cache-control"] = "public, max-age=300"
    return await service.jwks()


# Tenants, projects, spaces, cells


@router.post("/v1/tenants", response_model=TenantOut, status_code=201, dependencies=[Depends(operator)])
async def create_tenant(body: TenantCreate, service: Service) -> TenantOut:
    t = await service.create_tenant(body.name, body.slug, mfa_required=body.mfa_required)
    return TenantOut(
        tenant_id=t.tenant_id, name=t.name, slug=t.slug, mfa_required=t.mfa_required, created_at=t.created_at
    )


@router.get("/v1/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: UUID, caller: Person, service: Service) -> TenantOut:
    t = await service.get_tenant(caller, tenant_id)
    return TenantOut(
        tenant_id=t.tenant_id, name=t.name, slug=t.slug, mfa_required=t.mfa_required, created_at=t.created_at
    )


@router.post("/v1/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, caller: Person, service: Service) -> ProjectOut:
    view = await service.create_project(caller, body.name, body.slug, body.region)
    p = view.project
    return ProjectOut(
        project_id=p.project_id,
        name=p.name,
        slug=p.slug,
        region=p.region,
        spaces=[_space(s) for s in view.spaces],
    )


@router.get("/v1/projects", response_model=list[ProjectOut])
async def list_projects(caller: Person, service: Service) -> list[ProjectOut]:
    return [
        ProjectOut(
            project_id=v.project.project_id,
            name=v.project.name,
            slug=v.project.slug,
            region=v.project.region,
            spaces=[_space(s) for s in v.spaces],
        )
        for v in await service.projects(caller)
    ]


@router.get("/v1/spaces/{space_id}", response_model=SpaceOut)
async def get_space(space_id: UUID, caller: Person, service: Service) -> SpaceOut:
    return _space(await service.get_space(caller, space_id))


@router.delete("/v1/spaces/{space_id}", response_model=SpaceDeletionOut, status_code=202)
async def delete_space(space_id: UUID, caller: Person, service: Service) -> SpaceDeletionOut:
    """Deletes a space for good. Its keys stop working at once; the cell that serves it drops the space's
    database, destroys its data keys, deletes its stored objects, nightly dumps, secrets and cache keys,
    and sends back a deletion receipt, which `GET /v1/spaces/{space_id}/deletion-receipt` shows. The
    answer comes before the cell finishes, with `status` `deleting`; asking again returns the same state.
    Needs the admin role."""
    return _deletion(*await service.delete_space(caller, space_id))


@router.get("/v1/spaces/{space_id}/deletion-receipt", response_model=SpaceDeletionOut)
async def deletion_receipt(space_id: UUID, caller: Person, service: Service) -> SpaceDeletionOut:
    return _deletion(*await service.deletion(caller, space_id))


@router.post("/v1/spaces/{space_id}/deletion-receipt", response_model=DeletionReceiptOut)
async def store_deletion_receipt(
    space_id: UUID, body: DeletionReceiptIn, auth: CellCaller, service: Service
) -> DeletionReceiptOut:
    """Where a cell reports what it deleted for a deleted space, with the SHA-256 of the receipt's
    canonical JSON. Sending the same receipt again is accepted; a different one for the same space
    answers 409, and one whose hash does not match answers 422."""
    await auth.check(body.cell_id)
    receipt = await service.store_deletion_receipt(body.cell_id, space_id, body.receipt, body.receipt_hash)
    return _receipt(receipt)


@router.get("/v1/spaces/{space_id}/placement", response_model=PlacementOut)
async def placement(space_id: UUID, caller: Person, service: Service) -> PlacementOut:
    space, move = await service.placement(caller, space_id)
    return PlacementOut(space_id=space.space_id, cell_id=space.cell_id, move=_move(move) if move else None)


@router.post(
    "/v1/spaces/{space_id}/placement",
    response_model=MoveOut,
    status_code=202,
    dependencies=[Depends(operator)],
)
async def request_move(space_id: UUID, body: MoveRequest, service: Service) -> MoveOut:
    return _move(await service.request_move(space_id, body.cell_id, body.reason))


@router.post(
    "/v1/spaces/{space_id}/placement/complete", response_model=MoveOut, dependencies=[Depends(operator)]
)
async def complete_move(space_id: UUID, service: Service) -> MoveOut:
    return _move(await service.complete_move(space_id))


@router.get("/v1/cells", response_model=list[CellOut], dependencies=[Depends(cell_or_operator)])
async def list_cells(service: Service) -> list[CellOut]:
    return [_cell(c) for c in await service.list_cells()]


@router.post("/v1/cells", response_model=CellOut, status_code=201, dependencies=[Depends(operator)])
async def register_cell(body: CellCreate, service: Service) -> CellOut:
    return _cell(await service.register_cell(body.cell_id, body.region, body.address, body.capacity))


@router.put("/v1/spaces/{space_id}/kms-key", response_model=SpaceKeyOut, dependencies=[Depends(operator)])
async def set_space_key(space_id: UUID, body: SpaceKeyIn, service: Service) -> SpaceKeyOut:
    """Operators only: a customer-managed key is a contract, never a tenant setting."""
    space = await service.set_space_key(space_id, body.kms_key_arn)
    return SpaceKeyOut(space_id=space.space_id, kms_key_arn=space.kms_key_arn)


@router.put("/v1/quotas", response_model=QuotaIn, dependencies=[Depends(operator)])
async def set_quota(body: QuotaIn, service: Service) -> QuotaIn:
    q = await service.set_quota(body.tenant_id, body.metric, body.limit, body.period)
    return QuotaIn(tenant_id=q.tenant_id, metric=q.metric, limit=q.limit, period=q.period)


# Sources and keys


@router.post("/v1/sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceCreate, caller: Person, service: Service) -> SourceOut:
    spec = SourceSpec(
        name=body.name,
        audience=body.audience,
        vendor=body.vendor,
        channel=body.channel,
        purposes=frozenset(body.purposes),
        verification_ceiling=body.verification_ceiling,
        confirms_before_acting=body.confirms_before_acting,
        navigation_enabled=body.navigation_enabled,
        trusted_action_ops=frozenset(body.trusted_action_ops),
        critical=body.critical,
    )
    return _source(await service.create_source(caller, body.space_id, spec))


@router.get("/v1/sources", response_model=list[SourceOut])
async def list_sources(space_id: UUID, caller: Person, service: Service) -> list[SourceOut]:
    return [_source(s) for s in await service.list_sources(caller, space_id)]


@router.post("/v1/sources/{source_id}/keys", response_model=IssuedKeyOut, status_code=201)
async def issue_key(source_id: UUID, body: KeyCreate, caller: Person, service: Service) -> IssuedKeyOut:
    scopes = frozenset(body.scopes) if body.scopes else None
    return _issued(await service.issue_key(caller, source_id, scopes))


@router.get("/v1/sources/{source_id}/keys", response_model=list[KeyOut])
async def list_keys(source_id: UUID, caller: Person, service: Service) -> list[KeyOut]:
    return [_key(k) for k in await service.list_keys(caller, source_id)]


@router.post("/v1/sources/{source_id}/keys/{key_id}/rotate", response_model=IssuedKeyOut, status_code=201)
async def rotate_key(
    source_id: UUID, key_id: str, body: KeyRotate, caller: Person, service: Service
) -> IssuedKeyOut:
    return _issued(await service.rotate_key(caller, source_id, key_id, body.grace_seconds))


@router.post("/v1/sources/{source_id}/keys/{key_id}/revoke", response_model=RevocationOut)
async def revoke_key(
    source_id: UUID, key_id: str, body: RevokeRequest, caller: Person, service: Service
) -> RevocationOut:
    return _revocation(await service.revoke_key(caller, source_id, key_id, body.reason))


@router.post("/v1/sources/{source_id}/revoke", response_model=RevocationOut)
async def revoke_source(
    source_id: UUID, body: RevokeRequest, caller: Person, service: Service
) -> RevocationOut:
    return _revocation(await service.revoke_source(caller, source_id, body.reason))


# People and roles


@router.get("/v1/roles", response_model=list[RoleOut])
async def roles(_: Person) -> list[RoleOut]:
    return [RoleOut(role=r, description=d) for r, d in ROLE_DESCRIPTIONS.items()]


@router.post("/v1/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, caller: Person, service: Service) -> UserOut:
    """A new person of the tenant. Without `password`, the answer carries the invitation, shown once: a
    single-use link where the person chooses a password; the authenticator comes at the first sign-in. A
    person whose e-mail domain belongs to the tenant's SSO signs in through it and gets no invitation."""
    created = await service.create_user(
        caller, body.email, body.password, _bindings(body.roles), with_totp=body.with_totp
    )
    uri = totp_uri(created.totp_secret, created.user.email) if created.totp_secret else None
    view = PersonView(
        created.user,
        created.bindings,
        0,
        created.invitation.invitation if created.invitation else None,
        created.sso,
    )
    return _user(view, uri, created.invitation)


@router.get("/v1/users/me", response_model=MeOut)
async def me(caller: Person, service: Service) -> MeOut:
    found = await service.me(caller)
    return MeOut(
        user_id=found.user.user_id,
        tenant_id=found.user.tenant_id,
        email=found.user.email,
        password_set=has_password(found.user.password_hash),
        sso=found.sso,
        spaces=[
            SpaceRoles(
                space_id=s.space_id,
                project_id=s.project_id,
                environment=s.environment,
                slug=s.slug,
                region=s.region,
                roles=sorted({b.role for b in found.bindings if b.space_id in (None, s.space_id)}),
            )
            for s in found.spaces
        ],
    )


@router.get("/v1/users/me/preferences", response_model=PreferencesOut)
async def my_preferences(caller: Person, service: Service) -> PreferencesOut:
    """What the Console keeps for the signed-in person across browsers: the tutorials done, the welcome
    seen. Only the person reads and writes their own."""
    return PreferencesOut(preferences=await service.preferences(caller))


@router.put("/v1/users/me/preferences/{key}", response_model=PreferencesOut)
async def set_my_preference(key: str, body: PreferenceIn, caller: Person, service: Service) -> PreferencesOut:
    """Writes one key and answers every key. A person keeps at most 16 keys of 4096 bytes of JSON each;
    past either, 422."""
    return PreferencesOut(preferences=await service.set_preference(caller, key, body.value))


@router.get("/v1/users/me/second-factor", response_model=SecondFactorStatusOut)
async def my_second_factor(caller: Person, service: Service) -> SecondFactorStatusOut:
    """The second factor of the signed-in person: when the authenticator in use was confirmed, and how
    many recovery codes are left."""
    status = await service.second_factor_status(caller)
    return SecondFactorStatusOut(
        confirmed_at=status.confirmed_at, recovery_codes_left=status.recovery_codes_left
    )


@router.post("/v1/users/me/recovery-codes", response_model=RecoveryCodesOut)
async def regenerate_recovery_codes(
    body: PasswordRequest, caller: Person, service: Service
) -> RecoveryCodesOut:
    """Ten new recovery codes for the signed-in person, shown once; the earlier ones stop working. Asks for
    the password again, and a wrong one answers 403, so a typo never ends the session. 409 while the
    person has no confirmed authenticator."""
    return RecoveryCodesOut(
        recovery_codes=await service.regenerate_recovery_codes(caller, body.password, body.totp)
    )


@router.post("/v1/users/me/second-factor", response_model=SecondFactorOut)
async def change_second_factor(body: PasswordRequest, caller: Person, service: Service) -> SecondFactorOut:
    """Starts the move to another authenticator app, after the password again (403 when wrong). The new
    secret comes back and waits beside the current one, which keeps working until
    POST /v1/users/me/second-factor/confirm receives a first code of the new app. Starting again replaces
    a secret that was never confirmed."""
    new = await service.start_second_factor_change(caller, body.password, body.totp)
    return SecondFactorOut(secret=new.secret, totp_uri=totp_uri(new.secret, new.account))


@router.post("/v1/users/me/second-factor/confirm", response_model=RecoveryCodesOut)
async def confirm_second_factor(
    body: SecondFactorConfirmRequest, caller: Person, service: Service
) -> RecoveryCodesOut:
    """Switches to the new authenticator app with its first code: the old app stops working, and ten new
    recovery codes come back, shown once. A wrong code answers 403; 409 when no change was started."""
    return RecoveryCodesOut(recovery_codes=await service.confirm_second_factor_change(caller, body.totp))


@router.get("/v1/users", response_model=list[UserOut])
async def list_users(caller: Person, service: Service) -> list[UserOut]:
    """Every person of the tenant, with roles, how they sign in and the state of the second factor."""
    return [_user(v) for v in await service.list_users(caller)]


@router.put("/v1/users/{user_id}/roles", response_model=list[BindingOut])
async def set_roles(user_id: UUID, body: RolesSet, caller: Person, service: Service) -> list[BindingOut]:
    """Replaces every role of the person. The tenant keeps at least one admin (409 otherwise). For a person
    who signs in through the tenant's SSO, the next sign-in sets the roles from the provider's groups."""
    rows = await service.set_roles(caller, user_id, _bindings(body.roles), body.reason)
    return [_binding(b) for b in rows]


@router.post("/v1/users/{user_id}/deactivate", response_model=UserOut)
async def deactivate_user(user_id: UUID, body: UserStateRequest, caller: Person, service: Service) -> UserOut:
    """The person can no longer sign in, and the control plane refuses their token at once; the data API
    accepts a token already issued until it expires, 15 minutes at most. Open invitations stop working. 409
    for the caller themselves and for the last admin of the tenant."""
    await service.deactivate_user(caller, user_id, body.reason)
    return _user(await service.person(caller, user_id))


@router.post("/v1/users/{user_id}/reactivate", response_model=UserOut)
async def reactivate_user(user_id: UUID, body: UserStateRequest, caller: Person, service: Service) -> UserOut:
    """Lets a deactivated person sign in again, with the same roles, password and second factor."""
    await service.reactivate_user(caller, user_id, body.reason)
    return _user(await service.person(caller, user_id))


@router.post("/v1/users/{user_id}/invitation", response_model=InvitationOut, status_code=201)
async def reissue_invitation(user_id: UUID, caller: Person, service: Service) -> InvitationOut:
    """A new single-use link for the person to choose a password: for one who never used the first link, or
    who forgot the password. Earlier links stop working. Shown once."""
    return _invitation(await service.reissue_invitation(caller, user_id))


@router.get("/v1/users/{user_id}/history", response_model=list[AccessChangeOut])
async def access_history(
    user_id: UUID, caller: Person, service: Service, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AccessChangeOut]:
    """What changed in the person's access, newest first: invitations, roles, deactivation, second factor
    resets and what the tenant's SSO did at a sign-in, with who and why."""
    return [
        AccessChangeOut(
            change_id=c.change_id,
            kind=c.kind,
            at=c.at,
            user_id=c.user_id,
            actor_id=c.actor_id,
            reason=c.reason,
            detail=c.detail,
        )
        for c in await service.access_history(caller, user_id, limit)
    ]


@router.post("/v1/auth/invitation/preview", response_model=InvitationPreviewOut)
async def preview_invitation(body: InvitationTokenRequest, service: Service) -> InvitationPreviewOut:
    """Whose invitation this is, before the person chooses a password. 401 alike for an unknown, used or
    expired link."""
    preview = await service.preview_invitation(body.token)
    return InvitationPreviewOut(
        email=preview.email, tenant_name=preview.tenant_name, expires_at=preview.expires_at
    )


@router.post("/v1/auth/invitation", response_model=InvitationAcceptOut)
async def accept_invitation(body: InvitationAcceptRequest, service: Service) -> InvitationAcceptOut:
    """Sets the password of the invited person (12 characters or more) and spends the link. The person then
    signs in and sets up the authenticator app, like everyone."""
    return InvitationAcceptOut(email=await service.accept_invitation(body.token, body.password))


@router.post("/v1/users/{user_id}/second-factor/reset", response_model=SecondFactorResetOut)
async def reset_second_factor(
    user_id: UUID, body: SecondFactorResetRequest, caller: Person, service: Service
) -> SecondFactorResetOut:
    """For a person who lost the authenticator app and the recovery codes: clears the secret, its
    confirmation and every code, so the person sets up an authenticator again at the next login. The
    reset is kept with who did it and why. Needs the admin role; a person of another tenant answers 404."""
    r = await service.reset_second_factor(caller, user_id, body.reason)
    return SecondFactorResetOut(
        reset_id=r.reset_id, user_id=r.user_id, reset_by=r.reset_by, reason=r.reason, at=r.at
    )


# Versioned configuration. Fixed paths first: `/v1/config/{type}` would swallow them.


@router.get("/v1/config/types", response_model=list[ConfigTypeOut])
async def config_types(_: Person, service: Service) -> list[ConfigTypeOut]:
    return [
        ConfigTypeOut(type=name, description=t.description, roles=sorted(t.roles))
        for name, t in service.config_types().items()
    ]


@router.post("/v1/config/diffs", response_model=DiffOut, status_code=201)
async def submit_diff(body: DiffCreate, caller: Person, service: Service) -> DiffOut:
    return await _named(
        service, await service.submit_diff(caller, body.space_id, body.type, body.document, body.reason)
    )


@router.get("/v1/config/diffs", response_model=list[DiffOut])
async def list_diffs(
    space_id: UUID, caller: Person, service: Service, status: DiffStatus | None = None
) -> list[DiffOut]:
    diffs = await service.list_diffs(caller, space_id, status)
    people = await service.diff_people(diffs)
    return [_diff(d, people) for d in diffs]


@router.post("/v1/config/diffs/{diff_id}/approve", response_model=DiffOut)
async def approve_diff(diff_id: UUID, caller: Person, service: Service) -> DiffOut:
    return await _named(service, await service.approve_diff(caller, diff_id))


@router.post("/v1/config/diffs/{diff_id}/reject", response_model=DiffOut)
async def reject_diff(diff_id: UUID, caller: Person, service: Service) -> DiffOut:
    return await _named(service, await service.reject_diff(caller, diff_id))


@router.post("/v1/config/diffs/{diff_id}/rollback", response_model=DiffOut)
async def rollback_diff(diff_id: UUID, body: RollbackRequest, caller: Person, service: Service) -> DiffOut:
    return await _named(service, await service.rollback_diff(caller, diff_id, body.reason))


@router.get("/v1/config/history", response_model=list[VersionOut])
async def history(
    space_id: UUID,
    caller: Person,
    service: Service,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[VersionOut]:
    return [
        VersionOut(
            version=v.version,
            type=v.doc_type,
            diff_id=v.diff_id,
            created_at=v.created_at,
            document=v.document,
        )
        for v in await service.history(caller, space_id, type, limit)
    ]


@router.post("/v1/config/proposals", response_model=DiffOut, status_code=201)
async def propose(body: ProposalCreate, auth: CellCaller, service: Service) -> DiffOut:
    await auth.check(body.cell_id)
    return await _named(
        service,
        await service.propose_diff(body.cell_id, body.space_id, body.type, body.document, body.reason),
    )


@router.post("/v1/config/observed", response_model=DiffOut, status_code=201)
async def record_observed(body: ProposalCreate, auth: CellCaller, service: Service) -> DiffOut:
    """What a cell inferred of a document from the space's traffic, as an applied diff by `niadra:observed`;
    it changes no declaration."""
    await auth.check(body.cell_id)
    return await _named(
        service,
        await service.record_observed(body.cell_id, body.space_id, body.type, body.document, body.reason),
    )


@router.get("/v1/config/{type}", response_model=DocumentOut)
async def config_document(type: str, space_id: UUID, caller: Person, service: Service) -> DocumentOut:
    version, document = await service.config_document(caller, space_id, type)
    return DocumentOut(space_id=space_id, type=type, version=version, document=document)


@router.post("/v1/secret-refs", response_model=SecretRefOut, status_code=201)
async def put_secret_ref(body: SecretRefIn, caller: Person, service: Service) -> SecretRefOut:
    r = await service.put_secret_ref(caller, body.space_id, body.ref, body.kind, body.version)
    return SecretRefOut(
        space_id=r.space_id,
        ref=r.ref,
        kind=r.kind,
        version=r.version,
        rotated_by=r.rotated_by,
        rotated_at=r.rotated_at,
    )


@router.get("/v1/secret-refs", response_model=list[SecretRefOut])
async def secret_refs(space_id: UUID, caller: Person, service: Service) -> list[SecretRefOut]:
    return [
        SecretRefOut(
            space_id=r.space_id,
            ref=r.ref,
            kind=r.kind,
            version=r.version,
            rotated_by=r.rotated_by,
            rotated_at=r.rotated_at,
        )
        for r in await service.secret_refs(caller, space_id)
    ]


# Distribution to cells


@router.get("/v1/snapshot/{cell_id}", response_model=SnapshotOut)
async def snapshot(
    cell_id: str,
    auth: CellCaller,
    service: Service,
    after: Annotated[int | None, Query(ge=0)] = None,
    wait: Annotated[float, Query(ge=0, le=30)] = 25,
) -> SnapshotOut | Response:
    """With `after`, a long poll: answers when a newer version exists, or 304 at the deadline."""
    await auth.check(cell_id)
    if after is None:
        s = await service.snapshot(cell_id)
    else:
        found = await service.wait_snapshot(cell_id, after, wait)
        if found is None:
            return Response(status_code=304)
        s = found
    return SnapshotOut(
        manifest=s.manifest,
        signature=_b64(s.signature),
        kid=s.kid,
        documents=s.documents,
        keys=s.keys,
        keys_signature=_b64(s.keys_signature),
    )


@router.get("/v1/revocations", response_model=RevocationFeedOut)
async def revocations(
    cell_id: str, auth: CellCaller, service: Service, after: Annotated[int, Query(ge=0)] = 0
) -> RevocationFeedOut:
    await auth.check(cell_id)
    feed = await service.revocations(cell_id, after)
    return RevocationFeedOut(body=feed.body, kid=feed.kid, signature=_b64(feed.signature))


@router.get("/v1/access-changes", response_model=RevocationFeedOut)
async def access_changes(
    cell_id: str, auth: CellCaller, service: Service, after: datetime | None = None
) -> RevocationFeedOut:
    """Changes to people's access and to SSO, signed, for the cell's receipt chains. The body is
    `{"cell_id", "after", "changes": [...]}`; pass `after` back next time."""
    await auth.check(cell_id)
    feed = await service.access_feed(cell_id, after)
    return RevocationFeedOut(body=feed.body, kid=feed.kid, signature=_b64(feed.signature))


@router.post("/v1/usage", status_code=202)
async def push_usage(body: UsagePush, auth: CellCaller, service: Service) -> dict[str, int]:
    await auth.check(body.cell_id)
    aggregates = [
        UsageAggregate(
            i.space_id, i.period_start, i.metric, i.quantity, body.cell_id, i.period_start, i.cost_micros
        )
        for i in body.items
    ]
    return {"accepted": await service.push_usage(body.cell_id, aggregates)}


@router.get("/v1/usage", response_model=UsageOut)
async def usage(since: datetime, until: datetime, caller: Person, service: Service) -> UsageOut:
    """Usage aggregates of the spaces the caller's analysis, security or admin role covers, and the
    tenant's quotas."""
    report = await service.usage(caller, since, until)
    return UsageOut(
        items=[
            UsageItem(
                space_id=a.space_id,
                period_start=a.period_start,
                metric=a.metric,
                quantity=a.quantity,
                cost_micros=a.cost_micros,
            )
            for a in report.aggregates
        ],
        quotas=[
            QuotaIn(tenant_id=q.tenant_id, metric=q.metric, limit=q.limit, period=q.period)
            for q in report.quotas
        ],
    )


@router.get(
    "/v1/usage/statement",
    response_model=UsageStatementOut,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
async def usage_statement(
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$", description="The UTC month, `YYYY-MM`.")],
    caller: Person,
    service: Service,
    format: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
) -> UsageStatementOut | Response:
    """The month's usage per space, to invoice from: conversations and tasks, every metric and the AI
    cost; `format=csv` for a spreadsheet. It lists the spaces of the tenant that the caller's analysis,
    security or admin role covers: all of them for a role on the whole tenant, one for a role on one."""
    lines = await service.usage_statement(caller, month)
    if format == "csv":
        return Response(
            to_csv(lines),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="niadra-usage-{month}.csv"'},
        )
    return UsageStatementOut(
        month=month,
        lines=[
            UsageStatementLine(
                space_id=line.space_id,
                project_id=line.project_id,
                environment=line.environment,
                slug=line.slug,
                billable=line.billable,
                ai_cost_micros=line.ai_cost_micros,
                metrics=line.metrics,
            )
            for line in lines
        ],
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _space(s: Space) -> SpaceOut:
    return SpaceOut(
        space_id=s.space_id,
        project_id=s.project_id,
        environment=s.environment,
        slug=s.slug,
        region=s.region,
        cell_id=s.cell_id,
    )


def _cell(c: Cell) -> CellOut:
    return CellOut(
        cell_id=c.cell_id,
        region=c.region,
        address=c.address,
        capacity=c.capacity,
        status=c.status,
        code_version=c.code_version,
        schema_revision=c.schema_revision,
    )


def _move(m: PlacementMove) -> MoveOut:
    return MoveOut(
        move_id=m.move_id,
        from_cell=m.from_cell,
        to_cell=m.to_cell,
        status=m.status,
        requested_at=m.requested_at,
        completed_at=m.completed_at,
    )


def _source(s: Source) -> SourceOut:
    return SourceOut(
        source_id=s.source_id,
        space_id=s.space_id,
        name=s.name,
        audience=s.audience,
        vendor=s.vendor,
        channel=s.channel,
        purposes=sorted(s.purposes),
        verification_ceiling=s.verification_ceiling,
        confirms_before_acting=s.confirms_before_acting,
        navigation_enabled=s.navigation_enabled,
        trusted_action_ops=sorted(s.trusted_action_ops),
        critical=s.critical,
        revoked_at=s.revoked_at,
    )


def _key(k: ApiKey) -> KeyOut:
    return KeyOut(
        key_id=k.key_id,
        source_id=k.source_id,
        scopes=sorted(k.scopes),
        status=k.status,
        created_at=k.created_at,
        expires_at=k.expires_at,
        revoked_at=k.revoked_at,
    )


def _issued(issued: IssuedKey) -> IssuedKeyOut:
    return IssuedKeyOut(key=_key(issued.key), secret=issued.plaintext)


def _revocation(r: Revocation) -> RevocationOut:
    return RevocationOut(
        seq=r.seq, space_id=r.space_id, kind=r.kind.value, source_id=r.source_id, key_id=r.key_id, at=r.at
    )


def _bindings(items: list[BindingIn]) -> list[BindingSpec]:
    return [BindingSpec(b.role, b.space_id, frozenset(b.source_ids)) for b in items]


def _binding(b: RoleBinding) -> BindingOut:
    return BindingOut(role=b.role, space_id=b.space_id, source_ids=sorted(b.source_ids))


def _user(v: PersonView, uri: str | None = None, issued: IssuedInvitation | None = None) -> UserOut:
    u = v.user
    return UserOut(
        user_id=u.user_id,
        email=u.email,
        active=u.active,
        roles=[_binding(b) for b in v.bindings],
        last_login_at=u.last_login_at,
        totp_uri=uri,
        created_at=u.created_at,
        deactivated_at=u.deactivated_at,
        password_set=has_password(u.password_hash),
        sso=v.sso,
        last_sso_login_at=u.last_sso_login_at,
        second_factor_confirmed_at=u.totp_confirmed_at if u.totp_secret else None,
        recovery_codes_left=v.recovery_codes_left,
        invitation_expires_at=v.invitation.expires_at if v.invitation else None,
        invitation=_invitation(issued) if issued else None,
    )


def _invitation(issued: IssuedInvitation) -> InvitationOut:
    return InvitationOut(token=issued.token, expires_at=issued.invitation.expires_at)


async def _named(service: ControlService, diff: ConfigDiff) -> DiffOut:
    return _diff(diff, await service.diff_people([diff]))


def _diff(d: ConfigDiff, people: Mapping[UUID, str]) -> DiffOut:
    return DiffOut(
        diff_id=d.diff_id,
        space_id=d.space_id,
        type=d.doc_type,
        status=d.status,
        reason=d.reason,
        base_version=d.base_version,
        author_id=d.author_id,
        author_label=d.author_label or (people.get(d.author_id) if d.author_id else None),
        decided_by=d.decided_by,
        decided_by_label=people.get(d.decided_by) if d.decided_by else None,
        decided_at=d.decided_at,
        applied_version=d.applied_version,
        rollback_of=d.rollback_of,
        created_at=d.created_at,
        document=d.document,
    )


def _deletion(space: Space, receipt: DeletionReceipt | None) -> SpaceDeletionOut:
    assert space.deleted_at is not None
    return SpaceDeletionOut(
        space_id=space.space_id,
        deleted_at=space.deleted_at,
        status="deleted" if receipt else "deleting",
        receipt=_receipt(receipt) if receipt else None,
    )


def _receipt(record: DeletionReceipt) -> DeletionReceiptOut:
    doc = record.receipt
    return DeletionReceiptOut(
        space_id=record.space_id,
        cell_id=record.cell_id,
        db_name=doc["db_name"],
        dropped_at=doc["dropped_at"],
        dek_versions_destroyed=doc["dek_versions_destroyed"],
        objects_deleted=doc["objects_deleted"],
        backups_deleted=doc["backups_deleted"],
        secrets_deleted=doc["secrets_deleted"],
        instance_snapshot_expiry=doc["instance_snapshot_expiry"],
        receipt_hash=record.receipt_hash.hex(),
        received_at=record.received_at,
    )
